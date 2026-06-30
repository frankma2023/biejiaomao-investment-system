import sys, os, time, argparse, sqlite3, json
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))

import scanners.mw_signal as mw_sig_mod
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))

DB = os.path.join(PROJECT, 'data', 'lixinger.db')


# ══════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════

def get_trading_dates(start, end):
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date>=? AND date<=? ORDER BY date",
        (start, end)
    ).fetchall()
    db.close()
    return [r[0] for r in rows]


def get_existing_dates():
    """获取所有引擎已有扫描的日期（任一引擎有信号则跳过）"""
    db = sqlite3.connect(DB)
    existing = set()
    # MW 信号（含哨兵）
    for r in db.execute("SELECT DISTINCT scan_date FROM mw_signal_daily WHERE scan_date IS NOT NULL"):
        existing.add(r[0])
    # 口袋支点 V1
    for r in db.execute("SELECT DISTINCT date FROM pocket_pivot_daily WHERE engine_version='V1'"):
        existing.add(r[0])
    # 口袋支点 V2
    for r in db.execute("SELECT DISTINCT date FROM pocket_pivot_daily WHERE engine_version='V2'"):
        existing.add(r[0])
    # 基部突破 V2
    try:
        for r in db.execute("SELECT DISTINCT date FROM market_breakout_v2_daily"):
            existing.add(r[0])
    except:
        pass
    # 卖出信号
    for eng in ['climax_top', 'railroad_tracks', 'top_pattern', 'breakout_failure']:
        for r in db.execute(f"SELECT DISTINCT date FROM pattern_scan_signals WHERE signals_json LIKE '%\"{eng}\"%'"):
            existing.add(r[0])
    db.close()
    return existing


def quarter_to_range(q):
    import calendar
    year = int(q[:4]); qnum = int(q[-1])
    sm = (qnum - 1) * 3 + 1; em = sm + 2
    ld = calendar.monthrange(year, em)[1]
    return f"{year}-{sm:02d}-01", f"{year}-{em:02d}-{ld}"


# ══════════════════════════════════════════════════════════
# 缓存预加载（一次性，所有引擎共享）
# ══════════════════════════════════════════════════════════

def set_all_caches(conn, scan_date):
    """批量预加载所有引擎需要的缓存数据"""
    import scanners.pocket_pivot_v2 as ppv2
    t0 = time.time()

    # 1. 缠论笔缓存 → mw + ppv2 + base_breakout 共用
    mw_sig_mod._chanlun_cache = {}
    for row in conn.execute(
        "SELECT stock_code, bi_json FROM chanlun_bi_json WHERE scan_date=?",
        (scan_date,)
    ).fetchall():
        try:
            mw_sig_mod._chanlun_cache[(row['stock_code'], scan_date)] = __import__('orjson').loads(row['bi_json'])
        except:
            pass
    print(f'  缠论笔缓存: {len(mw_sig_mod._chanlun_cache)} 只, {time.time()-t0:.1f}s'); t0 = time.time()

    # 2. 个股 RS 缓存 → mw + ppv2 + base_breakout 共用
    mw_sig_mod._rs_cache = {}
    ppv2._rs_cache = mw_sig_mod._rs_cache  # 共享 MW 的 RS 缓存
    rows = conn.execute(
        "SELECT stock_code, rps_20, rps_250, date FROM stock_rs_daily WHERE date<=? ORDER BY date DESC",
        (scan_date,)
    ).fetchall()
    for r in rows:
        if r['stock_code'] not in mw_sig_mod._rs_cache:
            mw_sig_mod._rs_cache[r['stock_code']] = (r['rps_20'], r['rps_250'])
    print(f'  个股RS缓存: {len(mw_sig_mod._rs_cache)} 只, {time.time()-t0:.1f}s'); t0 = time.time()

    # 3. 行业成分缓存
    mw_sig_mod._idx_comp_cache = defaultdict(list)
    rows = conn.execute("SELECT stock_code, index_code FROM index_constituents").fetchall()
    for r in rows:
        mw_sig_mod._idx_comp_cache[r['stock_code']].append(r['index_code'])
    print(f'  行业成分缓存: {len(rows)} 条, {time.time()-t0:.1f}s'); t0 = time.time()

    # 4. 指数 RS 缓存
    mw_sig_mod._idx_rs_cache = {}
    rows = conn.execute(
        "SELECT stock_code, rs_20, rs_250 FROM index_rs_daily WHERE date<=? ORDER BY date DESC",
        (scan_date,)
    ).fetchall()
    for r in rows:
        if r['stock_code'] not in mw_sig_mod._idx_rs_cache:
            mw_sig_mod._idx_rs_cache[r['stock_code']] = (r['rs_20'], r['rs_250'])
    print(f'  指数RS缓存: {len(mw_sig_mod._idx_rs_cache)} 个指数, {time.time()-t0:.1f}s'); t0 = time.time()

    # 5. K线缓存 → 所有引擎共用
    min_date = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=400)).strftime('%Y-%m-%d')
    mw_sig_mod._kline_cache = defaultdict(list)
    rows = conn.execute(
        "SELECT stock_code, date, open, high, low, close, volume, amount FROM daily_kline WHERE date>=? AND date<=? ORDER BY stock_code, date",
        (min_date, scan_date)
    ).fetchall()
    for r in rows:
        mw_sig_mod._kline_cache[r['stock_code']].append(dict(r))
    print(f'  K线缓存: {len(rows)} 行 ({len(mw_sig_mod._kline_cache)} 只), {time.time()-t0:.1f}s'); t0 = time.time()

    # 6. 共振信号缓存
    mw_sig_mod._reso_cache = {}
    rows = conn.execute(
        "SELECT stock_code, signals_json FROM pattern_scan_signals WHERE date=?",
        (scan_date,)
    ).fetchall()
    for r in rows:
        mw_sig_mod._reso_cache[(r['stock_code'], scan_date)] = r['signals_json']
    print(f'  共振信号缓存: {len(mw_sig_mod._reso_cache)} 只, {time.time()-t0:.1f}s')


def get_candidate_stocks(conn, scan_date):
    """获取当天可扫描的股票列表"""
    rows = conn.execute(
        "SELECT DISTINCT k.stock_code, b.name FROM daily_kline k JOIN stock_basic b ON k.stock_code=b.stock_code WHERE k.date=?",
        (scan_date,)
    ).fetchall()
    return [(r['stock_code'], r['name']) for r in rows]


# ══════════════════════════════════════════════════════════
# 各引擎扫描
# ══════════════════════════════════════════════════════════

def scan_mw(scan_date):
    """MW B1/B2 信号"""
    from scanners.mw_signal import run_scan
    run_scan(scan_date, silent=True)
    db = sqlite3.connect(DB, timeout=5)
    r = db.execute("SELECT COUNT(*), COUNT(CASE WHEN b2_date IS NOT NULL THEN 1 END), COUNT(CASE WHEN b2_date IS NULL THEN 1 END) FROM mw_signal_daily WHERE b1_date=?", (scan_date,)).fetchone()
    db.close()
    return r[0], r[1], r[2]  # total, b2, pure_b1


def scan_pp_v1(conn, stocks):
    """口袋支点 V1"""
    from scanners.pocket_pivot import detect_for_stock
    signals = []
    for code, name in stocks:
        klines = get_cached_klines(code)
        if len(klines) < 120: continue
        raw = detect_for_stock(code, scan_date)
        if raw:
            for sig in raw:
                if isinstance(sig, dict):
                    sig['stock_code'] = code; sig['stock_name'] = name; sig['engine_version'] = 'V1'
                    signals.append(sig)
    # 保存
    saved = 0
    if signals:
        cur = conn.cursor()
        for s in signals:
            cur.execute("""INSERT OR REPLACE INTO pocket_pivot_daily (date,stock_code,stock_name,engine_version,pivot_type,b1_overlap,gain_pct,vol_ratio,close_position,rps_20,rps_250,sma10,sma60,pct_from_ma10,close,volume) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s.get('signal_date', scan_date) or scan_date, s['stock_code'], s.get('stock_name',''), 'V1', s.get('pivot_type','base'), int(s.get('b1_overlap',False)), s.get('gain_pct'), s.get('vol_ratio'), s.get('close_position'), s.get('rps_20'), s.get('rps_250'), s.get('sma10'), s.get('sma60'), s.get('pct_from_ma10'), s.get('close'), s.get('volume')))
        conn.commit()
        saved = len(signals)
    return saved


def scan_pp_v2(conn, stocks, scan_date):
    """口袋支点 V2"""
    from scanners.pocket_pivot_v2 import scan_date as ppv2_scan, save_to_db
    # ppv2 scan_date 是全量扫描函数
    signals = ppv2_scan(scan_date)
    if signals:
        for s in signals:
            # save_to_db 内部写 engine_version='V2'
            pass
        save_to_db(signals)
        return len(signals)
    return 0


def scan_base_breakout_v2(conn, stocks, scan_date):
    """基部突破 V2"""
    from scanners.base_breakout_v2 import detect
    signals = []
    for code, name in stocks:
        klines = get_cached_klines(code)
        if len(klines) < 120: continue
        raw = detect(klines, {'stock_code': code})
        if raw:
            for sig in raw:
                if isinstance(sig, dict):
                    sig['stock_code'] = code; sig['stock_name'] = name; sig['engine_version'] = 'V2'
                    signals.append(sig)
    if signals:
        cur = conn.cursor()
        for s in signals:
            cur.execute("""INSERT OR REPLACE INTO market_breakout_v2_daily (date,stock_code,stock_name,engine_version,breakout_type,base_type,h_date,h_price,l_date,l_price,decline_pct,c_start,c_end,c_days,gain_pct,vol_ratio,close_position,rps_20,rps_250,ind_rs250,ind_code,ind_name,sma50,sma150,sma200,close,volume) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s.get('date', scan_date), s['stock_code'], s.get('stock_name',''), 'V2', s.get('breakout_type'), s.get('base_type'), s.get('h_date'), s.get('h_price'), s.get('l_date'), s.get('l_price'), s.get('decline_pct'), s.get('c_start'), s.get('c_end'), s.get('c_days'), s.get('gain_pct'), s.get('vol_ratio'), s.get('close_position'), s.get('rps_20'), s.get('rps_250'), s.get('ind_rs250'), s.get('ind_code'), s.get('ind_name'), s.get('sma50'), s.get('sma150'), s.get('sma200'), s.get('close'), s.get('volume')))
        conn.commit()
        return len(signals)
    return 0


def scan_sell_signals(conn, stocks, scan_date):
    """卖出信号（高潮见顶+铁轨线+顶部形态+突破失败）"""
    from scanners.climax_top import detect as c_detect, load_params as c_params
    from scanners.railroad_tracks import detect as r_detect, load_params as r_params
    from scanners.top_pattern import detect as t_detect, load_params as t_params

    engines = {
        'climax_top': (c_detect, c_params()),
        'railroad_tracks': (r_detect, r_params()),
        'top_pattern': (t_detect, t_params()),
    }

    saved = 0
    for code, name in stocks:
        klines = get_cached_klines(code)
        if len(klines) < 120: continue

        for eng_name, (detect_fn, params) in engines.items():
            try:
                if eng_name == 'climax_top':
                    sigs = detect_fn(daily=klines, params=params, stock_code=code)
                elif eng_name == 'railroad_tracks':
                    sigs = detect_fn(daily=klines, params=params, stock_code=code)
                else:
                    sigs = detect_fn(daily=klines, params=params, stock_code=code)
                for s in (sigs or []):
                    if isinstance(s, dict):
                        s['source'] = eng_name
                        # 读取已有信号
                        existing = conn.execute("SELECT signals_json FROM pattern_scan_signals WHERE stock_code=? AND date=?", (code, scan_date)).fetchone()
                        existing_sigs = json.loads(existing[0]) if existing and existing[0] else []
                        seen = {s2.get('source','') for s2 in existing_sigs}
                        if eng_name not in seen:
                            existing_sigs.append(s)
                            conn.execute("INSERT OR REPLACE INTO pattern_scan_signals (stock_code,date,signals_json) VALUES(?,?,?)", (code, scan_date, json.dumps(existing_sigs, ensure_ascii=False)))
                            saved += 1
            except:
                pass
    conn.commit()
    return saved


def get_cached_klines(code):
    """从缓存取 K 线"""
    return mw_sig_mod._kline_cache.get(code, [])


def init_breakout_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS market_breakout_v2_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT,
        engine_version TEXT NOT NULL DEFAULT 'V2', breakout_type TEXT, base_type TEXT,
        h_date TEXT, h_price REAL, l_date TEXT, l_price REAL,
        decline_pct REAL, c_start TEXT, c_end TEXT, c_days INTEGER,
        gain_pct REAL, vol_ratio REAL, close_position REAL,
        rps_20 INTEGER, rps_250 INTEGER, ind_rs250 INTEGER, ind_code TEXT, ind_name TEXT,
        sma50 REAL, sma150 REAL, sma200 REAL, close REAL, volume INTEGER,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(date, stock_code, engine_version))""")
    conn.commit()


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='全信号批量回填（统一缓存）')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--start', help='起始日期')
    group.add_argument('--quarter', help='按季度')
    parser.add_argument('--end', help='结束日期')
    parser.add_argument('--incremental', action='store_true')
    parser.add_argument('--skip-mw', action='store_true', help='跳过MW信号')
    parser.add_argument('--skip-buy', action='store_true', help='跳过买入信号(PP+BO)')
    parser.add_argument('--skip-sell', action='store_true', help='跳过卖出信号')
    args = parser.parse_args()

    if args.quarter:
        start_date, end_date = quarter_to_range(args.quarter)
    else:
        start_date, end_date = args.start, args.end

    all_dates = get_trading_dates(start_date, end_date)
    if not all_dates:
        print(f"区间无交易日"); sys.exit(0)

    if args.incremental:
        existing = get_existing_dates()
        dates = [d for d in all_dates if d not in existing]
        print(f"增量模式: 已完成 {len(all_dates)-len(dates)} 天, 剩余 {len(dates)} 天")
    else:
        dates = all_dates
        print(f"全量模式: {len(dates)} 天")

    if not dates:
        print("无需处理"); sys.exit(0)

    init_breakout_table(sqlite3.connect(DB, timeout=30))

    t_start = time.time()
    completed = 0
    errors = []

    for i, scan_date in enumerate(dates):
        t0 = time.time()
        print(f"\n[{i+1}/{len(dates)}] {scan_date}")

        try:
            conn = sqlite3.connect(DB, timeout=30)
            conn.row_factory = sqlite3.Row

            print('  预加载缓存...', flush=True)
            set_all_caches(conn, scan_date)
            stocks = get_candidate_stocks(conn, scan_date)

            # MW
            if not args.skip_mw:
                b1_total, b1_b2, b1_pure = scan_mw(scan_date)
            else:
                b1_total = b1_b2 = b1_pure = 0

            # 买入信号
            ppv1 = ppv2 = bo = 0
            if not args.skip_buy:
                t_pp = time.time()
                ppv2 = scan_pp_v2(conn, stocks, scan_date)
                ppv1 = scan_pp_v1(conn, stocks)
                bo = scan_base_breakout_v2(conn, stocks, scan_date)
            else:
                ppv1 = ppv2 = bo = 0

            # 卖出信号
            sell = 0
            if not args.skip_sell:
                sell = scan_sell_signals(conn, stocks, scan_date)

            conn.close()

            elapsed = time.time() - t0
            completed += 1
            avg = (time.time() - t_start) / completed
            eta = avg * (len(dates) - i - 1)
            print(f'  MW: B1={b1_total} B2={b1_b2} 纯B1={b1_pure}  |  '
                  f'PP: V1={ppv1} V2={ppv2}  |  BO_V2={bo}  |  Sell={sell}  '
                  f'({elapsed:.0f}s) ETA {eta/3600:.1f}h')

        except Exception as e:
            elapsed = time.time() - t0
            errors.append((scan_date, str(e)[:200]))
            print(f'  ✗ ({elapsed:.0f}s) {e}')
            import traceback; traceback.print_exc()

    total_elapsed = time.time() - t_start
    print(f"\n=== 完成 ===")
    print(f"成功: {completed}/{len(dates)} 天, 失败: {len(errors)} 天")
    print(f"总耗时: {total_elapsed/3600:.1f}h")
    if errors:
        for d, e in errors[:10]:
            print(f"  {d}: {e}")
