"""
缠论笔全市场批量回填 v1.0
将全市场股票的缠论笔数据从指定起始日期计算至今

依赖：src/scanners/chanlun.py、chanlun_scan.py
输出：chanlun_scan_daily 表

用法：
    python scripts/backfill_chanlun.py --start 2016-01-01 --end 2016-03-31 --workers 8
    python scripts/backfill_chanlun.py --quarter 2016Q1 --incremental --workers 8
    python scripts/backfill_chanlun.py --incremental --workers 8  # 全量

性能参考：
    8进程: ~4000只/天, ~90秒/天, 全量2535天约64小时
"""
import sys, os, time, argparse, sqlite3, traceback
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT DISTINCT scan_date FROM chanlun_scan_daily").fetchall()
    db.close()
    return set(r[0] for r in rows)


def quarter_to_range(q):
    import calendar
    year = int(q[:4]); qnum = int(q[-1])
    sm = (qnum - 1) * 3 + 1; em = sm + 2
    ld = calendar.monthrange(year, em)[1]
    return f"{year}-{sm:02d}-01", f"{year}-{em:02d}-{ld}"


# ══════════════════════════════════════════════════════════
# Worker（进程内执行）
# ══════════════════════════════════════════════════════════

def scan_stock_worker(args):
    """单只股票扫描（worker 进程内调用），返回 scan_stock 结果或 (None, err_msg)"""
    code, scan_date = args
    try:
        from scanners.chanlun_scan import scan_stock
        r = scan_stock(code, scan_date)
        return (r, None)
    except Exception as e:
        return (None, f"{code}: {str(e)[:120]}")


def save_day_results(db_path, scan_date, results):
    """将一天的结果写入 chanlun_scan_daily（带重试，处理并发锁）"""
    import time as _time
    for attempt in range(5):
        try:
            db = sqlite3.connect(db_path, timeout=30)
            try:
                db.execute("DELETE FROM chanlun_scan_daily WHERE scan_date=?", (scan_date,))
                
                rows = []
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                for r in results:
                    if r is None:
                        continue
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
                    db.executemany("""
                        INSERT INTO chanlun_scan_daily
                        (scan_date, stock_code, stock_name, bi_count, zs_count, segment_count,
                         latest_bi_dir, latest_bi_power, divergence_count, latest_div_type,
                         trade_signal_count, latest_trade_type, latest_trade_side, latest_trade_price,
                         resonance_strength, created_at, bi_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, rows)
                    db.commit()
                return len(rows)
            finally:
                db.close()
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < 4:
                _time.sleep(3 * (attempt + 1))
            else:
                raise


def get_candidates(date):
    """获取当天可扫描的股票列表"""
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    stocks = db.execute("""
        SELECT DISTINCT k.stock_code, b.name
        FROM daily_kline k JOIN stock_basic b ON k.stock_code=b.stock_code
        WHERE b.listing_status='normally_listed' AND b.name NOT LIKE '%ST%'
        AND k.date=?
    """, (date,)).fetchall()
    db.close()
    return [(r['stock_code'], r['name']) for r in stocks]


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='缠论批量回填')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--start', help='起始日期（需同时指定 --end）')
    group.add_argument('--quarter', help='按季度，如 2016Q1')
    parser.add_argument('--end', help='结束日期')
    parser.add_argument('--workers', type=int, default=8, help='并行进程数（默认8）')
    parser.add_argument('--incremental', action='store_true', help='增量模式')
    args = parser.parse_args()

    if args.quarter:
        start_date, end_date = quarter_to_range(args.quarter)
    else:
        start_date, end_date = args.start, args.end

    all_dates = get_trading_dates(start_date, end_date)
    if not all_dates:
        print(f"区间 {start_date} ~ {end_date} 无交易日")
        sys.exit(0)

    # 增量过滤
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
    print()

    t_start = time.time()
    completed = 0
    total_stocks = 0
    total_bi = 0
    errors = []

    for date in dates:
        t0 = time.time()
        stocks = get_candidates(date)
        
        if completed > 0:
            eta = (time.time() - t_start) / completed * (len(dates) - completed)
            eta_str = f"ETA {eta/3600:.1f}h"
        else:
            eta_str = ""

        print(f"[{completed+1}/{len(dates)}] {date} ({len(stocks)}只) ...", end=' ', flush=True)

        # 并行扫描
        task_args = [(code, date) for code, name in stocks]
        results = []
        if workers == 1 or len(stocks) < 10:
            for a in task_args:
                results.append(scan_stock_worker(a))
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(scan_stock_worker, a): a for a in task_args}
                for f in as_completed(futures):
                    results.append(f.result())

        # 保存结果
        # 解析结果：成功返回 (dict, None)，失败返回 (None, err_msg)
        saved = save_day_results(DB, date, [r[0] for r in results])
        day_bi = sum(r[0].get('bi_count', 0) for r in results if r[0])
        day_errs = sum(1 for r in results if r[1])
        
        elapsed = time.time() - t0
        completed += 1
        total_stocks += len(stocks)
        total_bi += day_bi

        print(f"✓ 保存{saved}只, {day_bi}笔 ({elapsed:.1f}s) {eta_str}")

        if day_errs:
            err_samples = [r[1] for r in results if r[1]][:3]
            print(f"  ⚠ {day_errs}错: {'; '.join(err_samples)}")

    # 汇总
    total_elapsed = time.time() - t_start
    print(f"\n=== 完成 ===")
    print(f"成功: {completed}/{len(dates)} 天")
    print(f"总股票次: {total_stocks}")
    print(f"总笔数: {total_bi}")
    print(f"总耗时: {total_elapsed/3600:.1f}h ({total_elapsed/60:.0f}min)")
