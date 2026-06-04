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
        b1_vol_ok = k['volume'] > max_down_b1 and vol_r >= 1.5
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

        vol_20 = [klines[j]['volume'] for j in range(max(0,i-20), i) if klines[j].get('volume')]
        avg20 = sum(vol_20)/len(vol_20) if vol_20 else 0
        vol_r = k['volume']/avg20 if avg20 > 0 else 0
        # 最近10日最大下跌量
        max_down = max((klines[j]['volume'] for j in range(max(0,i-10), i) if j>0 and klines[j]['close']<klines[j-1]['close']), default=0)
        b2_vol_ok = k['volume'] > max_down and vol_r >= 1.1
        ret = (k['close']/klines[i-1]['close']-1) if i>0 else 0
        if k['high'] != k['low']: pos = (k['close']-k['low'])/(k['high']-k['low'])
        else: pos = 1.0

        is_gap = i > 0 and k['open'] > klines[i-1]['high']
        close_ok = (is_gap and pos >= 0.40) or (not is_gap and pos >= 0.80)
        if not close_ok: continue
        if not b2_vol_ok: continue
        if ret < 0.03: continue

        # 均线突破 ≥ 4
        ma_count = 0
        for period in [5,10,20,30,60]:
            if i >= period-1:
                ma = sum(klines[j]['close'] for j in range(i-period+1, i+1))/period
                if k['close'] > ma: ma_count += 1
        if ma_count < 4: continue

        b2_idx = i; break

    if b2_idx is None: return False, None

    # ── 检查 B2 必须在扫描日当天或之前,且在最近 N 天内 ──
    b2_date = dates[b2_idx]
    scan_dt = datetime.strptime(scan_date, '%Y-%m-%d')
    b2_dt = datetime.strptime(b2_date, '%Y-%m-%d')
    if b2_dt > scan_dt:
        return False, None  # B2 在未来
    if (scan_dt - b2_dt).days > B2_RECENT_DAYS * 2:
        return False, None  # B2 太久了

    # ── 6. 辅助评分(形态 H/D/C/P)──
    score = {'H': 0, 'D': 0, 'C': 0, 'P': 0}

    # H: SMA50 斜率 > 0
    if h_idx >= 60:
        sma50_now = sum(klines[j]['close'] for j in range(h_idx-50, h_idx))/50
        sma50_10d_ago = sum(klines[j]['close'] for j in range(h_idx-60, h_idx-10))/50 if h_idx >= 60 else sma50_now
        if sma50_now > sma50_10d_ago:
            score['H'] = 10

    # D: 15% ≤ 跌幅 ≤ 35%
    decline = (h_price - l_price)/h_price*100 if h_price > 0 else 0
    if 15 <= decline <= 35:
        score['D'] = 10

    # C: 振幅 < 10% AND 低点斜率 > 0
    c_closes = [klines[j]['close'] for j in range(c_start, c_end+1)]
    c_min = min(c_closes); c_max = max(c_closes)
    c_amp = (c_max-c_min)/c_min*100 if c_min > 0 else 999
    c_slope = linear_slope(c_closes)
    if c_amp < 10 and c_slope > 0:
        score['C'] = 10

    # P: 回撤 ≥ -7% AND 缩量
    if b2_idx > b1_idx+1:
        b1_close = klines[b1_idx]['close']
        p_dd = 0
        p_vols = []
        for i in range(b1_idx+1, b2_idx):
            dd = (klines[i]['close']/b1_close-1)*100
            if dd < p_dd: p_dd = dd
            p_vols.append(klines[i]['volume'])
        p_vol_avg = sum(p_vols)/len(p_vols) if p_vols else 0
        if p_dd >= -7 and p_vol_avg < klines[b1_idx]['volume']:
            score['P'] = 10
    else:
        # 只有 1 天间隔,P 几乎不存在,给满分(宽容处理)
        score['P'] = 0

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

    # ── 7. 行业共振评分(20分)──
    score_i1 = score_i2 = 0
    ind_rs20 = ind_rs250 = None
    if conn and code:
        # 获取行业名称
        ind_row = conn.execute(
            "SELECT industry_name FROM discipline_observation_pool WHERE stock_code=? ORDER BY date DESC LIMIT 1",
            (code,)
        ).fetchone()
        industry = ind_row[0] if ind_row else None
        if industry:
            # 匹配行业指数:在 index_rs_daily 中找名称包含行业名的指数
            idx_row = conn.execute("""
                SELECT r.stock_code, r.rs_20, r.rs_250
                FROM index_rs_daily r
                INNER JOIN index_daily_kline k ON r.stock_code=k.stock_code
                WHERE r.date=? AND k.stock_code LIKE '00%'
                GROUP BY r.stock_code
                LIMIT 1
            """, (scan_date,)).fetchone()
            # 简化:直接按行业名找指数 RS
            # 先查 index_constituents 看该股票属于哪些行业指数
            idx_codes = conn.execute("""
                SELECT DISTINCT ic.index_code FROM index_constituents ic
                WHERE ic.stock_code=?
            """, (code,)).fetchall()
            idx_set = set(r[0] for r in idx_codes)
            # 在这些指数中找有 RS 数据的,取 RS250 最高的(代表最相关的行业指数)
            if idx_set:
                placeholders = ','.join('?' * len(idx_set))
                best = conn.execute(f"""
                    SELECT stock_code, rs_20, rs_250 FROM index_rs_daily
                    WHERE date=? AND stock_code IN ({placeholders})
                    ORDER BY rs_250 DESC LIMIT 1
                """, [scan_date] + list(idx_set)).fetchone()
                if best:
                    ind_rs20 = best[1]
                    ind_rs250 = best[2]
            if ind_rs250 is not None and ind_rs250 >= 80:
                score_i1 = 10
            if ind_rs20 is not None and ind_rs20 >= 80:
                score_i2 = 10

    # ── 8. 欧奈尔质量评分(20分)──
    score_o1 = score_o2 = 0
    if conn and code:
        cs_row = conn.execute(
            "SELECT canslim_i, canslim_l FROM discipline_observation_pool WHERE stock_code=? ORDER BY date DESC LIMIT 1",
            (code,)
        ).fetchone()
        if cs_row:
            ci = cs_row[0] or 0
            cl = cs_row[1] or 0
            if ci >= 12: score_o1 = 10
            elif ci >= 8: score_o1 = 5
            if cl >= 15: score_o2 = 10
            elif cl >= 10: score_o2 = 5

    # ── 8.5 MA 排列质量评分(10分)──
    score_ma = 0
    mas = {}
    for p in [5,10,20,30,60]:
        if b2_idx >= p-1:
            mas[p] = sum(klines[j]['close'] for j in range(b2_idx-p+1, b2_idx+1))/p
        else:
            mas[p] = None
    # MA 斜率(5日前比较)
    ma_slopes = {}
    for p in [5,10,20,30,60]:
        if b2_idx >= p+4:
            ma_now = sum(klines[j]['close'] for j in range(b2_idx-p+1, b2_idx+1))/p
            ma_5ago = sum(klines[j]['close'] for j in range(b2_idx-p-4, b2_idx-4+1))/p if b2_idx >= p+4 else ma_now
            ma_slopes[p] = ma_now > ma_5ago
        else:
            ma_slopes[p] = False
    if all(mas[p] is not None for p in [5,10,20,30,60]):
        if mas[5] > mas[10] > mas[20] > mas[30] > mas[60] and all(ma_slopes.values()):
            score_ma = 10
        elif mas[5] > mas[10] > mas[20]:
            score_ma = 5

    # ── 8.6 B2 日信号共振评分(10分)──
    score_sig = 0
    if conn and code:
        row = conn.execute(
            "SELECT signals_json FROM pattern_scan_signals WHERE date=? AND stock_code=?",
            (b2_date, code)
        ).fetchone()
        if row and row[0]:
            try:
                import json
                sigs = json.loads(row[0])
                sources = set(s.get('source','') for s in sigs) if isinstance(sigs, list) else set()
                if 'base_breakout' in sources or 'pocket_pivot' in sources:
                    score_sig = 10
                elif 'cdl' in sources or 'talib' in sources:
                    score_sig = 5
            except:
                pass

    # ── 9. 综合评分 ──
    total = sum(score.values()) + score_i1 + score_i2 + score_o1 + score_o2 + score_ma + score_sig
    if total >= 80: conf = '高'
    elif total >= 55: conf = '中'
    else: conf = '低'

    # ── 10. 组装结果 ──
    b1k = klines[b1_idx]; b2k = klines[b2_idx]
    is_gap = b2_idx > 0 and b2k['open'] > klines[b2_idx-1]['high']
    b2_pos = (b2k['close']-b2k['low'])/(b2k['high']-b2k['low'])*100 if b2k['high']!=b2k['low'] else 100

    # 量比计算
    vol_20_b1 = [klines[j]['volume'] for j in range(max(0,b1_idx-20), b1_idx) if klines[j].get('volume')]
    b1_vr = b1k['volume']/(sum(vol_20_b1)/len(vol_20_b1)) if vol_20_b1 else 0
    vol_20_b2 = [klines[j]['volume'] for j in range(max(0,b2_idx-20), b2_idx) if klines[j].get('volume')]
    b2_vr = b2k['volume']/(sum(vol_20_b2)/len(vol_20_b2)) if vol_20_b2 else 0

    # 均线突破数
    b2_ma = 0
    for p in [5,10,20,30,60]:
        if b2_idx >= p-1:
            ma = sum(klines[j]['close'] for j in range(b2_idx-p+1, b2_idx+1))/p
            if b2k['close'] > ma: b2_ma += 1

    # 横盘期日均成交额
    c_amounts = [klines[j].get('amount', 0) or 0 for j in range(c_start, c_end+1)]
    c_amount_avg = sum(c_amounts)/len(c_amounts) if c_amounts else 0

    result = {
        'h_date': dates[h_idx], 'h_price': round(h_price, 2),
        'l_date': dates[l_idx], 'l_price': round(l_price, 2),
        'c_start': dates[c_start], 'c_end': dates[c_end],
        'b1_date': dates[b1_idx], 'b1_return_pct': round((b1k['close']/klines[b1_idx-1]['close']-1)*100, 2),
        'b1_vol_ratio': round(b1_vr, 2),
        'b2_date': dates[b2_idx], 'b2_return_pct': round((b2k['close']/klines[b2_idx-1]['close']-1)*100, 2),
        'b2_close_pos': round(b2_pos, 1),
        'b2_is_gap': 1 if is_gap else 0,
        'b2_ma_count': b2_ma,
        'decline_pct': round(decline, 1),
        'c_amplitude_pct': round(c_amp, 1),
        'h_rs20': h_rs20, 'h_rs250': h_rs250,
        'c_amount_avg': round(c_amount_avg, 0),
        'score': total, 'confidence': conf,
        'score_h': score['H'], 'score_d': score['D'],
        'score_c': score['C'], 'score_p': score['P'],
        'score_i1': score_i1, 'score_i2': score_i2,
        'score_o1': score_o1, 'score_o2': score_o2,
        'score_ma': score_ma, 'score_sig': score_sig,
        'ind_rs20': ind_rs20, 'ind_rs250': ind_rs250,
    }
    return True, result


def save_signals(conn, scan_date, signals):
    """保存信号,以 b2_date 为主维度"""
    for s in signals:
        conn.execute("""INSERT OR REPLACE INTO mw_signal_daily
            (b2_date,stock_code,stock_name,confidence,score,
             h_date,h_price,l_date,l_price,c_start,c_end,
             b1_date,b1_return_pct,b1_vol_ratio,
             b2_return_pct,b2_close_pos,b2_is_gap,b2_ma_count,
             decline_pct,c_amplitude_pct,
             h_rs20,h_rs250,c_amount_avg,
             score_h,score_d,score_c,score_p,
             score_i1,score_i2,score_o1,score_o2,
             score_ma,score_sig,
             ind_rs20,ind_rs250,scan_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            s['b2_date'],s['code'],s.get('name',''),s['confidence'],s['score'],
            s['h_date'],s['h_price'],s['l_date'],s['l_price'],s['c_start'],s['c_end'],
            s['b1_date'],s['b1_return_pct'],s['b1_vol_ratio'],
            s['b2_return_pct'],s['b2_close_pos'],s['b2_is_gap'],s['b2_ma_count'],
            s['decline_pct'],s['c_amplitude_pct'],
            s.get('h_rs20'),s.get('h_rs250'),s.get('c_amount_avg',0),
            s['score_h'],s['score_d'],s['score_c'],s['score_p'],
            s.get('score_i1',0),s.get('score_i2',0),s.get('score_o1',0),s.get('score_o2',0),
            s.get('score_ma',0),s.get('score_sig',0),
            s.get('ind_rs20'),s.get('ind_rs250'),scan_date
        ))
    conn.commit()


def run_scan(scan_date, fast=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 建表(v1.2: b2_date 为主维度)
    conn.execute("DROP TABLE IF EXISTS mw_signal_daily_old")
    # 先检查旧表是否存在且没有 b2_date 列
    try:
        conn.execute("SELECT b2_date FROM mw_signal_daily LIMIT 0")
    except:
        # 旧表,需要迁移
        conn.execute("ALTER TABLE mw_signal_daily RENAME TO mw_signal_daily_old")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mw_signal_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            b2_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            confidence TEXT,
            score INTEGER,
            h_date TEXT, h_price REAL,
            l_date TEXT, l_price REAL,
            c_start TEXT, c_end TEXT,
            b1_date TEXT, b1_return_pct REAL, b1_vol_ratio REAL,
            b2_return_pct REAL, b2_close_pos REAL, b2_is_gap INTEGER, b2_ma_count INTEGER,
            decline_pct REAL, c_amplitude_pct REAL,
            h_rs20 INTEGER, h_rs250 INTEGER,
            c_amount_avg REAL,
            score_h INTEGER, score_d INTEGER, score_c INTEGER, score_p INTEGER,
            score_i1 INTEGER, score_i2 INTEGER,
            score_o1 INTEGER, score_o2 INTEGER,
            ind_rs20 INTEGER, ind_rs250 INTEGER,
            scan_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, b2_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mw_b2date ON mw_signal_daily(b2_date)")
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
