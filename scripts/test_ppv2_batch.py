"""
PP V2 缓存模式测试 — 用回填缓存替代独立 SQL 加载
用法：python scripts/test_ppv2_batch.py --date 2024-08-08

预期加速：PP V2 的 load_klines_batch + load_rs_batch ≈ 30s → 0s
"""
import sys, os, time, sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')


def test_old_way(scan_date):
    """原方式：ppv2_scan 自己加载 K 线和 RS"""
    from scanners.pocket_pivot_v2 import scan_date as ppv2_scan
    t0 = time.time()
    signals = ppv2_scan(scan_date)
    elapsed = time.time() - t0
    print(f'原方式: {len(signals)} 信号, {elapsed:.0f}s')
    return elapsed


def test_cached_way(scan_date):
    """缓存方式：预加载 K 线 + RS 到模块缓存，然后调 scan_date
    
    模拟回填脚本 set_all_caches 的效果。
    PP V2 的 scan_date() 内部调用 load_klines_batch / load_rs_batch，
    这些函数自己查 SQL，不走模块级缓存。
    所以这里不修改 PP V2 内部逻辑，而是直接模拟回填的预加载。
    
    要真正让 PP V2 吃缓存，需改 pocket_pivot_v2.py 的 scan_date()，
    让它优先读模块级缓存。这个测试先对比：预加载开销。
    """
    import scanners.pocket_pivot_v2 as ppv2
    
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    
    # 模拟 set_all_caches 的 K 线加载
    min_date = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=400)).strftime('%Y-%m-%d')
    kline_cache = defaultdict(list)
    kt0 = time.time()
    for r in conn.execute(
        "SELECT stock_code, date, open, high, low, close, volume, amount FROM daily_kline WHERE date>=? AND date<=? ORDER BY stock_code, date",
        (min_date, scan_date)
    ).fetchall():
        kline_cache[r['stock_code']].append(dict(r))
    kt = time.time() - kt0
    print(f'  K线预加载: {len(kline_cache)} 只, {kt:.1f}s')
    
    # 模拟 set_all_caches 的 RS 加载
    rs_cache = {}
    rt0 = time.time()
    for r in conn.execute(
        "SELECT stock_code, rps_20, rps_250, date FROM stock_rs_daily WHERE date<=? ORDER BY date DESC",
        (scan_date,)
    ).fetchall():
        if r['stock_code'] not in rs_cache:
            rs_cache[r['stock_code']] = (r['rps_20'], r['rps_250'])
    rt = time.time() - rt0
    print(f'  RS预加载: {len(rs_cache)} 只, {rt:.1f}s')
    
    # 模拟 set_all_caches 的缠论笔加载
    chanlun_cache = {}
    ct0 = time.time()
    import orjson
    for r in conn.execute(
        "SELECT stock_code, bi_json FROM chanlun_bi_json WHERE scan_date=?",
        (scan_date,)
    ).fetchall():
        try:
            chanlun_cache[(r['stock_code'], scan_date)] = orjson.loads(r['bi_json'])
        except:
            pass
    ct = time.time() - ct0
    print(f'  缠论预加载: {len(chanlun_cache)} 只, {ct:.1f}s')
    
    conn.close()
    
    # 注入缓存到模块（模拟回填的 set_all_caches）
    import scanners.mw_signal as mw
    mw._kline_cache = kline_cache
    mw._rs_cache = rs_cache
    mw._chanlun_cache = chanlun_cache
    
    # 但实际上 ppv2.scan_date() 不吃这些缓存...
    # 真正要做的是给 ppv2 加缓存支持。先跑一下对比预加载 vs 原来的耗时
    print(f'\n总结: 预加载 {kt:.1f}s + {rt:.1f}s + {ct:.1f}s = {kt+rt+ct:.1f}s 可以在主流程复用到 PP V2')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True, help='测试日期 YYYY-MM-DD')
    args = parser.parse_args()
    
    print(f'\n{"="*60}')
    print(f'PP V2 原方式耗时测试')
    print(f'测试日期: {args.date}')
    print(f'{"="*60}\n')
    
    t_old = test_old_way(args.date)
    print()
    test_cached_way(args.date)
    print()
    print(f'{"="*60}')
    print(f'PP V2 耗时: {t_old:.0f}s')
    print(f'回填已有 K线+RS+缠论缓存，如果 PP V2 吃缓存可省 {t_old:.0f}s')
    print(f'{"="*60}')
