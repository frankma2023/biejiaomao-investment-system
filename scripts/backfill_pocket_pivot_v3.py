"""
口袋支点V3 批量补扫 — 进程内版（无子进程问题）
"""
import sys, os, time, argparse, sqlite3
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed

# 确保 src 在路径中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'scanners'))

DB = os.path.join(os.path.dirname(__file__), '..', 'data', 'lixinger.db')

def get_trading_dates(start, end):
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT DISTINCT date FROM daily_kline WHERE date>=? AND date<=? AND stock_code='000001' ORDER BY date",
                      (start, end)).fetchall()
    db.close()
    return [r[0] for r in rows]

def scan_one_day(date):
    """进程内扫描，直接导入引擎"""
    t0 = time.time()
    try:
        from pocket_pivot_v2 import scan_date, save_to_db, CFG
        CFG['max_distribution_days'] = 999  # backfill mode
        signals = scan_date(date)
        if signals:
            save_to_db(signals)
        elapsed = time.time() - t0
        return (date, len(signals), elapsed, None)
    except Exception as e:
        elapsed = time.time() - t0
        return (date, 0, elapsed, str(e)[:200])

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2023-06-01')
    parser.add_argument('--end', default='2026-06-05')
    parser.add_argument('--incremental', action='store_true')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    
    dates = get_trading_dates(args.start, args.end)
    
    if args.incremental:
        db = sqlite3.connect(DB)
        existing = set(r[0] for r in db.execute("SELECT DISTINCT date FROM pocket_pivot_daily").fetchall())
        db.close()
        dates = [d for d in dates if d not in existing]
        print(f"增量: 跳过 {len(existing)} 天, 剩余 {len(dates)} 天")
    else:
        print(f"全量: {len(dates)} 天")
    
    if not dates:
        print("无需扫描"); sys.exit(0)
    
    print(f"进程数: {args.workers}")
    t_start = time.time()
    completed = 0; total_signals = 0
    
    if args.workers == 1:
        for date in dates:
            date, count, elapsed, err = scan_one_day(date)
            completed += 1; total_signals += count
            status = f"✓ {count:>3}个" if err is None else f"✗ {err[:60]}"
            eta = (time.time() - t_start) / completed * (len(dates) - completed)
            print(f"[{completed}/{len(dates)}] {date} {status} ({elapsed:.1f}s) ETA {eta/60:.0f}min")
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(scan_one_day, d): d for d in dates}
            for future in as_completed(futures):
                date, count, elapsed, err = future.result()
                completed += 1; total_signals += count
                status = f"✓ {count:>3}个" if err is None else f"✗ {err[:60]}"
                eta = (time.time() - t_start) / completed * (len(dates) - completed)
                print(f"[{completed}/{len(dates)}] {date} {status} ({elapsed:.1f}s) ETA {eta/60:.0f}min")
    
    print(f"\n完成! {len(dates)}天, {total_signals}个信号, {(time.time()-t_start)/60:.1f}分钟")
