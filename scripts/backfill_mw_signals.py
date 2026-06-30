"""
MW 信号批量回填 v1.0
将全市场 MW B1/B2 信号从指定日期范围计算并写入 mw_signal_daily

依赖：chanlun_bi_json（缠论笔）, stock_rs_daily（个股RS）, index_rs_daily（指数RS）
输出：mw_signal_daily

用法：
    python scripts/backfill_mw_signals.py --start 2016-01-01 --end 2016-03-31
    python scripts/backfill_mw_signals.py --quarter 2016Q1 --incremental
    python scripts/backfill_mw_signals.py --incremental  # 全量增量

性能参考：单日全市场约 15~20 分钟，2535 天约 600~800 小时
建议按季度分批跑，每个季度约 2~3 小时
"""
import sys, os, time, argparse, sqlite3
from datetime import datetime

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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MW信号批量回填')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--start', help='起始日期 YYYY-MM-DD（需同时指定 --end）')
    group.add_argument('--quarter', help='按季度运行，如 2016Q1')
    parser.add_argument('--end', help='结束日期 YYYY-MM-DD')
    parser.add_argument('--incremental', action='store_true', help='增量模式：跳过已有扫描的日期')
    parser.add_argument('--workers', type=int, default=1, help='预留（MW引擎为单进程，暂不支持多进程）')
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

    from scanners.mw_signal import run_scan

    t_start = time.time()
    completed = 0
    errors = []

    for i, date in enumerate(dates):
        t0 = time.time()
        print(f"[{i+1}/{len(dates)}] {date} ...", end=' ', flush=True)
        try:
            run_scan(date, silent=True)
            # 补充打印当日结果
            import sqlite3 as _sql
            _db = _sql.connect(DB, timeout=5)
            cnt = _db.execute("SELECT COUNT(*) as c, COUNT(CASE WHEN b2_date IS NOT NULL THEN 1 END) as b2, COUNT(CASE WHEN b2_date IS NULL THEN 1 END) as b1only FROM mw_signal_daily WHERE b1_date=?", (date,)).fetchone()
            _db.close()
            elapsed = time.time() - t0
            completed += 1
            if completed > 0:
                avg = (time.time() - t_start) / completed
                eta = avg * (len(dates) - i - 1)
                print(f"✓ B1:{cnt[0]} B2:{cnt[1]} 纯B1:{cnt[2]} ({elapsed:.0f}s) ETA {eta/3600:.1f}h")
            else:
                print(f"✓ ({elapsed:.0f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            errors.append((date, str(e)[:200]))
            print(f"✗ ({elapsed:.0f}s) {e}")

    total_elapsed = time.time() - t_start
    print(f"\n=== 完成 ===")
    print(f"成功: {completed}/{len(dates)} 天")
    print(f"失败: {len(errors)} 天")
    print(f"总耗时: {total_elapsed/3600:.1f}h ({total_elapsed/60:.0f}min)")
    if errors:
        print(f"失败日期:")
        for d, e in errors[:10]:
            print(f"  {d}: {e}")
