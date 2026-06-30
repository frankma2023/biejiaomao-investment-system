"""
口袋支点 V1 批量回填 v1.0
写入 engine_version='V1' 到 pocket_pivot_daily

依赖：stock_rs_daily（个股RS）
输出：pocket_pivot_daily (engine_version='V1')

用法：
    python scripts/backfill_pocket_pivot_v1.py --start 2016-01-01 --end 2016-03-31
    python scripts/backfill_pocket_pivot_v1.py --quarter 2016Q1 --incremental
"""
import sys, os, time, argparse, sqlite3
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))

DB = os.path.join(PROJECT, 'data', 'lixinger.db')

SAVE_LOCK = __import__('threading').Lock()


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
    rows = db.execute(
        "SELECT DISTINCT date FROM pocket_pivot_daily WHERE engine_version='V1' AND date IS NOT NULL"
    ).fetchall()
    db.close()
    return set(r[0] for r in rows)


def quarter_to_range(q):
    import calendar
    year = int(q[:4]); qnum = int(q[-1])
    sm = (qnum - 1) * 3 + 1; em = sm + 2
    ld = calendar.monthrange(year, em)[1]
    return f"{year}-{sm:02d}-01", f"{year}-{em:02d}-{ld}"


def scan_one_day_v1(date):
    """Worker: 扫描单日全市场 V1 口袋支点"""
    t0 = time.time()
    try:
        from scanners.pocket_pivot import detect_for_stock, detect
        db = sqlite3.connect(DB, timeout=30)
        db.row_factory = sqlite3.Row

        # 获取当天有交易且 RS 数据存在的股票
        stocks = db.execute("""
            SELECT DISTINCT k.stock_code, b.name
            FROM daily_kline k
            JOIN stock_basic b ON k.stock_code=b.stock_code
            WHERE k.date=?
        """, (date,)).fetchall()

        signals = []
        for r in stocks:
            code, name = r['stock_code'], r['name']
            raw = detect_for_stock(code, date)
            if raw and isinstance(raw, list):
                for sig in raw:
                    if isinstance(sig, dict):
                        sig['stock_code'] = code
                        sig['stock_name'] = name
                        sig['engine_version'] = 'V1'
                        signals.append(sig)

        # 保存
        if signals:
            for attempt in range(3):
                try:
                    cur = db.cursor()
                    for s in signals:
                        # Map V1 fields to pocket_pivot_daily columns
                        cur.execute("""INSERT OR REPLACE INTO pocket_pivot_daily
                            (date,stock_code,stock_name,engine_version,pivot_type,b1_overlap,
                             gain_pct,vol_ratio,close_position,rps_20,rps_250,sma10,sma60,
                             pct_from_ma10,close,volume)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (s.get('signal_date', date) or date,
                             s['stock_code'], s.get('stock_name', ''),
                             'V1', s.get('pivot_type', 'base'), int(s.get('b1_overlap', False)),
                             s.get('gain_pct'), s.get('vol_ratio'), s.get('close_position'),
                             s.get('rps_20'), s.get('rps_250'), s.get('sma10'), s.get('sma60'),
                             s.get('pct_from_ma10'), s.get('close'), s.get('volume')))
                    db.commit()
                    break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))
                    else:
                        raise
        db.close()
        elapsed = time.time() - t0
        return (date, len(signals), elapsed, None)
    except Exception as e:
        elapsed = time.time() - t0
        return (date, 0, elapsed, str(e)[:200])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='口袋支点V1批量回填')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--start', help='起始日期 YYYY-MM-DD（需同时指定 --end）')
    group.add_argument('--quarter', help='按季度运行，如 2016Q1')
    parser.add_argument('--end', help='结束日期 YYYY-MM-DD')
    parser.add_argument('--workers', type=int, default=4, help='并行进程数（默认4）')
    parser.add_argument('--incremental', action='store_true', help='增量模式')
    args = parser.parse_args()

    if args.quarter:
        start_date, end_date = quarter_to_range(args.quarter)
    else:
        start_date, end_date = args.start, args.end

    all_dates = get_trading_dates(start_date, end_date)
    if not all_dates:
        print(f"区间无交易日")
        sys.exit(0)

    if args.incremental:
        existing = get_existing_dates()
        dates = [d for d in all_dates if d not in existing]
        print(f"增量模式: 已完成 {len(all_dates)-len(dates)} 天, 剩余 {len(dates)} 天")
    else:
        dates = all_dates
        print(f"全量模式: {len(dates)} 天 ({dates[0]} ~ {dates[-1]})")

    if not dates:
        print("无需处理")
        sys.exit(0)

    workers = args.workers
    print(f"并行: {workers} 进程")

    t_start = time.time()
    completed = 0
    total_signals = 0

    if workers == 1:
        for i, date in enumerate(dates):
            d, cnt, elapsed, err = scan_one_day_v1(date)
            completed += 1; total_signals += cnt
            status = f"✓ {cnt:>3}个" if err is None else f"✗ {err[:60]}"
            eta = (time.time()-t_start)/completed*(len(dates)-completed) if completed else 0
            print(f"[{completed}/{len(dates)}] {date} {status} ({elapsed:.1f}s) ETA {eta/60:.0f}min")
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(scan_one_day_v1, d): d for d in dates}
            for f in as_completed(futures):
                d, cnt, elapsed, err = f.result()
                completed += 1; total_signals += cnt
                status = f"✓ {cnt:>3}个" if err is None else f"✗ {err[:60]}"
                eta = (time.time()-t_start)/completed*(len(dates)-completed) if completed else 0
                print(f"[{completed}/{len(dates)}] {d} {status} ({elapsed:.1f}s) ETA {eta/60:.0f}min")

    total_minutes = (time.time() - t_start) / 60
    print(f"\n完成! {len(dates)}天, {total_signals}个信号, {total_minutes:.1f}分钟")
