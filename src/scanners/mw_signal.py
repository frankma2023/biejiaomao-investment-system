"""
MW 信号识别引擎 v1.0

每日全市场扫描,识别「牛市回调后再启动」突破形态。
B1/B2 为硬性门禁,H/D/C/P 为辅助评分。

用法:
  python src/scanners/mw_signal.py --date 2026-06-03        # 单日扫描
  python src/scanners/mw_signal.py --date 2026-06-03 --fast  # 快速模式(仅采样)
"""

import sqlite3, os, sys, json, argparse
from datetime import datetime, timedelta

SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

DB_PATH = os.path.join(SRC_DIR, '..', 'data', 'lixinger.db')
KLINE_LOOKBACK = 250  # 扫描所需的 K 线天数
B2_RECENT_DAYS = 3    # B2 必须在最近 N 天内
_chanlun_cache = {}   # 内存缓存 {code: bi_list}


def sma(values, n):
    if len(values) < n: return None
    return sum(values[-n:]) / n


def linear_slope(values):
    n = len(values)
    if n < 2: return 0
    x_mean = (n-1)/2; y_mean = sum(values)/n
    num = sum((i-x_mean)*(values[i]-y_mean) for i in range(n))
    den = sum((i-x_mean)**2 for i in range(n))
    return num/den if den != 0 else 0


def get_all_stocks(conn):
    """获取候选股票，排除 ST 和日成交额过低的"""
    rows = conn.execute("""
        SELECT DISTINCT k.stock_code
        FROM daily_kline k
        INNER JOIN stock_basic b ON k.stock_code=b.stock_code
        WHERE b.listing_status='normally_listed'
        AND b.name NOT LIKE '%ST%'
        AND k.date >= date('now','-20 days')
        GROUP BY k.stock_code
        HAVING AVG(k.amount) >= 50000000
    """).fetchall()
    return [r[0] for r in rows]


def get_klines(conn, code, min_date, max_date=None):
    """获取 K 线数据,可限制结束日期"""
    if max_date:
        rows = conn.execute("""
            SELECT date, open, high, low, close, volume, amount FROM daily_kline
            WHERE stock_code=? AND date >= ? AND date <= ? ORDER BY date
        """, (code, min_date, max_date)).fetchall()
    else:
        rows = conn.execute("""
            SELECT date, open, high, low, close, volume, amount FROM daily_kline
            WHERE stock_code=? AND date >= ? ORDER BY date
        """, (code, min_date)).fetchall()
    return [dict(r) for r in rows]


def scan_stock(klines, scan_date, code=None, conn=None):
    """
    扫描单只股票,返回 (passed, result_dict)
    passed: True/False
    result_dict: 信号详情或 None
    """
    n = len(klines)
    if n < 150:
        return False, None
    
    dates = [k['date'] for k in klines]

    # ── 1. 找前高 H：用缠论笔顶 ──
    h_date_str = None; h_price = 0
    # 从缓存获取 bi_list：先查 chanlun_scan_daily，没有再调 analyze()
    bi_list = None
    if code not in _chanlun_cache:
        if conn:
            row = conn.execute(
                "SELECT bi_json FROM chanlun_scan_daily WHERE stock_code=? ORDER BY scan_date DESC LIMIT 1",
                (code,)
            ).fetchone()
            if row and row[0]:
                try: bi_list = json.loads(row[0])
                except: pass
        if bi_list is None:
            from scanners.chanlun import analyze
            bi_list = analyze(code, 'D', 500, data_mode='stock').get('bi_list', [])
        _chanlun_cache[code] = bi_list
    else:
        bi_list = _chanlun_cache[code]
    tops = [(b['sdt'][:10], b['high']) for b in bi_list if b['direction'] == '向下']
    tops.sort(key=lambda x: x[0], reverse=True)
    for top_date, top_price in tops:
        if top_date > scan_date: continue
        try: top_idx = dates.index(top_date)
        except ValueError: continue
        future_low = min(klines[j]['close'] for j in range(top_idx+1, n)) if top_idx+1 < n else top_price
        decline = (top_price - future_low)/top_price if top_price > 0 else 0
        if decline < 0.10: continue
        pre60_start = max(0, top_idx-60)
        pre60_low = min(klines[j]['close'] for j in range(pre60_start, top_idx)) if pre60_start < top_idx else top_price
        pre_rise = (top_price - pre60_low)/pre60_low if pre60_low > 0 else 0
        if pre_rise >= 0.20:
            h_date_str = top_date; h_price = top_price; h_idx = top_idx
            break

    if h_price == 0:
        return False, None

    # ── 2. 找最低点 L：用缠论笔底 ──
    l_idx = h_idx; l_price = klines[h_idx]['close']
    bots = [(b['sdt'][:10], b['low']) for b in bi_list if b['direction'] == '向上']
    for bot_date, bot_price in bots:
        if bot_date > h_date_str:
            try: l_idx = dates.index(bot_date); l_price = bot_price
            except ValueError: pass
            break

    # ── 3. 找横盘区 C ──
    c_start = l_idx; c_end = l_idx
    for i in range(l_idx, min(l_idx+30, n)):
        seg = [klines[j]['close'] for j in range(l_idx, i+1)]
        seg_min = min(seg); seg_max = max(seg)
        amp = (seg_max - seg_min)/seg_min if seg_min > 0 else 999
        if amp <= 0.10:
            c_end = i
        elif i - l_idx >= 3:
            break

    # ── 4. 找 B1 ──
    # 计算最近10日最大下跌量(下跌 = 收盘 < 前日收盘)
    max_down_b1 = 0
    for j in range(max(0, c_end-9), c_end+1):
        if j > 0 and klines[j]['close'] < klines[j-1]['close']:
            if klines[j]['volume'] > max_down_b1:
                max_down_b1 = klines[j]['volume']

    b1_idx = None
    for i in range(c_end+1, min(c_end+20, n)):
        k = klines[i]
        if k['close'] <= k['open']: continue
        vol_20 = [klines[j]['volume'] for j in range(max(0,i-20), i) if klines[j].get('volume')]
        avg20 = sum(vol_20)/len(vol_20) if vol_20 else 0
        vol_r = k['volume']/avg20 if avg20 > 0 else 0
        ret = (k['close']/klines[i-1]['close']-1) if i>0 else 0
        if k['high'] != k['low']: pos = (k['close']-k['low'])/(k['high']-k['low'])
        else: pos = 1.0
        # B1:涨幅 + 均线(MA5>MA10且收盘站上) + 量 + 空间
        b1_vol_ok = k['volume'] > max_down_b1 and vol_r >= 1.3
        # MA5/MA10 check
        ma5 = sum(klines[j]['close'] for j in range(i-4, i+1))/5 if i>=4 else 0
        ma10 = sum(klines[j]['close'] for j in range(i-9, i+1))/10 if i>=9 else 0
        b1_ma_ok = ma5 > 0 and ma10 > 0 and k['close'] > ma5 and k['close'] > ma10 and ma5 > ma10
        if ret >= 0.02 and b1_vol_ok and b1_ma_ok and k['close'] > max(klines[j]['close'] for j in range(c_start, c_end+1)):
            b1_idx = i; break
    if b1_idx is None: return False, None

    # ── 5. 找 B2 ──
    # 先记录 B1 日的 MA 状态,B2 必须在此基础上有趋势结构升级
    b1_high = klines[b1_idx]['high']
    b1_close = klines[b1_idx]['close']

    def ma_status(idx):
        mas = {}
        for p in [5,10,20,30,60]:
            if idx >= p-1:
                mas[p] = sum(klines[j]['close'] for j in range(idx-p+1, idx+1))/p
            else:
                mas[p] = None
        above = [p for p in [60] if mas[p] and klines[idx]['close'] > mas[p] * 1.02]
        crosses = []
        if mas[20] and mas[30] and mas[20] > mas[30]: crosses.append('MA20>MA30')
        if mas[30] and mas[60] and mas[30] > mas[60]: crosses.append('MA30>MA60')
        return above, crosses

    b1_above, b1_crosses = ma_status(b1_idx)

    b2_idx = None
    for i in range(b1_idx+1, min(b1_idx+30, n)):
        k = klines[i]
        if k['close'] <= k['open']: continue

        # 平台约束:仅含 B1 之后收盘价,不含 B1 盘中高点
        if i > b1_idx+1:
            pmax_c = max(klines[j]['close'] for j in range(b1_idx+1, i))
            plateau = pmax_c
        else:
            plateau = b1_close
        if k['close'] <= plateau * 1.02: continue

        b2_above, b2_crosses = ma_status(i)
        new_above = bool(set(b2_above) - set(b1_above))
        new_cross = bool(set(b2_crosses) - set(b1_crosses))
        price_break = k['close'] > b1_high
        if not new_above and not new_cross and not price_break:
            continue

        # 成交额 > 前10日任意下跌日成交额（取消量比条件）
        max_down_amt = max((klines[j].get('amount') or 0 for j in range(max(0,i-10), i) if j>0 and klines[j]['close']<klines[j-1]['close']), default=0)
        amount_ok = (k.get('amount') or 0) > max_down_amt
        ret = (k['close']/klines[i-1]['close']-1) if i>0 else 0
        if k['high'] != k['low']: pos = (k['close']-k['low'])/(k['high']-k['low'])
        else: pos = 1.0

        is_gap = i > 0 and k['open'] > klines[i-1]['high']
        close_ok = (is_gap and pos >= 0.40) or (not is_gap and pos >= 0.80)
        if not close_ok: continue
        if not amount_ok: continue
        if ret < 0.03: continue

        # 均线突破 ≥ 4，且必须站上 MA60
        ma_count = 0
        ma60_val = None
        for period in [5,10,20,30,60]:
            if i >= period-1:
                ma = sum(klines[j]['close'] for j in range(i-period+1, i+1))/period
                if k['close'] > ma: ma_count += 1
                if period == 60: ma60_val = ma
        if ma_count < 4: continue
        if ma60_val is None or k['close'] <= ma60_val: continue

        b2_idx = i; break

    # ── v2.4: B1 可独立保存，不再强制要求 B2 ──
    # B1 结构完整即可作为独立信号
    
    # ── 6. 辅助评分(形态 H/D/C) ── B1 阶段只评 HDC
    # 权重调整：H15 P15 D5 C5（基于回测数据：H+9.1pp, P+6.4pp >> D+4.6pp, C+4.3pp）
    score = {'H': 0, 'D': 0, 'C': 0, 'P': 0}

    # H: SMA50 斜率 > 0（满分 15）
    if h_idx >= 60:
        sma50_now = sum(klines[j]['close'] for j in range(h_idx-50, h_idx))/50
        sma50_10d_ago = sum(klines[j]['close'] for j in range(h_idx-60, h_idx-10))/50 if h_idx >= 60 else sma50_now
        if sma50_now > sma50_10d_ago:
            score['H'] = 15

    # D: 15% ≤ 跌幅 ≤ 35%（满分 15）
    decline = (h_price - l_price)/h_price*100 if h_price > 0 else 0
    if 15 <= decline <= 35:
        score['D'] = 15

    # C: 振幅 < 10% AND 低点斜率 > 0（满分 5）
    c_closes = [klines[j]['close'] for j in range(c_start, c_end+1)]
    c_min = min(c_closes); c_max = max(c_closes)
    c_amp = (c_max-c_min)/c_min*100 if c_min > 0 else 999
    c_slope = linear_slope(c_closes)
    if c_amp < 10 and c_slope > 0:
        score['C'] = 5

    # P: 回撤 ≥ -7% AND 缩量（满分 15）- 仅 B2 存在时计算
    p_vol_avg = 0
    if b2_idx is not None and b2_idx > b1_idx+1:
        b1_close_val = klines[b1_idx]['close']
        p_dd = 0
        p_vols = []
        for i in range(b1_idx+1, b2_idx):
            dd = (klines[i]['close']/b1_close_val-1)*100
            if dd < p_dd: p_dd = dd
            p_vols.append(klines[i]['volume'])
        p_vol_avg = sum(p_vols)/len(p_vols) if p_vols else 0
        if p_dd >= -7 and p_vol_avg < klines[b1_idx]['volume']:
            score['P'] = 15

    # ── 6.5 前高时的 RS 强度 ──
    h_rs250 = h_rs20 = None
    if conn and code:
        row = conn.execute(
            "SELECT rps_20, rps_250 FROM stock_rs_daily WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1",
            (code, dates[h_idx])
        ).fetchone()
        if row:
            h_rs20 = row[0]
            h_rs250 = row[1]

    # ── 7. 行业共振 + 个股RS强度评分（25分：I1=15, I2=10）──
    # I1: 行业RS250（L2→L1兜底，二进制）
    # I2: 股票H点RS250（阶梯制）
    score_i1 = score_i2 = 0
    ind_rs20 = ind_rs250 = None
    if conn and code:
        # 加载L2/L1指数列表（缓存）
        if not hasattr(scan_stock, '_l2_codes'):
            import yaml, os as _os
            cfg_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), 'config', 'index_style.yaml')
            with open(cfg_path, 'r', encoding='utf-8') as _f:
                _cfg = yaml.safe_load(_f)
            scan_stock._l2_codes = set(i['code'] for i in _cfg['categories']['sector_l2'])
            scan_stock._l2_names = {i['code']: i['name'] for i in _cfg['categories']['sector_l2']}
            scan_stock._l1_codes = set(i['code'] for i in _cfg['categories']['sector_l1'])
            scan_stock._l1_names = {i['code']: i['name'] for i in _cfg['categories']['sector_l1']}
        l2_set = scan_stock._l2_codes
        l2_names_map = scan_stock._l2_names
        l1_names_map = scan_stock._l1_names
        l1_set_cached = scan_stock._l1_codes
        
        idx_codes = conn.execute("""
            SELECT DISTINCT ic.index_code FROM index_constituents ic WHERE ic.stock_code=?
        """, (code,)).fetchall()
        idx_set = set(r[0] for r in idx_codes)
        l2_set_local = idx_set & l2_set
        
        if not l2_set_local:
            l1_set_local = l1_set_cached & idx_set
            use_set = l1_set_local if l1_set_local else set()
        else:
            use_set = l2_set_local
        
        if use_set:
            placeholders = ','.join('?' * len(use_set))
            best = conn.execute(f"""
                SELECT stock_code, rs_20, rs_250 FROM index_rs_daily
                WHERE date=? AND stock_code IN ({placeholders})
                ORDER BY rs_250 DESC LIMIT 1
            """, [dates[h_idx]] + list(use_set)).fetchone()
            if best:
                ind_code = best[0]
                ind_name = l2_names_map.get(ind_code) or l1_names_map.get(ind_code, '')
                ind_rs20 = best[1]
                ind_rs250 = best[2]
        # I1: H点行业RS250，阶梯制
        if ind_rs250 is not None and ind_rs250 >= 85:
            score_i1 = 15
        elif ind_rs250 is not None and ind_rs250 >= 80:
            score_i1 = 10
        elif ind_rs250 is not None and ind_rs250 >= 75:
            score_i1 = 5
        # I2: 股票H点RS250，阶梯制（满分15）
        if h_rs250 is not None and h_rs250 >= 90:
            score_i2 = 15
        elif h_rs250 is not None and h_rs250 >= 85:
            score_i2 = 10
        elif h_rs250 is not None and h_rs250 >= 80:
            score_i2 = 5

    # ── 8. MA 排列质量评分 — 已移除，替换为 B2 跳空 ──
    # （B2 硬闸已保证站上 MA60，均线排列冗余）

    # ── 8.5 B2 日信号共振评分（10分，累加制，仅 B2 存在时）──
    score_sig = 0
    if conn and code and b2_idx is not None:
        b2_date = dates[b2_idx]
        row = conn.execute(
            "SELECT signals_json FROM pattern_scan_signals WHERE date=? AND stock_code=?",
            (b2_date, code)
        ).fetchone()
        if row and row[0]:
            try:
                import json
                sigs = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                sources_seen = set()
                for s in (sigs if isinstance(sigs, list) else []):
                    src = s.get('source', '')
                    if src in sources_seen:
                        continue
                    sources_seen.add(src)
                    if src in ('base_breakout', 'pocket_pivot'):
                        score_sig += 6
                    elif src in ('cdl', 'talib'):
                        score_sig += 1
                score_sig = min(score_sig, 10)
            except:
                pass
    elif conn and code and b2_idx is None:
        # B1 日共振信号（B1 Tab 展示用）
        b1_date_sig = dates[b1_idx]
        row = conn.execute(
            "SELECT signals_json FROM pattern_scan_signals WHERE date=? AND stock_code=?",
            (b1_date_sig, code)
        ).fetchone()
        if row and row[0]:
            try:
                import json
                sigs = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                sources_seen = set()
                for s in (sigs if isinstance(sigs, list) else []):
                    src = s.get('source', '')
                    if src in sources_seen:
                        continue
                    sources_seen.add(src)
                    if src in ('base_breakout', 'pocket_pivot'):
                        score_sig += 6
                    elif src in ('cdl', 'talib'):
                        score_sig += 1
                score_sig = min(score_sig, 10)
            except:
                pass

    # ── 8.6 B2 跳空高开（10分，仅 B2 存在时）──
    score_gap = 0
    if b2_idx is not None and b2_idx > 0:
        score_gap = 10 if klines[b2_idx]['open'] > klines[b2_idx-1]['high'] else 0

    # ── 8.6 新指标 M1: B2日相对大盘强度（5分，仅 B2 存在时）──
    score_m1 = 0
    if conn and code and b2_idx is not None:
        b2_date = dates[b2_idx]
        row985 = conn.execute(
            "SELECT change FROM index_daily_kline WHERE stock_code='000985' AND date=?",
            (b2_date,)
        ).fetchone()
        if row985 and row985[0] is not None:
            idx_chg = row985[0] * 100
            b2_ret = (klines[b2_idx]['close']/klines[b2_idx-1]['close']-1)*100 if b2_idx>0 else 0
            if b2_ret > idx_chg:
                score_m1 = 5

    # ── 8.7 新指标 M2: B1→B2 整理期缩量率（5分，仅 B2 存在时）──
    score_m2 = 0
    if b2_idx is not None and b2_idx > b1_idx+1:
        p_amounts = [klines[j].get('amount', 0) or 0 for j in range(b1_idx+1, b2_idx)]
        p_amt_avg = sum(p_amounts)/len(p_amounts) if p_amounts else 0
        b1_amount = klines[b1_idx].get('amount', 0) or 0
        if b1_amount > 0:
            ratio = p_amt_avg / b1_amount
            if ratio < 0.5:
                score_m2 = 5
            elif ratio < 0.7:
                score_m2 = 3

    # ── 8.8 新指标 M3: B2 跳空突破（5分，仅 B2 存在时）──
    score_m3 = 0
    if b2_idx is not None and b2_idx > 0:
        score_m3 = 5 if klines[b2_idx]['open'] > klines[b2_idx-1]['high'] else 0

    # ── 9. 综合评分 ──
    # 体系1（100分）= HDCP(40) + 行业(25) + Sig(10) + 跳空(10)
    # B1-only: HDC(35) + 行业(25) = 60, 有B2时满分100
    if b2_idx is not None:
        total = sum(score.values()) + score_i1 + score_i2 + score_sig + score_gap
    else:
        total = score['H'] + score['D'] + score['C'] + score_i1 + score_i2 + score_sig
    
    if total >= 80: conf = '高'
    elif total >= 55: conf = '中'
    else: conf = '低'

    # 体系2（仅 B2 存在时有意义）
    if b2_idx is not None:
        total_v2 = total + score_m1 + score_m2 + score_m3
        if total_v2 >= 92: conf_v2 = '高'
        elif total_v2 >= 63: conf_v2 = '中'
        else: conf_v2 = '低'
    else:
        total_v2 = 0
        conf_v2 = ''

    # ── MW PLUS 标志（仅 B2 完整时）──
    is_plus = 0
    if b2_idx is not None:
        is_plus = 1 if (total >= 80 and score['D'] == 5 and score_i1 == 15) else 0

    # ── 10. 组装结果 ──
    b1k = klines[b1_idx]
    # B1 量比
    vol_20_b1 = [klines[j]['volume'] for j in range(max(0,b1_idx-20), b1_idx) if klines[j].get('volume')]
    b1_vr = b1k['volume']/(sum(vol_20_b1)/len(vol_20_b1)) if vol_20_b1 else 0

    # 横盘期日均成交额
    c_amounts = [klines[j].get('amount', 0) or 0 for j in range(c_start, c_end+1)]
    c_amount_avg = sum(c_amounts)/len(c_amounts) if c_amounts else 0

    # H点前60日最低到H的涨幅
    h_pre_start = max(0, h_idx-60)
    h_pre_low = min(klines[j]['close'] for j in range(h_pre_start, h_idx)) if h_pre_start < h_idx else h_price
    h_pre_rise = round((h_price - h_pre_low)/h_pre_low*100, 1) if h_pre_low > 0 else 0

    # B2 相关字段（仅 B2 存在时填充）
    if b2_idx is not None:
        b2k = klines[b2_idx]
        b2_date_val = dates[b2_idx]
        is_gap = b2_idx > 0 and b2k['open'] > klines[b2_idx-1]['high']
        b2_pos = (b2k['close']-b2k['low'])/(b2k['high']-b2k['low'])*100 if b2k['high']!=b2k['low'] else 100
        vol_20_b2 = [klines[j]['volume'] for j in range(max(0,b2_idx-20), b2_idx) if klines[j].get('volume')]
        b2_vr = b2k['volume']/(sum(vol_20_b2)/len(vol_20_b2)) if vol_20_b2 else 0
        b2_ma = 0
        for p in [5,10,20,30,60]:
            if b2_idx >= p-1:
                ma = sum(klines[j]['close'] for j in range(b2_idx-p+1, b2_idx+1))/p
                if b2k['close'] > ma: b2_ma += 1
        b2_return = round((b2k['close']/klines[b2_idx-1]['close']-1)*100, 2)
    else:
        b2_date_val = None; is_gap = 0; b2_pos = None; b2_vr = 0; b2_ma = 0; b2_return = None

    # P段数据
    p_max_dd = 0; p_vol_ratio_val = 0
    if b2_idx is not None and b2_idx > b1_idx+1:
        for ii in range(b1_idx+1, b2_idx):
            dd = (klines[ii]['close']/klines[b1_idx]['close']-1)*100
            if dd < p_max_dd: p_max_dd = dd
        p_vol_sum = sum(klines[ii].get('volume', 0) or 0 for ii in range(b1_idx+1, b2_idx))
        p_vol_ratio_val = round(p_vol_sum/(b2_idx-b1_idx-1)/klines[b1_idx]['volume'], 2) if klines[b1_idx]['volume'] > 0 else 0

    result = {
        'h_date': dates[h_idx], 'h_price': round(h_price, 2),
        'l_date': dates[l_idx], 'l_price': round(l_price, 2),
        'c_start': dates[c_start], 'c_end': dates[c_end],
        'b1_date': dates[b1_idx], 'b1_return_pct': round((b1k['close']/klines[b1_idx-1]['close']-1)*100, 2) if b1_idx>0 else 0,
        'b1_vol_ratio': round(b1_vr, 2),
        'b2_date': b2_date_val, 'b2_return_pct': b2_return,
        'b2_close_pos': round(b2_pos, 1) if b2_pos else None,
        'b2_is_gap': 1 if is_gap else 0,
        'b2_ma_count': b2_ma,
        'decline_pct': round(decline, 1),
        'c_amplitude_pct': round(c_amp, 1),
        'h_rs20': h_rs20, 'h_rs250': h_rs250,
        'c_amount_avg': round(c_amount_avg, 0),
        'h_pre_rise_pct': h_pre_rise,
        'p_max_dd_pct': round(p_max_dd, 1),
        'p_vol_ratio': p_vol_ratio_val,
        'score': total, 'confidence': conf,
        'score_v2': total_v2, 'confidence_v2': conf_v2,
        'is_plus': is_plus,
        'score_h': score['H'], 'score_d': score['D'],
        'score_c': score['C'], 'score_p': score['P'],
        'score_i1': score_i1, 'score_i2': score_i2,
        'score_o1': 0, 'score_o2': 0,
        'score_ma': 0, 'score_sig': score_sig, 'score_gap': score_gap,
        'score_m1': score_m1, 'score_m2': score_m2, 'score_m3': score_m3,
        'ind_rs20': ind_rs20, 'ind_rs250': ind_rs250,
        'ind_code': ind_code if 'ind_code' in dir() else None,
        'ind_name': ind_name if 'ind_name' in dir() else None,
    }
    return True, result


def save_signals(conn, scan_date, signals):
    """保存信号。v2.4: B1 可独立保存(b2_date=NULL)，以 (stock_code, b1_date) 为唯一键"""
    for s in signals:
        # 检查是否已存在 (stock_code, b1_date)，有则 UPDATE，无则 INSERT
        existing = conn.execute(
            "SELECT id FROM mw_signal_daily WHERE stock_code=? AND b1_date=?",
            (s['code'], s['b1_date'])
        ).fetchone()
        if existing and s['b2_date']:
            # B2 更新：补齐 B2 字段和完整评分
            conn.execute("""UPDATE mw_signal_daily SET
                b2_date=?, b2_return_pct=?, b2_close_pos=?, b2_is_gap=?, b2_ma_count=?,
                confidence=?, score=?, confidence_v2=?, score_v2=?,
                score_p=?, score_sig=?, score_gap=?, score_m1=?, score_m2=?, score_m3=?, is_plus=?,
                p_max_dd_pct=?, p_vol_ratio=?, scan_date=?
                WHERE stock_code=? AND b1_date=?
            """, (
                s['b2_date'], s['b2_return_pct'], s['b2_close_pos'], s['b2_is_gap'], s['b2_ma_count'],
                s['confidence'], s['score'], s.get('confidence_v2',''), s.get('score_v2',0),
                s['score_p'], s.get('score_sig',0), s.get('score_gap',0),
                s.get('score_m1',0), s.get('score_m2',0), s.get('score_m3',0), s.get('is_plus',0),
                s.get('p_max_dd_pct'), s.get('p_vol_ratio'), scan_date,
                s['code'], s['b1_date']
            ))
        else:
            # 新插入（B1-only 或首次完整信号）
            conn.execute("""INSERT OR REPLACE INTO mw_signal_daily
                (b2_date,stock_code,stock_name,confidence,score,confidence_v2,score_v2,
                 h_date,h_price,l_date,l_price,c_start,c_end,
                 b1_date,b1_return_pct,b1_vol_ratio,
                 b2_return_pct,b2_close_pos,b2_is_gap,b2_ma_count,
                 decline_pct,c_amplitude_pct,
                 h_rs20,h_rs250,c_amount_avg,
                 h_pre_rise_pct,p_max_dd_pct,p_vol_ratio,
                 score_h,score_d,score_c,score_p,
                 score_i1,score_i2,score_o1,score_o2,
                 score_ma,score_sig,score_gap,score_m1,score_m2,score_m3,is_plus,
                 ind_rs20,ind_rs250,ind_code,ind_name,scan_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                s['b2_date'],s['code'],s.get('name',''),s['confidence'],s['score'],s.get('confidence_v2',''),s.get('score_v2',0),
                s['h_date'],s['h_price'],s['l_date'],s['l_price'],s['c_start'],s['c_end'],
                s['b1_date'],s['b1_return_pct'],s['b1_vol_ratio'],
                s['b2_return_pct'],s['b2_close_pos'],s['b2_is_gap'],s['b2_ma_count'],
                s['decline_pct'],s['c_amplitude_pct'],
                s.get('h_rs20'),s.get('h_rs250'),s.get('c_amount_avg',0),
                s.get('h_pre_rise_pct'),s.get('p_max_dd_pct'),s.get('p_vol_ratio'),
                s['score_h'],s['score_d'],s['score_c'],s['score_p'],
                s.get('score_i1',0),s.get('score_i2',0),s.get('score_o1',0),s.get('score_o2',0),
                s.get('score_ma',0),s.get('score_sig',0),s.get('score_gap',0),s.get('score_m1',0),s.get('score_m2',0),s.get('score_m3',0),s.get('is_plus',0),
                s.get('ind_rs20'),s.get('ind_rs250'),s.get('ind_code'),s.get('ind_name'),scan_date
            ))
    conn.commit()


def run_scan(scan_date, fast=False):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    # 建表 — 若缺少新列则重建
    try:
        conn.execute("SELECT confidence_v2 FROM mw_signal_daily LIMIT 0")
    except:
        conn.execute("DROP TABLE IF EXISTS mw_signal_daily")
        conn.execute("DROP TABLE IF EXISTS mw_signal_daily_old")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mw_signal_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            b2_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            confidence TEXT,
            score INTEGER,
            confidence_v2 TEXT,
            score_v2 INTEGER,
            h_date TEXT, h_price REAL,
            l_date TEXT, l_price REAL,
            c_start TEXT, c_end TEXT,
            b1_date TEXT, b1_return_pct REAL, b1_vol_ratio REAL,
            b2_return_pct REAL, b2_close_pos REAL, b2_is_gap INTEGER, b2_ma_count INTEGER,
            decline_pct REAL, c_amplitude_pct REAL,
            h_rs20 INTEGER, h_rs250 INTEGER,
            c_amount_avg REAL,
            h_pre_rise_pct REAL,
            p_max_dd_pct REAL,
            p_vol_ratio REAL,
            score_h INTEGER, score_d INTEGER, score_c INTEGER, score_p INTEGER,
            score_i1 INTEGER, score_i2 INTEGER,
            score_o1 INTEGER, score_o2 INTEGER,
            score_ma INTEGER, score_sig INTEGER, score_gap INTEGER,
            score_m1 INTEGER, score_m2 INTEGER, score_m3 INTEGER,
            is_plus INTEGER DEFAULT 0,
            ind_rs20 INTEGER, ind_rs250 INTEGER,
            ind_code TEXT, ind_name TEXT,
            scan_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, b1_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mw_b1date ON mw_signal_daily(b1_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mw_code ON mw_signal_daily(stock_code)")

    stocks = get_all_stocks(conn)
    if fast:
        import random
        stocks = random.sample(stocks, min(200, len(stocks)))
        print(f'快速模式: 随机采样 {len(stocks)} 只')

    min_date = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=1000)).strftime('%Y-%m-%d')
    signals = []
    b1_count = 0

    for i, code in enumerate(stocks):
        klines = get_klines(conn, code, min_date, scan_date)
        if len(klines) < 150: continue

        passed, result = scan_stock(klines, scan_date, code, conn)
        if result:
            b1_count += 1
        if passed:
            result['code'] = code
            # 获取名称
            row = conn.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
            result['name'] = row['name'] if row else code
            signals.append(result)

        if (i+1) % 1000 == 0:
            print(f'  进度: {i+1}/{len(stocks)} (B1: {b1_count}, 信号: {len(signals)})')

    save_signals(conn, scan_date, signals)
    conn.close()

    print(f'\n扫描完成: {len(stocks)} 只, B1触发 {b1_count}, MW信号 {len(signals)}')
    for s in signals:
        print(f'  {s["code"]} {s["name"]} B1:{s["b1_date"]} B2:{s["b2_date"]} 置信度:{s["confidence"]}({s["score"]}分)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MW信号扫描引擎')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--fast', action='store_true', help='快速模式(随机采样200只)')
    args = parser.parse_args()
    run_scan(args.date, fast=args.fast)
