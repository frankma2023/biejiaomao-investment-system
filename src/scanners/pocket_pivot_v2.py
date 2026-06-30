"""
口袋支点识别引擎 v2.0
集成 MW 信号引擎的缠论 H/L/C 结构
不再从零猜测盘整，而是在已知的 C 区间内查找量价信号
"""
import sqlite3, os, sys, argparse, time, json
from datetime import datetime, timedelta
from collections import defaultdict

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SRC_DIR))
if SRC_DIR not in sys.path: sys.path.insert(0, SRC_DIR)

DB_PATH = os.path.join(PROJECT_ROOT, "data", "lixinger.db")
_chanlun_cache = {}  # 缠论笔缓存

CFG = {
    'min_gain_pct': 3.0,
    'close_position_min': 0.50,
    'vol_down_lookback': 10,
    'extension_from_ma10_pct': 0.20,
    'rs_threshold': 80,
    'min_amount': 50_000_000,
    'max_distribution_days': 6,
    'min_c_days': 3,          # C 区间至少 3 个交易日
    'min_l_distance': 5,     # 距 L 低点至少 10 个交易日（避免 V 形反转）
}

def sma(values, n):
    if len(values) < n: return None
    return sum(values[-n:]) / n

def count_distribution_days(db, scan_date):
    rows = db.execute("""
        SELECT date, close, volume FROM index_daily_kline
        WHERE stock_code='000985' AND date <= ? ORDER BY date DESC LIMIT 25
    """, (scan_date,)).fetchall()
    dist = 0
    for i in range(len(rows)-1):
        if rows[i]['close'] < rows[i+1]['close'] and rows[i]['volume'] > rows[i+1]['volume']:
            dist += 1
    return dist

# ═══ 批量数据加载 ═══

def load_klines_batch(db, codes, scan_date):
    code_set = set(codes)
    start = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=150)).strftime('%Y-%m-%d')
    cache = defaultdict(list)
    for r in db.execute("""
        SELECT stock_code, date, open, high, low, close, volume, amount
        FROM daily_kline WHERE date >= ? AND date <= ? ORDER BY stock_code, date
    """, (start, scan_date)).fetchall():
        if r['stock_code'] in code_set:
            cache[r['stock_code']].append(dict(r))
    return cache

def load_rs_batch(db, codes, scan_date):
    code_set = set(codes)
    cache = {}
    for r in db.execute("""
        SELECT stock_code, rps_20, rps_250 FROM stock_rs_daily
        WHERE date <= ? ORDER BY date DESC
    """, (scan_date,)).fetchall():
        if r['stock_code'] in code_set and r['stock_code'] not in cache:
            cache[r['stock_code']] = (r['rps_20'], r['rps_250'])
    return cache

def load_mw_structures(db, codes):
    """从 mw_signal_daily 批量加载 H/L/C/B1 结构"""
    structures = {}
    placeholders = ','.join('?' * len(codes)) if codes else ''
    if not placeholders: return structures
    
    rows = db.execute(f"""
        SELECT stock_code, h_date, h_price, l_date, l_price, 
               c_start, c_end, b1_date, decline_pct
        FROM mw_signal_daily
        WHERE stock_code IN ({placeholders})
        ORDER BY b2_date DESC
    """, codes).fetchall()
    
    for r in rows:
        code = r['stock_code']
        if code not in structures:  # 取最新的
            structures[code] = {
                'h_date': r['h_date'], 'h_price': r['h_price'],
                'l_date': r['l_date'], 'l_price': r['l_price'],
                'c_start': r['c_start'], 'c_end': r['c_end'],
                'b1_date': r['b1_date'], 'decline_pct': r['decline_pct']
            }
    return structures

def get_hlc_from_chanlun(klines, code, db_conn):
    """没有 MW 信号时，使用 chanlun_structure 共享层获取 H/L/C"""
    from chanlun_structure import get_bi_list, get_bi_peaks, get_bi_troughs

    n = len(klines)
    if n < 60:
        return None

    bi_list = get_bi_list(code)
    if not bi_list:
        return None

    peaks = get_bi_peaks(bi_list)
    troughs = get_bi_troughs(bi_list)
    if len(peaks) < 2 or len(troughs) < 2:
        return None

    dates = [k['date'] for k in klines]

    # 在最近 200 根 K 线范围内找有效的 H-L 对
    lookback = min(200, n - 10)
    # 找 H：lookback 范围内的最高 bi 峰
    cutoff_idx = n - lookback
    cutoff_date = dates[cutoff_idx]
    recent_peaks = [p for p in peaks if p['date'] >= cutoff_date]
    if not recent_peaks:
        recent_peaks = peaks[-3:]

    # 取最近的有效峰作为 H
    h_peak = None
    for p in reversed(recent_peaks):
        if p['date'] in dates:
            h_idx = dates.index(p['date'])
            # 确保 H 后面有足够空间
            if h_idx < n - 10:
                h_peak = p
                break
    if not h_peak:
        h_peak = recent_peaks[-1]
        h_idx = dates.index(h_peak['date']) if h_peak['date'] in dates else min(n - 30, n - 2)

    if h_idx >= n - 2:
        h_idx = n - 2

    h_date = h_peak['date']
    h_price = h_peak['price']

    # 找 L：H 之后到当前的最低收盘价
    l_idx = h_idx + 1
    l_price = klines[l_idx]['close']
    for i in range(h_idx + 1, n):
        if klines[i]['close'] < l_price:
            l_price = klines[i]['close']
            l_idx = i
    l_date = dates[l_idx]

    # 回撤深度
    decline_pct = round((h_price - l_price) / h_price * 100, 2) if h_price else 0

    # C 区间：L 之后到当前
    c_start = l_idx
    c_end = n - 1

    return {
        'h_date': h_date, 'h_price': h_price,
        'l_date': l_date, 'l_price': l_price,
        'c_start': dates[c_start], 'c_end': dates[c_end],
        'b1_date': None,
        'decline_pct': decline_pct
    }


# ═══ 核心判断 ═══

def evaluate_stock(klines, scan_date, structure, rps20, rps250):
    """在已知 H/L/C 结构下判断口袋支点"""
    n = len(klines)
    if n < 65: return None
    
    dates = [k['date'] for k in klines]
    if scan_date not in dates: return None
    idx = dates.index(scan_date)
    
    today = klines[idx]
    o, h, l, c, v = today['open'], today['high'], today['low'], today['close'], today['volume']
    if c <= 0 or v <= 0: return None
    
    s = structure
    l_date, c_start_date, b1_date = s.get('l_date'), s.get('c_start'), s.get('b1_date')
    if not l_date: return None
    
    # 基础趋势
    closes = [k['close'] for k in klines[:idx+1]]
    sma10 = sma(closes, 10); sma60 = sma(closes, 60)
    if sma10 is None or sma60 is None: return None
    if not (c > sma60 and c > sma10): return {'skip': 'trend'}
    
    sma60_10d = sma(closes[:-10], 60) if len(closes) > 70 else sma60
    if sma60_10d and sma60 <= sma60_10d: return {'skip': 'trend'}
    
    # 不追延伸
    pct_ma10 = (c - sma10) / sma10 * 100
    if pct_ma10 > CFG['extension_from_ma10_pct'] * 100: return {'skip': 'extended'}
    
    # 距 L 低点的交易日数
    l_idx = dates.index(l_date) if l_date in dates else -1
    days_from_l = idx - l_idx if l_idx >= 0 else 999
    if days_from_l < 3: return {'skip': 'too_early'}  # 纯 V 形反转
    
    # ═══ 类型判断 ═══
    pivot_type = None
    b1_overlap = False
    
    # 判断是否在 C 区间内（基部口袋支点）
    in_c_zone = False
    c_end_date = s.get('c_end', '')
    if c_start_date and l_idx >= 0:
        c_end_idx = dates.index(c_end_date) if c_end_date in dates else idx
        in_c_zone = (l_idx <= idx <= min(c_end_idx + 5, n-1))  # C 结束后 5 天内也算
    
    # ═══ 基部盘整质量检查（仅对 C 区间内的信号，排除最近 3 天突破段）═══
    if in_c_zone and days_from_l >= 6:
        check_end = idx - 3
        check_days = check_end - l_idx
        if check_days >= 6:
            mid = check_days // 2
            if mid >= 3:
                v1 = [klines[l_idx + i]['volume'] for i in range(mid)]
                v2 = [klines[l_idx + mid + i]['volume'] for i in range(mid) if l_idx + mid + i < check_end]
                if v1 and v2 and sum(v2)/len(v2) > sum(v1)/len(v1) * 1.15:
                    in_c_zone = False
            if in_c_zone and mid >= 3:
                a1 = [(klines[l_idx+i]['high']-klines[l_idx+i]['low'])/klines[l_idx+i]['close'] for i in range(mid)]
                a2 = [(klines[l_idx+mid+i]['high']-klines[l_idx+mid+i]['low'])/klines[l_idx+mid+i]['close'] for i in range(mid) if l_idx+mid+i<check_end]
                if a1 and a2 and sum(a2)/len(a2) > sum(a1)/len(a1) * 1.15:
                    in_c_zone = False
        # 盘整期内至少 3 天收盘在 MA60 上方
        if in_c_zone:
            above = 0
            for i in range(l_idx, min(idx, l_idx + check_days)):
                ci = [kl['close'] for kl in klines[:i+1]]
                s60 = sma(ci, 60)
                if s60 and klines[i]['close'] > s60: above += 1
            if above < 3:
                in_c_zone = False
    
    # 判断是否在 P 区间内（延续型：B1 后回踩均线再放量）
    in_p_zone = False
    days_after_b1 = 0
    if b1_date and b1_date in dates:
        b1_idx = dates.index(b1_date)
        days_after_b1 = idx - b1_idx
        # P 区间：B1 后 3~15 天，且不能太靠近 L（确保不是在 C 末尾）
        in_p_zone = (3 <= days_after_b1 <= 15) and days_from_l > 15
    
    # 判断是否在 10 日线反弹位置
    is_10ma_bounce = (l <= sma10 * 1.02 and c > klines[idx-1]['close'] if idx > 0 else False)
    
    # ═══ 量价规则（所有类型共用）═══
    
    # 涨幅 ≥ 3%
    gain_pct = (c - klines[idx-1]['close']) / klines[idx-1]['close'] * 100 if idx > 0 else 0
    if gain_pct < CFG['min_gain_pct']:
        return {'skip': 'gain'} if (in_c_zone or in_p_zone) else None
    
    # 日内位置
    hl_range = h - l
    if hl_range <= 0: return None
    close_pos = (c - l) / hl_range
    if close_pos < CFG['close_position_min']:
        return {'skip': 'close_pos'} if (in_c_zone or in_p_zone) else None
    if c <= o:
        return {'skip': 'not_green'} if (in_c_zone or in_p_zone) else None
    
    # 成交量：今日 > 前10天所有下跌日最大量
    down_vols = []
    for i in range(max(0, idx-10), idx):
        if klines[i]['close'] < klines[i-1]['close']:
            down_vols.append(klines[i]['volume'])
    if down_vols and v <= max(down_vols):
        return {'skip': 'volume'} if (in_c_zone or in_p_zone) else None
    
    # 突破盘整区：今日最高 ≥ 前10天最高
    prev_highs = [klines[i]['high'] for i in range(max(0, idx-10), idx)]
    if prev_highs and h < max(prev_highs):
        return {'skip': 'no_breakout'} if (in_c_zone or in_p_zone) else None
    
    # RS
    if rps20 is None or rps250 is None:
        return {'skip': 'rs'} if (in_c_zone or in_p_zone) else None
    if not (rps20 >= CFG['rs_threshold'] or rps250 >= CFG['rs_threshold']):
        return {'skip': 'rs'} if (in_c_zone or in_p_zone) else None
    
    # 抛盘日过滤已关闭（人工判断市场环境，此处只做形态识别）
    # if market_too_bearish: return {'skip': 'bearish'}
    if today.get('amount', 0) < CFG['min_amount']: return {'skip': 'amount'}
    
    # ═══ 确定类型 ═══
    if in_c_zone and c > sma10:
        pivot_type = 'base'
        if b1_date and scan_date == b1_date:
            b1_overlap = True
    elif in_p_zone and is_10ma_bounce and days_after_b1 >= 3:
        pivot_type = 'continuation'
    elif is_10ma_bounce and c > sma60 and sma10 > sma60:
        pivot_type = '10ma_bounce'
    else:
        return None  # 量价满足但不在任何已知区间 → 不产生信号
    
    vol_ratio = round(v / max(down_vols), 2) if down_vols else 1.0
    
    return {
        'date': scan_date, 'is_pivot': True,
        'pivot_type': pivot_type, 'b1_overlap': b1_overlap,
        'h_date': s.get('h_date'), 'l_date': s.get('l_date'),
        'c_days': days_from_l,
        'gain_pct': round(gain_pct, 2),
        'vol_ratio': vol_ratio,
        'close_position': round(close_pos, 2),
        'rps_20': rps20, 'rps_250': rps250,
        'sma10': round(sma10, 2), 'sma60': round(sma60, 2),
        'pct_from_ma10': round(pct_ma10, 2),
        'base_depth': s.get('decline_pct'),
        'close': c, 'volume': v,
    }


# ═══ 主扫描 ═══

def scan_date(scan_date):
    db = sqlite3.connect(DB_PATH); db.row_factory = sqlite3.Row
    t0 = time.time()
    
    stocks = db.execute("""
        SELECT DISTINCT k.stock_code, b.name
        FROM daily_kline k JOIN stock_basic b ON k.stock_code=b.stock_code
        WHERE b.listing_status='normally_listed' AND b.name NOT LIKE '%ST%'
        AND k.date >= date(?, '-20 days')
        GROUP BY k.stock_code HAVING AVG(k.amount) >= ?
    """, (scan_date, CFG['min_amount'])).fetchall()
    
    stock_list = [(r['stock_code'], r['name']) for r in stocks]
    codes = [s[0] for s in stock_list]
    print(f"[1/3] {len(codes)} stocks")
    
    # ── K线 + RS：回填已预加载到 mw 模块缓存，命中则跳过 SQL ──
    kline_min = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=150)).strftime('%Y-%m-%d')
    kline_cache = {}
    rs_cache = {}
    try:
        import scanners.mw_signal as mw
        if mw._kline_cache and len(mw._kline_cache) > 100:
            # 从回填缓存取 K 线，截取最近 150 天
            code_set = set(codes)
            for code in code_set:
                rows = mw._kline_cache.get(code, [])
                if rows:
                    sliced = [r for r in rows if r['date'] >= kline_min]
                    if sliced:
                        kline_cache[code] = sliced
            if mw._rs_cache and len(mw._rs_cache) > 100:
                rs_cache = {code: mw._rs_cache[code] for code in code_set if code in mw._rs_cache}
            if kline_cache and rs_cache:
                print(f"[2/3] K-line + RS from cache ({len(kline_cache)}/{len(rs_cache)} stocks, {time.time()-t0:.1f}s)")
    except Exception:
        pass
    
    if not kline_cache:
        print(f"[2/3] Loading K-line + RS...")
        kline_cache = load_klines_batch(db, codes, scan_date)
        rs_cache = load_rs_batch(db, codes, scan_date)
    
    print(f"[3/3] Scanning...")
    signals, skipped = [], defaultdict(int)
    chanlun_ok = 0

    for i, (code, name) in enumerate(stock_list):
        if i % 1000 == 0 and i > 0: print(f"  ... {i}/{len(stock_list)}")
        
        klines = kline_cache.get(code, [])
        if len(klines) < 65: continue
        
        # H/L/C 结构统一走缠论，与 MW 信号解耦
        structure = get_hlc_from_chanlun(klines, code, db)
        if not structure: continue
        chanlun_ok += 1
        
        rps20, rps250 = rs_cache.get(code, (None, None))
        result = evaluate_stock(klines, scan_date, structure, rps20, rps250)
        if result:
            result['stock_code'] = code
            result['stock_name'] = name
            if result.get('is_pivot'):
                signals.append(result)
            elif result.get('skip'):
                skipped[result['skip']] += 1
    
    db.close()
    elapsed = time.time() - t0
    print(f"\n✓ PP V2: {len(signals)} | {elapsed:.1f}s")
    print(f"  漏斗: {dict(skipped)}")
    print(f"  缠论结构: {chanlun_ok}/{len(stock_list)} 只有效")
    
    return signals


def save_to_db(signals):
    db = sqlite3.connect(DB_PATH, timeout=120)
    db.execute("PRAGMA busy_timeout=120000")
    db.execute("PRAGMA wal_autocheckpoint=10000")  # 减少多进程 checkpoint 冲突
    cur = db.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS pocket_pivot_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, stock_code TEXT NOT NULL,
        stock_name TEXT, engine_version TEXT DEFAULT 'V1', pivot_type TEXT, b1_overlap INTEGER,
        h_date TEXT, l_date TEXT, c_days INTEGER,
        gain_pct REAL, vol_ratio REAL, close_position REAL,
        rps_20 INTEGER, rps_250 INTEGER, sma10 REAL, sma60 REAL,
        pct_from_ma10 REAL, base_depth REAL,
        close REAL, volume INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(date, stock_code, engine_version))""")
    for s in signals:
        for attempt in range(5):
            try:
                cur.execute("""INSERT OR REPLACE INTO pocket_pivot_daily
                    (date,stock_code,stock_name,engine_version,pivot_type,b1_overlap,h_date,l_date,c_days,
                     gain_pct,vol_ratio,close_position,rps_20,rps_250,sma10,sma60,
                     pct_from_ma10,base_depth,close,volume)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (s['date'],s['stock_code'],s.get('stock_name',''),'V2',s['pivot_type'],
                     int(s.get('b1_overlap',False)),s.get('h_date'),s.get('l_date'),
                     s.get('c_days',0),s['gain_pct'],s['vol_ratio'],s['close_position'],
                     s['rps_20'],s['rps_250'],s['sma10'],s['sma60'],
                     s['pct_from_ma10'],s.get('base_depth'),s['close'],s['volume']))
                break
            except sqlite3.OperationalError:
                if attempt < 4:
                    time.sleep(2)
                else:
                    raise
    db.commit(); db.close()
    print(f"PP V2 已保存 {len(signals)} 条到 DB")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    parser.add_argument('--save', action='store_true')
    parser.add_argument('--backfill', action='store_true', help='回填模式：放宽抛盘日限制')
    args = parser.parse_args()
    
    if args.backfill:
        CFG['max_distribution_days'] = 999
    signals = scan_date(args.date)
    
    if signals:
        print(f"\n{'='*80}")
        print(f"{'代码':<8}{'名称':<10}{'价格':>8}{'涨幅':>7}{'量比':>5}{'RS20':>6}{'RS250':>6}{'MA10':>7}{'盘整天':>6}{'类型':>14}{'B1重合':>6}")
        print("-"*80)
        for s in sorted(signals, key=lambda x: -x['vol_ratio']):
            b1_tag = '★B1' if s.get('b1_overlap') else ''
            print(f"{s['stock_code']:<8}{s.get('stock_name',''):<10}{s['close']:>8.2f}{s['gain_pct']:>+6.1f}%{s['vol_ratio']:>4.1f}x{s['rps_20']:>6}{s['rps_250']:>6}{s['pct_from_ma10']:>+6.1f}%{s.get('c_days',0):>6}{s['pivot_type']:>14}{b1_tag:>6}")
    
    if hasattr(args, 'save') and args.save and signals:
        save_to_db(signals)
