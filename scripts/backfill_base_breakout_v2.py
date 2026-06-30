"""
基部突破 V2 批量回填 v1.0
写入 market_breakout_v2_daily 表（26列完整结构）

依赖：chanlun_bi_json（缠论笔）, stock_rs_daily（个股RS）, index_rs_daily（行业RS）
输出：market_breakout_v2_daily

用法：
    python scripts/backfill_base_breakout_v2.py --start 2016-01-01 --end 2016-03-31
    python scripts/backfill_base_breakout_v2.py --quarter 2016Q1 --incremental --workers 4
"""
import sys, os, time, argparse, sqlite3
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))

DB = os.path.join(PROJECT, 'data', 'lixinger.db')


def get_trading_dates(start, end):
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date>=? AND date<=? ORDER BY date",
        (start, end)
    ).fetchall()
    db.close()
    return [r[0] for r in rows]


def get_existing_dates():
    db = sqlite3.connect(DB)
    try:
        rows = db.execute("SELECT DISTINCT date FROM market_breakout_v2_daily WHERE date IS NOT NULL").fetchall()
        result = set(r[0] for r in rows)
    except sqlite3.OperationalError:
        result = set()
    db.close()
    return result


def quarter_to_range(q):
    import calendar
    year = int(q[:4]); qnum = int(q[-1])
    sm = (qnum - 1) * 3 + 1; em = sm + 2
    ld = calendar.monthrange(year, em)[1]
    return f"{year}-{sm:02d}-01", f"{year}-{em:02d}-{ld}"


def init_table():
    db = sqlite3.connect(DB, timeout=30)
    db.execute("""CREATE TABLE IF NOT EXISTS market_breakout_v2_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT,
        engine_version TEXT NOT NULL DEFAULT 'V2',
        breakout_type TEXT, base_type TEXT,
        h_date TEXT, h_price REAL, l_date TEXT, l_price REAL,
        decline_pct REAL, c_start TEXT, c_end TEXT, c_days INTEGER,
        gain_pct REAL, vol_ratio REAL, close_position REAL,
        rps_20 INTEGER, rps_250 INTEGER,
        ind_rs250 INTEGER, ind_code TEXT, ind_name TEXT,
        sma50 REAL, sma150 REAL, sma200 REAL,
        close REAL, volume INTEGER,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(date, stock_code, engine_version))""")
    db.commit()
    db.close()


def scan_one_day_v2(date):
    """Worker: 扫描单日全市场 V2 基部突破"""
    t0 = time.time()
    try:
        from scanners.base_breakout_v2 import detect
        db = sqlite3.connect(DB, timeout=30)
        db.row_factory = sqlite3.Row

        stocks = db.execute("""
            SELECT DISTINCT k.stock_code, b.name
            FROM daily_kline k
            JOIN stock_basic b ON k.stock_code=b.stock_code
            WHERE k.date=?
        """, (date,)).fetchall()

        signals = []
        for r in stocks:
            code, name = r['stock_code'], r['name']
            # 取K线
            krows = db.execute(
                "SELECT date,open,high,low,close,volume FROM daily_kline WHERE stock_code=? AND date<=? ORDER BY date",
                (code, date)
            ).fetchall()
            if len(krows) < 120: continue
            klines = [dict(r2) for r2 in krows]

            raw = detect(klines, {'stock_code': code})
            if raw:
                for sig in raw:
                    if isinstance(sig, dict):
                        sig['stock_code'] = code
                        sig['stock_name'] = name
                        sig['engine_version'] = 'V2'
                        signals.append(sig)

        # 保存
        if signals:
            for attempt in range(3):
                try:
                    cur = db.cursor()
                    for s in signals:
                        cur.execute("""INSERT OR REPLACE INTO market_breakout_v2_daily
                            (date,stock_code,stock_name,engine_version,breakout_type,base_type,
                             h_date,h_price,l_date,l_price,decline_pct,c_start,c_end,c_days,
                             gain_pct,vol_ratio,close_position,rps_20,rps_250,
                             ind_rs250,ind_code,ind_name,
                             sma50,sma150,sma200,close,volume)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (s.get('date', date), s['stock_code'], s.get('stock_name',''),
                             'V2', s.get('breakout_type'), s.get('base_type'),
                             s.get('h_date'), s.get('h_price'), s.get('l_date'), s.get('l_price'),
                             s.get('decline_pct'), s.get('c_start'), s.get('c_end'), s.get('c_days'),
                             s.get('gain_pct'), s.get('vol_ratio'), s.get('close_position'),
                             s.get('rps_20'), s.get('rps_250'),
                             s.get('ind_rs250'), s.get('ind_code'), s.get('ind_name'),
                             s.get('sma50'), s.get('sma150'), s.get('sma200'),
                             s.get('close'), s.get('volume')))
                    db.commit()
                    break
                except Exception as e:
                    if attempt < 2: time.sleep(2*(attempt+1))
                    else: raise
        db.close()
        elapsed = time.time() - t0
        return (date, len(signals), elapsed, None)
    except Exception as e:
        return (date, 0, time.time()-t0, str(e)[:200])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='基部突破V2批量回填')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--start', help='起始日期')
    group.add_argument('--quarter', help='按季度')
    parser.add_argument('--end', help='结束日期')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--incremental', action='store_true')
    args = parser.parse_args()

    if args.quarter:
        start_date, end_date = quarter_to_range(args.quarter)
    else:
        start_date, end_date = args.start, args.end

    init_table()
    all_dates = get_trading_dates(start_date, end_date)
    if not all_dates:
        print("区间无交易日"); sys.exit(0)

    if args.incremental:
        existing = get_existing_dates()
        dates = [d for d in all_dates if d not in existing]
        print(f"增量: 已完成 {len(all_dates)-len(dates)} 天, 剩余 {len(dates)} 天")
    else:
        dates = all_dates
        print(f"全量: {len(dates)} 天")

    if not dates:
        print("无需处理"); sys.exit(0)

    workers = args.workers
    print(f"并行: {workers} 进程")

    t_start = time.time()
    completed = 0; total_signals = 0

    if workers == 1:
        for i, date in enumerate(dates):
            d, cnt, elapsed, err = scan_one_day_v2(date)
            completed += 1; total_signals += cnt
            status = f"✓ {cnt:>3}个" if err is None else f"✗ {err[:60]}"
            eta = (time.time()-t_start)/completed*(len(dates)-completed) if completed else 0
            print(f"[{completed}/{len(dates)}] {date} {status} ({elapsed:.1f}s) ETA {eta/60:.0f}min")
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(scan_one_day_v2, d): d for d in dates}
            for f in as_completed(futures):
                d, cnt, elapsed, err = f.result()
                completed += 1; total_signals += cnt
                status = f"✓ {cnt:>3}个" if err is None else f"✗ {err[:60]}"
                eta = (time.time()-t_start)/completed*(len(dates)-completed) if completed else 0
                print(f"[{completed}/{len(dates)}] {d} {status} ({elapsed:.1f}s) ETA {eta/60:.0f}min")

    total_minutes = (time.time() - t_start) / 60
    print(f"\n完成! {len(dates)}天, {total_signals}个信号, {total_minutes:.1f}分钟")
