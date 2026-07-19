"""
MW 信号逐日回填脚本 v3（对齐 backfill_all_signals_v2 的速度）
─────────────────────────────────────────────
用法:
  python scripts/backfill_mw.py --start 2026-04-01 --end 2026-07-17
  python scripts/backfill_mw.py --start 2016-01-01 --end 2026-07-17 --staggered

预加载策略（与 backfill_all 一致）:
  - bi: WHERE scan_date=? 精确匹配（走索引，快）
  - K线: 400 天全量预加载
  - RS/行业/共振/名称: 逐日刷新
  - 引擎兜底: scan_stock 中 ORDER BY scan_date DESC LIMIT 1 确保不丢笔
"""
import sys, os, time, argparse, sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))

DB = os.path.join(PROJECT, 'data', 'lixinger.db')


def get_trading_dates(start, end):
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date>=? AND date<=? ORDER BY date",
        (start, end)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def preload_all(scan_date):
    """并行预加载所有缓存（对齐 set_all_caches 的实现）"""
    import scanners.mw_signal as mw
    
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    
    stocks = mw.get_all_stocks(conn, scan_date)
    codes = [s for s in stocks]
    
    # 1. 缠论笔（并行，4 线程，按 scan_date 精确匹配 → 走索引）──
    mw._chanlun_cache = {}
    
    def _load_bi(chunk):
        import orjson
        c2 = sqlite3.connect(DB, timeout=30)
        c2.row_factory = sqlite3.Row
        result = {}
        ph = ','.join('?' * len(chunk))
        for row in c2.execute(
            f"SELECT stock_code, bi_json FROM chanlun_bi_json WHERE scan_date=? AND stock_code IN ({ph})",
            [scan_date] + chunk
        ).fetchall():
            try:
                result[(row['stock_code'], scan_date)] = orjson.loads(row['bi_json'])
            except:
                pass
        c2.close()
        return result
    
    chunk_sz = max(1, len(codes) // 4)
    chunks = [codes[i:i+chunk_sz] for i in range(0, len(codes), chunk_sz)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for fut in as_completed([pool.submit(_load_bi, c) for c in chunks]):
            mw._chanlun_cache.update(fut.result())
    
    # 2. K线 ──
    mw._kline_cache = defaultdict(list)
    kmin = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=400)).strftime('%Y-%m-%d')
    for r in conn.execute(
        "SELECT stock_code, date, open, high, low, close, volume, amount FROM daily_kline WHERE date>=? AND date<=? ORDER BY stock_code, date",
        (kmin, scan_date)
    ).fetchall():
        mw._kline_cache[r['stock_code']].append(dict(r))
    
    # 3. RS ──
    mw._rs_cache = {}
    for r in conn.execute("SELECT stock_code, rps_20, rps_250 FROM stock_rs_daily WHERE date=?", (scan_date,)).fetchall():
        mw._rs_cache[r['stock_code']] = (r['rps_20'], r['rps_250'])
    
    # 4. 行业成分 ──
    mw._idx_comp_cache = defaultdict(list)
    for r in conn.execute("SELECT stock_code, index_code FROM index_constituents").fetchall():
        mw._idx_comp_cache[r['stock_code']].append(r['index_code'])
    
    # 5. 指数 RS ──
    mw._idx_rs_cache = {}
    for r in conn.execute("SELECT stock_code, rs_20, rs_250 FROM index_rs_daily WHERE date=?", (scan_date,)).fetchall():
        mw._idx_rs_cache[r['stock_code']] = (r['rs_20'], r['rs_250'])
    
    # 6. 共振信号 ──
    mw._reso_cache = {}
    for r in conn.execute("SELECT stock_code, signals_json FROM pattern_scan_signals WHERE date=?", (scan_date,)).fetchall():
        mw._reso_cache[(r['stock_code'], scan_date)] = r['signals_json']
    
    # 7. 名称 ──
    mw._names_cache = {}
    for r in conn.execute("SELECT stock_code, name FROM stock_basic").fetchall():
        mw._names_cache[r['stock_code']] = r['name']
    
    mw._sell_existing_cache = {}
    
    conn.close()


def scan_single_date(scan_date, allow_fallback=False):
    """扫描单个日期"""
    import scanners.mw_signal as mw
    from scanners.mw_signal import run_scan
    
    # 重置兜底计数器 + 设置兜底策略
    mw._fallback_log.clear()
    for k in mw._fallback_stats:
        mw._fallback_stats[k] = 0
    mw._disable_fallback = not allow_fallback  # 默认禁止兜底（等同实盘）
    
    # 预加载
    t0 = time.time()
    preload_all(scan_date)
    preload_t = time.time() - t0
    
    # 扫描
    t1 = time.time()
    run_scan(scan_date, silent=True)
    scan_t = time.time() - t1
    
    # 收集兜底统计
    fallback_sql = mw._fallback_stats['fallback_sql']
    fallback_live = mw._fallback_stats['fallback_live']
    total_scanned = mw._fallback_stats['total_scanned']
    skipped = len([e for e in mw._fallback_log if e[2] == 'skipped'])
    
    # 统计信号
    conn = sqlite3.connect(DB)
    b1 = conn.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b1_date=?", (scan_date,)).fetchone()[0]
    b2 = conn.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b2_date=?", (scan_date,)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE scan_date=?", (scan_date,)).fetchone()[0]
    conn.close()
    
    return total, b1, b2, preload_t, scan_t, fallback_sql, fallback_live, total_scanned, skipped


def run_staggered(allow_fallback=False):
    """锚点模式：6 个日期覆盖 2016-2026"""
    anchors = [
        ('2017-06-30', '2014-10 ~ 2017-06'),
        ('2019-06-30', '2016-10 ~ 2019-06'),
        ('2021-06-30', '2018-10 ~ 2021-06'),
        ('2023-06-30', '2020-10 ~ 2023-06'),
        ('2025-06-30', '2022-10 ~ 2025-06'),
        ('2026-07-17', '2023-10 ~ 2026-07'),
    ]
    
    fb_label = '允许兜底(有未来信息)' if allow_fallback else '0%兜底(等同实盘)'
    print(f"锚点模式: {len(anchors)} 个锚点覆盖 10 年 | {fb_label}")
    header = f"{'锚点':<14} {'覆盖范围':<22} {'预加载':>7} {'扫描':>7} {'B1':>6} {'B2':>6} {'跳过':>6}"
    print(header)
    print("-" * len(header))
    
    t_total = time.time()
    grand_b1 = grand_b2 = grand_skipped = 0
    for anchor, coverage in anchors:
        total, b1, b2, pre_t, scan_t, fb_sql, fb_live, scanned, skipped = scan_single_date(anchor, allow_fallback)
        grand_b1 += b1
        grand_b2 += b2
        grand_skipped += skipped
        print(f"{anchor:<14} {coverage:<22} {pre_t:>5.0f}s {scan_t:>5.0f}s {b1:>6} {b2:>6} {skipped:>6}")
    
    tt = time.time() - t_total
    print("-" * len(header))
    print(f"总计: {tt:.0f}s ({tt/60:.1f}min), B1={grand_b1}, B2={grand_b2}")
    
    if not allow_fallback:
        if grand_skipped > 0:
            print(f"\n⚡ 跳过 {grand_skipped} 只股票（预加载缓存未命中，兜底已禁止）")
            print(f"   这等同于实盘行为：当天没有笔数据的股票，MW 引擎不会为它产生信号。")
        else:
            print(f"\n✅ 所有股票笔数据来自预加载缓存，0%兜底，完全等同实盘。")


def run_sequential(dates, allow_fallback=False):
    """逐日模式"""
    total_dates = len(dates)
    fb_label = '允许兜底' if allow_fallback else '0%兜底'
    print(f"逐日模式: {total_dates} 个交易日 ({dates[0]} ~ {dates[-1]}) | {fb_label}")
    print(f"{'日期':<12} {'预加载':>6} {'扫描':>6} {'B1':>6} {'B2':>6} {'总信号':>7} {'跳过':>6} {'累计':>10} {'ETA':>8}")
    print("-" * 83)
    
    t_total = time.time()
    grand_total = grand_b1 = grand_b2 = grand_skipped = 0
    errors = []
    
    for i, d in enumerate(dates):
        try:
            total, b1, b2, pre_t, scan_t, fb_sql, fb_live, scanned, skipped = scan_single_date(d, allow_fallback)
            grand_total += total
            grand_b1 += b1
            grand_b2 += b2
            grand_skipped += skipped
            
            elapsed = pre_t + scan_t
            et = time.time() - t_total
            eta = et / (i + 1) * (total_dates - i - 1) if i > 0 else 0
            eta_str = f"{eta/3600:.1f}h" if eta > 3600 else f"{eta/60:.0f}m"
            print(f"{d:<12} {pre_t:>4.0f}s {scan_t:>4.0f}s {b1:>6} {b2:>6} {total:>7} {skipped:>6} {grand_total:>10} {eta_str:>8}")
        except Exception as e:
            errors.append((d, str(e)[:100]))
            print(f"{d:<12} ✗ {e}")
    
    n_ok = total_dates - len(errors)
    tt = time.time() - t_total
    print("-" * 83)
    print(f"完成: {n_ok}/{total_dates} 天, "
          f"累计 {grand_total} 信号 (B1={grand_b1}, B2={grand_b2}), "
          f"耗时 {tt:.0f}s ({tt/3600:.1f}h)")
    
    if not allow_fallback:
        if grand_skipped > 0:
            print(f"\n⚡ 跳过 {grand_skipped} 只股票（预加载缓存未命中）")
        else:
            print(f"\n✅ 所有股票笔数据来自预加载缓存，0%兜底，完全等同实盘。")
    else:
        print(f"\n⚠️ 兜底触发 {grand_skipped} 次，含未来信息偏差。")
    
    if errors:
        print(f"\n失败 {len(errors)} 天:")
        for d, e in errors[:10]:
            print(f"  {d}: {e}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MW 信号逐日回填 v3')
    parser.add_argument('--start', type=str, required=True)
    parser.add_argument('--end', type=str, required=True)
    parser.add_argument('--staggered', action='store_true')
    parser.add_argument('--allow-fallback', action='store_true',
                        help='允许兜底（ORDER BY DESC LIMIT 1 取最新笔，含未来信息偏差）')
    args = parser.parse_args()
    
    if args.allow_fallback:
        print('⚠️ 兜底模式已启用：预加载未命中的股票将用 ORDER BY scan_date DESC LIMIT 1 取最新笔数据。')
        print('   回填结果可能含有未来信息偏差，实盘(daily_update)中不会触发此路径。\n')
    else:
        print('🔒 默认 0%% 兜底模式：预加载缓存未命中的股票直接跳过，等同实盘行为。\n')
    
    if args.staggered:
        run_staggered(allow_fallback=args.allow_fallback)
    else:
        dates = get_trading_dates(args.start, args.end)
        if not dates:
            print("区间无交易日")
            sys.exit(0)
        run_sequential(dates, allow_fallback=args.allow_fallback)
