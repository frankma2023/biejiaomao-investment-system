"""
全信号统一回填引擎 v2 · 并行预加载版
─────────────────────────────────────
方案1：ThreadPoolExecutor 并行加载 chanlun bi_json
orjson.loads 释放 GIL，4 线程可真正并行解析 JSON

预期：缠论笔缓存从 300s 降到 ~1s（实测）

用法：
    python scripts/backfill_all_signals_v2.py --start 2026-06-01 --end 2026-06-21 --incremental
    python scripts/backfill_all_signals_v2.py --quarter 2016Q1 --incremental
"""
import sys, os, time, argparse, sqlite3, json, subprocess
from datetime import datetime, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))

DB = os.path.join(PROJECT, 'data', 'lixinger.db')
ERROR_LOG = os.path.join(PROJECT, 'logs', 'backfill_errors.log')
THREADS = 4  # 并行线程数（orjson 释放 GIL，线程有效）

# ── 全局拦截：所有 sqlite3.connect 默认 timeout=60s ──
# 引擎内部自己开连接时也生效，多实例并行时排队等锁而非直接报 database is locked
_original_connect = sqlite3.connect
def _connect(*args, **kwargs):
    kwargs.setdefault('timeout', 60)
    return _original_connect(*args, **kwargs)
sqlite3.connect = _connect


# ═══════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════

def get_trading_dates(start, end):
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date>=? AND date<=? ORDER BY date",
        (start, end)
    ).fetchall()
    db.close()
    return [r[0] for r in rows]


def get_existing_dates():
    """获取 v2 已完成扫描的日期（查 backfill_v2_progress 表）。
    
    v2 每天跑完全部引擎后写一行标记，增量模式只认这个表。
    不再用各引擎表的 UNION 推测（MW 单独跑过会导致误判已完成）。
    """
    db = sqlite3.connect(DB)
    existing = set()
    try:
        for r in db.execute("SELECT date FROM backfill_v2_progress"):
            existing.add(r[0])
    except sqlite3.OperationalError:
        pass  # 表还不存在，没有已完成日期
    db.close()
    return existing


def get_engine_status(scan_date):
    """检查每个引擎在指定日期是否已有数据（增量模式下按引擎粒度跳过）。"""
    db = sqlite3.connect(DB)
    status = {'mw': False, 'pp_v1': False, 'pp_v2': False, 'bo_v2': False, 'sell': False}
    r = db.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE scan_date=?", (scan_date,)).fetchone()
    status['mw'] = r[0] > 0
    r = db.execute("SELECT COUNT(*) FROM pocket_pivot_daily WHERE date=? AND engine_version='V1'", (scan_date,)).fetchone()
    status['pp_v1'] = r[0] > 0
    r = db.execute("SELECT COUNT(*) FROM pocket_pivot_daily WHERE date=? AND engine_version='V2'", (scan_date,)).fetchone()
    status['pp_v2'] = r[0] > 0
    try:
        r = db.execute("SELECT COUNT(*) FROM market_breakout_v2_daily WHERE date=?", (scan_date,)).fetchone()
        status['bo_v2'] = r[0] > 0
    except:
        pass
    r = db.execute("SELECT COUNT(*) FROM pattern_scan_signals WHERE date=?", (scan_date,)).fetchone()
    status['sell'] = r[0] > 0
    db.close()
    return status


def quarter_to_range(q):
    import calendar
    year = int(q[:4]); qnum = int(q[-1])
    sm = (qnum - 1) * 3 + 1; em = sm + 2
    ld = calendar.monthrange(year, em)[1]
    return f"{year}-{sm:02d}-01", f"{year}-{em:02d}-{ld}"


# ═══════════════════════════════════════════
# 并行预加载 chanlun bi_json
# ═══════════════════════════════════════════

def _load_chanlun_chunk(chunk_codes, scan_date):
    """
    子线程：打开独立 DB 连接，加载分配给它的股票缠论笔。
    orjson.loads 释放 GIL，多线程可真正并行。
    返回 {(code, scan_date): bi_list} 子字典。
    """
    import orjson
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    result = {}
    placeholders = ','.join('?' * len(chunk_codes))
    rows = conn.execute(
        f"SELECT stock_code, bi_json FROM chanlun_bi_json WHERE scan_date=? AND stock_code IN ({placeholders})",
        [scan_date] + chunk_codes
    ).fetchall()
    for row in rows:
        try:
            result[(row['stock_code'], scan_date)] = orjson.loads(row['bi_json'])
        except:
            pass
    conn.close()
    return result


# ═══════════════════════════════════════════
# 缓存预加载
# ═══════════════════════════════════════════

def set_all_caches(conn, scan_date, stocks):
    import scanners.mw_signal as mw
    t0 = time.time()

    # ── 1. 缠论笔缓存（并行加载）──
    codes = [s[0] for s in stocks]
    chunk_size = max(1, len(codes) // THREADS)
    chunks = [codes[i:i+chunk_size] for i in range(0, len(codes), chunk_size)]

    mw._chanlun_cache = {}
    print(f'  缠论笔缓存 ({len(codes)} 只, {len(chunks)} 线程并行)...', end=' ', flush=True)
    t_chan = time.time()
    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        futures = [pool.submit(_load_chanlun_chunk, chunk, scan_date) for chunk in chunks]
        for fut in as_completed(futures):
            mw._chanlun_cache.update(fut.result())
    t_chan_elapsed = time.time() - t_chan
    print(f'{len(mw._chanlun_cache)} 只, {t_chan_elapsed:.1f}s'); t0 = time.time()

    # ── 2. 个股 RS 缓存 ──
    mw._rs_cache = {}
    rows = conn.execute(
        "SELECT stock_code, rps_20, rps_250 FROM stock_rs_daily WHERE date=?",
        (scan_date,)
    ).fetchall()
    for r in rows:
        mw._rs_cache[r['stock_code']] = (r['rps_20'], r['rps_250'])
    print(f'  个股RS缓存: {len(mw._rs_cache)} 只, {time.time()-t0:.1f}s'); t0 = time.time()

    # ── 3. 行业成分缓存 ──
    mw._idx_comp_cache = defaultdict(list)
    rows = conn.execute("SELECT stock_code, index_code FROM index_constituents").fetchall()
    for r in rows:
        mw._idx_comp_cache[r['stock_code']].append(r['index_code'])
    print(f'  行业成分缓存: {len(rows)} 条, {time.time()-t0:.1f}s'); t0 = time.time()

    # ── 4. 指数 RS 缓存 ──
    mw._idx_rs_cache = {}
    rows = conn.execute(
        "SELECT stock_code, rs_20, rs_250 FROM index_rs_daily WHERE date=?",
        (scan_date,)
    ).fetchall()
    for r in rows:
        mw._idx_rs_cache[r['stock_code']] = (r['rs_20'], r['rs_250'])
    print(f'  指数RS缓存: {len(mw._idx_rs_cache)} 个指数, {time.time()-t0:.1f}s'); t0 = time.time()

    # ── 5. K线缓存 ──
    min_date = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=400)).strftime('%Y-%m-%d')
    mw._kline_cache = defaultdict(list)
    rows = conn.execute(
        "SELECT stock_code, date, open, high, low, close, volume, amount FROM daily_kline WHERE date>=? AND date<=? ORDER BY stock_code, date",
        (min_date, scan_date)
    ).fetchall()
    for r in rows:
        mw._kline_cache[r['stock_code']].append(dict(r))
    print(f'  K线缓存: {len(rows)} 行 ({len(mw._kline_cache)} 只), {time.time()-t0:.1f}s'); t0 = time.time()

    # ── 6. 共振信号缓存 ──
    mw._reso_cache = {}
    rows = conn.execute(
        "SELECT stock_code, signals_json FROM pattern_scan_signals WHERE date=?",
        (scan_date,)
    ).fetchall()
    for r in rows:
        mw._reso_cache[(r['stock_code'], scan_date)] = r['signals_json']
    print(f'  共振信号缓存: {len(mw._reso_cache)} 只, {time.time()-t0:.1f}s'); t0 = time.time()

    # ── 7. 卖出信号已有数据缓存（消除 scan_sell_signals 中 per-signal SQL 读）──
    mw._sell_existing_cache = {}
    for r in rows:
        try:
            mw._sell_existing_cache[r['stock_code']] = json.loads(r['signals_json']) if r['signals_json'] else []
        except:
            mw._sell_existing_cache[r['stock_code']] = []
    print(f'  卖出已有信号缓存: {len(mw._sell_existing_cache)} 只, {time.time()-t0:.1f}s')

    # ppv2 内部自己调 load_rs_batch()，不读 _rs_cache，此处不注入


def get_candidate_stocks(conn, scan_date):
    rows = conn.execute(
        "SELECT DISTINCT k.stock_code, b.name FROM daily_kline k JOIN stock_basic b ON k.stock_code=b.stock_code WHERE k.date=?",
        (scan_date,)
    ).fetchall()
    return [(r['stock_code'], r['name']) for r in rows]


# ═══════════════════════════════════════════
# 引擎扫描
# ═══════════════════════════════════════════

def scan_mw(scan_date):
    from scanners.mw_signal import run_scan
    run_scan(scan_date, silent=True)
    db = sqlite3.connect(DB, timeout=5)
    b1_total = db.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b1_date=?", (scan_date,)).fetchone()[0]
    b2_total = db.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b2_date=?", (scan_date,)).fetchone()[0]
    db.close()
    return b1_total, b2_total


def scan_pp_v1(conn, stocks, scan_date):
    """口袋支点 V1 — 用回填缓存批量检测，不再逐只开 SQL 连接"""
    from scanners.pocket_pivot import detect, load_params
    import scanners.mw_signal as mw
    signals = []
    n_total = len(stocks)
    t0 = time.time()
    params = load_params()
    for i, (code, name) in enumerate(stocks):
        if i % 1000 == 0 and i > 0:
            print(f'  PP_V1: {i}/{n_total}... ({time.time()-t0:.0f}s)', flush=True)
        try:
            klines = mw._kline_cache.get(code, [])
            if len(klines) < 120:
                continue
            # RS 从缓存取，格式转换 (rps_20, rps_250) → {'rs_20':..., 'rs_250':...}
            rs_vals = mw._rs_cache.get(code)
            rs_info = {'rs_20': rs_vals[0] or 0, 'rs_250': rs_vals[1] or 0} if rs_vals else None
            raw = detect(klines, params, rs_info)
            if raw:
                for sig in raw:
                    sig_date = sig.get('date', '') if isinstance(sig, dict) else ''
                    if isinstance(sig, dict) and sig_date == scan_date:
                        sig['stock_code'] = code; sig['stock_name'] = name; sig['engine_version'] = 'V1'
                        signals.append(sig)
        except:
            pass
    t_elapsed = time.time() - t0
    saved = 0
    if signals:
        cur = conn.cursor()
        for s in signals:
            cur.execute("""INSERT OR REPLACE INTO pocket_pivot_daily (date,stock_code,stock_name,engine_version,pivot_type,b1_overlap,gain_pct,vol_ratio,close_position,rps_20,rps_250,sma10,sma60,pct_from_ma10,close,volume) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s.get('signal_date', scan_date) or scan_date, s['stock_code'], s.get('stock_name',''), 'V1', s.get('pivot_type','base'), int(s.get('b1_overlap',False)), s.get('gain_pct'), s.get('vol_ratio'), s.get('close_position'), s.get('rps_20'), s.get('rps_250'), s.get('sma10'), s.get('sma60'), s.get('pct_from_ma10'), s.get('close'), s.get('volume')))
        conn.commit()
        saved = len(signals)
    print(f'  PP_V1: {saved} 信号 ({t_elapsed:.0f}s)', flush=True)
    return saved


def scan_pp_v2(conn, stocks, scan_date):
    from scanners.pocket_pivot_v2 import scan_date as ppv2_scan, save_to_db
    signals = ppv2_scan(scan_date)
    if signals:
        save_to_db(signals)
        return len(signals)
    return 0


def scan_base_breakout_v2(conn, stocks, scan_date):
    from scanners.base_breakout_v2 import detect
    signals = []
    t0 = time.time()
    for code, name in stocks:
        try:
            import scanners.mw_signal as mw
            klines = mw._kline_cache.get(code, [])
            if len(klines) < 120: continue
            raw = detect(klines, {'stock_code': code})
            if raw:
                for sig in raw:
                    if isinstance(sig, dict):
                        sig['stock_code'] = code; sig['stock_name'] = name; sig['engine_version'] = 'V2'
                        signals.append(sig)
        except:
            pass
    saved = 0
    if signals:
        cur = conn.cursor()
        for s in signals:
            cur.execute("""INSERT OR REPLACE INTO market_breakout_v2_daily (date,stock_code,stock_name,engine_version,breakout_type,base_type,h_date,h_price,l_date,l_price,decline_pct,c_start,c_end,c_days,gain_pct,vol_ratio,close_position,rps_20,rps_250,ind_rs250,ind_code,ind_name,sma50,sma150,sma200,close,volume) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s.get('date', scan_date), s['stock_code'], s.get('stock_name',''), 'V2', s.get('breakout_type'), s.get('base_type'), s.get('h_date'), s.get('h_price'), s.get('l_date'), s.get('l_price'), s.get('decline_pct'), s.get('c_start'), s.get('c_end'), s.get('c_days'), s.get('gain_pct'), s.get('vol_ratio'), s.get('close_position'), s.get('rps_20'), s.get('rps_250'), s.get('ind_rs250'), s.get('ind_code'), s.get('ind_name'), s.get('sma50'), s.get('sma150'), s.get('sma200'), s.get('close'), s.get('volume')))
        conn.commit()
        saved = len(signals)
    print(f'  BO_V2: {saved} 信号 ({time.time()-t0:.0f}s)', flush=True)
    return saved


def _detect_one_engine(args):
    """单个引擎检测（线程池调用）"""
    eng_name, detect_fn, params, klines, code = args
    try:
        sigs = detect_fn(daily=klines, params=params, stock_code=code)
        results = []
        for s in (sigs or []):
            if isinstance(s, dict):
                s['source'] = eng_name
                results.append(s)
        return eng_name, results
    except:
        return eng_name, []


def scan_sell_signals(conn, stocks, scan_date):
    t0 = time.time()
    from scanners.climax_top import detect as c_detect, load_params as c_params
    from scanners.railroad_tracks import detect as r_detect, load_params as r_params
    from scanners.top_pattern import detect as t_detect, load_params as t_params
    import scanners.mw_signal as mw
    # breakout_failure 依赖基部突破 V2 先算，单独补跑，暂不纳入

    # ── 预筛选：只跑可能产生卖出信号的股票 ──
    # 节省大量 CPU：没有明显上涨的股票不可能高潮见顶/头肩顶/铁轨线
    candidates = []
    for code, name in stocks:
        klines = mw._kline_cache.get(code, [])
        if len(klines) < 120:
            continue
        # 近 60 日涨幅
        recent = klines[-60:]
        close_now = recent[-1]['close']
        close_60d_ago = recent[0]['close']
        gain_60d = (close_now - close_60d_ago) / close_60d_ago * 100 if close_60d_ago > 0 else 0
        # 距 60 日最高价的距离
        high_60d = max(k['high'] for k in recent)
        pct_from_high = (close_now - high_60d) / high_60d * 100 if high_60d > 0 else 0
        # 条件：近 60 日涨超 15%，或当前价在 60 日高点 10% 以内
        if gain_60d > 15 or pct_from_high > -10:
            candidates.append((code, name, klines))

    skipped = len(stocks) - len(candidates)
    if skipped:
        print(f'  卖出预筛选: {len(candidates)}/{len(stocks)} 只候选 ({skipped} 跳过)', flush=True)

    # 从缓存读已有信号
    existing_cache = getattr(mw, '_sell_existing_cache', {})

    # ── 按引擎截断 K 线（各引擎最低需求不同，传多了白费 CPU）──
    # climax_top: 390天 | railroad_tracks: 250天 | top_pattern: 200天
    def _slice_klines(klines, n_days):
        return klines[-n_days:] if len(klines) > n_days else klines

    saved = 0
    total_produced = 0  # 所有引擎产出的信号数（去重前）
    eng_counts = {'climax_top': 0, 'railroad_tracks': 0, 'head_shoulders': 0, 'triple_top': 0, 'double_top': 0}
    pool = ThreadPoolExecutor(max_workers=3)
    for code, name, klines in candidates:

        # 3 个引擎并行检测：各自传不同长度的 K 线
        tasks = [
            ('climax_top',     c_detect, c_params(), _slice_klines(klines, 400), code),
            ('railroad_tracks', r_detect, r_params(), _slice_klines(klines, 250), code),
            ('top_pattern',     t_detect, t_params(), _slice_klines(klines, 200), code),
        ]
        all_results = list(pool.map(_detect_one_engine, tasks))

        # 收集信号，合并去重（基于信号内容的 JSON 指纹）
        existing_sigs = existing_cache.get(code, [])
        existing_keys = {json.dumps(s2, sort_keys=True, default=str) for s2 in existing_sigs}
        for eng_name, sigs in all_results:
            total_produced += len(sigs)
            for s in sigs:
                key = json.dumps(s, sort_keys=True, default=str)
                if key not in existing_keys:
                    existing_sigs.append(s)
                    existing_keys.add(key)
                    saved += 1
                    if eng_name == 'top_pattern':
                        eng_counts[s.get('pattern', 'top_pattern')] += 1
                    else:
                        eng_counts[eng_name] += 1

        # 有新增则写库
        if len(existing_sigs) > len(existing_cache.get(code, [])):
            conn.execute("INSERT OR REPLACE INTO pattern_scan_signals (stock_code,date,signals_json) VALUES(?,?,?)",
                (code, scan_date, json.dumps(existing_sigs, ensure_ascii=False)))
            existing_cache[code] = existing_sigs

    pool.shutdown(wait=True)
    conn.commit()
    print(f'  Sell: 高潮见顶={eng_counts["climax_top"]} 铁轨线={eng_counts["railroad_tracks"]} 头肩顶={eng_counts["head_shoulders"]} 三重顶={eng_counts["triple_top"]} 双重顶={eng_counts["double_top"]} 合计={total_produced} ({time.time()-t0:.0f}s)', flush=True)
    return total_produced


def init_tables():
    db = sqlite3.connect(DB, timeout=30)
    # ── WAL 优化：拉大自动检查点阈值，减少多进程写锁争用 ──
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=10000")  # 40MB WAL 才触发（默认 4MB）
    db.execute("""CREATE TABLE IF NOT EXISTS market_breakout_v2_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT,
        engine_version TEXT NOT NULL DEFAULT 'V2', breakout_type TEXT, base_type TEXT,
        h_date TEXT, h_price REAL, l_date TEXT, l_price REAL,
        decline_pct REAL, c_start TEXT, c_end TEXT, c_days INTEGER,
        gain_pct REAL, vol_ratio REAL, close_position REAL,
        rps_20 INTEGER, rps_250 INTEGER, ind_rs250 INTEGER, ind_code TEXT, ind_name TEXT,
        sma50 REAL, sma150 REAL, sma200 REAL, close REAL, volume INTEGER,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(date, stock_code, engine_version))""")
    # v2 完成标记表：每天全部引擎跑完写一行，增量模式凭此判断
    db.execute("""CREATE TABLE IF NOT EXISTS backfill_v2_progress (
        date TEXT PRIMARY KEY,
        completed_at TEXT DEFAULT (datetime('now','localtime')))""")
    db.commit()
    db.close()


# ═══════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='全信号统一回填 v2（并行预加载）')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--start', help='起始日期')
    group.add_argument('--quarter', help='按季度')
    group.add_argument('--retry-failed', action='store_true', help='从错误日志读取失败日期，逐天补跑（建议 --parallel 1）')
    parser.add_argument('--end', help='结束日期')
    parser.add_argument('--incremental', action='store_true')
    parser.add_argument('--skip-mw', action='store_true')
    parser.add_argument('--skip-buy', action='store_true')
    parser.add_argument('--skip-sell', action='store_true')
    parser.add_argument('--threads', type=int, default=4, help='并行线程数（默认4）')
    parser.add_argument('--parallel', type=int, default=1, help='多进程并行数（默认1=单进程，>1时自动切分日期范围）')
    args = parser.parse_args()

    THREADS = args.threads

    # ── 补漏模式：只跑错误日志中的日期 ──
    if args.retry_failed:
        if not os.path.exists(ERROR_LOG):
            print("无错误日志，无需补漏"); sys.exit(0)
        with open(ERROR_LOG, 'r', encoding='utf-8') as f:
            failed_dates = sorted(set(line.strip() for line in f if line.strip()))
        if not failed_dates:
            print("错误日志为空"); sys.exit(0)
        print(f"补漏模式: {len(failed_dates)} 个失败日期: {failed_dates[0]} ~ {failed_dates[-1]}")
        dates = failed_dates
        args.incremental = True
        args.parallel = 1  # 补漏强制单进程，避免再次撞锁
        os.remove(ERROR_LOG)  # 清空日志
    else:
        if args.quarter:
            start_date, end_date = quarter_to_range(args.quarter)
        else:
            if not args.start:
                print("请指定 --start 或 --quarter 或 --retry-failed"); sys.exit(1)
            start_date, end_date = args.start, args.end

        all_dates = get_trading_dates(start_date, end_date)
        if not all_dates:
            print("区间无交易日"); sys.exit(0)

        if args.incremental:
            existing = get_existing_dates()
            dates = [d for d in all_dates if d not in existing]
            print(f"增量模式: 已完成 {len(all_dates)-len(dates)} 天, 剩余 {len(dates)} 天")
        else:
            dates = all_dates
            print(f"全量模式: {len(dates)} 天")

        if not dates:
            print("无需处理"); sys.exit(0)

    # ── 多进程并行：切分日期范围，各自独立运行 ──
    if args.parallel > 1:
        # 切分为恰好 N 段（最后一段可能少几天）
        chunk_size = max(1, len(dates) // args.parallel)
        chunks = [dates[i:i+chunk_size] for i in range(0, len(dates), chunk_size)]
        # 如果段数超过 parallel，合并最后两段
        if len(chunks) > args.parallel:
            chunks[-2].extend(chunks[-1])
            chunks.pop()
        print(f"多进程并行: {len(chunks)} 进程 × ~{chunk_size} 天/进程")
        for idx, chunk in enumerate(chunks):
            print(f"  Worker {idx}: {chunk[0]} ~ {chunk[-1]} ({len(chunk)} 天)")

        processes = []
        for idx, chunk in enumerate(chunks):
            cmd = [sys.executable, __file__,
                   '--start', chunk[0], '--end', chunk[-1],
                   '--threads', str(args.threads)]
            if args.incremental:
                cmd.append('--incremental')
            if args.skip_mw:
                cmd.append('--skip-mw')
            if args.skip_buy:
                cmd.append('--skip-buy')
            if args.skip_sell:
                cmd.append('--skip-sell')
            p = subprocess.Popen(cmd)
            processes.append((idx, p))
            print(f"  Worker {idx} PID={p.pid} 已启动")

        # 等待全部完成
        for idx, p in processes:
            rc = p.wait()
            status = '✓' if rc == 0 else f'✗(code={rc})'
            print(f"  Worker {idx} {status}")
        print("\n全部 Worker 完成")
        sys.exit(0)

    # ── 单进程模式 ──
    init_tables()

    # 抑制 chanlun bi 数据过期刷屏（双重保障：环境变量 + 模块开关）
    os.environ['CHANLUN_SILENT'] = '1'
    import scanners.chanlun_structure as cls
    cls._verbose = False
    cls._log_path = os.path.join(PROJECT, 'logs', 'chanlun_bi_age.log')
    os.makedirs(os.path.dirname(cls._log_path), exist_ok=True)

    t_start = time.time()
    completed = 0
    errors = []

    for i, scan_date in enumerate(dates):
        t0 = time.time()
        print(f"\n[{i+1}/{len(dates)}] {scan_date}")

        last_error = None
        stocks = None  # 预加载后缓存

        for attempt in range(5):
            if attempt > 0:
                wait = 15 * attempt
                print(f'  重试 {attempt+1}/5 (等待 {wait}s)...', flush=True)
                time.sleep(wait)
            try:
                # ── 增量模式：按引擎粒度检查 ──
                eng = get_engine_status(scan_date) if args.incremental else {'mw': False, 'pp_v1': False, 'pp_v2': False, 'bo_v2': False, 'sell': False}
                all_done = all(eng.values())
                if all_done:
                    try:
                        pd = sqlite3.connect(DB, timeout=5)
                        pd.execute("INSERT OR IGNORE INTO backfill_v2_progress(date) VALUES(?)", (scan_date,))
                        pd.commit(); pd.close()
                    except: pass
                    print(f'  全部引擎已完成，跳过', flush=True)
                    completed += 1
                    break

                # ── 预加载（仅第一次，重试时不重复）──
                if stocks is None:
                    conn = sqlite3.connect(DB, timeout=30)
                    conn.row_factory = sqlite3.Row
                    print('  预加载缓存...', flush=True)
                    stocks = get_candidate_stocks(conn, scan_date)
                    set_all_caches(conn, scan_date, stocks)
                    conn.close()

                # 设缠论数据截止日期（防 BO/Sell 引擎读到未来 bi 数据）
                cls._target_date = scan_date

                # ── MW ──
                if not args.skip_mw and not eng['mw']:
                    b1_total, b2_total = scan_mw(scan_date)
                elif eng['mw']:
                    b1_total = b2_total = -1
                else:
                    b1_total = b2_total = 0

                # ── 买入 ──
                ppv1 = ppv2 = bo = 0
                need_buy = not args.skip_buy and (not eng['pp_v1'] or not eng['pp_v2'] or not eng['bo_v2'])
                if need_buy:
                    conn = sqlite3.connect(DB, timeout=120)
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA busy_timeout=120000")
                    if not eng['pp_v2']: ppv2 = scan_pp_v2(conn, stocks, scan_date)
                    else: ppv2 = -1
                    if not eng['pp_v1']: ppv1 = scan_pp_v1(conn, stocks, scan_date)
                    else: ppv1 = -1
                    if not eng['bo_v2']: bo = scan_base_breakout_v2(conn, stocks, scan_date)
                    else: bo = -1
                    conn.close()

                # ── 卖出 ──
                sell = 0
                if not args.skip_sell and not eng['sell']:
                    conn = sqlite3.connect(DB, timeout=120)
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA busy_timeout=120000")
                    sell = scan_sell_signals(conn, stocks, scan_date)
                    conn.close()
                elif eng['sell']:
                    sell = -1

                # ── 进度标记：成功跑完就写（不以信号数量为准）──
                try:
                    pd = sqlite3.connect(DB, timeout=5)
                    pd.execute("INSERT OR IGNORE INTO backfill_v2_progress(date) VALUES(?)", (scan_date,))
                    pd.commit(); pd.close()
                except Exception:
                    pass

                elapsed = time.time() - t0
                completed += 1
                avg = (time.time() - t_start) / completed
                eta = avg * (len(dates) - i - 1)
                def _v(x): return '-' if x == -1 else str(x)
                print(f'  MW: B1={_v(b1_total)} B2={_v(b2_total)}  |  '
                      f'PP: V1={_v(ppv1)} V2={_v(ppv2)}  |  BO_V2={_v(bo)}  |  Sell={_v(sell)}')
                print(f'  全天 {elapsed:.0f}s | ETA {eta/3600:.1f}h')
                last_error = None
                break  # 成功，跳出重试循环

            except sqlite3.OperationalError as e:
                last_error = e
                if 'locked' not in str(e).lower() or attempt == 2:
                    break  # 非锁错误或最后一次重试，不再重试
                # 锁错误，继续下一次重试
            except Exception as e:
                last_error = e
                break  # 非 SQLite 错误，不重试

        if last_error:
            elapsed = time.time() - t0
            errors.append((scan_date, str(last_error)[:200]))
            print(f'  ✗ ({elapsed:.0f}s) {last_error}')
            # 写入错误日志，供 --retry-failed 批量补跑
            try:
                os.makedirs(os.path.dirname(ERROR_LOG), exist_ok=True)
                with open(ERROR_LOG, 'a', encoding='utf-8') as f:
                    f.write(f"{scan_date}\n")
            except:
                pass

    total_elapsed = time.time() - t_start
    print(f"\n=== 完成 ===")
    print(f"成功: {completed}/{len(dates)} 天, 失败: {len(errors)} 天")
    print(f"总耗时: {total_elapsed/3600:.1f}h")
    if errors:
        for d, e in errors[:10]:
            print(f"  {d}: {e}")
        print(f"\n失败日期已写入 {ERROR_LOG}")
        print(f"全部跑完后执行以下命令补漏：")
        print(f"  python scripts/backfill_all_signals_v2.py --retry-failed --parallel 1")
