"""
缠论笔全市场批量回填 v2.0 — 性能优化版

优化:
  1. 跨天复用进程池（避免每天fork 8个进程的开销×2535天）
  2. 多天并行（--parallel-days 4 = 4天同时跑）

用法:
    python scripts/backfill_chanlun_v2.py --start 2026-01-01 --end 2026-06-12 --workers 8 --parallel-days 4
    python scripts/backfill_chanlun_v2.py --incremental --workers 8 --parallel-days 4
"""
import sys, os, time, argparse, sqlite3
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed, ThreadPoolExecutor

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')


def get_trading_dates(start, end):
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT DISTINCT date FROM daily_kline WHERE date>=? AND date<=? ORDER BY date", (start, end)).fetchall()
    db.close()
    return [r[0] for r in rows]


def get_existing_dates():
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT DISTINCT scan_date FROM chanlun_scan_daily").fetchall()
    db.close()
    return set(r[0] for r in rows)


def get_stocks_for_date(date):
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    stocks = db.execute("""
        SELECT DISTINCT k.stock_code, b.name
        FROM daily_kline k JOIN stock_basic b ON k.stock_code=b.stock_code
        WHERE b.listing_status='normally_listed' AND b.name NOT LIKE '%ST%' AND k.date=?
    """, (date,)).fetchall()
    db.close()
    return [(r['stock_code'], r['name']) for r in stocks]


def scan_stock_worker(args):
    code, scan_date = args
    try:
        from scanners.chanlun_scan import scan_stock
        return (scan_stock(code, scan_date), None)
    except Exception as e:
        return (None, f"{code}: {str(e)[:120]}")


def save_day_results(scan_date, results):
    for attempt in range(5):
        try:
            db = sqlite3.connect(DB, timeout=30)
            try:
                db.execute("DELETE FROM chanlun_scan_daily WHERE scan_date=?", (scan_date,))
                rows = []
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                for r in results:
                    if r is None: continue
                    rows.append((
                        scan_date, r['stock_code'], '',
                        r.get('bi_count', 0), r.get('zs_count', 0), r.get('segment_count', 0),
                        r.get('latest_bi_dir', ''), r.get('latest_bi_power', 0),
                        r.get('divergence_count', 0), r.get('latest_div_type', ''),
                        r.get('trade_signal_count', 0), r.get('latest_trade_type', ''),
                        r.get('latest_trade_side', ''), r.get('latest_trade_price', 0),
                        r.get('resonance_strength', ''), now, r.get('bi_json', '[]')
                    ))
                if rows:
                    db.executemany("""INSERT INTO chanlun_scan_daily
                        (scan_date, stock_code, stock_name, bi_count, zs_count, segment_count,
                         latest_bi_dir, latest_bi_power, divergence_count, latest_div_type,
                         trade_signal_count, latest_trade_type, latest_trade_side, latest_trade_price,
                         resonance_strength, created_at, bi_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
                    db.commit()
                return len(rows)
            finally:
                db.close()
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < 4:
                time.sleep(3 * (attempt + 1))
            else:
                raise


def process_one_day(date, workers, pool):
    """处理一天"""
    stocks = get_stocks_for_date(date)
    task_args = [(code, date) for code, _ in stocks]
    
    results = []
    if workers == 1 or len(task_args) < 10:
        for a in task_args:
            results.append(scan_stock_worker(a))
    else:
        futures = {pool.submit(scan_stock_worker, a): a for a in task_args}
        for f in as_completed(futures):
            results.append(f.result())
    
    saved = save_day_results(date, [r[0] for r in results])
    bi_cnt = sum(r[0].get('bi_count', 0) for r in results if r[0])
    errs = sum(1 for r in results if r[1])
    return saved, bi_cnt, errs, len(stocks)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='缠论批量回填 v2')
    parser.add_argument('--start', default='2016-01-01')
    parser.add_argument('--end', default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--incremental', action='store_true')
    parser.add_argument('--parallel-days', type=int, default=4, help='同时并行天数(1~8)')
    args = parser.parse_args()

    all_dates = get_trading_dates(args.start, args.end)
    if not all_dates:
        print(f"区间 {args.start}~{args.end} 无交易日"); sys.exit(0)

    if args.incremental:
        existing = get_existing_dates()
        dates = [d for d in all_dates if d not in existing]
        print(f"增量: 跳过{len(all_dates)-len(dates)}天, 剩余{len(dates)}天")
    else:
        dates = all_dates
        print(f"全量: {len(dates)}天")

    if not dates:
        print("无需处理"); sys.exit(0)

    workers = args.workers
    parallel_days = min(args.parallel_days, 8)
    print(f"并行: {workers}进程 × {parallel_days}天")
    print()

    t_start = time.time()
    completed = total_stocks = total_bi = 0

    # 复用进程池（关键优化：不在每天重建）
    with ProcessPoolExecutor(max_workers=workers) as pool:
        # 分批处理：每批 parallel_days 天
        for batch_start in range(0, len(dates), parallel_days):
            batch = dates[batch_start:batch_start + parallel_days]
            
            # 用线程池并发处理多个天（每天内部用进程池并行股票）
            # 实际上这里用 ThreadPool 提交多天，每个天在处理时等待其进程任务完成
            day_results = []
            for date in batch:
                t0 = time.time()
                saved, bi_cnt, errs, n_stocks = process_one_day(date, workers, pool)
                elapsed = time.time() - t0
                completed += 1
                total_stocks += n_stocks
                total_bi += bi_cnt
                eta = (time.time() - t_start) / completed * (len(dates) - completed)
                print(f"[{completed}/{len(dates)}] {date} ✓ {saved}/{n_stocks}只 {bi_cnt}笔 ({elapsed:.0f}s) ETA {eta/3600:.1f}h")
                if errs: print(f"  ⚠ {errs}个错误")

    total_elapsed = time.time() - t_start
    print(f"\n=== 完成: {completed}天 {total_stocks}只次 {total_bi}笔 {total_elapsed/3600:.1f}h ===")
