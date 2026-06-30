"""
PP V1 批量模式测试 — 用回填的 K 线缓存替代逐只 SQL 查询
用法：python scripts/test_ppv1_batch.py --date 2024-01-02

预期加速：5500 次 sqlite3.connect → 0 次
"""
import sys, os, time, sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')


def test_old_way(scan_date):
    """原方式：逐只 detect_for_stock"""
    from scanners.pocket_pivot import detect_for_stock
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    stocks = conn.execute("""
        SELECT DISTINCT k.stock_code, b.name FROM daily_kline k 
        JOIN stock_basic b ON k.stock_code=b.stock_code WHERE k.date=?
    """, (scan_date,)).fetchall()
    conn.close()
    stocks = [(r['stock_code'], r['name']) for r in stocks]
    print(f'原方式: {len(stocks)} 只股票')
    
    t0 = time.time()
    signals = 0
    for i, (code, name) in enumerate(stocks):
        try:
            raw = detect_for_stock(code, scan_date)
            if raw:
                for sig in raw:
                    if isinstance(sig, dict) and sig.get('date', '') == scan_date:
                        signals += 1
        except:
            pass
        if i % 1000 == 0 and i > 0:
            print(f'  ... {i}/{len(stocks)} ({time.time()-t0:.0f}s)')
    elapsed = time.time() - t0
    print(f'原方式: {signals} 信号, {elapsed:.0f}s')
    return elapsed


def test_batch_way(scan_date):
    """批量方式：预加载 K 线 + RS，逐只 detect()"""
    from scanners.pocket_pivot import detect, load_params, get_rs
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    
    # 获取股票列表
    stocks = conn.execute("""
        SELECT DISTINCT k.stock_code, b.name FROM daily_kline k 
        JOIN stock_basic b ON k.stock_code=b.stock_code WHERE k.date=?
    """, (scan_date,)).fetchall()
    stocks = [(r['stock_code'], r['name']) for r in stocks]
    codes = [s[0] for s in stocks]
    print(f'批量方式: {len(stocks)} 只股票')
    
    t0 = time.time()
    
    # 一次性加载所有 K 线（最近 200 天）
    min_date = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=250)).strftime('%Y-%m-%d')
    kline_cache = defaultdict(list)
    for r in conn.execute("""
        SELECT stock_code, date, open, high, low, close, volume 
        FROM daily_kline WHERE date >= ? AND date <= ? ORDER BY stock_code, date
    """, (min_date, scan_date)).fetchall():
        kline_cache[r['stock_code']].append(dict(r))
    print(f'  K线加载: {len(kline_cache)} 只 ({time.time()-t0:.1f}s)')
    
    # 一次性加载所有 RS（匹配 detect_for_stock 的逻辑：取 ≤target_date 最近一条）
    rs_cache = {}
    for r in conn.execute("""
        SELECT stock_code, rps_20, rps_250 FROM stock_rs_daily 
        WHERE date<=? ORDER BY date DESC
    """, (scan_date,)).fetchall():
        if r['stock_code'] not in rs_cache:
            rs_cache[r['stock_code']] = {'rs_20': r['rps_20'], 'rs_250': r['rps_250']}
    print(f'  RS加载: {len(rs_cache)} 只 ({time.time()-t0:.1f}s)')
    conn.close()
    
    params = load_params()
    signals = 0
    for i, (code, name) in enumerate(stocks):
        try:
            klines = kline_cache.get(code, [])
            if len(klines) < 120:
                continue
            rs_info = rs_cache.get(code)
            raw = detect(klines, params, rs_info)
            if raw:
                for sig in raw:
                    if isinstance(sig, dict) and sig.get('date', '') == scan_date:
                        signals += 1
        except:
            pass
        if i % 1000 == 0 and i > 0:
            print(f'  ... {i}/{len(stocks)} ({time.time()-t0:.0f}s)')
    
    elapsed = time.time() - t0
    print(f'批量方式: {signals} 信号, {elapsed:.0f}s')
    return elapsed


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True, help='测试日期 YYYY-MM-DD')
    args = parser.parse_args()
    
    print(f'\n{"="*50}')
    print(f'PP V1 批量 vs 原方式 对比测试')
    print(f'测试日期: {args.date}')
    print(f'{"="*50}\n')
    
    t_old = test_old_way(args.date)
    print()
    t_batch = test_batch_way(args.date)
    print()
    print(f'{"="*50}')
    print(f'加速比: {t_old/t_batch:.1f}x  (原 {t_old:.0f}s → 新 {t_batch:.0f}s)')
    print(f'{"="*50}')
