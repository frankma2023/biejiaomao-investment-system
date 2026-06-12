"""
缠论笔批量扫描 性能测试
用法：python scripts/bench_chanlun.py --days 5 --workers 4
"""
import sys, os, time, argparse, sqlite3, multiprocessing
from datetime import datetime, timedelta
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

def get_candidate_stocks(date, min_amount=50_000_000):
    """获取当天正常交易且有成交额的股票"""
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    stocks = db.execute("""
        SELECT DISTINCT k.stock_code, b.name
        FROM daily_kline k JOIN stock_basic b ON k.stock_code=b.stock_code
        WHERE b.listing_status='normally_listed' AND b.name NOT LIKE '%ST%'
        AND k.date=? AND k.amount>=?
    """, (date, min_amount)).fetchall()
    db.close()
    return [(r['stock_code'], r['name']) for r in stocks]

def scan_one_stock(args):
    """单只股票缠论扫描（worker进程内执行）"""
    code, name, date = args
    t0 = time.time()
    try:
        from scanners.chanlun import analyze
        result = analyze(code, "D", 500, data_mode="stock")
        elapsed = time.time() - t0
        if result and not result.get('error') and result.get('bi_list'):
            return (code, len(result['bi_list']), elapsed, None)
        return (code, 0, elapsed, 'no_bi')
    except Exception as e:
        return (code, 0, time.time() - t0, str(e)[:100])

def scan_one_day(date, workers=4):
    """单日全市场扫描"""
    stocks = get_candidate_stocks(date)
    print(f"  [{date}] {len(stocks)} stocks, workers={workers}")

    args_list = [(code, name, date) for code, name in stocks]
    
    t0 = time.time()
    results = []
    
    if workers == 1:
        for args in args_list:
            results.append(scan_one_stock(args))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(scan_one_stock, a): a for a in args_list}
            for f in as_completed(futures):
                results.append(f.result())
    
    elapsed = time.time() - t0
    bi_count = sum(1 for r in results if r[1] > 0)
    errors = sum(1 for r in results if r[3])
    
    return {
        'date': date,
        'stocks': len(stocks),
        'with_bi': bi_count,
        'errors': errors,
        'elapsed': elapsed,
        'per_stock_ms': elapsed / max(len(stocks), 1) * 1000,
        'eta_per_day': elapsed,
    }

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='缠论性能基准测试')
    parser.add_argument('--days', type=int, default=5, help='测试天数')
    parser.add_argument('--workers', type=int, default=4, help='并行数')
    parser.add_argument('--start', default='2026-06-01', help='起始日期')
    args = parser.parse_args()

    dates = get_trading_dates(args.start, '2026-06-11')[:args.days]
    print(f"=== 缠论性能测试 ===")
    print(f"日期范围: {dates[0]} ~ {dates[-1]} ({len(dates)} 天)")
    print(f"并行数: {args.workers}")
    print()

    results = []
    total_stocks = 0
    total_bi = 0
    total_time = 0

    for date in dates:
        r = scan_one_day(date, args.workers)
        results.append(r)
        total_stocks += r['stocks']
        total_bi += r['with_bi']
        total_time += r['elapsed']
        print(f"  {r['date']}: {r['stocks']}只, {r['with_bi']}有笔, "
              f"{r['elapsed']:.1f}s ({r['per_stock_ms']:.0f}ms/只)")

    print()
    print("=== 汇总 ===")
    avg_per_day = total_time / len(results)
    print(f"日均耗时: {avg_per_day:.1f}s ({avg_per_day/60:.1f}min)")
    print(f"日均股票: {total_stocks/len(results):.0f}只")
    print(f"千只耗时: {avg_per_day/(total_stocks/len(results))*1000:.1f}s/千只")

    # 估算 2016~2026 全量
    trading_days = get_trading_dates('2016-01-01', '2026-06-11')
    print(f"\n2016-01-01 ~ 今 共 {len(trading_days)} 个交易日")
    total_hours = avg_per_day * len(trading_days) / 3600
    print(f"预估总耗时: {total_hours:.0f} 小时 ({total_hours/24:.1f} 天)")
    print(f"使用 {args.workers} 进程穷跑")
