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
_fallback_log = []    # 兜底日志: [(code, scan_date, source), ...]
_fallback_stats = {'total_scanned': 0, 'fallback_sql': 0, 'fallback_live': 0}
_disable_fallback = False  # True=禁止兜底(等同实盘，预加载未命中则跳过该股票)

# ── 批量预加载缓存（回填脚本设置后，scan_stock 跳过 SQL）──
_rs_cache = None          # {stock_code: (rps_20, rps_250)}
_idx_comp_cache = None    # {stock_code: {index_code, ...}}
_idx_rs_cache = None      # {index_code: (rs_20, rs_250)}
_reso_cache = None        # {(stock_code, scan_date): signals_json}
_names_cache = None       # {stock_code: name}
_kline_cache = None       # {stock_code: [row_dict_sorted_by_date, ...]}
_sell_existing_cache = None  # {stock_code: [signal_dict, ...]} (回填脚本注入)


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


def get_all_stocks(conn, scan_date=None):
    """获取候选股票，排除 ST"""
    ref_date = scan_date if scan_date else "date('now')"
    rows = conn.execute(f"""SELECT DISTINCT k.stock_code FROM daily_kline k INNER JOIN stock_basic b ON k.stock_code=b.stock_code WHERE b.listing_status='normally_listed' AND b.name NOT LIKE '%ST%' AND k.date = ?""", (ref_date,)).fetchall()
    return [r[0] for r in rows]


def get_klines(conn, code, min_date, max_date=None):
    """获取 K 线数据,可限制结束日期"""
    if _kline_cache is not None:
        # 缓存格式: {code: [row_sorted_by_date, ...]}
        rows = _kline_cache.get(code)
        if rows:
            klines = [r for r in rows if r['date'] >= min_date and (max_date is None or r['date'] <= max_date)]
            if klines:
                return klines

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

    # ── 1. 找前高 H：用缠论笔顶，选 pre_rise 最大的 ──
    h_date_str = None; h_price = 0; h_idx = 0
    bi_list = None
    cache_key = (code, scan_date)
    if cache_key not in _chanlun_cache:
        if _disable_fallback:
            _fallback_log.append((code, scan_date, 'skipped'))
            bi_list = []
        else:
            fallback_source = 'sql'
            if conn:
                row = conn.execute(
                    "SELECT bi_json FROM chanlun_bi_json WHERE stock_code=? ORDER BY scan_date DESC LIMIT 1",
                    (code,)
                ).fetchone()
                if row and row[0]:
                    try: bi_list = json.loads(row[0])
                    except: pass
            if bi_list is None:
                from scanners.chanlun import analyze as _af
                bi_list = _af(code, 'D', 500, data_mode='stock', end_date=scan_date).get('bi_list', [])
                fallback_source = 'live'
            else:
                # DB 缓存的 bi 可能 lookback 不够（如 2026 算的 bi 没有 2016 的顶）
                # 检查：bi 中是否有 scan_date 之前的顶，没有则走实时计算（带 end_date）
                cached_tops = [b for b in bi_list if b['direction'] == '向下' and b['sdt'][:10] <= scan_date]
                if len(cached_tops) < 1 and bi_list:
                    from scanners.chanlun import analyze as _af
                    bi_list = _af(code, 'D', 500, data_mode='stock', end_date=scan_date).get('bi_list', [])
                    fallback_source = 'live'
            _fallback_log.append((code, scan_date, fallback_source))
            if fallback_source == 'sql':
                _fallback_stats['fallback_sql'] += 1
            else:
                _fallback_stats['fallback_live'] += 1
        _chanlun_cache[cache_key] = bi_list
    else:
        bi_list = _chanlun_cache[cache_key]
    _fallback_stats['total_scanned'] += 1
    tops = [(b['sdt'][:10], b['high']) for b in bi_list if b['direction'] == '向下']
    
    # 在所有满足条件的顶中，选 pre_rise 最大的
    best_h = None  # (pre_rise, date, price, idx)
    for top_date, top_price in tops:
        if top_date > scan_date: continue
        try: top_idx = dates.index(top_date)
        except ValueError: continue
        
        # 500天时间窗
        scan_idx = len(dates) - 1
        if top_idx < max(0, scan_idx - 500): continue
        
        future_low = min(klines[j]['close'] for j in range(top_idx+1, n)) if top_idx+1 < n else top_price
        decline = (top_price - future_low)/top_price if top_price > 0 else 0
        if decline < 0.10: continue
        
        pre60_start = max(0, top_idx-60)
        pre60_low = min(klines[j]['close'] for j in range(pre60_start, top_idx)) if pre60_start < top_idx else top_price
        pre_rise = (top_price - pre60_low)/pre60_low if pre60_low > 0 else 0
        if pre_rise < 0.30: continue
        
        if best_h is None or pre_rise > best_h[0]:
            best_h = (pre_rise, top_date, top_price, top_idx)
    
    if best_h:
        h_date_str = best_h[1]; h_price = best_h[2]; h_idx = best_h[3]
        
        # ── 升级规则：沿笔顶序列向前，如果后续笔顶的 high 更高则升级 ──
        tops_forward = sorted([(t[0], t[1]) for t in tops if t[0] > h_date_str], key=lambda x: x[0])
        for next_date, next_high in tops_forward:
            if next_date > scan_date: continue
            if next_high >= h_price:
                try:
                    next_idx = dates.index(next_date)
                    h_date_str = next_date; h_price = next_high; h_idx = next_idx
                except ValueError:
                    pass
            # Note: 不 break——lower high 可能只是小回调，后面还有更高的

    if h_price == 0:
        return False, None

    # ── 2. 找最低点 L：H 之后最低收盘的缠论笔底 ──
    l_idx = h_idx; l_price = klines[h_idx]['close']
    bots = [(b['sdt'][:10], b['low']) for b in bi_list if b['direction'] == '向上']
    for bot_date, bot_price in bots:
        if bot_date > h_date_str:
            try: bi = dates.index(bot_date)
            except ValueError: continue
            if klines[bi]['close'] < l_price:
                l_idx = bi; l_price = bot_price

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

    b1_date_str = dates[b1_idx]

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
    
    # ── 6. 辅助评分(形态 H/D) ── v3.0重标定
    score = {'H': 0, 'D': 0, 'C': 0, 'P': 0}

    # H: SMA50 斜率 > 0 AND 价格在 MA200 上方（满分 15）
    if h_idx >= 60:
        sma50_now = sum(klines[j]['close'] for j in range(h_idx-50, h_idx))/50
        sma50_10d_ago = sum(klines[j]['close'] for j in range(h_idx-60, h_idx-10))/50 if h_idx >= 60 else sma50_now
        price_above_ma200 = False
        if h_idx >= 200:
            ma200_h = sum(klines[j]['close'] for j in range(h_idx-200, h_idx))/200
            price_above_ma200 = klines[h_idx]['close'] > ma200_h
        if sma50_now > sma50_10d_ago and price_above_ma200:
            score['H'] = 15

    # D: 25% ≤ 跌幅 ≤ 40%（满分25），20%~25%（15分），15%~20%（5分）
    decline = (h_price - l_price)/h_price*100 if h_price > 0 else 0
    if 25 <= decline <= 40:
        score['D'] = 25
    elif 20 <= decline < 25:
        score['D'] = 15
    elif 15 <= decline < 20:
        score['D'] = 5

    # C: 横盘质量 — v3.0 已删除（全周期 r=-0.036，与收益负相关）
    c_closes = [klines[j]['close'] for j in range(c_start, c_end+1)]
    c_min = min(c_closes); c_max = max(c_closes)
    c_amp = (c_max-c_min)/c_min*100 if c_min > 0 else 999
    # C 不再参与评分，但保留振幅计算供参考

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

    # ── 6.5 前高时的 RS 强度（按 H 点日期查询）──
    h_rs250 = h_rs20 = None
    if conn and code:
        row = conn.execute(
            "SELECT rps_20, rps_250 FROM stock_rs_daily WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1",
            (code, dates[h_idx])
        ).fetchone()
        if not row:
            # H 日期在 RS 数据覆盖之前（如 H=2015-08，stock_rs_daily 最早 2016-01-04）
            # 取最早可用的 RS 数据，不直接拒绝信号
            row = conn.execute(
                "SELECT rps_20, rps_250 FROM stock_rs_daily WHERE stock_code=? ORDER BY date ASC LIMIT 1",
                (code,)
            ).fetchone()
        if row:
            h_rs20 = row[0]
            h_rs250 = row[1]

    # v3.0 硬门禁：前高 RS250 ≥ 50，不满足则不出 B1
    if h_rs250 is None or h_rs250 < 50:
        return False, None

    # ── 7. 行业共振 + 个股RS强度评分 ──
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
        
        if _idx_comp_cache is not None:
            # 从预加载缓存读取
            idx_set = set(_idx_comp_cache.get(code, []))
        else:
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
        
        # 兜底：L2/L1 都没匹配 → 查 SW 行业映射 → 最后兜底 000985
        if not use_set:
            # 查 stock_sw_industry 获取申万行业
            if not hasattr(scan_stock, '_sw_map'):
                import yaml
                _sw_cfg = yaml.safe_load(open(_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), 'config', 'sw_to_index.yaml'), 'r', encoding='utf-8'))
                scan_stock._sw_map = _sw_cfg
            sw_row = conn.execute(
                "SELECT industry_name FROM stock_sw_industry WHERE stock_code=? ORDER BY updated_at DESC LIMIT 1",
                (code,)
            ).fetchone()
            if sw_row and sw_row[0] in scan_stock._sw_map:
                mapped = scan_stock._sw_map[sw_row[0]]
                # 优先 L2，其次 L1
                use_set = {str(mapped['l2'])} if 'l2' in mapped else set()
                if not use_set and 'l1' in mapped:
                    use_set = {str(mapped['l1'])}
        # 最终兜底：中证全指
        if not use_set:
            use_set = {'000985'}
        
        if use_set:
            placeholders = ','.join('?' * len(use_set))
            # 取预加载缓存中的最高行业 RS（缓存非空才用，空则走 SQL 兜底查 H 日期数据）
            if _idx_rs_cache:
                best_idx = max(use_set, key=lambda c: _idx_rs_cache.get(c, (0,0))[1], default=None)
                best = (best_idx,) + _idx_rs_cache.get(best_idx, (0,0)) if best_idx and best_idx in _idx_rs_cache else None
            else:
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
        # I1: H点行业RS250，阶梯制（满分20）
        if ind_rs250 is not None and ind_rs250 >= 85:
            score_i1 = 20
        elif ind_rs250 is not None and ind_rs250 >= 80:
            score_i1 = 10
        # I2: 股票H点RS250，阶梯制（满分30）
        if h_rs250 is not None and h_rs250 >= 90:
            score_i2 = 30
        elif h_rs250 is not None and h_rs250 >= 85:
            score_i2 = 20
        elif h_rs250 is not None and h_rs250 >= 75:
            score_i2 = 10

    # ── 8. MA 排列质量评分 — 已移除，替换为 B2 跳空 ──
    # （B2 硬闸已保证站上 MA60，均线排列冗余）

    # ── 8.5 信号共振评分（v3.0：满分10，累加制，B1/B2日均用）──
    # PP_V1 +5, BO_V2 +3, 缠论背驰 +2, 蜡烛形态 +1
    score_sig = 0
    def _compute_sig(code, sig_date, score_sig):
        if not (conn and code):
            return score_sig
        row = None
        if _reso_cache is not None:
            sigs_json = _reso_cache.get((code, sig_date))
            if sigs_json is not None:
                row = (sigs_json,)
        if row is None:
            row = conn.execute(
                "SELECT signals_json FROM pattern_scan_signals WHERE date=? AND stock_code=?",
                (sig_date, code)
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
                    if src == 'pocket_pivot':
                        score_sig += 5
                    elif src == 'base_breakout':
                        score_sig += 3
                    elif src == 'chanlun_divergence':
                        score_sig += 2
                    elif src in ('cdl', 'talib'):
                        score_sig += 1
                score_sig = min(score_sig, 10)
            except:
                pass
        return score_sig
    
    if b2_idx is not None:
        score_sig = _compute_sig(code, dates[b2_idx], score_sig)
    else:
        score_sig = _compute_sig(code, dates[b1_idx], score_sig)

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

    # ── 9. 综合评分 v3.0 ──
    # B1-only: H(15) + D(25) + I1(20) + I2(30) + Sig(10) = 100
    # B1+B2: 同 B1-only（P / Gap / M1/M2/M3 保留供回测参考，不参与主评分）
    total = score['H'] + score['D'] + score_i1 + score_i2 + score_sig
    
    # v3.0 置信度分层（满分100）
    if total >= 70:
        conf = '高'
    elif total >= 50:
        conf = '中'
    else:
        conf = '低'

    # 体系2（扩展指标，仅 B2 存在时有意义，供回测参考）
    if b2_idx is not None:
        total_v2 = total + score_gap + score_m1 + score_m2 + score_m3
        if total_v2 >= 110: conf_v2 = '高'
        elif total_v2 >= 80: conf_v2 = '中'
        else: conf_v2 = '低'
    else:
        total_v2 = 0
        conf_v2 = ''

    # ── MW PLUS 标志（仅 B2 完整时）──
    # v3.0: 总分≥80 AND D满分(25) AND I1满分(20)
    is_plus = 0
    if b2_idx is not None:
        is_plus = 1 if (total >= 80 and score['D'] == 25 and score_i1 >= 20) else 0

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
    if not signals:
        conn.execute("INSERT OR REPLACE INTO mw_signal_daily (b2_date, stock_code, b1_date, scan_date) VALUES (NULL, '_sentinel_', '_sentinel_', ?)", (scan_date,))
        conn.commit()
        return
    for s in signals:
        # 计算 B1 关注度评分 v3.0（替代旧 tech_score）
        ts = 0
        ts_detail_json = ''
        try:
            ts, ts_detail = compute_attention_score(
                s['code'], s['b1_date'], 
                _kline_cache.get(s['code'], []) if _kline_cache else [],
                s.get('decline_pct'), s.get('h_rs250'), s.get('b1_return_pct'),
                s.get('h_date'), s.get('c_amount_avg', 0),
                ind_rs20=s.get('ind_rs20'),
                conn=conn, return_detail=True
            )
            import json as _json
            ts_detail_json = _json.dumps(ts_detail, ensure_ascii=False)
        except Exception as _e:
            import traceback as _tb
            _tb.print_exc()
            print(f'  [ATTN ERR] {s.get("code","?")} {s.get("b1_date","?")}: {_e}')
        
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
                tech_score=?, tech_score_detail=?,
                p_max_dd_pct=?, p_vol_ratio=?, scan_date=?
                WHERE stock_code=? AND b1_date=?
            """, (
                s['b2_date'], s['b2_return_pct'], s['b2_close_pos'], s['b2_is_gap'], s['b2_ma_count'],
                s['confidence'], s['score'], s.get('confidence_v2',''), s.get('score_v2',0),
                s['score_p'], s.get('score_sig',0), s.get('score_gap',0),
                s.get('score_m1',0), s.get('score_m2',0), s.get('score_m3',0), s.get('is_plus',0),
                ts, ts_detail_json,
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
                 tech_score,tech_score_detail,
                 ind_rs20,ind_rs250,ind_code,ind_name,scan_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                s.get('score_ma',0),s.get('score_sig',0),s.get('score_gap',0),s.get('score_m1',0),s.get('score_m2',0),s.get('score_m3',0),s.get('is_plus',0),ts,ts_detail_json,
                s.get('ind_rs20'),s.get('ind_rs250'),s.get('ind_code'),s.get('ind_name'),scan_date
            ))
    conn.commit()


# ══════════════════ B1 技术面置信度评分 ══════════════════

def compute_tech_score(code, b1_date, klines, rs_cache=None, return_detail=False):
    """[已废弃] 旧版技术置信度评分。全周期验证无区分力，已替换为 compute_attention_score。
    保留此函数仅为向后兼容，新扫描请使用 compute_attention_score。"""
    return (0, {}) if return_detail else 0


# ══════════════════ B1 关注度评分 v4.4 (IC校准) ══════════════════

def compute_attention_score(code, b1_date, klines, decline_pct, h_rs250, b1_return_pct,
                             h_date, c_amount_avg, ind_rs20=None, conn=None, return_detail=False):
    """
    B1 关注度评分 v4.4（满分 100）。

    2026-07-24 基于 49,395 条信号 Spearman IC 校准权重。
    Q1-Q5 区分力从 v3.5 的 0.3pp 提升至 11.2pp。

    1. 上轨突破% (25分) — 反向，远低于布林上轨=安全
       <-5%→25, -5~-2%→18, -2~0%→12, 0~5%→6, >5%→0

    2. 乖离率 MA20 (20分) — 反向，低乖离=安全
       <0%→20, 0~5%→15, 5~10%→10, >10%→5

    3. 趋势效率 (20分) — 反向，横盘整理=蓄力
       <-0.5→20, -0.5~-0.2→15, -0.2~0→10, 0~0.3→5, >0.3→0

    4. 距H天数 (15分) — 正向，40~60天最佳
       40~60→15, 30~40→12, 20~30或60~80→8, >80→5

    5. 回调深度 (10分) — 正向，深调=洗盘充分
       >35%→10, 25~35%→8, 20~25%→5, 15~20%→3

    6. 行业 RS_20 (5分) — 正向，行业动量
       ≥90→5, ≥80→4, ≥70→3, ≥60→2

    7. h_rs250 (5分) — 正向，仅保留微弱权重
       ≥90→5, ≥80→3, ≥70→2
    """
    from datetime import date
    import numpy as np
    sc = 0
    detail = {}

    # 上轨突破% 和 趋势效率 需从 klines 实时计算
    ub_pct = 0
    teh = 0
    dev_ma20 = 0
    if klines and len(klines) >= 20:
        closes = np.array([k['close'] for k in klines[-20:]])
        ma20 = np.mean(closes)
        std20 = np.std(closes)
        upper = ma20 + 2 * std20
        # 上轨突破% = (B1收盘 - 布林上轨) / 布林上轨 * 100
        if upper > 0:
            ub_pct = (klines[-1]['close'] - upper) / upper * 100
        # 趋势效率 = 净涨跌 / 路径长度
        net = closes[-1] - closes[0]
        path = np.sum(np.abs(np.diff(closes)))
        if path > 0:
            teh = net / path
        # 乖离率
        if ma20 > 0:
            dev_ma20 = (klines[-1]['close'] - ma20) / ma20 * 100

    # 1. 上轨突破% (25分) 反向
    if ub_pct < -5: v = 25
    elif ub_pct < -2: v = 18
    elif ub_pct < 0: v = 12
    elif ub_pct < 5: v = 6
    else: v = 0
    sc += v; detail['upper_band'] = v

    # 2. 乖离率 MA20 (20分) 反向
    if dev_ma20 < 0: v = 20
    elif dev_ma20 < 5: v = 15
    elif dev_ma20 < 10: v = 10
    else: v = 5
    sc += v; detail['deviation'] = v

    # 3. 趋势效率 (20分) 反向
    if teh < -0.5: v = 20
    elif teh < -0.2: v = 15
    elif teh < 0: v = 10
    elif teh < 0.3: v = 5
    else: v = 0
    sc += v; detail['trend_eff'] = v

    # 4. 距H天数 (15分) 正向
    dh_v = 0
    if h_date and h_date > '2000-01-01' and b1_date:
        dh = (date.fromisoformat(b1_date) - date.fromisoformat(h_date)).days
        if 40 <= dh <= 60: dh_v = 15
        elif 30 <= dh < 40: dh_v = 12
        elif (20 <= dh < 30) or (60 < dh <= 80): dh_v = 8
        elif dh > 80: dh_v = 5
    sc += dh_v; detail['days_since_h'] = dh_v

    # 5. 回调深度 (10分) 正向
    dec = decline_pct or 0
    if dec > 35: dec_v = 10
    elif dec >= 25: dec_v = 8
    elif dec >= 20: dec_v = 5
    elif dec >= 15: dec_v = 3
    else: dec_v = 0
    sc += dec_v; detail['decline'] = dec_v

    # 6. 行业 RS_20 (5分) 正向
    ind_v = 0
    if ind_rs20 is not None and ind_rs20 >= 90: ind_v = 5
    elif ind_rs20 is not None and ind_rs20 >= 80: ind_v = 4
    elif ind_rs20 is not None and ind_rs20 >= 70: ind_v = 3
    elif ind_rs20 is not None and ind_rs20 >= 60: ind_v = 2
    sc += ind_v; detail['ind_rs20'] = ind_v

    # 7. h_rs250 (5分) 正向
    rs = h_rs250 or 0
    if rs >= 90: rs_v = 5
    elif rs >= 80: rs_v = 3
    elif rs >= 70: rs_v = 2
    else: rs_v = 0
    sc += rs_v; detail['h_rs250'] = rs_v

    return (sc, detail) if return_detail else sc


def run_scan(scan_date, fast=False, silent=False):
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
            tech_score INTEGER DEFAULT 0,
            tech_score_detail TEXT DEFAULT '',
            ind_rs20 INTEGER, ind_rs250 INTEGER,
            ind_code TEXT, ind_name TEXT,
            scan_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, b1_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mw_b1date ON mw_signal_daily(b1_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mw_code ON mw_signal_daily(stock_code)")
    # 确保 tech_score 列存在（兼容旧表）
    try:
        conn.execute("ALTER TABLE mw_signal_daily ADD COLUMN tech_score INTEGER DEFAULT 0")
    except:
        pass
    # 确保 tech_score_detail 列存在
    try:
        conn.execute("ALTER TABLE mw_signal_daily ADD COLUMN tech_score_detail TEXT DEFAULT ''")
    except:
        pass

    # ── 批量预加载：缠论笔（取每只股票最新，不限 scan_date）──
    global _chanlun_cache, _kline_cache, _rs_cache, _idx_comp_cache, _idx_rs_cache, _reso_cache, _names_cache
    if not _chanlun_cache:
        for row in conn.execute(
            "SELECT stock_code, bi_json FROM chanlun_bi_json GROUP BY stock_code HAVING scan_date=MAX(scan_date)"
        ).fetchall():
            try:
                _chanlun_cache[(row['stock_code'], scan_date)] = __import__('orjson').loads(row['bi_json'])
            except:
                pass

    # ── 自动批量预加载（外部回填脚本可能已设置，为 None 时才加载）──
    if _kline_cache is None:
        from collections import defaultdict
        _kline_cache = defaultdict(list)
        kline_min = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=400)).strftime('%Y-%m-%d')
        for r in conn.execute(
            "SELECT stock_code, date, open, high, low, close, volume, amount FROM daily_kline WHERE date>=? AND date<=? ORDER BY stock_code, date",
            (kline_min, scan_date)
        ).fetchall():
            _kline_cache[r['stock_code']].append(dict(r))

    if _rs_cache is None:
        _rs_cache = {}
        for r in conn.execute(
            "SELECT stock_code, rps_20, rps_250 FROM stock_rs_daily WHERE date=?",
            (scan_date,)
        ).fetchall():
            _rs_cache[r['stock_code']] = (r['rps_20'], r['rps_250'])

    if _idx_comp_cache is None:
        from collections import defaultdict
        _idx_comp_cache = defaultdict(list)
        for r in conn.execute("SELECT stock_code, index_code FROM index_constituents").fetchall():
            _idx_comp_cache[r['stock_code']].append(r['index_code'])

    if _idx_rs_cache is None:
        _idx_rs_cache = {}
        for r in conn.execute(
            "SELECT stock_code, rs_20, rs_250 FROM index_rs_daily WHERE date=?",
            (scan_date,)
        ).fetchall():
            _idx_rs_cache[r['stock_code']] = (r['rs_20'], r['rs_250'])

    if _reso_cache is None:
        _reso_cache = {}
        for r in conn.execute(
            "SELECT stock_code, signals_json FROM pattern_scan_signals WHERE date=?",
            (scan_date,)
        ).fetchall():
            _reso_cache[(r['stock_code'], scan_date)] = r['signals_json']

    if _names_cache is None:
        _names_cache = {}
        for r in conn.execute("SELECT stock_code, name FROM stock_basic").fetchall():
            _names_cache[r['stock_code']] = r['name']

    stocks = get_all_stocks(conn, scan_date)
    if fast:
        import random
        stocks = random.sample(stocks, min(200, len(stocks)))
        if not silent: print(f'快速模式: 随机采样 {len(stocks)} 只')

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
            # 获取名称（优先缓存）
            result['name'] = _names_cache.get(code) if _names_cache else (conn.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone() or {}).get('name', code)
            signals.append(result)

        if (i+1) % 1000 == 0:
            if not silent: print(f'  进度: {i+1}/{len(stocks)} (B1: {b1_count}, 信号: {len(signals)})')

    save_signals(conn, scan_date, signals)
    conn.close()

    print(f'\n扫描完成: {len(stocks)} 只, B1触发 {b1_count}, MW信号 {len(signals)}')
    for s in signals:
        if not silent: print(f'  {s["code"]} {s["name"]} B1:{s["b1_date"]} B2:{s["b2_date"]} 置信度:{s["confidence"]}({s["score"]}分)')


def _scan_worker(stock_codes, scan_date, db_path):
    """
    独立进程 worker：加载指定股票的数据并运行 scan_stock。
    必须为模块级函数才能被 ProcessPoolExecutor(pickle) 在 Windows spawn 模式下使用。
    """
    import os as _os, sys as _sys
    from datetime import datetime, timedelta
    from collections import defaultdict
    import sqlite3 as _sql

    # 确保 src/ 在 path 中（subprocess 的 cwd 可能不同）
    _src = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..')
    if _src not in _sys.path:
        _sys.path.insert(0, _src)

    # 在 worker 内部导入模块（避免 pickle 整个 mw_signal）
    import scanners.mw_signal as mw

    # 重置模块级缓存
    mw._chanlun_cache = {}
    mw._kline_cache = None
    mw._rs_cache = None
    mw._idx_comp_cache = None
    mw._idx_rs_cache = None
    mw._reso_cache = {}
    mw._names_cache = None
    mw._sell_existing_cache = {}

    conn = _sql.connect(db_path)
    conn.row_factory = _sql.Row
    conn.execute("PRAGMA busy_timeout=30000")

    codes = list(stock_codes)
    if not codes:
        conn.close()
        return []

    # ── 1. K 线 ──
    mw._kline_cache = defaultdict(list)
    kline_min = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=400)).strftime('%Y-%m-%d')
    ph = ','.join('?' * len(codes))
    for r in conn.execute(
        f"SELECT stock_code, date, open, high, low, close, volume, amount FROM daily_kline WHERE stock_code IN ({ph}) AND date>=? AND date<=? ORDER BY stock_code, date",
        codes + [kline_min, scan_date]
    ).fetchall():
        mw._kline_cache[r['stock_code']].append(dict(r))

    # ── 2. 缠论笔 ──
    try:
        import orjson as _ojson
    except ImportError:
        import json as _ojson
    for row in conn.execute(
        f"SELECT stock_code, bi_json FROM chanlun_bi_json WHERE stock_code IN ({ph}) GROUP BY stock_code HAVING scan_date=MAX(scan_date)",
        codes
    ).fetchall():
        try:
            mw._chanlun_cache[(row['stock_code'], scan_date)] = _ojson.loads(row['bi_json'])
        except:
            pass

    # ── 3. 个股 RS ──
    mw._rs_cache = {}
    for r in conn.execute(
        f"SELECT stock_code, rps_20, rps_250 FROM stock_rs_daily WHERE date=? AND stock_code IN ({ph})",
        [scan_date] + codes
    ).fetchall():
        mw._rs_cache[r['stock_code']] = (r['rps_20'], r['rps_250'])

    # ── 4. 行业成分 ──
    mw._idx_comp_cache = defaultdict(list)
    for r in conn.execute(
        f"SELECT stock_code, index_code FROM index_constituents WHERE stock_code IN ({ph})",
        codes
    ).fetchall():
        mw._idx_comp_cache[r['stock_code']].append(r['index_code'])

    # ── 5. 指数 RS ──
    mw._idx_rs_cache = {}
    for r in conn.execute(
        "SELECT stock_code, rs_20, rs_250 FROM index_rs_daily WHERE date=?",
        (scan_date,)
    ).fetchall():
        mw._idx_rs_cache[r['stock_code']] = (r['rs_20'], r['rs_250'])

    # ── 6. 名称 ──
    mw._names_cache = {}
    for r in conn.execute(
        f"SELECT stock_code, name FROM stock_basic WHERE stock_code IN ({ph})",
        codes
    ).fetchall():
        mw._names_cache[r['stock_code']] = r['name']

    # ── 扫描 ──
    min_date = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=1000)).strftime('%Y-%m-%d')
    results = []
    for code in codes:
        klines = mw.get_klines(conn, code, min_date, scan_date)
        if len(klines) < 150:
            continue
        passed, result = mw.scan_stock(klines, scan_date, code, conn)
        if passed and result:
            result['code'] = code
            result['name'] = mw._names_cache.get(code, code)
            results.append(result)

    conn.close()
    return results


def run_scan_parallel(scan_date, n_workers=8, fast=False, silent=False):
    """多进程并行扫描。每个 worker 加载自己的数据子集并独立运行 scan_stock。"""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    import time as _time
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    
    # 确保表存在
    try:
        conn.execute("SELECT confidence_v2 FROM mw_signal_daily LIMIT 0")
    except:
        conn.execute("DROP TABLE IF EXISTS mw_signal_daily")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mw_signal_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            b2_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            confidence TEXT, score INTEGER, confidence_v2 TEXT, score_v2 INTEGER,
            h_date TEXT, h_price REAL, l_date TEXT, l_price REAL,
            c_start TEXT, c_end TEXT,
            b1_date TEXT, b1_return_pct REAL, b1_vol_ratio REAL,
            b2_return_pct REAL, b2_close_pos REAL, b2_is_gap INTEGER, b2_ma_count INTEGER,
            decline_pct REAL, c_amplitude_pct REAL,
            h_rs20 INTEGER, h_rs250 INTEGER, c_amount_avg REAL, h_pre_rise_pct REAL,
            p_max_dd_pct REAL, p_vol_ratio REAL,
            score_h INTEGER, score_d INTEGER, score_c INTEGER, score_p INTEGER,
            score_i1 INTEGER, score_i2 INTEGER, score_o1 INTEGER, score_o2 INTEGER,
            score_ma INTEGER, score_sig INTEGER, score_gap INTEGER,
            score_m1 INTEGER, score_m2 INTEGER, score_m3 INTEGER,
            is_plus INTEGER DEFAULT 0,
            tech_score INTEGER DEFAULT 0, tech_score_detail TEXT DEFAULT '',
            ind_rs20 INTEGER, ind_rs250 INTEGER, ind_code TEXT, ind_name TEXT,
            scan_date TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, b1_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mw_b1date ON mw_signal_daily(b1_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mw_code ON mw_signal_daily(stock_code)")
    try:
        conn.execute("ALTER TABLE mw_signal_daily ADD COLUMN tech_score INTEGER DEFAULT 0")
    except: pass
    try:
        conn.execute("ALTER TABLE mw_signal_daily ADD COLUMN tech_score_detail TEXT DEFAULT ''")
    except: pass
    
    stocks = get_all_stocks(conn, scan_date)
    conn.close()
    
    if fast:
        import random
        stocks = random.sample(stocks, min(200, len(stocks)))
    
    # 切分股票列表
    chunk_size = max(1, len(stocks) // n_workers)
    chunks = [stocks[i:i+chunk_size] for i in range(0, len(stocks), chunk_size)]
    # 如果段数超过 workers，合并最后两段
    if len(chunks) > n_workers:
        chunks[-2].extend(chunks[-1])
        chunks.pop()
    
    actual_workers = len(chunks)
    if not silent:
        print(f'多进程并行: {actual_workers} 进程 × {len(stocks)} 只股票')
    
    # 多进程扫描
    all_signals = []
    t_start = _time.time()
    
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(_scan_worker, chunk, scan_date, DB_PATH): idx 
                   for idx, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                signals = future.result()
                all_signals.extend(signals)
                if not silent:
                    print(f'  Worker {idx+1}/{actual_workers}: {len(signals)} 信号 ({len(chunks[idx])} 只)')
            except Exception as e:
                import traceback
                print(f'  Worker {idx+1} 异常: {e}')
                traceback.print_exc()
    
    # 保存结果（主进程写库，避免锁冲突）
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    save_signals(conn, scan_date, all_signals)
    
    total_b1 = conn.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b1_date=?", (scan_date,)).fetchone()[0]
    conn.close()
    
    elapsed = _time.time() - t_start
    if not silent:
        b2_count = sum(1 for s in all_signals if s.get('b2_date'))
        print(f'\n扫描完成: {len(stocks)} 只, B1={total_b1}, B2={b2_count} | {elapsed:.0f}s')
        for s in all_signals[:20]:
            print(f'  {s["code"]} {s.get("name","")} B1:{s.get("b1_date","")} B2:{s.get("b2_date","")}')
        if len(all_signals) > 20:
            print(f'  ... 共 {len(all_signals)} 条')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MW信号扫描引擎')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--fast', action='store_true', help='快速模式(随机采样200只)')
    parser.add_argument('--workers', type=int, default=1, help='并行进程数(默认1=单进程, 建议4~8)')
    args = parser.parse_args()
    if args.workers > 1:
        run_scan_parallel(args.date, n_workers=args.workers, fast=args.fast)
    else:
        run_scan(args.date, fast=args.fast)