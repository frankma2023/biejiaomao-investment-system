"""
缠论笔全市场批量回填 v3.0 — 全异步并行版

优化:
  1. 一次性提交所有天的所有股票任务到进程池
  2. 进程池自动调度，消除天与天之间的等待空隙
  3. 按天分组收结果，单天集齐即写入DB

用法:
    python scripts/backfill_chanlun_v3.py --start 2026-01-01 --end 2026-06-12 --workers 8
    python scripts/backfill_chanlun_v3.py --incremental --workers 8
"""
import sys, os, time, argparse, sqlite3
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import defaultdict

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


def get_all_stocks(dates):
    """批量获取所有天的股票列表 → {date: [(code, name), ...]}"""
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    result = defaultdict(list)
    for date in dates:
        rows = db.execute("""
            SELECT DISTINCT k.stock_code, b.name
            FROM daily_kline k JOIN stock_basic b ON k.stock_code=b.stock_code
            WHERE b.listing_status='normally_listed' AND b.name NOT LIKE '%ST%' AND k.date=?
        """, (date,)).fetchall()
        result[date] = [(r['stock_code'], r['name']) for r in rows]
    db.close()
    return dict(result)


def scan_stock_worker(args):
    code, scan_date = args
    try:
        from scanners.chanlun_scan import scan_stock
        r = scan_stock(code, scan_date)
        return (r, scan_date, None)
    except Exception as e:
        return (None, scan_date, f"{code}: {str(e)[:120]}")


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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='缠论批量回填 v3 全异步')
    parser.add_argument('--start', default='2016-01-01')
    parser.add_argument('--end', default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--incremental', action='store_true')
    args = parser.parse_args()

    all_dates = get_trading_dates(args.start, args.end)
    if not all_dates:
        print("无交易日"); sys.exit(0)

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
    print(f"预加载股票列表...")
    stocks_by_date = get_all_stocks(dates)
    total_tasks = sum(len(v) for v in stocks_by_date.values())
    print(f"{len(dates)}天 × 约{total_tasks//len(dates)}只/天 = {total_tasks}个任务")
    print(f"并行: {workers}进程, 全异步提交\n")

    t_start = time.time()
    all_tasks = []
    for date in dates:
        for code, _ in stocks_by_date.get(date, []):
            all_tasks.append((code, date))

    # 按天追踪进度
    day_total = {d: len(stocks_by_date.get(d, [])) for d in dates}
    day_done = defaultdict(int)
    day_results = defaultdict(list)
    days_completed = 0
    total_bi = 0

    # 一次性提交所有任务
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scan_stock_worker, t): t for t in all_tasks}
        
        for f in as_completed(futures):
            r, scan_date, err = f.result()
            day_done[scan_date] += 1
            if r:
                day_results[scan_date].append(r)

            # 该天全部完成 → 写入DB
            if day_done[scan_date] >= day_total[scan_date]:
                saved = save_day_results(scan_date, day_results[scan_date])
                bi_cnt = sum(x.get('bi_count', 0) for x in day_results[scan_date])
                total_bi += bi_cnt
                days_completed += 1
                elapsed = time.time() - t_start
                eta = elapsed / days_completed * (len(dates) - days_completed)
                print(f"[{days_completed}/{len(dates)}] {scan_date} ✓ {saved}只 {bi_cnt}笔 ETA {eta/3600:.1f}h")
                del day_results[scan_date]  # 释放内存

    total_elapsed = time.time() - t_start
    print(f"\n=== 完成: {days_completed}天 {total_tasks}只次 {total_bi}笔 {total_elapsed/3600:.1f}h ===")
