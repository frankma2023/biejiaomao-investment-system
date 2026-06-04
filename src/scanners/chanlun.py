"""缠论分析引擎 - 基于 CZSC 库

计算流程: K线 → 分型 → 笔 → 中枢 → 背驰 → 买卖信号
数据持久化到 SQLite: chanlun_bi, chanlun_fx, chanlun_zs, chanlun_signals

v2.0 (2026-05-28): 新增中枢检测、背驰诊断、买卖信号生成
"""

import sqlite3, pandas as pd, math, json
from datetime import datetime
from czsc import CZSC, RawBar, Freq

DB_PATH = r"D:\hanako\investment-system\data\lixinger.db"
SUPPORTED_FREQ = {"D": Freq.D, "W": Freq.W, "M": Freq.M}

def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chanlun_bi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            freq TEXT NOT NULL,
            sdt TEXT NOT NULL,
            edt TEXT NOT NULL,
            direction TEXT NOT NULL,
            high REAL, low REAL,
            power REAL, slope REAL, angle REAL, length INTEGER,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE TABLE IF NOT EXISTS chanlun_fx (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            freq TEXT NOT NULL,
            dt TEXT NOT NULL,
            fx_type TEXT NOT NULL,
            high REAL, low REAL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE TABLE IF NOT EXISTS chanlun_zs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            freq TEXT NOT NULL,
            start_dt TEXT NOT NULL,
            end_dt TEXT NOT NULL,
            zg REAL, zd REAL, zz REAL,
            bi_count INTEGER,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_chanlun_bi_code ON chanlun_bi(stock_code, freq);
        CREATE INDEX IF NOT EXISTS idx_chanlun_fx_code ON chanlun_fx(stock_code, freq);
        CREATE INDEX IF NOT EXISTS idx_chanlun_zs_code ON chanlun_zs(stock_code, freq);
        CREATE TABLE IF NOT EXISTS chanlun_segment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            freq TEXT NOT NULL,
            sdt TEXT NOT NULL,
            edt TEXT NOT NULL,
            direction TEXT NOT NULL,
            high REAL, low REAL,
            bi_count INTEGER,
            amplitude REAL, slope REAL, days INTEGER,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_chanlun_segment_code ON chanlun_segment(stock_code, freq);
    """)
    conn.commit()


# ═══════════════════════════════════════════════
# 中枢检测
# ═══════════════════════════════════════════════

def compute_zhongshu(bi_list_raw):
    """从笔列表计算中枢（连续3笔重叠法）
    
    缠论定义：连续三段次级别走势类型（笔）重叠部分构成中枢。
    这里用简化版：滑动窗口检测每3笔是否有重叠区间。
    
    Args:
        bi_list_raw: CZSC 的 BI 对象列表
    
    Returns:
        list[dict]: 每项 {start_dt, end_dt, zg, zd, zz, width, bi_indices}
    """
    n = len(bi_list_raw)
    if n < 3:
        return []
    
    zs_list = []
    i = 0
    while i <= n - 3:
        b0, b1, b2 = bi_list_raw[i], bi_list_raw[i+1], bi_list_raw[i+2]
        ZG = min(b0.high, b1.high, b2.high)
        ZD = max(b0.low, b1.low, b2.low)
        
        if ZG > ZD and ZG > 0 and ZD > 0:
            ZZ = (ZG + ZD) / 2  # 中枢中轴
            zs_list.append({
                "start_dt": str(b0.sdt)[:10],
                "end_dt": str(b2.edt)[:10],
                "zg": float(ZG),
                "zd": float(ZD),
                "zz": float(ZZ),
                "width": float(ZG - ZD),
                "width_pct": round(float(ZG - ZD) / float(ZZ) * 100, 2) if ZZ else 0,
                "bi_indices": [i, i+1, i+2],
                "bi_dirs": [str(b0.direction), str(b1.direction), str(b2.direction)]
            })
            # 有重叠 → 可能有延伸，检查下一笔
            i += 1
        else:
            i += 1
    
    # 合并连续重叠的中枢（简化：相邻且重叠则合并为一个延伸中枢）
    merged = []
    for zs in zs_list:
        if not merged:
            merged.append(zs)
            continue
        last = merged[-1]
        # 如果两个中枢在日期上相邻（bi_indices 连续），且价格区间重叠>50%
        overlap_low = max(zs["zd"], last["zd"])
        overlap_high = min(zs["zg"], last["zg"])
        if overlap_high > overlap_low:
            overlap_pct = (overlap_high - overlap_low) / (zs["zg"] - zs["zd"]) if (zs["zg"] - zs["zd"]) else 0
            if overlap_pct > 0.5 and zs["bi_indices"][0] > last["bi_indices"][0]:
                # 延伸：扩展 end_dt 和 bi_indices
                last["end_dt"] = zs["end_dt"]
                last["zg"] = min(last["zg"], zs["zg"])
                last["zd"] = max(last["zd"], zs["zd"])
                last["zz"] = (last["zg"] + last["zd"]) / 2
                last["width"] = last["zg"] - last["zd"]
                last["bi_indices"].extend(zs["bi_indices"])
                last["bi_dirs"].extend(zs["bi_dirs"])
                continue
        merged.append(zs)
    
    # 去重 & 给每个中枢编号
    for idx, zs in enumerate(merged):
        zs["idx"] = idx + 1
        unique_bi = list(set(zs["bi_indices"]))
        unique_bi.sort()
        zs["bi_count"] = len(unique_bi)
    
    return merged


# ═══════════════════════════════════════════════
# 线段计算
# ═══════════════════════════════════════════════

def compute_segments(bi_list_raw):
    """从笔列表计算线段（笔的高级别走势单元）
    
    缠论定义：线段由至少 3 笔构成，前三笔有重叠区间。
    MVP 采用简化规则：基于极值突破判断线段终结。
    
    Args:
        bi_list_raw: CZSC 的 BI 对象列表
    
    Returns:
        list[dict]: 线段列表
    """
    n = len(bi_list_raw)
    if n < 3:
        return []
    
    segments = []
    i = 0
    
    while i <= n - 3:
        b0, b1, b2 = bi_list_raw[i], bi_list_raw[i+1], bi_list_raw[i+2]
        
        # 检查前三笔是否有重叠区间（构成线段的基本条件）
        zg = min(b0.high, b1.high, b2.high)
        zd = max(b0.low, b1.low, b2.low)
        
        if zg <= zd:
            # 无重叠，不构成线段，继续前进
            i += 1
            continue
        
        # 前三笔构成线段，确定线段方向和极值
        seg_dir = str(b0.direction)
        is_up = seg_dir in ("up", "向上")
        
        # 收集线段内的笔
        seg_bis = [b0, b1, b2]
        seg_bi_indices = [i, i+1, i+2]
        seg_high = max(b0.high, b1.high, b2.high)
        seg_low = min(b0.low, b1.low, b2.low)
        
        # 逐笔延伸：向后延伸直到线段被破坏
        j = i + 3
        while j < n:
            curr = bi_list_raw[j]
            curr_dir = str(curr.direction)
            
            if curr_dir == seg_dir:
                # 同向笔：延续线段
                seg_bis.append(curr)
                seg_bi_indices.append(j)
                seg_high = max(seg_high, float(curr.high))
                seg_low = min(seg_low, float(curr.low))
                j += 1
            else:
                # 反向笔：检查是否破坏线段
                # 向上线段被破坏：反向笔的低点 < 线段最低点
                # 向下线段被破坏：反向笔的高点 > 线段最高点
                broken = False
                if is_up and float(curr.low) < seg_low:
                    broken = True
                elif not is_up and float(curr.high) > seg_high:
                    broken = True
                
                if broken:
                    # 线段在此终结
                    break
                else:
                    # 反向笔未破坏线段，继续延伸
                    seg_bis.append(curr)
                    seg_bi_indices.append(j)
                    seg_high = max(seg_high, float(curr.high))
                    seg_low = min(seg_low, float(curr.low))
                    j += 1
        
        # 线段必须有奇数笔（线段方向由第一笔定，最后一笔与第一笔同向）
        # 简化处理：如果最后收集的是偶数笔，去掉最后一笔
        bi_count = len(seg_bis)
        if bi_count % 2 == 0 and bi_count > 3:
            seg_bis = seg_bis[:-1]
            seg_bi_indices = seg_bi_indices[:-1]
        
        first_bi = seg_bis[0]
        last_bi = seg_bis[-1]
        
        # 计算线段属性
        amplitude = seg_high - seg_low
        days = (last_bi.edt - first_bi.sdt).days if hasattr(last_bi.edt, 'days') else 0
        if days == 0:
            days = len(seg_bis) * 5  # 估算
        
        # 斜率：用高低点估算
        if days > 0:
            if is_up:
                slope = (seg_high - seg_low) / days
            else:
                slope = (seg_low - seg_high) / days
        else:
            slope = 0
        
        segments.append({
            "sdt": str(first_bi.sdt)[:10],
            "edt": str(last_bi.edt)[:10],
            "direction": seg_dir,
            "high": round(float(seg_high), 2),
            "low": round(float(seg_low), 2),
            "bi_count": len(seg_bis),
            "bi_indices": seg_bi_indices,
            "amplitude": round(float(amplitude), 2),
            "slope": round(float(slope), 4),
            "days": days
        })
        
        # 下一段从 j 开始（即破坏线段的那一笔）
        i = j
    
    # 给线段编号
    for idx, seg in enumerate(segments):
        seg["idx"] = idx + 1
    
    return segments


def compute_segment_zs(segment_list):
    """从线段列表计算线段级别中枢
    
    逻辑与 compute_zhongshu() 一致，输入从笔列表换为线段列表。
    """
    n = len(segment_list)
    if n < 3:
        return []
    
    zs_list = []
    i = 0
    while i <= n - 3:
        s0, s1, s2 = segment_list[i], segment_list[i+1], segment_list[i+2]
        # 线段对象是 dict，用键访问
        h0, h1, h2 = s0["high"], s1["high"], s2["high"]
        l0, l1, l2 = s0["low"], s1["low"], s2["low"]
        
        ZG = min(h0, h1, h2)
        ZD = max(l0, l1, l2)
        
        if ZG > ZD:
            ZZ = (ZG + ZD) / 2
            zs_list.append({
                "start_dt": s0["sdt"],
                "end_dt": s2["edt"],
                "zg": round(float(ZG), 2),
                "zd": round(float(ZD), 2),
                "zz": round(float(ZZ), 2),
                "width": round(float(ZG - ZD), 2),
                "width_pct": round(float(ZG - ZD) / float(ZZ) * 100, 2) if ZZ else 0,
                "segment_indices": [i, i+1, i+2],
                "level": "线段级别"
            })
        i += 1
    
    for idx, zs in enumerate(zs_list):
        zs["idx"] = idx + 1
    
    return zs_list


# ═══════════════════════════════════════════════
# 背驰检测
# ═══════════════════════════════════════════════

def detect_divergence(bi_list_raw, zs_list):
    """检测背驰信号
    
    两类背驰：
    1. 趋势背驰：中枢前后两段同向趋势的力度衰减
    2. 盘整背驰：中枢内部震荡的力度衰减
    
    Returns:
        list[dict]: 背驰信号列表
    """
    signals = []
    n = len(bi_list_raw)
    if n < 5:
        return signals
    
    # ── 趋势背驰 ──
    # 缠论笔是严格交替的（上→下→上→下），需比较间隔的同向笔
    # 即笔[i] vs 笔[i+2]（中间隔一个反向笔）
    for i in range(0, n - 2):
        prev_bi = bi_list_raw[i]
        curr_bi = bi_list_raw[i+2]  # 间隔一个反向笔
        
        # 必须同向
        if prev_bi.direction != curr_bi.direction:
            continue
        
        # 同向笔比较力度
        if prev_bi.power and curr_bi.power and prev_bi.power > 0:
            power_ratio = curr_bi.power / prev_bi.power
            
            # 力度衰减阈值：< 0.6 视为背驰（调整后更敏感）
            if power_ratio < 0.6:
                direction_str = str(curr_bi.direction)
                # 向上笔力度衰减 = 顶背驰
                sig_type = "顶背驰" if direction_str in ("up", "向上") else "底背驰"
                
                signals.append({
                    "type": sig_type,
                    "category": "趋势背驰",
                    "dt": str(curr_bi.sdt)[:10],
                    "bi_idx": i + 2,
                    "prev_bi_idx": i,
                    "power_ratio": round(power_ratio, 2),
                    "prev_power": round(float(prev_bi.power), 1),
                    "curr_power": round(float(curr_bi.power), 1),
                    "price_ampl_ratio": round(float(curr_bi.high - curr_bi.low) / float(prev_bi.high - prev_bi.low), 2) 
                        if (prev_bi.high - prev_bi.low) > 0 else 0,
                    "severity": "强" if power_ratio < 0.3 else "中" if power_ratio < 0.45 else "弱"
                })
    
    # ── 盘整背驰 ──
    # 中枢内部：检查离开中枢的笔是否背驰
    for zs in zs_list:
        bi_indices = zs.get("bi_indices", [])
        if len(bi_indices) < 4:
            continue
        
        # 中枢第一笔（进入中枢）和最后一笔（离开中枢）同向则比较
        first_bi_idx = bi_indices[0]
        last_bi_idx = bi_indices[-1]
        if last_bi_idx < n and first_bi_idx < n:
            first_bi = bi_list_raw[first_bi_idx]
            last_bi = bi_list_raw[last_bi_idx]
            if first_bi.direction == last_bi.direction and first_bi.power and last_bi.power and first_bi.power > 0:
                power_ratio = last_bi.power / first_bi.power
                if power_ratio < 0.6:
                    direction_str = str(last_bi.direction)
                    sig_type = "盘整顶背驰" if direction_str == "up" or direction_str == "向上" else "盘整底背驰"
                    signals.append({
                        "type": sig_type,
                        "category": "盘整背驰",
                        "dt": str(last_bi.sdt)[:10],
                        "bi_idx": last_bi_idx,
                        "zs_idx": zs.get("idx", 0),
                        "power_ratio": round(power_ratio, 2),
                        "prev_power": round(float(first_bi.power), 1),
                        "curr_power": round(float(last_bi.power), 1),
                        "severity": "强" if power_ratio < 0.4 else "中" if power_ratio < 0.5 else "弱"
                    })
    
    # 按日期倒序排列（最新在前）
    signals.sort(key=lambda x: x["dt"], reverse=True)
    return signals


# ═══════════════════════════════════════════════
# 买卖信号生成
# ═══════════════════════════════════════════════

def generate_trade_signals(bi_list_raw, zs_list, divergence_signals):
    """基于中枢和背驰生成缠论买卖信号
    
    一买：下跌趋势 + 底背驰 → 趋势反转点
    二买：一买后回调不破新低 → 确认信号
    三买：中枢上方回调不破ZG → 强势信号
    一卖：上涨趋势 + 顶背驰 → 趋势反转点
    二卖：一卖后反弹不破新高 → 确认信号
    三卖：中枢下方反弹不破ZD → 弱势信号
    """
    trade_sigs = []
    
    # 解析趋势背驰 → 一买/一卖
    for div in divergence_signals:
        if div["category"] != "趋势背驰":
            continue
        
        bi_idx = div.get("bi_idx", 0)
        prev_bi_idx = div.get("prev_bi_idx", 0)
        if bi_idx >= len(bi_list_raw) or prev_bi_idx >= len(bi_list_raw):
            continue
        
        curr_bi = bi_list_raw[bi_idx]
        direction = str(curr_bi.direction)
        
        # 底背驰（向下笔力度衰减） → 一买
        if direction in ("down", "向下") and div["type"] == "底背驰":
            trade_sigs.append({
                "type": "一买",
                "side": "buy",
                "dt": str(curr_bi.sdt)[:10],
                "price": round(float(curr_bi.low), 2),
                "reason": f"下跌趋势底背驰（力度比{div['power_ratio']}），可能趋势反转",
                "confidence": {"强": "高", "中": "中"}.get(div["severity"], "低"),
                "div_signal": div["type"],
                "bi_idx": bi_idx
            })
        
        # 顶背驰（向上笔力度衰减） → 一卖
        if direction in ("up", "向上") and div["type"] == "顶背驰":
            trade_sigs.append({
                "type": "一卖",
                "side": "sell",
                "dt": str(curr_bi.sdt)[:10],
                "price": round(float(curr_bi.high), 2),
                "reason": f"上涨趋势顶背驰（力度比{div['power_ratio']}），可能趋势反转",
                "confidence": {"强": "高", "中": "中"}.get(div["severity"], "低"),
                "div_signal": div["type"],
                "bi_idx": bi_idx
            })
    
    # ═══ 盘整背驰 → 类一买/类一卖 ═══
    for div in divergence_signals:
        if div["category"] != "盘整背驰":
            continue
        
        bi_idx = div.get("bi_idx", 0)
        if bi_idx >= len(bi_list_raw):
            continue
        
        curr_bi = bi_list_raw[bi_idx]
        
        # 盘整底背驰 → 类一买（中枢内向下笔衰竭，可能向上离开中枢）
        if div["type"] == "盘整底背驰":
            trade_sigs.append({
                "type": "类一买",
                "side": "buy",
                "dt": str(curr_bi.sdt)[:10],
                "price": round(float(curr_bi.low), 2),
                "reason": f"中枢盘整底背驰（力度比{div['power_ratio']}），中枢内向下衰竭，关注突破方向",
                "confidence": {"强": "中", "中": "低"}.get(div["severity"], "低"),
                "div_signal": div["type"],
                "bi_idx": bi_idx
            })
        
        # 盘整顶背驰 → 类一卖（中枢内向上笔衰竭，可能向下离开中枢）
        if div["type"] == "盘整顶背驰":
            trade_sigs.append({
                "type": "类一卖",
                "side": "sell",
                "dt": str(curr_bi.sdt)[:10],
                "price": round(float(curr_bi.high), 2),
                "reason": f"中枢盘整顶背驰（力度比{div['power_ratio']}），中枢内向上衰竭，关注跌破方向",
                "confidence": {"强": "中", "中": "低"}.get(div["severity"], "低"),
                "div_signal": div["type"],
                "bi_idx": bi_idx
            })
    
    # ═══ 二买/二卖检测（基于一买/一卖位置） ═══
    first_buy_sigs = [s for s in trade_sigs if s["type"] == "一买"]
    first_sell_sigs = [s for s in trade_sigs if s["type"] == "一卖"]
    
    for fb in first_buy_sigs:
        bi_idx = fb["bi_idx"]
        if bi_idx + 2 >= len(bi_list_raw):
            continue
        bounce_bi = bi_list_raw[bi_idx + 1]
        ret_bi = bi_list_raw[bi_idx + 2]
        bounce_dir = str(bounce_bi.direction)
        ret_dir = str(ret_bi.direction)
        
        # 一买后：反弹向上 → 回调向下 → 回调低点高于一买低点 → 二买
        if bounce_dir in ("up", "向上") and ret_dir in ("down", "向下"):
            if float(ret_bi.low) > fb["price"]:
                trade_sigs.append({
                    "type": "二买",
                    "side": "buy",
                    "dt": str(ret_bi.sdt)[:10],
                    "price": round(float(ret_bi.low), 2),
                    "reason": f"一买后回调不破前低（回调低点{ret_bi.low:.1f} > 一买低点{fb['price']:.1f}），确认反转",
                    "confidence": "高",
                    "bi_idx": bi_idx + 2
                })
    
    for fs in first_sell_sigs:
        bi_idx = fs["bi_idx"]
        if bi_idx + 2 >= len(bi_list_raw):
            continue
        fall_bi = bi_list_raw[bi_idx + 1]
        ret_bi = bi_list_raw[bi_idx + 2]
        fall_dir = str(fall_bi.direction)
        ret_dir = str(ret_bi.direction)
        
        # 一卖后：下跌 → 反弹向上 → 反弹高点低于一卖高点 → 二卖
        if fall_dir in ("down", "向下") and ret_dir in ("up", "向上"):
            if float(ret_bi.high) < fs["price"]:
                trade_sigs.append({
                    "type": "二卖",
                    "side": "sell",
                    "dt": str(ret_bi.sdt)[:10],
                    "price": round(float(ret_bi.high), 2),
                    "reason": f"一卖后反弹不破前高（反弹高点{ret_bi.high:.1f} < 一卖高点{fs['price']:.1f}），确认反转",
                    "confidence": "高",
                    "bi_idx": bi_idx + 2
                })
    
    # 三买/三卖检测（基于中枢位置）
    for zs in zs_list:
        bi_indices = zs.get("bi_indices", [])
        if len(bi_indices) < 4:
            continue
        ZG = zs["zg"]
        ZD = zs["zd"]
        last_bi_idx = bi_indices[-1]
        
        # 三买：中枢上方回调不破ZG（离开中枢后回踩不破ZG）
        if last_bi_idx + 2 < len(bi_list_raw):
            out_bi = bi_list_raw[last_bi_idx + 1]  # 离开中枢的笔
            ret_bi = bi_list_raw[last_bi_idx + 2]  # 回踩的笔
            out_dir = str(out_bi.direction)
            ret_dir = str(ret_bi.direction)
            
            if out_dir in ("up", "向上") and ret_dir in ("down", "向下"):
                # 向上离开后向下回踩，不破ZG
                if ret_bi.low > ZG:
                    trade_sigs.append({
                        "type": "三买",
                        "side": "buy",
                        "dt": str(ret_bi.sdt)[:10],
                        "price": round(float(ret_bi.low), 2),
                        "reason": f"中枢上方回调不破ZG({ZG:.1f})，强势确认",
                        "confidence": "高",
                        "zs_idx": zs.get("idx", 0)
                    })
            
            if out_dir in ("down", "向下") and ret_dir in ("up", "向上"):
                # 向下离开后向上回抽，不破ZD
                if ret_bi.high < ZD:
                    trade_sigs.append({
                        "type": "三卖",
                        "side": "sell",
                        "dt": str(ret_bi.sdt)[:10],
                        "price": round(float(ret_bi.high), 2),
                        "reason": f"中枢下方反弹不破ZD({ZD:.1f})，弱势确认",
                        "confidence": "高",
                        "zs_idx": zs.get("idx", 0)
                    })
    
    # 按日期倒序排列（最新在前）
    trade_sigs.sort(key=lambda x: x["dt"], reverse=True)
    
    return trade_sigs


# ═══════════════════════════════════════════════
# 背驰后追踪
# ═══════════════════════════════════════════════

def track_divergence_outcomes(divergence_signals, bi_list, zs_list):
    """追踪背驰信号后的实际走势结果
    
    对每条背驰信号，分析后续笔的走势，判断属于三种结果之一：
    - 反趋势（最强）：离开原中枢后形成同向新中枢，且不重叠
    - 更大级别盘整（较弱）：形成新中枢但与原中枢有重叠
    - 中枢扩展（最弱）：未形成新中枢，在原中枢范围内震荡
    
    Returns:
        list[dict]: 每条背驰信号附加 outcome 字段
    """
    results = []
    
    for div in divergence_signals:
        bi_idx = div.get("bi_idx", 0)
        n_after = len(bi_list) - bi_idx - 1
        
        outcome = {
            "div_type": div["type"],
            "div_dt": div["dt"],
            "div_bi_idx": bi_idx,
            "bis_after": n_after,
            "outcome": "数据不足",
            "detail": ""
        }
        
        if n_after < 3:
            outcome["outcome"] = "数据不足"
            outcome["detail"] = f"信号后仅{n_after}笔，无法判断"
            results.append(outcome)
            continue
        
        # 取信号后的笔
        after_bis = bi_list[bi_idx + 1:]
        
        # 在后续笔中重新检测中枢
        # 用简化的3笔重叠法检测后续是否有新中枢
        new_zs_list = _detect_zs_in_bis(after_bis)
        
        # 找到原中枢（背驰发生时所在的中枢）
        ref_zs = None
        for zs in zs_list:
            indices = zs.get("bi_indices", [])
            if indices and min(indices) <= bi_idx <= max(indices):
                ref_zs = zs
                break
        # 如果没找到，取最后一个包含 bi_idx 之前笔的中枢
        if ref_zs is None and zs_list:
            for zs in reversed(zs_list):
                if zs.get("bi_indices", []) and max(zs["bi_indices"]) <= bi_idx:
                    ref_zs = zs
                    break
        
        is_bottom = "底" in div["type"]  # 底背驰（买点）
        
        if not new_zs_list:
            # 无新中枢 → 中枢扩展（最弱）
            outcome["outcome"] = "中枢扩展"
            outcome["detail"] = f"信号后{len(after_bis)}笔未形成新中枢，原中枢震荡延续"
        elif ref_zs:
            first_new_zs = new_zs_list[0]
            if is_bottom:
                # 底背驰后期望向上
                if first_new_zs["zd"] > ref_zs["zg"]:
                    # 新中枢完全在原中枢上方 → 反趋势
                    outcome["outcome"] = "反趋势"
                    outcome["detail"] = f"形成新中枢{first_new_zs['zd']:.1f}~{first_new_zs['zg']:.1f}，完全在原中枢({ref_zs['zd']:.1f}~{ref_zs['zg']:.1f})上方"
                elif first_new_zs["zg"] >= ref_zs["zd"] and first_new_zs["zd"] <= ref_zs["zg"]:
                    # 有重叠 → 更大级别盘整
                    outcome["outcome"] = "更大级别盘整"
                    outcome["detail"] = f"新中枢与原有重叠（新:{first_new_zs['zd']:.1f}~{first_new_zs['zg']:.1f} 原:{ref_zs['zd']:.1f}~{ref_zs['zg']:.1f}）"
                else:
                    outcome["outcome"] = "中枢扩展"
                    outcome["detail"] = f"新中枢在原中枢下方，底背驰失效"
            else:
                # 顶背驰后期望向下
                if first_new_zs["zg"] < ref_zs["zd"]:
                    outcome["outcome"] = "反趋势"
                    outcome["detail"] = f"形成新中枢{first_new_zs['zd']:.1f}~{first_new_zs['zg']:.1f}，完全在原中枢({ref_zs['zd']:.1f}~{ref_zs['zg']:.1f})下方"
                elif first_new_zs["zg"] >= ref_zs["zd"] and first_new_zs["zd"] <= ref_zs["zg"]:
                    outcome["outcome"] = "更大级别盘整"
                    outcome["detail"] = f"新中枢与原有重叠（新:{first_new_zs['zd']:.1f}~{first_new_zs['zg']:.1f} 原:{ref_zs['zd']:.1f}~{ref_zs['zg']:.1f}）"
                else:
                    outcome["outcome"] = "中枢扩展"
                    outcome["detail"] = f"新中枢在原中枢上方，顶背驰失效"
        else:
            # 有中枢但无参考中枢
            outcome["outcome"] = "反趋势"
            outcome["detail"] = f"形成新中枢（无原中枢参照）"
        
        results.append(outcome)
    
    return results


def _detect_zs_in_bis(bi_list):
    """在给定的笔列表中检测中枢（简化版，不合并延伸）"""
    n = len(bi_list)
    if n < 3:
        return []
    
    zs_list = []
    for i in range(n - 2):
        b0, b1, b2 = bi_list[i], bi_list[i+1], bi_list[i+2]
        h0 = float(b0.get("high", 0)) if isinstance(b0, dict) else float(b0.high)
        l0 = float(b0.get("low", 0)) if isinstance(b0, dict) else float(b0.low)
        h1 = float(b1.get("high", 0)) if isinstance(b1, dict) else float(b1.high)
        l1 = float(b1.get("low", 0)) if isinstance(b1, dict) else float(b1.low)
        h2 = float(b2.get("high", 0)) if isinstance(b2, dict) else float(b2.high)
        l2 = float(b2.get("low", 0)) if isinstance(b2, dict) else float(b2.low)
        
        ZG = min(h0, h1, h2)
        ZD = max(l0, l1, l2)
        
        if ZG > ZD and ZG > 0 and ZD > 0:
            zs_list.append({
                "zg": round(ZG, 2),
                "zd": round(ZD, 2),
                "bi_indices": [i, i+1, i+2]
            })
    
    # 简单去重：相邻且重叠的合并
    merged = []
    for zs in zs_list:
        if not merged:
            merged.append(zs)
            continue
        last = merged[-1]
        if max(zs["zd"], last["zd"]) < min(zs["zg"], last["zg"]):
            # 重叠，合并
            last["zg"] = min(last["zg"], zs["zg"])
            last["zd"] = max(last["zd"], zs["zd"])
        else:
            merged.append(zs)
    
    return merged


# ═══════════════════════════════════════════════
# 走势类型判断
# ═══════════════════════════════════════════════

def classify_trend(zs_list, bi_list=None, ubi=None):
    """基于中枢列表判断走势类型，结合当前价格位置修正
    
    缠论定义：
    - 上涨趋势：至少两个中枢，后一个完全在前一个上方（ZG[i] < ZD[i+1]）
    - 下跌趋势：至少两个中枢，后一个完全在前一个下方（ZD[i] > ZG[i+1]）
    - 盘整：只有一个中枢，或多个中枢但有重叠
    
    修正规则（结合当前价格位置）：
    - 盘整 + 价格在最新中枢下方 + 向下笔 → 盘整偏空（可能正在向下离开）
    - 盘整 + 价格在最新中枢上方 + 向上笔 → 盘整偏多（可能正在向上离开）
    
    Args:
        zs_list: compute_zhongshu() 的输出（已合并重叠中枢）
        bi_list: 笔列表，用于判断历史笔方向
        ubi: 未完成笔（CZSC.ubi），用于获取当前实时价格和方向
    
    Returns:
        dict: {trend_type, direction, zs_count, description, confidence, ...}
    """
    n = len(zs_list)
    
    # 当前笔方向和价格：优先用未完成笔 ubi，其次用最后已完成笔
    current_dir = None
    current_price = None
    
    if ubi and isinstance(ubi, dict):
        current_dir = str(ubi.get("direction", ""))
        if current_dir in ("down", "向下"):
            current_price = float(ubi.get("low", 0))
        else:
            current_price = float(ubi.get("high", 0))
    
    if current_price is None and bi_list and len(bi_list) > 0:
        last_bi = bi_list[-1]
        current_dir = str(last_bi.get("direction", "")) if isinstance(last_bi, dict) else str(last_bi.direction)
        if current_dir in ("down", "向下"):
            current_price = float(last_bi.get("low", 0)) if isinstance(last_bi, dict) else float(last_bi.low)
        else:
            current_price = float(last_bi.get("high", 0)) if isinstance(last_bi, dict) else float(last_bi.high)
    
    if n == 0:
        return {
            "trend_type": "无中枢",
            "direction": "不明",
            "zs_count": 0,
            "description": "未检测到有效中枢，走势为单边或杂乱波动",
            "confidence": "低"
        }
    
    if n == 1:
        zs = zs_list[0]
        width_pct = zs.get("width_pct", 0)
        return {
            "trend_type": "盘整",
            "direction": "不明",
            "zs_count": 1,
            "description": f"仅一个中枢（{zs['zd']:.1f}~{zs['zg']:.1f}，宽{width_pct}%），处于盘整中",
            "zg": zs["zg"],
            "zd": zs["zd"],
            "confidence": "高"
        }
    
    # 两两比较相邻中枢
    up_pairs = 0
    down_pairs = 0
    overlap_pairs = 0
    
    for i in range(n - 1):
        prev_zs = zs_list[i]
        next_zs = zs_list[i + 1]
        
        if prev_zs["zg"] < next_zs["zd"]:
            # 前一个中枢完全在下一个下方 → 上涨
            up_pairs += 1
        elif prev_zs["zd"] > next_zs["zg"]:
            # 前一个中枢完全在下一个上方 → 下跌
            down_pairs += 1
        else:
            # 有重叠 → 盘整
            overlap_pairs += 1
    
    total_pairs = n - 1
    
    # 判定
    if overlap_pairs > 0:
        trend_type = "盘整"
        direction = "不明"
        desc = f"{n}个中枢中有重叠，处于盘整中"
        confidence = "高" if overlap_pairs == total_pairs else "中"
    elif up_pairs == total_pairs:
        trend_type = "上涨趋势"
        direction = "向上"
        desc = f"{n}个中枢依次上移（无重叠），处于上涨趋势中"
        confidence = "高" if n >= 3 else "中"
    elif down_pairs == total_pairs:
        trend_type = "下跌趋势"
        direction = "向下"
        desc = f"{n}个中枢依次下移（无重叠），处于下跌趋势中"
        confidence = "高" if n >= 3 else "中"
    else:
        # 混合：有上有下
        trend_type = "盘整"
        direction = "不明"
        desc = f"{n}个中枢方向不一致，整体处于盘整"
        confidence = "低"
    
    # 最近中枢信息
    last_zs = zs_list[-1]
    first_zs = zs_list[0]
    
    # ── 结合当前价格修正 ──
    position_vs_zs = "内部"
    if current_price and last_zs:
        if current_price > last_zs["zg"]:
            position_vs_zs = "上方"
        elif current_price < last_zs["zd"]:
            position_vs_zs = "下方"
    
    bi_dir_label = "向上笔" if current_dir in ("up", "向上") else "向下笔" if current_dir in ("down", "向下") else ""
    
    # 修正：盘整但价格已离开中枢
    if trend_type == "盘整" and position_vs_zs == "下方" and current_dir in ("down", "向下"):
        direction = "向下"
        desc += f"，但当前价格{current_price:.1f}已跌破中枢下沿{last_zs['zd']:.1f}（{bi_dir_label}），正在向下离开盘整区"
    elif trend_type == "盘整" and position_vs_zs == "上方" and current_dir in ("up", "向上"):
        direction = "向上"
        desc += f"，当前价格{current_price:.1f}已突破中枢上沿{last_zs['zg']:.1f}（{bi_dir_label}），正在向上离开盘整区"
    elif trend_type == "盘整" and position_vs_zs == "下方" and current_dir not in ("down", "向下"):
        desc += f"，当前价格{current_price:.1f}低于中枢下沿{last_zs['zd']:.1f}，关注是否有效跌破"
    elif trend_type == "盘整" and position_vs_zs == "上方" and current_dir not in ("up", "向上"):
        desc += f"，当前价格{current_price:.1f}高于中枢上沿{last_zs['zg']:.1f}，关注是否有效突破"
    elif trend_type == "盘整" and position_vs_zs == "内部":
        desc += f"，当前价格{current_price:.1f}在中枢范围内"
    elif trend_type == "上涨趋势" and position_vs_zs == "下方":
        desc += f"，⚠️ 当前价格{current_price:.1f}已回落至最新中枢下沿{last_zs['zd']:.1f}下方，趋势可能转弱"
    elif trend_type == "下跌趋势" and position_vs_zs == "上方":
        desc += f"，⚠️ 当前价格{current_price:.1f}已反弹至最新中枢上沿{last_zs['zg']:.1f}上方，趋势可能转强"
    
    return {
        "trend_type": trend_type,
        "direction": direction,
        "zs_count": n,
        "description": desc,
        "confidence": confidence,
        "position_vs_zs": position_vs_zs,
        "latest_zg": last_zs["zg"],
        "latest_zd": last_zs["zd"],
        "latest_range": f"{last_zs['zd']:.1f}~{last_zs['zg']:.1f}",
        "first_zg": first_zs["zg"],
        "first_zd": first_zs["zd"],
    }


# ═══════════════════════════════════════════════
# 主分析函数
# ═══════════════════════════════════════════════

def analyze(code, freq="D", limit=500, data_mode="auto"):
    """分析单只股票/指数的缠论结构
    
    Args:
        code: 股票代码 (如 000985)
        freq: K线周期 (D/W/M)
        limit: 读取K线数量
        data_mode: 'stock'|'index'|'auto' — 数据表选择，auto 按前缀推断
    
    Returns:
        dict: {kline_count, bi_count, fx_count, zs_count, divergence_count,
               bi_list, fx_list, zs_list, divergence_signals, trade_signals}
    """
    freq_enum = SUPPORTED_FREQ.get(freq, Freq.D)
    
    # 确定数据表：优先用 data_mode，auto 按前缀推断
    if data_mode == 'stock':
        table = 'daily_kline'
    elif data_mode == 'index':
        table = 'index_daily_kline'
    else:
        # auto: 000/399 开头的优先查指数表，查不到再试个股表
        if code.startswith("000") or code.startswith("399"):
            # 先在指数表试，无数据则回退个股表
            conn = _connect()
            cnt = conn.execute("SELECT COUNT(*) FROM index_daily_kline WHERE stock_code=?", (code,)).fetchone()[0]
            conn.close()
            table = "index_daily_kline" if cnt > 0 else "daily_kline"
        else:
            table = "daily_kline"
    
    # 日线数据读取范围：按日期回溯，确保三种周期都覆盖约 3 年
    # 日线模式用 max(limit, 750)；周/月线需要更多日线来合成
    if freq == "D":
        read_limit = max(limit, 750)
    else:
        read_limit = max(limit * 3, 900)
    
    conn = _connect()
    df = pd.read_sql(f"""
        SELECT date, open, high, low, close, volume, amount
        FROM {table}
        WHERE stock_code = ?
        ORDER BY date DESC
        LIMIT ?
    """, conn, params=(code, read_limit))
    conn.close()
    
    if df.empty:
        return {"error": f"无K线数据: {code}"}
    
    df = df.sort_values("date").reset_index(drop=True)
    
    # 转换为 RawBar
    df["dt"] = pd.to_datetime(df["date"])
    bars = []
    for _, row in df.iterrows():
        bars.append(RawBar(
            symbol=code, dt=row["dt"], freq=freq_enum,
            open=row.open, close=row.close, high=row.high, low=row.low,
            vol=row.volume, amount=row.amount
        ))
    
    # CZSC 计算
    czsc_obj = CZSC(bars)
    
    # ── 提取笔列表 ──
    bi_list = []
    for bi in czsc_obj.bi_list:
        bi_list.append({
            "sdt": str(bi.sdt),
            "edt": str(bi.edt),
            "direction": str(bi.direction),
            "high": float(bi.high) if bi.high is not None and bi.high == bi.high else 0,
            "low": float(bi.low) if bi.low is not None and bi.low == bi.low else 0,
            "power": float(bi.power) if bi.power is not None and bi.power == bi.power else 0,
            "slope": float(bi.slope) if bi.slope is not None and bi.slope == bi.slope else 0,
            "angle": float(bi.angle) if bi.angle is not None and bi.angle == bi.angle else 0,
            "length": int(bi.length) if bi.length else 0
        })
    
    # ── 提取分型列表 ──
    fx_list = []
    for fx in czsc_obj.fx_list:
        fx_type = getattr(fx, 'mark', getattr(fx, 'fx_type', getattr(fx, 'fx', '?')))
        fx_list.append({
            "dt": str(fx.dt),
            "fx_type": str(fx_type),
            "high": float(fx.high) if fx.high is not None and fx.high == fx.high else 0,
            "low": float(fx.low) if fx.low is not None and fx.low == fx.low else 0
        })
    
    # ── 中枢检测 ──
    zs_list = compute_zhongshu(czsc_obj.bi_list)
    
    # ── 线段计算 ──
    segment_list = compute_segments(czsc_obj.bi_list)
    segment_zs_list = compute_segment_zs(segment_list)
    
    # ── 背驰检测 ──
    divergence_signals = detect_divergence(czsc_obj.bi_list, zs_list)
    
    # ── 买卖信号 ──
    trade_signals = generate_trade_signals(czsc_obj.bi_list, zs_list, divergence_signals)
    
    # ── 未完成笔 ──
    ubi_info = None
    if czsc_obj.ubi:
        ubi_info = {
            "direction": str(czsc_obj.ubi.get("direction", "?")),
            "high": float(czsc_obj.ubi.get("high", 0)),
            "low": float(czsc_obj.ubi.get("low", 0))
        }
    
    # ── 走势类型判断 ──
    trend_classification = classify_trend(zs_list, bi_list, ubi_info)
    
    # ── 背驰后追踪 ──
    divergence_outcomes = track_divergence_outcomes(divergence_signals, bi_list, zs_list)
    
    return {
        "code": code,
        "freq": freq,
        "kline_count": len(bars),
        "bi_count": len(bi_list),
        "fx_count": len(fx_list),
        "zs_count": len(zs_list),
        "segment_count": len(segment_list),
        "segment_zs_count": len(segment_zs_list),
        "divergence_count": len(divergence_signals),
        "trade_signal_count": len(trade_signals),
        "ubi": ubi_info,
        "bi_list": bi_list,
        "fx_list": fx_list,
        "zs_list": zs_list,
        "segment_list": segment_list,
        "segment_zs_list": segment_zs_list,
        "divergence_signals": divergence_signals,
        "trade_signals": trade_signals,
        "trend_classification": trend_classification,
        "divergence_outcomes": divergence_outcomes
    }


# ═══════════════════════════════════════════════
# K线周期合成（日→周/月）
# ═══════════════════════════════════════════════

def _synthesize_klines(df, target_freq):
    """从日线 DataFrame 合成周线或月线 OHLC
    
    Args:
        df: 日线 DataFrame（含 date, open, high, low, close, volume, amount）
        target_freq: "W" 或 "M"
    
    Returns:
        DataFrame: 合成后的 OHLC，含 period_date 列
    """
    df = df.copy()
    df["date_dt"] = pd.to_datetime(df["date"])
    df = df.set_index("date_dt").sort_index()
    
    if target_freq == "W":
        # 周线：按周五收盘日合成（A股周五是每周最后交易日）
        rule = "W-FRI"
        label = "right"
    elif target_freq == "M":
        # 月线：按月最后一天合成
        rule = "ME"
        label = "right"
    else:
        return df.reset_index().rename(columns={"date_dt": "period_date"})
    
    resampled = df.resample(rule, label=label, closed="right").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "amount": "sum"
    }).dropna()
    
    # 如果该周/月只有一个交易日，跳过（不完整的周期）
    # 实际按 A 股规则：少于 2 个交易日的周期也保留
    
    resampled = resampled.reset_index()
    resampled["period_date"] = resampled["date_dt"].dt.strftime("%Y-%m-%d")
    return resampled


def _build_period_index_map(daily_df, target_freq, period_dates):
    """构建 日线日期 → 合成周期索引 的映射表
    
    例如：3月24日~3月28日这周的日线日期都映射到该周K线的索引
    
    Args:
        daily_df: 日线 DataFrame
        target_freq: "W" 或 "M"
        period_dates: 合成后的周期日期列表
    
    Returns:
        dict: {日线日期str: 周期索引int}
    """
    date_idx = {}
    daily_df = daily_df.copy()
    daily_df["dt"] = pd.to_datetime(daily_df["date"])
    
    for i, period_str in enumerate(period_dates):
        period_dt = pd.to_datetime(period_str)
        
        if target_freq == "W":
            # 该周：period_dt 所在周的周一~period_dt（周五）
            week_start = period_dt - pd.Timedelta(days=period_dt.weekday())
            mask = (daily_df["dt"] >= week_start) & (daily_df["dt"] <= period_dt)
        else:  # "M"
            # 该月：period_dt 所在月的第一天~最后一天
            month_start = period_dt.replace(day=1)
            # 下月第一天减一天 = 本月最后一天
            if period_dt.month == 12:
                month_end = period_dt.replace(year=period_dt.year+1, month=1, day=1) - pd.Timedelta(days=1)
            else:
                month_end = period_dt.replace(month=period_dt.month+1, day=1) - pd.Timedelta(days=1)
            mask = (daily_df["dt"] >= month_start) & (daily_df["dt"] <= month_end)
        
        matched_dates = daily_df.loc[mask, "date"].tolist()
        for d in matched_dates:
            date_idx[d] = i
    
    return date_idx


# ═══════════════════════════════════════════════
# ECharts 配置生成
# ═══════════════════════════════════════════════

def get_echarts_option(code, freq="D", limit=400, theme="dark", data_mode="auto"):
    """构建 ECharts option（含 K线 + 成交量 + 笔标记 + 中枢矩形 + 买卖点）
    
    当 freq="W" 或 "M" 时，K线图使用从日线合成的周/月 K线，
    而非原始日线 K线。CZSC 仍基于日线 RawBar 计算笔/中枢（freq 参数控制级别）。
    
    日线数据读取范围：按日期回溯 3 年（约 750 根日线），确保周/月合成后
    仍有足够数量的 K 线（周线约 150 根，月线约 36 根）。
    """
    from datetime import date, timedelta
    
    freq_enum = SUPPORTED_FREQ.get(freq, Freq.D)
    
    # 确定数据表
    if data_mode == 'stock':
        table = 'daily_kline'
    elif data_mode == 'index':
        table = 'index_daily_kline'
    else:
        if code.startswith("000") or code.startswith("399"):
            conn = _connect()
            cnt = conn.execute("SELECT COUNT(*) FROM index_daily_kline WHERE stock_code=?", (code,)).fetchone()[0]
            conn.close()
            table = "index_daily_kline" if cnt > 0 else "daily_kline"
        else:
            table = "daily_kline"
    chg_col = "change" if table == "index_daily_kline" else "change_pct"
    
    # 日线需要足够多数据来合成周/月线：按日期范围回溯 3 年
    if freq == "D":
        read_limit = max(limit, 750)
    else:
        read_limit = max(limit * 3, 900)  # 周/月线需要大量日线来合成
    
    conn = _connect()
    df = pd.read_sql(f"""
        SELECT date, open, high, low, close, volume, amount, {chg_col} as change
        FROM {table}
        WHERE stock_code = ?
        ORDER BY date DESC LIMIT ?
    """, conn, params=(code, read_limit))
    conn.close()
    
    if df.empty:
        return {"error": "无数据"}
    
    df = df.sort_values("date").reset_index(drop=True)
    df = df.dropna(subset=["open", "close", "high", "low"])
    
    # ── 日线 RawBar → CZSC（始终用日线，freq 参数控制笔/中枢的级别处理）──
    df["dt"] = pd.to_datetime(df["date"])
    bars = [RawBar(symbol=code, dt=row["dt"], freq=freq_enum,
                   open=row.open, close=row.close, high=row.high, low=row.low,
                   vol=row.volume, amount=row.amount) for _, row in df.iterrows()]
    czsc_obj = CZSC(bars)
    
    # ── K线数据：日线直接使用，周/月线合成 ──
    if freq == "D":
        ohlc_df = df.copy()
        ohlc_df["period_date"] = df["date"]
    else:
        ohlc_df = _synthesize_klines(df, freq)
    
    dates = ohlc_df["period_date"].tolist()
    
    # 构建 date → index 映射（bi 的日线日期 → 合成后的周/月索引）
    if freq == "D":
        date_idx = {d: i for i, d in enumerate(dates)}
    else:
        # 日线日期 → 所属周期日期 → 合成索引
        date_idx = _build_period_index_map(df, freq, dates)
    
    # ── hanako-glass 配色 ──
    is_dark = theme == "dark"
    chart_bg = "rgba(26,26,31,.6)" if is_dark else "rgba(255,255,255,.75)"
    axis_color = "rgba(200,200,200,0.3)" if is_dark else "rgba(128,128,128,0.3)"
    grid_color = "rgba(200,200,200,0.08)" if is_dark else "rgba(128,128,128,0.1)"
    up_color = "#ef4444" if is_dark else "#dc2626"
    down_color = "#10b981" if is_dark else "#059669"
    
    # ── K线数据 ──
    ohlc_data = [[row.open, row.close, row.low, row.high] for _, row in ohlc_df.iterrows()]
    
    # ── 成交量数据 ──
    vol_data = []
    for _, row in ohlc_df.iterrows():
        is_up = row.close >= row.open
        vol_data.append({"value": int(row.volume), "itemStyle": {"color": up_color if is_up else down_color}})
    
    # ── 笔标记 (markPoint) ──
    mark_points = []
    for bi in czsc_obj.bi_list:
        try:
            s = str(bi.sdt)[:10]
            if s in date_idx:
                idx = date_idx[s]
                is_down = str(bi.direction) in ("down", "向下")
                mark_points.append({
                    "name": "笔顶" if is_down else "笔底",
                    "coord": [idx, float(bi.high) if is_down else float(bi.low)],
                    "value": ("顶" if is_down else "底") + str(round(float(bi.high if is_down else bi.low), 0)),
                    "symbol": "arrow",
                    "symbolRotate": 0 if is_down else 180,
                    "symbolSize": 10,
                    "symbolOffset": [0, -6 if is_down else 6],
                    "itemStyle": {"color": up_color if is_down else down_color},
                    "label": {"show": True, "fontSize": 8, "fontWeight": "normal",
                              "color": up_color if is_down else down_color,
                              "offset": [0, -10] if is_down else [0, 10]}
                })
        except Exception:
            pass
    
    # ── 中枢矩形 (markArea) ──
    zs_list = compute_zhongshu(czsc_obj.bi_list)
    zs_mark_areas = []
    zs_mark_lines = []  # ZG/ZD 线
    
    # 中枢配色 (按层级深浅)
    zs_colors = {
        0: "rgba(245,158,11,0.12)",    # 金色（第一层中枢）
        1: "rgba(59,130,246,0.10)",    # 蓝色
        2: "rgba(168,85,247,0.10)",    # 紫色
        3: "rgba(236,72,153,0.08)",    # 粉色
    }
    
    for zi, zs in enumerate(zs_list):
        sdt = zs["start_dt"]
        edt = zs["end_dt"]
        if sdt in date_idx and edt in date_idx:
            si = date_idx[sdt]
            ei = date_idx[edt]
            ZG = zs["zg"]
            ZD = zs["zd"]
            fill_color = zs_colors.get(zi % 4, "rgba(245,158,11,0.08)")
            
            # 中枢矩形
            zs_mark_areas.append([{
                "xAxis": si,
                "yAxis": ZD,
                "itemStyle": {"color": fill_color, "borderColor": "rgba(245,158,11,0.3)", "borderWidth": 1, "borderType": "dashed"}
            }, {
                "xAxis": ei,
                "yAxis": ZG
            }])
            
            # ZG 虚线
            zs_mark_lines.append({
                "name": f"ZG{zi+1}",
                "yAxis": ZG,
                "lineStyle": {"color": "rgba(245,158,11,0.4)", "type": "dashed", "width": 1},
                "label": {"show": True, "formatter": f"ZG{zi+1}:{ZG:.0f}", "position": "end",
                          "fontSize": 9, "color": "rgba(245,158,11,0.7)"}
            })
            # ZD 虚线
            zs_mark_lines.append({
                "name": f"ZD{zi+1}",
                "yAxis": ZD,
                "lineStyle": {"color": "rgba(245,158,11,0.4)", "type": "dashed", "width": 1},
                "label": {"show": True, "formatter": f"ZD{zi+1}:{ZD:.0f}", "position": "start",
                          "fontSize": 9, "color": "rgba(245,158,11,0.7)"}
            })
    
    # ── 买卖点标记 ──
    trade_signals = generate_trade_signals(czsc_obj.bi_list, zs_list,
        detect_divergence(czsc_obj.bi_list, zs_list))
    
    # ── 线段色带 (markArea) + 端点标记 ──
    segment_list = compute_segments(czsc_obj.bi_list)
    segment_areas = []
    for seg in segment_list:
        sdt = seg["sdt"]
        edt = seg["edt"]
        if sdt in date_idx and edt in date_idx:
            si = date_idx[sdt]
            ei = date_idx[edt]
            is_up = seg["direction"] in ("up", "向上")
            seg_color = "rgba(239,68,68,0.08)" if is_up else "rgba(16,185,129,0.08)"
            seg_border = "rgba(239,68,68,0.25)" if is_up else "rgba(16,185,129,0.25)"
            segment_areas.append([{
                "xAxis": si,
                "yAxis": seg["low"],
                "itemStyle": {"color": seg_color, "borderColor": seg_border, "borderWidth": 1}
            }, {
                "xAxis": ei,
                "yAxis": seg["high"]
            }])
            # 线段端点小方块标记
            for pt, price in [(si, seg["low"] if is_up else seg["high"]), (ei, seg["high"] if is_up else seg["low"])]:
                mark_points.append({
                    "name": "段" + ("起" if pt == si else "终"),
                    "coord": [pt, price],
                    "value": "段" + ("起" if pt == si else "终"),
                    "symbol": "rect",
                    "symbolSize": 8,
                    "itemStyle": {"color": "#ef4444" if is_up else "#10b981"}
                })
    
    # 线段中枢渲染
    segment_zs_list = compute_segment_zs(segment_list)
    for zs in segment_zs_list:
        sdt = zs["start_dt"]
        edt = zs["end_dt"]
        if sdt in date_idx and edt in date_idx:
            si = date_idx[sdt]
            ei = date_idx[edt]
            ZG = zs["zg"]
            ZD = zs["zd"]
            segment_areas.append([{
                "xAxis": si, "yAxis": ZD,
                "itemStyle": {"color": "rgba(245,158,11,0.18)", "borderColor": "rgba(245,158,11,0.5)", "borderWidth": 2, "borderType": "dashed"}
            }, {
                "xAxis": ei, "yAxis": ZG
            }])
    
    # 合并笔中枢和线段色带到同一个 markArea
    all_mark_areas = zs_mark_areas + segment_areas
    
    buy_marks = []
    sell_marks = []
    for ts in trade_signals:
        dt = ts["dt"]
        if dt in date_idx:
            idx = date_idx[dt]
            is_buy = ts["side"] == "buy"
            mark = {
                "name": ts["type"],
                "coord": [idx, ts["price"]],
                "value": ts["type"],
                "symbol": "pin",
                "symbolSize": 20,
                "itemStyle": {"color": up_color if is_buy else down_color}
            }
            if is_buy:
                buy_marks.append(mark)
            else:
                sell_marks.append(mark)
    
    # ── 构建 Option ──
    series_kline = {
        "name": "K线",
        "type": "candlestick",
        "data": ohlc_data,
        "itemStyle": {"color": up_color, "color0": down_color,
                      "borderColor": up_color, "borderColor0": down_color},
        "markPoint": {
            "data": mark_points,
            "symbol": "arrow",
            "symbolSize": 10,
            "label": {"show": True, "fontSize": 8, "fontWeight": "normal"}
        },
        "markArea": {
            "silent": False,
            "data": all_mark_areas
        },
        "markLine": {
            "silent": True,
            "symbol": "none",
            "data": zs_mark_lines
        }
    }
    
    series_list = [series_kline]
    
    # 买卖点独立系列
    if buy_marks:
        series_list.append({
            "name": "买点",
            "type": "scatter",
            "data": buy_marks,
            "symbol": "arrow",
            "symbolRotate": 180,
            "symbolSize": 12,
            "itemStyle": {"color": up_color},
            "label": {"show": True, "fontSize": 8, "fontWeight": "bold", "color": up_color}
        })
    
    if sell_marks:
        series_list.append({
            "name": "卖点",
            "type": "scatter",
            "data": sell_marks,
            "symbol": "pin",
            "symbolSize": 12,
            "itemStyle": {"color": down_color},
            "label": {"show": True, "fontSize": 8, "fontWeight": "bold", "color": down_color}
        })
    
    # 成交量系列
    series_list.append({
        "name": "成交量",
        "type": "bar",
        "xAxisIndex": 1,
        "yAxisIndex": 1,
        "data": vol_data
    })
    
    return {
        "backgroundColor": chart_bg,
        "animation": False,
        "axisPointer": {"link": [{"xAxisIndex": [0, 1]}]},
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "cross", "crossStyle": {"color": axis_color},
                            "link": [{"xAxisIndex": [0, 1]}]},
            "backgroundColor": "rgba(20,20,25,0.95)" if is_dark else "rgba(255,255,255,0.95)",
            "borderColor": "rgba(255,255,255,0.06)" if is_dark else "rgba(0,0,0,0.08)",
            "textStyle": {"fontSize": 11, "color": "#e4e4e7" if is_dark else "#1a1a2e"}
        },
        "legend": {
            "data": ["K线", "成交量"],
            "bottom": 20,
            "textStyle": {"color": axis_color, "fontSize": 10},
            "selectedMode": True
        },
        "grid": [
            {"left": "8%", "right": "4%", "top": 8, "height": "60%"},
            {"left": "8%", "right": "4%", "top": "75%", "height": "15%"}
        ],
        "xAxis": [
            {"type": "category", "data": dates, "axisLabel": {"color": axis_color, "fontSize": 9},
             "axisLine": {"lineStyle": {"color": grid_color}}},
            {"type": "category", "gridIndex": 1, "data": dates, "axisLabel": {"show": False},
             "axisLine": {"lineStyle": {"color": grid_color}}}
        ],
        "yAxis": [
            {"type": "value", "scale": True, "axisLabel": {"color": axis_color, "fontSize": 9},
             "splitLine": {"lineStyle": {"color": grid_color}}},
            {"type": "value", "gridIndex": 1, "axisLabel": {"color": axis_color, "fontSize": 9},
             "splitLine": {"show": False}}
        ],
        "dataZoom": [
            {"type": "inside", "xAxisIndex": [0, 1], "start": 70, "end": 100},
            {"type": "slider", "xAxisIndex": [0, 1], "start": 70, "end": 100, "height": 16, "bottom": 4}
        ],
        "series": series_list
    }


# ═══════════════════════════════════════════════
# 数据持久化
# ═══════════════════════════════════════════════

def save_to_db(code, freq, result):
    """将分析结果持久化到数据库"""
    conn = _connect()
    _ensure_tables(conn)
    
    # 清空旧数据
    conn.execute("DELETE FROM chanlun_bi WHERE stock_code=? AND freq=?", (code, freq))
    conn.execute("DELETE FROM chanlun_fx WHERE stock_code=? AND freq=?", (code, freq))
    conn.execute("DELETE FROM chanlun_zs WHERE stock_code=? AND freq=?", (code, freq))
    conn.execute("DELETE FROM chanlun_segment WHERE stock_code=? AND freq=?", (code, freq))
    
    # 写入笔
    for bi in result.get("bi_list", []):
        conn.execute("""
            INSERT INTO chanlun_bi (stock_code, freq, sdt, edt, direction, high, low, power, slope, angle, length)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (code, freq, bi["sdt"], bi["edt"], bi["direction"],
              bi["high"], bi["low"], bi["power"], bi["slope"], bi["angle"], bi["length"]))
    
    # 写入分型
    for fx in result.get("fx_list", []):
        conn.execute("""
            INSERT INTO chanlun_fx (stock_code, freq, dt, fx_type, high, low)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (code, freq, fx["dt"], fx["fx_type"], fx["high"], fx["low"]))
    
    # 写入中枢
    for zs in result.get("zs_list", []):
        conn.execute("""
            INSERT INTO chanlun_zs (stock_code, freq, start_dt, end_dt, zg, zd, zz, bi_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (code, freq, zs["start_dt"], zs["end_dt"], zs["zg"], zs["zd"], zs["zz"], zs.get("bi_count", 0)))
    
    # 写入线段
    for seg in result.get("segment_list", []):
        conn.execute("""
            INSERT INTO chanlun_segment (stock_code, freq, sdt, edt, direction, high, low, bi_count, amplitude, slope, days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (code, freq, seg["sdt"], seg["edt"], seg["direction"],
              seg["high"], seg["low"], seg["bi_count"], seg["amplitude"], seg["slope"], seg["days"]))
    
    conn.commit()
    conn.close()
    return True


# ═══════════════════════════════════════════════
# 多周期联立分析
# ═══════════════════════════════════════════════

def multi_period_analyze(code, limit=400, data_mode="auto"):
    """对同一代码进行日/周/月三周期联立分析
    
    Returns:
        dict: {
            periods: {D: {...}, W: {...}, M: {...}},
            resonance: { 共振信号分析 }
        }
    """
    results = {}
    for freq in ["D", "W", "M"]:
        try:
            r = analyze(code, freq, limit, data_mode=data_mode)
            results[freq] = r
        except Exception as e:
            results[freq] = {"error": str(e)}
    
    # ── 共振分析 ──
    resonance = _analyze_resonance(results)
    
    return {
        "code": code,
        "periods": results,
        "resonance": resonance
    }


def _analyze_resonance(results):
    """分析多周期共振信号
    
    共振规则：
    - 日/周/月笔方向一致 → 强趋势
    - 日/周中枢重叠 → 大级别中枢确认
    - 多周期同时出现背驰 → 高置信度反转
    """
    resonance = {
        "direction_consensus": None,   # 三周期笔方向是否一致
        "zs_overlap": False,           # 中枢是否跨周期重叠
        "divergence_resonance": [],    # 多周期背驰共振
        "trade_resonance": [],         # 多周期买卖点共振
        "strength": "弱"               # 共振强度
    }
    
    # 1. 笔方向一致检测
    dirs = {}
    for freq, r in results.items():
        if "error" in r or not r.get("bi_list"):
            continue
        last_bi = r["bi_list"][-1]
        dirs[freq] = str(last_bi["direction"])
    
    if len(dirs) == 3:
        vals = list(dirs.values())
        if vals[0] == vals[1] == vals[2]:
            resonance["direction_consensus"] = vals[0]
    
    # 2. 跨周期中枢重叠检测
    zs_ranges = {}
    for freq, r in results.items():
        if "error" in r or not r.get("zs_list"):
            continue
        # 取最后一个中枢的 ZG/ZD
        last_zs = r["zs_list"][-1]
        zs_ranges[freq] = (last_zs["zd"], last_zs["zg"])
    
    if len(zs_ranges) >= 2:
        freqs = list(zs_ranges.keys())
        for i in range(len(freqs)):
            for j in range(i+1, len(freqs)):
                zd_i, zg_i = zs_ranges[freqs[i]]
                zd_j, zg_j = zs_ranges[freqs[j]]
                # 检查重叠
                if max(zd_i, zd_j) < min(zg_i, zg_j):
                    resonance["zs_overlap"] = True
                    break
    
    # 3. 背驰共振
    div_dates = {}
    for freq, r in results.items():
        if "error" in r:
            continue
        for div in r.get("divergence_signals", []):
            dt_month = div["dt"][:7]  # YYYY-MM
            key = f"{div['type']}_{dt_month}"
            if key not in div_dates:
                div_dates[key] = []
            div_dates[key].append(freq)
    
    for key, freqs in div_dates.items():
        if len(freqs) >= 2:
            resonance["divergence_resonance"].append({
                "signal": key.split("_")[0],
                "month": key.split("_")[1],
                "freqs": freqs,
                "confidence": "高" if len(freqs) == 3 else "中"
            })
    
    # 4. 买卖点共振
    trade_dates = {}
    for freq, r in results.items():
        if "error" in r:
            continue
        for ts in r.get("trade_signals", []):
            dt_month = ts["dt"][:7]
            key = f"{ts['type']}_{dt_month}"
            if key not in trade_dates:
                trade_dates[key] = []
            trade_dates[key].append({"freq": freq, "confidence": ts["confidence"]})
    
    for key, items in trade_dates.items():
        if len(items) >= 2:
            resonance["trade_resonance"].append({
                "signal": key.split("_")[0],
                "month": key.split("_")[1],
                "details": items,
                "confidence": "高" if len(items) == 3 else "中"
            })
    
    # 5. 共振强度
    score = 0
    if resonance["direction_consensus"]:
        score += 2
    if resonance["zs_overlap"]:
        score += 2
    if resonance["divergence_resonance"]:
        score += len(resonance["divergence_resonance"]) * 2
    if resonance["trade_resonance"]:
        score += len(resonance["trade_resonance"]) * 3
    
    if score >= 6:
        resonance["strength"] = "强"
    elif score >= 3:
        resonance["strength"] = "中"
    else:
        resonance["strength"] = "弱"
    resonance["score"] = score
    
    return resonance


# ═══════════════════════════════════════════════
# 区间套分析（日→60分钟→15分钟三级级联）
# ═══════════════════════════════════════════════

SUPPORTED_MINUTE_FREQ = {"15": Freq.F15, "60": Freq.F60}


def _parse_baostock_time(date_str, time_str):
    """解析 baostock 时间格式: '20260529', '150000000' → datetime"""
    dt_str = str(date_str).strip()
    tm_str = str(time_str).strip()
    # time 格式: '150000000' → '15:00:00'
    if len(tm_str) >= 6:
        hh = tm_str[0:2]
        mm = tm_str[2:4]
        ss = tm_str[4:6]
    else:
        hh, mm, ss = '00', '00', '00'
    return pd.to_datetime(f"{dt_str} {hh}:{mm}:{ss}")


def _get_minute_bars(code, frequency, start_date=None, end_date=None):
    """从缓存读取分钟 K 线并转换为 CZSC RawBar 列表
    
    Args:
        code: 股票代码
        frequency: '15' | '60'
        start_date: 起始日期
        end_date: 结束日期
    
    Returns:
        list[RawBar]
    """
    from data.lixr_api.api_stock_minute import sync_minute_kline, get_cached_klines
    
    # 确保缓存有数据
    sync_minute_kline(code, frequency, start_date, end_date)
    
    # 读取缓存
    df = get_cached_klines(code, frequency, start_date, end_date)
    if df.empty:
        return []
    
    freq_enum = SUPPORTED_MINUTE_FREQ.get(frequency, Freq.F15)
    bars = []
    for _, row in df.iterrows():
        dt = _parse_baostock_time(row['date'], row['time'])
        bars.append(RawBar(
            symbol=code, dt=dt, freq=freq_enum,
            open=float(row['open']), close=float(row['close']),
            high=float(row['high']), low=float(row['low']),
            vol=float(row['volume']), amount=float(row['amount'])
        ))
    
    return bars


def get_minute_echarts_option(code, frequency, limit=400, theme="dark"):
    """分钟级 K 线 ECharts 配置（60分钟/15分钟）
    
    Args:
        code: 股票代码
        frequency: '15' | '60'
        limit: K线数量
        theme: 'dark' | 'light'
    """
    freq_enum = SUPPORTED_MINUTE_FREQ.get(frequency, Freq.F15)
    
    # 从缓存读取分钟数据
    bars = _get_minute_bars(code, frequency)
    if not bars:
        return {"error": f"无{频率}分钟K线数据: {code}"}
    
    # 截取最近 limit 根
    if len(bars) > limit:
        bars = bars[-limit:]
    
    # CZSC 计算
    czsc_obj = CZSC(bars)
    
    # 构建 X 轴标签（日期+时间）
    x_labels = []
    for b in bars:
        dt = getattr(b, 'dt', None)
        if dt:
            x_labels.append(dt.strftime("%m-%d %H:%M") if hasattr(dt, 'strftime') else str(dt)[:16])
        else:
            x_labels.append('')
    
    # OHLC 数据
    ohlc_data = [[float(b.open), float(b.close), float(b.low), float(b.high)] for b in bars]
    
    # 配色
    is_dark = theme == "dark"
    up_color = "#ef4444" if is_dark else "#dc2626"
    down_color = "#10b981" if is_dark else "#059669"
    axis_color = "rgba(200,200,200,0.3)" if is_dark else "rgba(128,128,128,0.3)"
    grid_color = "rgba(200,200,200,0.08)" if is_dark else "rgba(128,128,128,0.1)"
    chart_bg = "rgba(26,26,31,.6)" if is_dark else "rgba(255,255,255,.75)"
    
    # 笔标记
    date_idx = {x_labels[i]: i for i in range(len(x_labels))}
    mark_points = []
    for bi in czsc_obj.bi_list:
        try:
            s = str(bi.dt)[:16] if hasattr(bi.dt, 'strftime') else str(bi.dt)[:16]
            for k, idx in date_idx.items():
                if k.startswith(s[:11]):
                    is_down = str(bi.direction) in ("down", "向下")
                    mark_points.append({
                        "coord": [idx, float(bi.high) if is_down else float(bi.low)],
                        "value": ("顶" if is_down else "底") + str(round(float(bi.high if is_down else bi.low), 0)),
                        "symbol": "arrow", "symbolRotate": 0 if is_down else 180,
                        "symbolSize": 8, "symbolOffset": [0, -5 if is_down else 5],
                        "itemStyle": {"color": up_color if is_down else down_color},
                        "label": {"show": True, "fontSize": 7, "fontWeight": "normal",
                                  "color": up_color if is_down else down_color, "offset": [0, -8] if is_down else [0, 8]}
                    })
                    break
        except Exception:
            pass
    
    # 中枢
    zs_list = compute_zhongshu(czsc_obj.bi_list)
    zs_areas = []
    for zs in zs_list:
        sdt, edt = zs["start_dt"], zs["end_dt"]
        si = ei = -1
        for k, idx in date_idx.items():
            if k.startswith(sdt[:7]): si = idx
            if k.startswith(edt[:7]): ei = idx
        if si >= 0 and ei >= 0:
            zs_areas.append([{"xAxis": si, "yAxis": zs["zd"],
                "itemStyle": {"color": "rgba(245,158,11,0.12)", "borderColor": "rgba(245,158,11,0.3)", "borderWidth": 1, "borderType": "dashed"}},
                {"xAxis": ei, "yAxis": zs["zg"]}])
    
    return {
        "backgroundColor": chart_bg,
        "animation": False,
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross", "crossStyle": {"color": axis_color}},
            "backgroundColor": "rgba(20,20,25,0.95)" if is_dark else "rgba(255,255,255,0.95)",
            "borderColor": "rgba(255,255,255,0.06)" if is_dark else "rgba(0,0,0,0.08)",
            "textStyle": {"fontSize": 11, "color": "#e4e4e7" if is_dark else "#1a1a2e"}},
        "grid": [{"left": "8%", "right": "4%", "top": 8, "height": "75%"}],
        "xAxis": [{"type": "category", "data": x_labels, "axisLabel": {"color": axis_color, "fontSize": 8},
                    "axisLine": {"lineStyle": {"color": grid_color}}}],
        "yAxis": [{"type": "value", "scale": True, "axisLabel": {"color": axis_color, "fontSize": 9},
                    "splitLine": {"lineStyle": {"color": grid_color}}}],
        "dataZoom": [{"type": "inside", "start": 70, "end": 100},
                     {"type": "slider", "start": 70, "end": 100, "height": 16, "bottom": 4}],
        "series": [{
            "name": "K线", "type": "candlestick", "data": ohlc_data,
            "itemStyle": {"color": up_color, "color0": down_color, "borderColor": up_color, "borderColor0": down_color},
            "markPoint": {"data": mark_points, "symbol": "arrow", "symbolSize": 8,
                          "label": {"show": True, "fontSize": 7}},
            "markArea": {"silent": False, "data": zs_areas}
        }]
    }


def cascade_analyze(code, daily_date=None, daily_side=None):
    """区间套分析：日线背驰信号 → 60分钟确认 → 15分钟精确定位
    
    Args:
        code: 股票代码
        daily_date: 日线背驰信号日期（可选，默认取最新日线信号）
        daily_side: 'buy' | 'sell'（可选，默认取最新信号方向）
    
    Returns:
        dict: {
            daily_signal: 日线信号详情,
            cascade_signals: [区间套信号列表],
            levels: {daily, 60min, 15min} 各级分析结果,
            has_cascade: 是否存在区间套
        }
    """
    # Step 1: 日线分析（获取最新的买卖信号）
    daily_result = analyze(code, "D", 400, data_mode="stock")
    if daily_result.get("error"):
        return {"error": daily_result["error"]}
    
    trade_signals = daily_result.get("trade_signals", [])
    if not trade_signals:
        return {"error": "日线无买卖信号，无法进行区间套分析", "daily_signal": None, "cascade_signals": []}
    
    # 选择目标信号
    target_signal = None
    if daily_date:
        for ts in trade_signals:
            if ts["dt"] == daily_date:
                target_signal = ts
                break
    
    if not target_signal:
        # 取最新信号
        target_signal = trade_signals[0]
    
    target_date = target_signal["dt"]
    target_side = daily_side or target_signal["side"]
    is_buy = target_side == "buy"
    
    # 确定分钟数据的日期范围（CZSC笔划分对数据范围敏感，需给足前后空间）
    from datetime import datetime as dt_cls
    dt_obj = dt_cls.strptime(target_date, "%Y-%m-%d")
    m_start = (dt_obj - pd.DateOffset(days=180)).strftime("%Y-%m-%d")
    m_end = (dt_obj + pd.DateOffset(days=60)).strftime("%Y-%m-%d")
    
    # Step 2: 60分钟确认
    bars_60 = _get_minute_bars(code, "60", m_start, m_end)
    if len(bars_60) < 10:
        return {"error": "60分钟数据不足", "daily_signal": target_signal, "cascade_signals": []}
    
    czsc_60 = CZSC(bars_60)
    zs_60 = compute_zhongshu(czsc_60.bi_list)
    div_60 = detect_divergence(czsc_60.bi_list, zs_60)
    
    # 匹配：60分钟的背驰方向与日线一致，且在 ±3 天内
    matched_60 = []
    for d in div_60:
        if d["category"] != "趋势背驰":
            continue
        d_dt = d["dt"]
        try:
            d_obj = dt_cls.strptime(d_dt, "%Y-%m-%d")
            diff_days = abs((d_obj - dt_obj).days)
            same_dir = (is_buy and d["type"] == "底背驰") or (not is_buy and d["type"] == "顶背驰")
            if diff_days <= 3 and same_dir:
                # 提取笔的完整时间（含时分）
                bi_idx = d.get("bi_idx", 0)
                if bi_idx < len(czsc_60.bi_list):
                    bi_dt = czsc_60.bi_list[bi_idx].sdt
                    if hasattr(bi_dt, 'strftime'):
                        d["dt_full"] = bi_dt.strftime("%Y-%m-%d %H:%M")
                    else:
                        d["dt_full"] = str(bi_dt)[:16]
                else:
                    d["dt_full"] = d_dt
                matched_60.append(d)
        except ValueError:
            continue
    
    if not matched_60:
        return {
            "daily_signal": target_signal,
            "cascade_signals": [],
            "has_cascade": False,
            "levels_passed": 1,
            "confidence": "低（仅日线确认）",
            "reason": "60分钟未检测到与日线匹配的背驰信号"
        }
    
    # Step 3: 15分钟精确定位
    # 在60分钟确认点附近缩小范围
    best_60 = matched_60[0]  # 取第一个匹配的
    dt_60 = dt_cls.strptime(best_60["dt"], "%Y-%m-%d")
    m15_start = (dt_60 - pd.DateOffset(days=90)).strftime("%Y-%m-%d")
    m15_end = (dt_60 + pd.DateOffset(days=10)).strftime("%Y-%m-%d")
    
    bars_15 = _get_minute_bars(code, "15", m15_start, m15_end)
    if len(bars_15) < 10:
        return {
            "daily_signal": target_signal,
            "cascade_signals": [{
                "type": f"区间套{'一买' if is_buy else '一卖'}",
                "side": target_side,
                "dt_daily": target_date,
                "dt_60min": best_60.get("dt_full", best_60["dt"]),
                "price_daily": target_signal["price"],
                "confidence": "中（日线+60分确认）",
                "levels_passed": 2
            }],
            "has_cascade": True,
            "levels_passed": 2,
            "confidence": "中",
            "reason": "15分钟数据不足"
        }
    
    czsc_15 = CZSC(bars_15)
    zs_15 = compute_zhongshu(czsc_15.bi_list)
    div_15 = detect_divergence(czsc_15.bi_list, zs_15)
    
    # 匹配：15分钟背驰方向一致，且在60分钟确认点 ±1 天内
    matched_15 = []
    for d in div_15:
        if d["category"] != "趋势背驰":
            continue
        d_dt = d["dt"]
        try:
            d_obj = dt_cls.strptime(d_dt, "%Y-%m-%d")
            diff_days = abs((d_obj - dt_60).days)
            same_dir = (is_buy and d["type"] == "底背驰") or (not is_buy and d["type"] == "顶背驰")
            if diff_days <= 1 and same_dir:
                # 提取笔的完整时间
                bi_idx = d.get("bi_idx", 0)
                if bi_idx < len(czsc_15.bi_list):
                    bi_dt = czsc_15.bi_list[bi_idx].sdt
                    if hasattr(bi_dt, 'strftime'):
                        d["dt_full"] = bi_dt.strftime("%Y-%m-%d %H:%M")
                    else:
                        d["dt_full"] = str(bi_dt)[:16]
                else:
                    d["dt_full"] = d_dt
                matched_15.append(d)
        except ValueError:
            continue
    
    # 构建结果
    cascade_sigs = []
    if matched_15:
        for d15 in matched_15:
            # 精确入场价：15分钟背驰点的极值
            bi_15 = czsc_15.bi_list
            entry_price = None
            for bi in bi_15:
                bi_sdt = str(bi.sdt)[:10]
                if bi_sdt == d15["dt"] and str(bi.direction) in ("down", "向下"):
                    entry_price = round(float(bi.low), 2)
                    break
            if entry_price is None and d15.get("bi_idx", 0) < len(bi_15):
                entry_bi = bi_15[d15["bi_idx"]]
                entry_price = round(float(entry_bi.low) if is_buy else float(entry_bi.high), 2)
            
            cascade_sigs.append({
                "type": f"区间套{'一买' if is_buy else '一卖'}",
                "side": target_side,
                "dt_daily": target_date,
                "dt_60min": best_60.get("dt_full", best_60["dt"]),
                "dt_15min": d15.get("dt_full", d15["dt"]),
                "price_daily": target_signal["price"],
                "price_entry": entry_price or target_signal["price"],
                "entry_range": [round(entry_price * 0.998, 2), round(entry_price * 1.005, 2)] if entry_price else None,
                "confidence": "高",
                "levels_passed": 3,
                "daily_div": {"type": target_signal.get("div_signal", ""), "power_ratio": 0, "severity": target_signal.get("confidence", "")},
                "60min_div": {"type": best_60["type"], "power_ratio": best_60["power_ratio"], "severity": best_60["severity"]},
                "15min_div": {"type": d15["type"], "power_ratio": d15["power_ratio"], "severity": d15["severity"]}
            })
    else:
        # 仅有日线+60分确认
        cascade_sigs.append({
            "type": f"区间套{'一买' if is_buy else '一卖'}",
            "side": target_side,
            "dt_daily": target_date,
            "dt_60min": best_60.get("dt_full", best_60["dt"]),
            "price_daily": target_signal["price"],
            "confidence": "中",
            "levels_passed": 2,
            "daily_div": {"type": target_signal.get("div_signal", ""), "power_ratio": 0, "severity": target_signal.get("confidence", "")},
            "60min_div": {"type": best_60["type"], "power_ratio": best_60["power_ratio"], "severity": best_60["severity"]}
        })
    
    levels_passed = cascade_sigs[0]["levels_passed"]
    confidence = "高" if levels_passed == 3 else "中"
    
    return {
        "code": code,
        "daily_signal": target_signal,
        "cascade_signals": cascade_sigs,
        "has_cascade": True,
        "levels_passed": levels_passed,
        "confidence": confidence,
        "daily": {
            "bi_count": daily_result["bi_count"],
            "zs_count": daily_result["zs_count"],
            "divergence_count": daily_result["divergence_count"]
        },
        "min60": {
            "bi_count": len(czsc_60.bi_list),
            "zs_count": len(zs_60),
            "divergence_count": len(div_60),
            "matched_count": len(matched_60)
        },
        "min15": {
            "bi_count": len(czsc_15.bi_list) if len(bars_15) >= 10 else 0,
            "zs_count": len(zs_15) if len(bars_15) >= 10 else 0,
            "divergence_count": len(div_15) if len(bars_15) >= 10 else 0,
            "matched_count": len(matched_15)
        }
    }


# ═══════════════════════════════════════════════
# 自检
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    r = analyze("000985", "D", 300)
    print(f"笔: {r['bi_count']}, 分型: {r['fx_count']}, 中枢: {r['zs_count']}, 背驰: {r['divergence_count']}, 买卖点: {r['trade_signal_count']}")
    if r["bi_list"]:
        last = r["bi_list"][-1]
        print(f"最新笔: {last['direction']} {last['sdt'][:10]}→{last['edt'][:10]} L:{last['low']:.1f} H:{last['high']:.1f} P:{last['power']:.1f}")
    if r["zs_list"]:
        print(f"\n中枢列表:")
        for zs in r["zs_list"]:
            print(f"  #{zs['idx']} {zs['start_dt']}~{zs['end_dt']} ZG:{zs['zg']:.1f} ZD:{zs['zd']:.1f} 宽:{zs['width']:.1f}({zs['width_pct']}%)")
    if r["divergence_signals"]:
        print(f"\n背驰信号:")
        for d in r["divergence_signals"]:
            print(f"  {d['type']}({d['category']}) {d['dt']} 力度比:{d['power_ratio']} {d['severity']}")
    if r["trade_signals"]:
        print(f"\n买卖信号:")
        for ts in r["trade_signals"]:
            side_sym = "🟢" if ts["side"] == "buy" else "🔴"
            print(f"  {side_sym} {ts['type']} @{ts['dt']} 价格:{ts['price']} 置信度:{ts['confidence']}")


# ═══════════════════════════════════════════════
# 买入决策评估
# ═══════════════════════════════════════════════

def evaluate_buy_decision(code, freq="D", limit=500, data_mode="auto"):
    """三步规则链评估是否买入
    
    流程：
    1. 结构过滤：走势类型决定能不能买
    2. 信号匹配：在允许的结构里，按信号类型分级
    3. 历史校验：背驰后追踪结果评估信号质量
    
    Returns:
        dict: {decision, grade, reason, signals, structure, quality}
    """
    result = analyze(code, freq, limit, data_mode)
    if "error" in result:
        return {"decision": "不买", "grade": "—", "reason": result["error"]}
    
    tc = result.get("trend_classification", {})
    trade_sigs = result.get("trade_signals", [])
    outcomes = result.get("divergence_outcomes", [])
    
    trend_type = tc.get("trend_type", "无中枢")
    direction = tc.get("direction", "不明")
    position = tc.get("position_vs_zs", "内部")
    
    # ═══ 第1步：结构过滤 ═══
    structure_allow = {
        "上涨趋势": "允许",
        "下跌趋势": "禁止",
        "盘整": "允许",  # 盘整允许，不要求偏多
        "无中枢": "禁止"
    }
    trend_label = trend_type
    
    allow = structure_allow.get(trend_type, "禁止")
    if allow == "禁止":
        return {
            "decision": "不买",
            "grade": "—",
            "trend": trend_label,
            "reason": f"结构不满足：{trend_label}（方向:{direction} 价格在最新中枢{position}）",
            "detail": tc.get("description", "")
        }
    
    # ═══ 第2步：信号匹配 ═══
    # 取最近3个月的买入信号
    buy_signals = [s for s in trade_sigs if s["side"] == "buy"]
    if not buy_signals:
        return {
            "decision": "观察",
            "grade": "—",
            "trend": trend_label,
            "reason": f"结构允许（{trend_label}），但无买入信号",
            "detail": tc.get("description", "")
        }
    
    # 信号分级
    signal_grades = {
        "三买": 3,
        "二买": 2,
        "类一买": 2 if position == "上方" else 1,
        "一买": 1  # 一买是左侧，所有结构下降一级
    }
    
    best_signal = None
    best_grade = 0
    for sig in buy_signals:
        g = signal_grades.get(sig["type"], 0)
        if g > best_grade:
            best_grade = g
            best_signal = sig
    
    if best_signal is None:
        return {"decision": "观察", "grade": "—", "trend": trend_label,
                "reason": "无有效买入信号类型"}
    
    # ═══ 第3步：历史校验 ═══
    quality_score = _evaluate_signal_quality(outcomes)
    quality_label = "优" if quality_score >= 70 else "中" if quality_score >= 40 else "差"
    
    # 综合评级
    if best_grade == 3 and quality_score >= 40:
        grade = "⭐⭐⭐"
        decision = "买入"
    elif best_grade >= 2 and quality_score >= 40:
        grade = "⭐⭐"
        decision = "买入"
    elif best_grade >= 1:
        grade = "⭐"
        decision = "观察"
    else:
        grade = "—"
        decision = "不买"
    
    return {
        "decision": decision,
        "grade": grade,
        "trend": trend_label,
        "best_signal": best_signal,
        "signal_count": len(buy_signals),
        "quality_score": quality_score,
        "quality_label": quality_label,
        "reason": f"结构:{trend_label} + 最优信号:{best_signal['type']}(置信:{best_signal.get('confidence','?')}) + 历史质量:{quality_label}({quality_score}分)",
        "detail": tc.get("description", "")
    }


def _evaluate_signal_quality(outcomes):
    """根据背驰后追踪结果评估信号历史质量（0-100分）"""
    if not outcomes:
        return 50  # 无历史数据，中性
    
    total = len(outcomes)
    strong = sum(1 for o in outcomes if o["outcome"] == "反趋势")
    medium = sum(1 for o in outcomes if o["outcome"] == "更大级别盘整")
    weak = sum(1 for o in outcomes if o["outcome"] == "中枢扩展")
    
    if total == 0:
        return 50
    
    score = (strong * 100 + medium * 50 + weak * 0) / total
    return round(score)
