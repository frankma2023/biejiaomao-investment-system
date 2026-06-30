"""
补足缠论数据缺口：对已有 chanlun_scan_daily 数据的日期，补算缺失的股票
用法：python scripts/backfill_chanlun_gap.py --start 2017-01-01 --end 2026-06-11 --workers 8
"""
import sys, os, time, argparse, sqlite3
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))

DB = os.path.join(PROJECT, 'data', 'lixinger.db')


def get_dates_with_data(start, end):
    """获取区间内已有缠论数据的日期"""
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT DISTINCT scan_date FROM chanlun_scan_daily WHERE scan_date>=? AND scan_date<=? ORDER BY scan_date",
        (start, end)
    ).fetchall()
    db.close()
    return [r[0] for r in rows]


def get_missing_stocks(date):
    """获取当天有 K线但无缠论数据的股票（即被过滤误杀的）"""
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    stocks = db.execute("""
        SELECT DISTINCT k.stock_code, b.name
        FROM daily_kline k
        JOIN stock_basic b ON k.stock_code = b.stock_code
        WHERE k.date = ?
          AND k.stock_code NOT IN (
            SELECT stock_code FROM chanlun_scan_daily WHERE scan_date = ?
          )
    """, (date, date)).fetchall()
    db.close()
    return [(r['stock_code'], r['name']) for r in stocks]


def scan_stock_worker(args):
    code, scan_date = args
    try:
        from scanners.chanlun_scan import scan_stock
        r = scan_stock(code, scan_date)
        return (r, None)
    except Exception as e:
        return (None, f"{code}: {str(e)[:120]}")


def save_gap_results(db_path, scan_date, results):
    """追加写入（不删已有数据）"""
    import time as _time
    for attempt in range(5):
        try:
            db = sqlite3.connect(db_path, timeout=30)
            try:
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                rows = []; bi_rows = []
                for r, err in results:
                    if r is None: continue
                    rows.append((scan_date, r['stock_code'], '',
                        r.get('bi_count',0), r.get('zs_count',0), r.get('segment_count',0),
                        r.get('latest_bi_dir',''), r.get('latest_bi_power',0),
                        r.get('divergence_count',0), r.get('latest_div_type',''),
                        r.get('trade_signal_count',0), r.get('latest_trade_type',''),
                        r.get('latest_trade_side',''), r.get('latest_trade_price',0),
                        r.get('resonance_strength',''), now))
                    bi = r.get('bi_json')
                    if bi:
                        bi_rows.append((r['stock_code'], scan_date, bi))
                if rows:
                    db.executemany("""INSERT OR IGNORE INTO chanlun_scan_daily
                        (scan_date,stock_code,stock_name,bi_count,zs_count,segment_count,
                         latest_bi_dir,latest_bi_power,divergence_count,latest_div_type,
                         trade_signal_count,latest_trade_type,latest_trade_side,latest_trade_price,
                         resonance_strength,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
                if bi_rows:
                    db.executemany("INSERT OR IGNORE INTO chanlun_bi_json(stock_code,scan_date,bi_json) VALUES(?,?,?)", bi_rows)
                db.commit()
                return len(rows)
            finally:
                db.close()
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < 4:
                _time.sleep(3 * (attempt + 1))
            else:
                raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='补足缠论数据缺口')
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--min-gap', type=int, default=10, help='缺口少于N只的日期跳过')
    args = parser.parse_args()

    dates = get_dates_with_data(args.start, args.end)
    print(f"区间 {args.start}~{args.end}: {len(dates)} 天有缠论数据")
    print(f"并行: {args.workers} 进程, 最小缺口: {args.min_gap} 只")
    print()

    t_start = time.time()
    completed = 0
    total_filled = 0
    total_bi = 0

    for date in dates:
        missing = get_missing_stocks(date)
        if len(missing) < args.min_gap:
            continue

        if completed > 0:
            eta = (time.time() - t_start) / completed * (len(dates) - completed)
            eta_str = f"ETA {eta/3600:.1f}h"
        else:
            eta_str = ""

        print(f"[{completed+1}] {date}: 缺口{len(missing)}只 ...", end=' ', flush=True)
        t0 = time.time()

        task_args = [(code, date) for code, name in missing]
        results = []
        if args.workers == 1 or len(missing) < 10:
            for a in task_args:
                results.append(scan_stock_worker(a))
        else:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(scan_stock_worker, a): a for a in task_args}
                for f in as_completed(futures):
                    results.append(f.result())

        saved = save_gap_results(DB, date, results)
        day_bi = sum(r[0].get('bi_count', 0) for r in results if r[0])
        day_errs = sum(1 for r in results if r[1])

        elapsed = time.time() - t0
        completed += 1
        total_filled += saved
        total_bi += day_bi

        print(f"✓ 补{saved}只, {day_bi}笔 ({elapsed:.1f}s) {eta_str}")
        if day_errs:
            err_samples = [r[1] for r in results if r[1]][:3]
            print(f"  ⚠ {day_errs}错: {'; '.join(err_samples)}")

    total_elapsed = time.time() - t_start
    print(f"\n=== 完成 ===")
    print(f"处理: {completed} 天")
    print(f"补足: {total_filled} 只股票, {total_bi} 笔")
    print(f"总耗时: {total_elapsed/3600:.1f}h ({total_elapsed/60:.0f}min)")
