"""
MW 信号逐日回填脚本 v3
─────────────────────
逐日调用 run_scan，模拟 daily_update 的实盘行为。默认 0% 兜底，等同实盘。

用法:
  python scripts/backfill_mw.py --start 2026-04-01 --end 2026-07-17
  python scripts/backfill_mw.py --start 2016-01-01 --end 2026-07-17 --staggered
  python scripts/backfill_mw.py --start 2016-01-01 --end 2026-07-17 --allow-fallback

── 设计决策 ──

1. 预加载 bi 用 WHERE scan_date=? 精确匹配（走 PK 索引，毫秒级）。
   不用 GROUP BY MAX(scan_date) —— chanlun_bi_json 1068 万行，全表聚合需数分钟。

2. 默认 0% 兜底（等同实盘）。预加载未命中的股票直接跳过，不触发引擎内部的
   ORDER BY scan_date DESC LIMIT 1 兜底。加 --allow-fallback 可启用兜底，
   但会引入未来信息偏差（用最新笔数据回看历史走势）。

3. bi 加载线程池（3 线程 + 60s 超时 + 3 次重试 + finally 关连接）。
   DB 连续写入数小时后 WAL 膨胀，线程可能因锁竞争超时。
   超时后返回空 dict → bi=0 → 该日期不出信号（等同于当天 bi 数据缺失）。

4. 哨兵防二次预加载：bi=0 时缓存为空 {}，run_scan 内部的 `if not _chanlun_cache`
   会触发 GROUP BY MAX(scan_date) 慢查询（扫 1068 万行，看起来像卡死）。
   解决：bi=0 时往缓存塞 ('__loaded__', scan_date) 哨兵，使 not 判为 False，
   跳过二次预加载。scan_stock 用 (code, scan_date) 查缓存，'__loaded__'
   不匹配任何 6 位股票代码，不受影响。

5. K 线预加载 800 天。锚点模式下每锚点只扫一次，需长窗口防止 H 检测的
   pre_rise 截断（需前 60 天数据）。逐日模式下每次窗口向前滑动一天，
   累计覆盖无缺口。

6. 行业 RS 兜底：mw_signal.py scan_stock 中，股票若不归属 L2/L1 指数，
   回退到中证全指 000985（覆盖所有 A 股，index_rs_daily 有全量历史数据）。
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


def preload_all(scan_date, verbose=True):
    """
    并行预加载所有缓存（对齐 set_all_caches 的实现）。
    返回 (n_bi_loaded, n_bi_failed) — 失败的股票被跳过（0%兜底等同实盘）。
    """
    import scanners.mw_signal as mw
    
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    
    try:
        stocks = mw.get_all_stocks(conn, scan_date)
        codes = [s for s in stocks]
    except Exception:
        conn.close()
        raise
    n_stocks = len(codes)
    if verbose:
        print(f'  预加载 {n_stocks} 只...', end=' ', flush=True)
    t0 = time.time()
    
    # 1. 缠论笔（并行，3 线程减少锁竞争，3 次重试）──
    mw._chanlun_cache = {}
    
    def _load_bi(chunk):
        import orjson
        for attempt in range(3):
            c2 = None
            try:
                c2 = sqlite3.connect(DB, timeout=30)
                c2.row_factory = sqlite3.Row
                c2.execute("PRAGMA busy_timeout=30000")
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
                return result
            except Exception:
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
            finally:
                if c2:
                    try: c2.close()
                    except: pass
        return {}
    
    chunk_sz = max(1, n_stocks // 3)
    chunks = [codes[i:i+chunk_sz] for i in range(0, n_stocks, chunk_sz)]
    
    failed_chunks = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_load_bi, c): i for i, c in enumerate(chunks)}
        for fut in as_completed(futures):
            try:
                result = fut.result(timeout=60)  # 60s 超时防卡死
                mw._chanlun_cache.update(result)
            except Exception:
                failed_chunks.append(futures[fut])
    
    n_bi = len(mw._chanlun_cache)
    n_bi_failed = sum(len(chunks[i]) for i in failed_chunks) if failed_chunks else 0
    # ── 哨兵：防止 run_scan 二次预加载 ──
    # bi=0 时缓存为 {}，run_scan 的 not _chanlun_cache 为 True，
    # 会触发 GROUP BY MAX(scan_date) 慢查询扫 1068 万行（看起来像卡死）。
    # 塞一个哨兵让缓存非空，跳过二次预加载。
    # '__loaded__' 不匹配任何 6 位股票代码，scan_stock 不受影响。
    if not mw._chanlun_cache:
        mw._chanlun_cache[('__loaded__', scan_date)] = []
    if verbose:
        print(f'bi={n_bi}', end=' ', flush=True)
    
    # 2. K线（800 天确保 H 检测窗口完整）──
    # H 检测需前 60 天数据算 pre_rise，加上 H 到 B1 可能跨 1~2 年。
    # 800 日历天 ≈ 570 交易日，可靠窗口 = 570 - 60 = 510 天 ≈ 24 个月。
    # 6 个锚点 × 24 月窗口 × 2 年间距 → 充足重叠，无覆盖缺口。
    mw._kline_cache = defaultdict(list)
    kmin = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=800)).strftime('%Y-%m-%d')
    for r in conn.execute(
        "SELECT stock_code, date, open, high, low, close, volume, amount FROM daily_kline WHERE date>=? AND date<=? ORDER BY stock_code, date",
        (kmin, scan_date)
    ).fetchall():
        mw._kline_cache[r['stock_code']].append(dict(r))
    n_kline = len(mw._kline_cache)
    if verbose:
        print(f'K={n_kline}', end=' ', flush=True)
    
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
    
    if verbose:
        print(f'({time.time()-t0:.0f}s) 失败={n_bi_failed}', flush=True)
    
    return n_bi, n_bi_failed


def scan_single_date(scan_date, allow_fallback=False):
    """扫描单个日期。返回 (total, b1, b2, pre_t, scan_t, skipped_bi, skipped_fb, bi_failed)"""
    import scanners.mw_signal as mw
    from scanners.mw_signal import run_scan
    
    # 重置计数器 + 设置兜底策略
    mw._fallback_log.clear()
    for k in mw._fallback_stats:
        mw._fallback_stats[k] = 0
    mw._disable_fallback = not allow_fallback
    
    # 预加载
    t0 = time.time()
    n_bi, n_bi_failed = preload_all(scan_date)
    preload_t = time.time() - t0
    
    # 扫描
    t1 = time.time()
    run_scan(scan_date, silent=True)
    scan_t = time.time() - t1
    
    # 收集统计
    skipped_fb = len([e for e in mw._fallback_log if e[2] == 'skipped'])
    total_scanned = mw._fallback_stats['total_scanned']
    # bi 预加载失败的股票数（它们在引擎里因为没有 bi 数据会被跳过）
    skipped_bi = n_bi_failed
    
    # 统计信号
    conn = sqlite3.connect(DB)
    b1 = conn.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b1_date=?", (scan_date,)).fetchone()[0]
    b2 = conn.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b2_date=?", (scan_date,)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE scan_date=?", (scan_date,)).fetchone()[0]
    conn.close()
    
    return total, b1, b2, preload_t, scan_t, skipped_bi, skipped_fb, n_bi_failed


def run_staggered(allow_fallback=False):
    """锚点模式：6 个日期覆盖 2016-2026"""
    anchors = [
        ('2017-06-30', '2015-04 ~ 2017-06'),
        ('2019-06-30', '2017-04 ~ 2019-06'),
        ('2021-06-30', '2019-04 ~ 2021-06'),
        ('2023-06-30', '2021-04 ~ 2023-06'),
        ('2025-06-30', '2023-04 ~ 2025-06'),
        ('2026-07-17', '2024-05 ~ 2026-07'),
    ]
    
    fb_label = '允许兜底(有未来信息)' if allow_fallback else '0%兜底(等同实盘)'
    print(f"锚点模式: {len(anchors)} 个锚点覆盖 10 年 | {fb_label}")
    header = f"{'锚点':<14} {'覆盖范围':<22} {'预加载':>7} {'扫描':>7} {'B1':>6} {'B2':>6} {'跳过':>6}"
    print(header)
    print("-" * len(header))
    
    t_total = time.time()
    grand_b1 = grand_b2 = grand_skipped = grand_failed = 0
    for anchor, coverage in anchors:
        total, b1, b2, pre_t, scan_t, skipped_bi, skipped_fb, bi_failed = scan_single_date(anchor, allow_fallback)
        grand_b1 += b1
        grand_b2 += b2
        grand_skipped += skipped_bi + skipped_fb
        grand_failed += bi_failed
        skip_str = f"{skipped_bi+skipped_fb}" if skipped_bi+skipped_fb > 0 else "0"
        warn = " ⚡" if bi_failed > 0 else ""
        print(f"{anchor:<14} {coverage:<22} {pre_t:>5.0f}s {scan_t:>5.0f}s {b1:>6} {b2:>6} {skip_str:>6}{warn}")
    
    tt = time.time() - t_total
    print("-" * len(header))
    print(f"总计: {tt:.0f}s ({tt/60:.1f}min), B1={grand_b1}, B2={grand_b2}")
    
    if not allow_fallback:
        if grand_skipped > 0 or grand_failed > 0:
            print(f"\n⚡ 跳过 {grand_skipped} 只（预加载未命中）+ {grand_failed} 只（bi加载失败）")
            print(f"   这等同于实盘行为。bi 加载失败多因 DB 锁竞争——重跑可恢复。")
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
    grand_total = grand_b1 = grand_b2 = grand_skipped = grand_failed = 0
    errors = []
    
    for i, d in enumerate(dates):
        try:
            total, b1, b2, pre_t, scan_t, skipped_bi, skipped_fb, bi_failed = scan_single_date(d, allow_fallback)
            grand_total += total
            grand_b1 += b1
            grand_b2 += b2
            skipped_total = skipped_bi + skipped_fb
            grand_skipped += skipped_total
            grand_failed += bi_failed
            
            elapsed = pre_t + scan_t
            et = time.time() - t_total
            eta = et / (i + 1) * (total_dates - i - 1) if i > 0 else 0
            eta_str = f"{eta/3600:.1f}h" if eta > 3600 else f"{eta/60:.0f}m"
            warn = "⚡" if bi_failed > 0 else " "
            print(f"{d:<12} {pre_t:>4.0f}s {scan_t:>4.0f}s {b1:>6} {b2:>6} {total:>7} {skipped_total:>6}{warn} {grand_total:>10} {eta_str:>8}")
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
        if grand_skipped > 0 or grand_failed > 0:
            print(f"\n⚡ 跳过 {grand_skipped} 只（预加载未命中）+ {grand_failed} 只（bi加载失败）")
            print(f"   这等同于实盘行为。bi 加载失败通常是瞬时 DB 锁——重跑该日期通常可恢复。")
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
