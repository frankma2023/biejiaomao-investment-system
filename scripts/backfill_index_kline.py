#!/usr/bin/env python3
"""scripts/backfill_index_kline.py — 为指定指数补拉价格 K 线。

fetch_index_daily_kline.py 只能按分类整批拉取，没有单指数入口。
补单个指数的历史缺口时用它代价太大，故单独提供本脚本。

用法：
    python scripts/backfill_index_kline.py 930914 --start 2016-01-01
    python scripts/backfill_index_kline.py 930914 930839 --start 2016-01-01 --end 2018-08-31

注意：理杏仁 API 单次请求区间不超过 10 年，超过会自动分批。
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import api_post, get_db, log  # noqa: E402

API_PATH = "/index/candlestick"
UPSERT_SQL = """INSERT OR REPLACE INTO index_daily_kline
    (stock_code, date, kline_type, open, close, high, low,
     volume, amount, change)
    VALUES (?, ?, 'normal', ?, ?, ?, ?, ?, ?, ?)"""


def _batches(start: str, end: str):
    """把区间切成不超过 10 年的批。"""
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    while s <= e:
        b = min(s + timedelta(days=365 * 9 + 2), e)
        yield s.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d")
        s = b + timedelta(days=1)


def fetch(code: str, start: str, end: str, conn) -> int:
    saved = 0
    for s, e in _batches(start, end):
        data = api_post(API_PATH, {
            "stockCode": code, "type": "normal",
            "startDate": s, "endDate": e,
        })
        rows = []
        for it in data or []:
            d = (it.get("date") or "")[:10]
            if not d:
                continue
            rows.append((code, d, it.get("open"), it.get("close"),
                         it.get("high"), it.get("low"), it.get("volume"),
                         it.get("amount"), it.get("change")))
        if rows:
            conn.executemany(UPSERT_SQL, rows)
            conn.commit()
        saved += len(rows)
        log.info("  %s %s ~ %s：%d 条", code, s, e, len(rows))
    return saved


def main():
    args, skip_val = [], False
    for a in sys.argv[1:]:
        if skip_val:
            skip_val = False
            continue
        if a in ("--start", "--end"):
            skip_val = True
            continue
        if a.startswith("--"):
            continue
        args.append(a)
    if not args:
        print(__doc__)
        return
    start = end = None
    for i, a in enumerate(sys.argv):
        if a == "--start" and i + 1 < len(sys.argv):
            start = sys.argv[i + 1]
        if a == "--end" and i + 1 < len(sys.argv):
            end = sys.argv[i + 1]
    start = start or "2016-01-01"
    end = end or datetime.now().strftime("%Y-%m-%d")

    conn = get_db()
    total = 0
    for code in args:
        before = conn.execute(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM index_daily_kline "
            "WHERE stock_code=? AND kline_type='normal'", (code,)).fetchone()
        log.info("拉取 %s，区间 %s ~ %s", code, start, end)
        n = fetch(code, start, end, conn)
        after = conn.execute(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM index_daily_kline "
            "WHERE stock_code=? AND kline_type='normal'", (code,)).fetchone()
        total += n
        log.info("  %s 补拉前 %s 条 (%s ~ %s) → 补拉后 %s 条 (%s ~ %s)",
                 code, before[0], before[1], before[2],
                 after[0], after[1], after[2])
    conn.close()
    log.info("合计写入 %d 条", total)


if __name__ == "__main__":
    main()
