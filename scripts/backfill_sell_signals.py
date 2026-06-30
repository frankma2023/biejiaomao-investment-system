"""
卖出信号批量回填 v1.0
逐只股票扫描四个卖出引擎，写入 pattern_scan_signals

引擎: climax_top（高潮见顶）, railroad_tracks（铁轨线）, top_pattern（头肩顶/双顶/三重顶）, breakout_failure（突破失败）

依赖：daily_kline, chanlun_bi_json, market_breakout_v2_daily（breakout_failure依赖）
输出：pattern_scan_signals

用法：
    python scripts/backfill_sell_signals.py --start 2016-01-01 --end 2016-03-31
    python scripts/backfill_sell_signals.py --quarter 2016Q1 --incremental --workers 4
    python scripts/backfill_sell_signals.py --engines climax_top,railroad_tracks  # 只跑指定引擎
"""
import sys, os, time, argparse, sqlite3, json
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))

DB = os.path.join(PROJECT, 'data', 'lixinger.db')

SELL_ENGINES = ['climax_top', 'railroad_tracks', 'top_pattern', 'breakout_failure']


def get_trading_dates(start, end):
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date>=? AND date<=? ORDER BY date",
        (start, end)
    ).fetchall()
    db.close()
    return [r[0] for r in rows]


def get_existing_dates(engines):
    """获取已有这些引擎信号的日期"""
    db = sqlite3.connect(DB)
    existing = set()
    for eng in engines:
        rows = db.execute(
            "SELECT DISTINCT date FROM pattern_scan_signals WHERE signals_json LIKE ?",
            (f'%"{eng}"%',)
        ).fetchall()
        existing.update(r[0] for r in rows)
    db.close()
    return existing


def quarter_to_range(q):
    import calendar
    year = int(q[:4]); qnum = int(q[-1])
    sm = (qnum - 1) * 3 + 1; em = sm + 2
    ld = calendar.monthrange(year, em)[1]
    return f"{year}-{sm:02d}-01", f"{year}-{em:02d}-{ld}"


def scan_stock_worker(args):
    """Worker: 对单只股票运行指定卖出引擎"""
    code, date, engines = args
    try:
        db = sqlite3.connect(DB, timeout=10)
        db.row_factory = sqlite3.Row
        krows = db.execute(
            "SELECT date,open,high,low,close,volume FROM daily_kline WHERE stock_code=? AND date<=? ORDER BY date",
            (code, date)
        ).fetchall()
        db.close()
        if len(krows) < 120:
            return (code, date, [])

        klines = [dict(r) for r in krows]
        all_sigs = []

        for eng_name in engines:
            try:
                if eng_name == 'climax_top':
                    from scanners.climax_top import detect as c_detect, load_params as c_params
                    sigs = c_detect(daily=klines, params=c_params(), stock_code=code)
                elif eng_name == 'railroad_tracks':
                    from scanners.railroad_tracks import detect as r_detect, load_params as r_params
                    sigs = r_detect(daily=klines, params=r_params(), stock_code=code)
                elif eng_name == 'top_pattern':
                    from scanners.top_pattern import detect as t_detect, load_params as t_params
                    sigs = t_detect(daily=klines, params=t_params(), stock_code=code)
                elif eng_name == 'breakout_failure':
                    from scanners.breakout_failure import detect as b_detect, load_params as b_params
                    sigs = b_detect(daily=klines, params=b_params(), stock_code=code)
                else:
                    continue

                for sig in sigs or []:
                    if isinstance(sig, dict):
                        sig['source'] = eng_name
                        all_sigs.append(sig)
            except Exception as e:
                pass  # 引擎报错不影响其他引擎

        return (code, date, all_sigs)
    except Exception as e:
        return (code, date, [])


def save_stock_results(db_path, date, stock_results):
    """为一批股票追加写入 pattern_scan_signals"""
    for attempt in range(5):
        try:
            db = sqlite3.connect(db_path, timeout=30)
            try:
                for code, _, sigs in stock_results:
                    if not sigs:
                        continue
                    # 先读已有信号
                    existing = db.execute(
                        "SELECT signals_json FROM pattern_scan_signals WHERE stock_code=? AND date=?",
                        (code, date)
                    ).fetchone()
                    existing_sigs = json.loads(existing[0]) if existing and existing[0] else []
                    # 合并：同 source 去重
                    seen_sources = {s.get('source', '') for s in existing_sigs}
                    new_sigs = [s for s in sigs if s.get('source', '') not in seen_sources]
                    if not new_sigs:
                        continue
                    merged = existing_sigs + new_sigs
                    db.execute(
                        "INSERT OR REPLACE INTO pattern_scan_signals (stock_code, date, signals_json) VALUES (?,?,?)",
                        (code, date, json.dumps(merged, ensure_ascii=False))
                    )
                db.commit()
                break
            finally:
                db.close()
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < 4:
                time.sleep(3 * (attempt + 1))
            else:
                raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='卖出信号批量回填')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--start', help='起始日期')
    group.add_argument('--quarter', help='按季度')
    parser.add_argument('--end', help='结束日期')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--incremental', action='store_true')
    parser.add_argument('--engines', default=','.join(SELL_ENGINES),
                        help=f'指定引擎，逗号分隔。可选: {SELL_ENGINES}')
    args = parser.parse_args()

    engines = [e.strip() for e in args.engines.split(',') if e.strip() in SELL_ENGINES]
    if not engines:
        print(f"无效引擎，可选: {SELL_ENGINES}")
        sys.exit(1)
    print(f"引擎: {engines}")

    if args.quarter:
        start_date, end_date = quarter_to_range(args.quarter)
    else:
        start_date, end_date = args.start, args.end

    all_dates = get_trading_dates(start_date, end_date)
    if not all_dates:
        print("区间无交易日"); sys.exit(0)

    if args.incremental:
        existing = get_existing_dates(engines)
        dates = [d for d in all_dates if d not in existing]
        print(f"增量: 已完成 {len(all_dates)-len(dates)} 天, 剩余 {len(dates)} 天")
    else:
        dates = all_dates
        print(f"全量: {len(dates)} 天")

    if not dates:
        print("无需处理"); sys.exit(0)

    workers = args.workers
    print(f"并行: {workers} 进程")

    t_start = time.time()
    completed = 0; total_signals = 0

    for date in dates:
        t0 = time.time()
        db = sqlite3.connect(DB, timeout=10)
        stocks = db.execute("SELECT DISTINCT stock_code FROM daily_kline WHERE date=?", (date,)).fetchall()
        db.close()
        codes = [r[0] for r in stocks]

        task_args = [(code, date, engines) for code in codes]
        stock_results = []

        if workers == 1 or len(codes) < 10:
            for a in task_args:
                stock_results.append(scan_stock_worker(a))
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(scan_stock_worker, a): a for a in task_args}
                for f in as_completed(futures):
                    stock_results.append(f.result())

        save_stock_results(DB, date, stock_results)
        day_sigs = sum(len(r[2]) for r in stock_results)
        elapsed = time.time() - t0
        completed += 1; total_signals += day_sigs

        eta = (time.time()-t_start)/completed*(len(dates)-completed) if completed else 0
        print(f"[{completed}/{len(dates)}] {date} ✓ {day_sigs}个卖出信号 ({elapsed:.1f}s) ETA {eta/3600:.1f}h")

    total_elapsed = time.time() - t_start
    print(f"\n=== 完成 ===")
    print(f"{len(dates)}天, {total_signals}个卖出信号, {total_elapsed/3600:.1f}h")
