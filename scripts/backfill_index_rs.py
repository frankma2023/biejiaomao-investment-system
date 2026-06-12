"""
指数 RS 强度批量回填 v1.0
将 408 个指数的 RS_20/60/120/250 从 2016-01-01 计算至今

用法：
    python scripts/backfill_index_rs.py --start 2016-01-01 --end 2026-06-11
    python scripts/backfill_index_rs.py --quarter 2016Q1 --incremental
    python scripts/backfill_index_rs.py --incremental  # 全量增量

依赖：src/scanners/index_rs.py、config/index_style.yaml、index_daily_kline 表
输出：index_rs_daily 表
"""
import sys, os, time, argparse, sqlite3
from datetime import datetime, timedelta

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))

DB = os.path.join(PROJECT, 'data', 'lixinger.db')


# ══════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════

def get_trading_dates(start, end):
    """获取区间内 index_daily_kline 中有数据的交易日"""
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT DISTINCT date FROM index_daily_kline WHERE date>=? AND date<=? AND kline_type='normal' ORDER BY date",
        (start, end)
    ).fetchall()
    db.close()
    return [r[0] for r in rows]


def get_existing_dates():
    """获取 index_rs_daily 中已有数据的日期集合"""
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT DISTINCT date FROM index_rs_daily").fetchall()
    db.close()
    return set(r[0] for r in rows)


def quarter_to_range(q):
    """2016Q1 → ('2016-01-01', '2016-03-31')"""
    year = int(q[:4])
    qnum = int(q[-1])
    start_month = (qnum - 1) * 3 + 1
    end_month = start_month + 2
    start = f"{year}-{start_month:02d}-01"
    import calendar
    last_day = calendar.monthrange(year, end_month)[1]
    end = f"{year}-{end_month:02d}-{last_day}"
    return start, end


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='指数RS批量回填')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--start', help='起始日期 YYYY-MM-DD（需同时指定 --end）')
    group.add_argument('--quarter', help='按季度运行，如 2016Q1')
    parser.add_argument('--end', help='结束日期 YYYY-MM-DD')
    parser.add_argument('--incremental', action='store_true', help='增量模式：跳过已有数据的日期')
    args = parser.parse_args()

    # 解析日期范围
    if args.quarter:
        start_date, end_date = quarter_to_range(args.quarter)
    elif args.start and args.end:
        start_date, end_date = args.start, args.end
    else:
        parser.error("需要 --quarter 或 (--start + --end)")
        sys.exit(1)

    # 获取交易日
    all_dates = get_trading_dates(start_date, end_date)
    if not all_dates:
        print(f"区间 {start_date} ~ {end_date} 无交易日")
        sys.exit(0)

    # 增量过滤
    if args.incremental:
        existing = get_existing_dates()
        total_before = len(all_dates)
        dates = [d for d in all_dates if d not in existing]
        skipped = total_before - len(dates)
        print(f"增量模式: 区间内已完成 {skipped} 天, 剩余 {len(dates)} 天")
    else:
        dates = all_dates
        print(f"全量模式: {len(dates)} 天 ({dates[0]} ~ {dates[-1]})")

    if not dates:
        print("无需处理，退出")
        sys.exit(0)

    # 导入引擎并静默日志
    import logging
    logging.getLogger('scripts.common').setLevel(logging.WARNING)
    from scanners.index_rs import compute, ensure_table
    ensure_table()

    # 逐日计算
    t_start = time.time()
    completed = 0
    errors = []

    for i, date in enumerate(dates):
        t0 = time.time()
        # 进度预估
        if completed > 0:
            avg = (time.time() - t_start) / completed
            eta = avg * (len(dates) - completed)
            eta_str = f"ETA {eta/60:.0f}min"
        else:
            eta_str = ""
        print(f"[{completed+1}/{len(dates)}] {date} ...", end=' ', flush=True)
        try:
            compute(date)
            elapsed = time.time() - t0
            completed += 1
            print(f"✓ ({elapsed:.1f}s) {eta_str}")

        except Exception as e:
            elapsed = time.time() - t0
            errors.append((date, str(e)[:200]))
            print(f"[{completed}/{len(dates)}] {date}  ✗ ({elapsed:.1f}s) {e}")

    # 汇总
    total_elapsed = time.time() - t_start
    print()
    print(f"=== 完成 ===")
    print(f"成功: {completed}/{len(dates)} 天")
    print(f"失败: {len(errors)} 天")
    print(f"总耗时: {total_elapsed/60:.1f}min")
    if errors:
        print(f"失败日期:")
        for d, e in errors[:10]:
            print(f"  {d}: {e}")
