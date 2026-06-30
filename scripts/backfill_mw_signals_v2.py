"""
MW 信号批量回填 v1.1 · 性能优化版
─────────────────────────────────
关键优化：批量预加载 K线/RS/行业/共振信号到内存 dict，
消除 per-stock SQL 查询。预期单日耗时 470s → 60~90s。

用法：
    python scripts/backfill_mw_signals_v2.py --start 2026-06-01 --end 2026-06-21 --incremental
"""
import sys, os, time, argparse, sqlite3, json
from datetime import datetime, timedelta
from collections import defaultdict

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


def get_existing_dates():
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT DISTINCT scan_date FROM mw_signal_daily WHERE scan_date IS NOT NULL").fetchall()
    db.close()
    return set(r[0] for r in rows)


def quarter_to_range(q):
    import calendar
    year = int(q[:4]); qnum = int(q[-1])
    sm = (qnum - 1) * 3 + 1; em = sm + 2
    ld = calendar.monthrange(year, em)[1]
    return f"{year}-{sm:02d}-01", f"{year}-{em:02d}-{ld}"


def set_module_caches(conn, scan_date):
    """
    批量预加载到 mw_signal 模块级缓存变量。
    调用后，scan_stock 内部所有 SQL 查询将直接从内存读取。
    """
    import scanners.mw_signal as mw
    t0 = time.time()

    # 1. 缠论笔缓存
    mw._chanlun_cache = {}
    for row in conn.execute(
        "SELECT stock_code, bi_json FROM chanlun_bi_json WHERE scan_date=?",
        (scan_date,)
    ).fetchall():
        try:
            mw._chanlun_cache[(row['stock_code'], scan_date)] = __import__('orjson').loads(row['bi_json'])
        except:
            pass
    print(f'  缠论笔缓存: {len(mw._chanlun_cache)} 只, {time.time()-t0:.1f}s'); t0 = time.time()

    # 2. 个股 RS 缓存: {stock_code: (rps_20, rps_250)}
    mw._rs_cache = {}
    rows = conn.execute(
        "SELECT stock_code, rps_20, rps_250, date FROM stock_rs_daily WHERE date<=? ORDER BY date DESC",
        (scan_date,)
    ).fetchall()
    for r in rows:
        if r['stock_code'] not in mw._rs_cache:
            mw._rs_cache[r['stock_code']] = (r['rps_20'], r['rps_250'])
    print(f'  个股RS缓存: {len(mw._rs_cache)} 只, {time.time()-t0:.1f}s'); t0 = time.time()

    # 3. 行业成分缓存: {stock_code: [idx_code, ...]}
    mw._idx_comp_cache = defaultdict(list)
    rows = conn.execute("SELECT stock_code, index_code FROM index_constituents").fetchall()
    for r in rows:
        mw._idx_comp_cache[r['stock_code']].append(r['index_code'])
    print(f'  行业成分缓存: {len(rows)} 条, {time.time()-t0:.1f}s'); t0 = time.time()

    # 4. 指数 RS 缓存: {index_code: (rs_20, rs_250)}
    mw._idx_rs_cache = {}
    rows = conn.execute(
        "SELECT stock_code, rs_20, rs_250 FROM index_rs_daily WHERE date<=? ORDER BY date DESC",
        (scan_date,)
    ).fetchall()
    for r in rows:
        if r['stock_code'] not in mw._idx_rs_cache:
            mw._idx_rs_cache[r['stock_code']] = (r['rs_20'], r['rs_250'])
    print(f'  指数RS缓存: {len(mw._idx_rs_cache)} 个指数, {time.time()-t0:.1f}s'); t0 = time.time()

    # 5. 共振信号缓存: {stock_code: signals_json}
    mw._reso_cache = {}
    rows = conn.execute(
        "SELECT stock_code, signals_json FROM pattern_scan_signals WHERE date=?",
        (scan_date,)
    ).fetchall()
    for r in rows:
        mw._reso_cache[(r['stock_code'], scan_date)] = r['signals_json']
    print(f'  共振信号缓存: {len(mw._reso_cache)} 只, {time.time()-t0:.1f}s'); t0 = time.time()

    # 6. K线缓存: {stock_code: [row_dict_sorted_by_date, ...]}
    min_date = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=400)).strftime('%Y-%m-%d')
    mw._kline_cache = defaultdict(list)
    rows = conn.execute(
        "SELECT stock_code, date, open, high, low, close, volume, amount FROM daily_kline WHERE date>=? AND date<=? ORDER BY stock_code, date",
        (min_date, scan_date)
    ).fetchall()
    for r in rows:
        mw._kline_cache[r['stock_code']].append(dict(r))
    print(f'  K线缓存: {len(rows)} 行 ({len(mw._kline_cache)} 只股票), {time.time()-t0:.1f}s')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MW信号批量回填')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--start', help='起始日期 YYYY-MM-DD')
    group.add_argument('--quarter', help='按季度运行')
    parser.add_argument('--end', help='结束日期 YYYY-MM-DD')
    parser.add_argument('--incremental', action='store_true')
    args = parser.parse_args()

    if args.quarter:
        start_date, end_date = quarter_to_range(args.quarter)
    elif args.start and args.end:
        start_date, end_date = args.start, args.end
    else:
        parser.error("需要 --quarter 或 (--start + --end)")
        sys.exit(1)

    all_dates = get_trading_dates(start_date, end_date)
    if not all_dates:
        print(f"区间 {start_date} ~ {end_date} 无交易日")
        sys.exit(0)

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

    # 清理旧哨兵
    _db = sqlite3.connect(DB, timeout=5)
    _db.execute("DELETE FROM mw_signal_daily WHERE stock_code='_sentinel_'")
    _db.commit()
    _db.close()

    from scanners.mw_signal import run_scan

    t_start = time.time()
    completed = 0
    errors = []

    for i, scan_date in enumerate(dates):
        t0 = time.time()
        print(f"\n[{i+1}/{len(dates)}] {scan_date}")

        try:
            # ── 连接数据库（预加载用）──
            conn = sqlite3.connect(DB, timeout=30)
            conn.row_factory = sqlite3.Row

            # ── 设置模块缓存 ──
            print(f'  预加载缓存...', flush=True)
            set_module_caches(conn, scan_date)

            conn.close()

            # ── 调用原始 run_scan（它内部会新开连接，缓存已就位）──
            run_scan(scan_date, silent=True)

            # 打印结果
            _db = sqlite3.connect(DB, timeout=5)
            cnt = _db.execute(
                "SELECT COUNT(*) as c, COUNT(CASE WHEN b2_date IS NOT NULL THEN 1 END) as b2, COUNT(CASE WHEN b2_date IS NULL THEN 1 END) as b1only FROM mw_signal_daily WHERE b1_date=?",
                (scan_date,)
            ).fetchone()
            _db.close()

            elapsed = time.time() - t0
            completed += 1
            avg = (time.time() - t_start) / completed
            eta = avg * (len(dates) - i - 1)
            print(f'  ✓ B1:{cnt[0]} B2:{cnt[1]} 纯B1:{cnt[2]} ({elapsed:.0f}s) ETA {eta/3600:.1f}h')

        except Exception as e:
            elapsed = time.time() - t0
            errors.append((scan_date, str(e)[:200]))
            print(f'  ✗ ({elapsed:.0f}s) {e}')
            import traceback
            traceback.print_exc()

    total_elapsed = time.time() - t_start
    print(f"\n=== 完成 ===")
    print(f"成功: {completed}/{len(dates)} 天")
    print(f"失败: {len(errors)} 天")
    print(f"总耗时: {total_elapsed/3600:.1f}h ({total_elapsed/60:.0f}min)")
    if errors:
        print(f"失败日期:")
        for d, e in errors[:10]:
            print(f"  {d}: {e}")
