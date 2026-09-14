#!/usr/bin/env python3
"""
scripts/pull_adj_close.py — ⚠️ 已停用（2026-09-12）

【为什么停用】
本脚本用 `{"date": ..., "type": "lxr_fc_rights"}` 单日全市场查询，
但理杏仁在 `date` 查询下**会忽略 `type`**（且复权类接口要求传 `adjustForwardDate`，本脚本未传），
实际拿到的是**不复权**价格，却被写进了 `adj_open/adj_high/adj_low/adj_close` 四列。
这就是「`adj_*` 四列长期失效（2019 起恒等于 close）」的根因。

【现在该怎么办】
个股前复权价请直接用视图 `daily_kline_adj`（底层是 `lxr_fc_*` 四列）：
    SELECT stock_code, date, open, high, low, close, volume, amount
    FROM daily_kline_adj WHERE stock_code=? ORDER BY date;
视图的 `close` 即理杏仁前复权价，`raw_close` 为原始成交价。
四个口径的维护见 `scripts/fetch_kline_multi_adjust.py` 与 `scripts/fetch_stock_daily_kline.py --caliber`。

如确需重跑（例如做历史对比），加 `--force` 绕过下面的护栏。
"""

import sys
from datetime import datetime, timedelta
from common import api_post, get_db, get_latest_date, log

API_PATH = "/company/candlestick"

UPSERT_SQL = """INSERT INTO daily_kline
    (stock_code, date, adj_open, adj_high, adj_low, adj_close)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(stock_code, date) DO UPDATE SET
    adj_open=excluded.adj_open, adj_high=excluded.adj_high,
    adj_low=excluded.adj_low, adj_close=excluded.adj_close"""


def fetch_date(date_str: str) -> list:
    payload = {"date": date_str, "type": "lxr_fc_rights"}
    return api_post(API_PATH, payload)


def save_klines(conn, klines: list) -> int:
    if not klines:
        return 0
    rows = []
    for k in klines:
        rows.append((
            k["stockCode"], k["date"][:10],
            k.get("open"), k.get("high"), k.get("low"), k.get("close"),
        ))
    conn.executemany(UPSERT_SQL, rows)
    conn.commit()
    return len(rows)


def generate_weekdays(start: str, end: str) -> list:
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    days = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:
            days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return days


def pull_all():
    """全量重拉 2000-01-01 至今"""
    conn = get_db()
    start_date = "2000-01-01"
    end_date = datetime.now().strftime("%Y-%m-%d")
    weekdays = generate_weekdays(start_date, end_date)

    log.info(f"全量重拉 {len(weekdays)} 个工作日 ({start_date} ~ {end_date})")
    log.info(f"预估耗时: 约 {len(weekdays)*0.1/60:.0f} 分钟")

    total = 0
    trading = 0
    
    for i, date_str in enumerate(weekdays, 1):
        try:
            klines = fetch_date(date_str)
            if klines:
                n = save_klines(conn, klines)
                total += n
                trading += 1
                if i % 50 == 0 or i == len(weekdays):
                    log.info(f"[{i}/{len(weekdays)}] {date_str} ✅ {n}条 | 累计{total:,}条")
            else:
                if i % 100 == 0:
                    log.info(f"[{i}/{len(weekdays)}] {date_str} ⏭")
        except Exception as e:
            log.error(f"[{i}/{len(weekdays)}] {date_str} ❌ {e}")

    log.info(f"拉取完成: {trading}交易日, {total:,}条")
    conn.close()
    return total


def compute_adj_close():
    """计算前复权收盘价: adj_close = close / cf × latest_cf"""
    conn = get_db()
    
    log.info("计算前复权价格...")
    
    # 对每只股票，找到最新交易日的complex_factor
    log.info("Step 1: 获取每只股票的最新复权因子...")
    stocks = conn.execute('''SELECT stock_code, complex_factor
        FROM daily_kline WHERE complex_factor IS NOT NULL
        ORDER BY stock_code, date DESC''').fetchall()
    
    # 取每只股票的最新complex_factor
    latest_cf = {}
    for s in stocks:
        if s['stock_code'] not in latest_cf:
            latest_cf[s['stock_code']] = s['complex_factor']
    
    log.info(f"  共 {len(latest_cf)} 只股票有复权因子数据")
    
    # 批量更新adj_close
    log.info("Step 2: 批量计算 adj_close...")
    count = 0
    for code, lcf in latest_cf.items():
        if not lcf or lcf == 0:
            continue
        # 前复权: adj_close = close / cf × latest_cf
        result = conn.execute('''UPDATE daily_kline
            SET adj_close = ROUND(close * ? / complex_factor, 4),
                adj_open = ROUND(open * ? / complex_factor, 4),
                adj_high = ROUND(high * ? / complex_factor, 4),
                adj_low = ROUND(low * ? / complex_factor, 4)
            WHERE stock_code = ? AND complex_factor IS NOT NULL AND complex_factor > 0''',
            (lcf, lcf, lcf, lcf, code)).rowcount
        count += result
        if count % 100000 == 0:
            conn.commit()
            log.info(f"  已更新 {count:,} 条...")
    
    conn.commit()
    log.info(f"完成: {count:,} 条记录已更新adj_close")
    conn.close()


if __name__ == "__main__":
    if "--force" not in sys.argv:
        log.error("⚠️ 本脚本已停用：它用 date 单日查询取不到复权口径，会把不复权价写进 adj_*。")
        log.error("   个股前复权价请改用视图 daily_kline_adj（close 列即理杏仁前复权价）。")
        log.error("   确实需要重跑请加 --force。")
        sys.exit(2)
    total = pull_all()
    log.info(f"完成: {total:,}条前复权数据已写入 adj_* 列")
