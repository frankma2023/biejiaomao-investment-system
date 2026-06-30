#!/usr/bin/env python3
"""
scripts/fetch_stock_daily_kline.py — 增量拉取A股所有股票日K线

用法：
    python scripts/fetch_stock_daily_kline.py                          # 增量更新（默认）
    python scripts/fetch_stock_daily_kline.py --full                   # 全量拉取所有历史数据
    python scripts/fetch_stock_daily_kline.py --start 2024-01-01       # 从指定日期起增量
    python scripts/fetch_stock_daily_kline.py --start 2024-01-01 --end 2024-12-31  # 指定区间

原理：
    利用理杏仁 API 的 date 参数（单日返回全市场 K 线），按日批量拉取。
    增量模式：从 daily_kline 表的最新日期 + 1 天开始拉。
    全量模式：从 2000-01-01 开始拉（API 会自动跳过无数据的日期）。
"""

import sys
from datetime import datetime, timedelta
from common import api_post, get_db, get_latest_date, log, RateLimiter

# ── 配置 ──────────────────────────────────────────────
API_PATH = "/company/candlestick"

UPSERT_SQL = """INSERT INTO daily_kline
    (stock_code, date, open, close, high, low,
     volume, amount, change_pct, turnover_rate, complex_factor)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(stock_code, date) DO UPDATE SET
    open=excluded.open, close=excluded.close, high=excluded.high, low=excluded.low,
    volume=excluded.volume, amount=excluded.amount, change_pct=excluded.change_pct,
    turnover_rate=excluded.turnover_rate, complex_factor=excluded.complex_factor"""


# ════════════════════════════════════════════════════════

def fetch_date(date_str: str) -> list:
    """拉取单日全市场K线"""
    payload = {"date": date_str}
    return api_post(API_PATH, payload)


def save_klines(conn, klines: list):
    """批量写入K线到数据库"""
    if not klines:
        return 0
    rows = []
    for k in klines:
        rows.append((
            k["stockCode"],
            k["date"][:10],
            k.get("open"),
            k.get("close"),
            k.get("high"),
            k.get("low"),
            k.get("volume"),
            k.get("amount"),
            k.get("change"),
            k.get("to_r"),
            k.get("complexFactor"),
        ))
    conn.executemany(UPSERT_SQL, rows)
    conn.commit()
    return len(rows)


def generate_weekdays(start: str, end: str) -> list:
    """生成日期范围内所有工作日（周一~周五）"""
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    days = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:
            days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return days


def parse_date_arg(argv: list, flag: str) -> str | None:
    """从命令行参数提取日期值，例如 --start 2024-01-01"""
    try:
        idx = argv.index(flag)
        return argv[idx + 1]
    except (ValueError, IndexError):
        return None


def main():
    conn = get_db()
    full_mode = "--full" in sys.argv
    arg_start = parse_date_arg(sys.argv, "--start")
    arg_end = parse_date_arg(sys.argv, "--end")

    if arg_start:
        start_date = arg_start
        log.info(f"📅 指定起始日: {start_date}")
    elif full_mode:
        start_date = "2000-01-01"
        log.info("🔁 全量模式：从 2000-01-01 开始拉取")
    else:
        latest = get_latest_date(conn, "daily_kline") or "2000-01-01"
        start = datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)
        start_date = start.strftime("%Y-%m-%d")
        log.info(f"📅 增量模式：最新数据日期 {latest}，从 {start_date} 开始")

    end_date = arg_end or datetime.now().strftime("%Y-%m-%d")
    weekdays = generate_weekdays(start_date, end_date)

    if not weekdays:
        log.info("✅ 没有需要拉取的日期，数据已最新")
        conn.close()
        return

    log.info(f"📊 共 {len(weekdays)} 个工作日待拉取")

    total_klines = 0
    trading_days = 0

    for i, date_str in enumerate(weekdays, 1):
        try:
            klines = fetch_date(date_str)
            if klines:
                n = save_klines(conn, klines)
                total_klines += n
                trading_days += 1
                log.info(f"[{i}/{len(weekdays)}] {date_str} ✅ 交易日，{n} 条K线")
            else:
                log.info(f"[{i}/{len(weekdays)}] {date_str} ⏭ 非交易日，跳过")
        except Exception as e:
            log.error(f"[{i}/{len(weekdays)}] {date_str} ❌ 失败: {e}")
            # 继续下一个日期，不中断

    log.info(f"🏁 完成: {trading_days} 个交易日，{total_klines} 条K线")

    # 打印汇总
    if trading_days > 0:
        row = conn.execute("SELECT COUNT(*) as cnt FROM daily_kline").fetchone()
        log.info(f"   daily_kline 表总计: {row['cnt']:,} 条记录")

    conn.close()


if __name__ == "__main__":
    main()
