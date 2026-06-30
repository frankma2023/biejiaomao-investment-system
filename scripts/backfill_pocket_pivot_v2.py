"""
口袋支点V2 批量补扫脚本
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

功能：对指定日期范围内的每个交易日，运行口袋支点V2引擎进行全市场扫描，
      并将识别到的信号保存到 pocket_pivot_daily 表中。

计算内容：
  1. 对当天全市场约4500~5000只正常上市股票，逐一检查是否满足口袋支点条件
  2. 口袋支点条件包括：
     a. 趋势基础：收盘 > MA60 且 > MA10，SMA60斜率 > 0
     b. 盘整质量：距前低(L)≥5天，量能萎缩，振幅收窄，≥3天站上MA60
     c. 量价爆发：涨幅≥3%，量>前10天最大下跌量，收盘位置≥50%
     d. 突破盘整区：今日最高≥前10天最高
     e. RS确认：RPS20≥80 或 RPS250≥80
     f. 不延伸：距10日线≤20%
  3. 对满足条件的信号，判断类型：base(基部) / continuation(延续) / 10ma_bounce(10日反弹)
  4. 检测是否与MW信号的B1日重合
  5. 写入 pocket_pivot_daily 表 (UNIQUE约束：date + stock_code)

数据源：
  - daily_kline：全市场日K线 (OHLCV + amount)
  - stock_rs_daily：个股RS强度 (RPS20/RPS250)
  - mw_signal_daily：MW信号H/L/C结构 (用于精准定位盘整区间)
  - chanlun_scan_daily：缠论笔列表 (MW信号缺失时的兜底方案)
  - index_daily_kline (000985)：大盘抛盘日计数

输出表：pocket_pivot_daily
  字段：date, stock_code, stock_name, pivot_type, b1_overlap,
        gain_pct, vol_ratio, close_position, rps_20, rps_250,
        sma10, sma60, pct_from_ma10, quiet_amp, base_depth,
        h_date, l_date, c_days, close, volume

用法：
  # 全量扫描（覆盖已有数据）
  python scripts/backfill_pocket_pivot_v2.py --start 2023-06-01 --end 2026-06-05 --workers 4

  # 增量扫描（跳过已有数据的日期）
  python scripts/backfill_pocket_pivot_v2.py --start 2023-06-01 --end 2026-06-05 --incremental --workers 4

  # 单进程（便于调试）
  python scripts/backfill_pocket_pivot_v2.py --start 2026-06-01 --end 2026-06-05 --workers 1

参数：
  --start       起始日期 (YYYY-MM-DD)，默认 2023-06-01
  --end         结束日期 (YYYY-MM-DD)，默认 2026-06-05
  --incremental 增量模式：跳过 pocket_pivot_daily 中已有数据的日期
  --workers     并行进程数，默认 4 (推荐 4~8)

性能参考：
  - 单日全市场扫描：约 3~6 秒 (取决于股票数量和缠论计算量)
  - 4 进程并行：约 1~2 秒/天
  - 3 年数据 (700+ 交易日)：约 15~20 分钟
"""
import sys, os, time, argparse, sqlite3
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed

# ── 路径设置：确保可以从 scripts/ 目录导入 src/scanners/ 下的模块 ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'scanners'))

# 数据库路径
DB = os.path.join(os.path.dirname(__file__), '..', 'data', 'lixinger.db')


def get_trading_dates(start, end):
    """获取区间内的交易日列表"""
    db = sqlite3.connect(DB)
    rows = db.execute("""
        SELECT DISTINCT date FROM daily_kline
        WHERE date >= ? AND date <= ?
        ORDER BY date
    """, (start, end)).fetchall()
    db.close()
    return [r[0] for r in rows]


def quarter_to_range(q):
    import calendar
    year = int(q[:4]); qnum = int(q[-1])
    sm = (qnum - 1) * 3 + 1; em = sm + 2
    ld = calendar.monthrange(year, em)[1]
    return f"{year}-{sm:02d}-01", f"{year}-{em:02d}-{ld}"


def scan_one_day(date):
    """扫描单日全市场口袋支点信号。
    每个 worker 进程独立调用此函数。
    直接导入引擎模块（非子进程），避免子进程的路径和环境问题。

    Returns:
        (date, signal_count, elapsed_seconds, error_string_or_None)
    """
    t0 = time.time()
    try:
        # 导入口袋支点V2引擎（文件名是 pocket_pivot_v2.py，实际是V3版本）
        from pocket_pivot_v2 import scan_date, save_to_db, CFG

        # 回填模式：放宽抛盘日限制（历史熊市中抛盘日可能很多）
        CFG['max_distribution_days'] = 999

        # 执行全市场扫描
        signals = scan_date(date)

        # 如果有信号，保存到数据库（重试3次防止并发锁冲突）
        if signals:
            for attempt in range(3):
                try:
                    save_to_db(signals)
                    break
                except Exception as e:
                    if attempt < 2:
                        import time as _time
                        _time.sleep(2 * (attempt + 1))  # 递增等待
                    else:
                        raise

        elapsed = time.time() - t0
        return (date, len(signals), elapsed, None)
    except Exception as e:
        elapsed = time.time() - t0
        return (date, 0, elapsed, str(e)[:200])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='口袋支点V2 批量补扫 — 多进程版'
    )
    parser.add_argument('--start', help='起始日期 YYYY-MM-DD')
    parser.add_argument('--end', help='结束日期 YYYY-MM-DD')
    parser.add_argument('--quarter', help='按季度运行，如 2016Q1')
    parser.add_argument('--incremental', action='store_true',
                        help='增量模式：跳过已有数据的日期')
    parser.add_argument('--workers', type=int, default=4,
                        help='并行进程数 (默认4，推荐4~8)')
    args = parser.parse_args()

    if args.quarter:
        start_date, end_date = quarter_to_range(args.quarter)
    elif args.start and args.end:
        start_date, end_date = args.start, args.end
    else:
        parser.error("需要 --quarter 或 (--start + --end)")
        sys.exit(1)

    # ── 1. 获取交易日列表 ──
    dates = get_trading_dates(start_date, end_date)

    # ── 2. 增量模式：过滤已有数据的日期 ──
    if args.incremental:
        db = sqlite3.connect(DB)
        # 只查询请求区间内的已有日期
        existing = set(r[0] for r in db.execute(
            "SELECT DISTINCT date FROM pocket_pivot_daily WHERE date >= ? AND date <= ?",
            (start_date, end_date)
        ).fetchall())
        db.close()
        total_before = len(dates)
        dates = [d for d in dates if d not in existing]
        skipped = total_before - len(dates)
        print(f"增量: 区间内已完成 {skipped} 天, 剩余 {len(dates)} 天")
    else:
        print(f"全量: {len(dates)} 天")

    if not dates:
        print("无需扫描")
        sys.exit(0)

    print(f"进程数: {args.workers}")
    t_start = time.time()
    completed = 0
    total_signals = 0

    # ── 3. 执行扫描 (单进程或多进程) ──
    if args.workers == 1:
        # 单进程模式：便于调试，输出更详细
        for date in dates:
            date, count, elapsed, err = scan_one_day(date)
            completed += 1
            total_signals += count
            status = f"✓ {count:>3}个" if err is None else f"✗ {err[:60]}"
            eta = (time.time() - t_start) / completed * (len(dates) - completed)
            print(f"[{completed}/{len(dates)}] {date} {status} "
                  f"({elapsed:.1f}s) ETA {eta/60:.0f}min")
    else:
        # 多进程模式：ProcessPoolExecutor 并行
        # 每个 worker 进程独立导入引擎，避免 GIL 和 SQLite 锁竞争
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(scan_one_day, d): d for d in dates}
            for future in as_completed(futures):
                date, count, elapsed, err = future.result()
                completed += 1
                total_signals += count
                status = f"✓ {count:>3}个" if err is None else f"✗ {err[:60]}"
                eta = (time.time() - t_start) / completed * (len(dates) - completed)
                print(f"[{completed}/{len(dates)}] {date} {status} "
                      f"({elapsed:.1f}s) ETA {eta/60:.0f}min")

    # ── 4. 完成汇总 ──
    total_minutes = (time.time() - t_start) / 60
    print(f"\n完成! {len(dates)}天, {total_signals}个信号, {total_minutes:.1f}分钟")
    print(f"数据已保存到 pocket_pivot_daily 表")
