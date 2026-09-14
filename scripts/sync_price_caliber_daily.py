#!/usr/bin/env python3
"""
scripts/sync_price_caliber_daily.py — 每日维护口径列（接在个股日K线拉取之后）

为什么需要它
----------
`scripts/fetch_stock_daily_kline.py` 用 `{"date": ...}` 单日全市场查询，只写
`open/close/high/low/volume/amount/change_pct/turnover_rate/complex_factor`，
**不写 ex_*/lxr_fc_*/fc_*/bc_* 这 16 列**。而 `daily_kline_adj` 视图是
`COALESCE(lxr_fc_close, ex_close)`，两个都空时视图返回空价格，
所有走视图的引擎会在新交易日集体失效。

本脚本把这三件事补上：

  1. **ex_*** ← 从 `open/high/low/close` 回填
     自 2026-09 起 `close` 已统一为不复权口径，与 ex_* 同义，回填是等值操作。

  2. **lxr_fc_*** ← 用 `change_pct` 递推（不需要任何 API 调用）
     恒等式：change_pct 就是理杏仁前复权的日收益率，因此
        lxr_fc_close(t) = lxr_fc_close(t-1) × (1 + change_pct(t))
        k(t)            = lxr_fc_close(t) / ex_close(t)
        lxr_fc_o/h/l(t) = ex_o/h/l(t) × k(t)
     实测递推偏差中位 0.0025pp/日（8 只 × 410 天），一年累积约 0.6pp。
     ⚠️ 因此本脚本只适合**短期增量**；建议每周（或至少每月）跑一次
        `fetch_kline_multi_adjust.py --types lxr_fc --start <近30天> ` 校正漂移，
        或直接用本脚本的 `--resync` 走 API 逐只校正。

  3. **fc_* / bc_***：留档列，加法口径，无任何代码读取 → 保持 NULL，不处理。

用法
----
    python scripts/sync_price_caliber_daily.py              # 干跑，只报告
    python scripts/sync_price_caliber_daily.py --apply      # 执行
    python scripts/sync_price_caliber_daily.py --verify     # 只校验覆盖率
"""

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DB_PATH, log  # noqa: E402

APPLY = '--apply' in sys.argv


def report_gap(conn):
    """报告视图会返回空价格的行（新交易日的主要风险点）"""
    r = conn.execute("""SELECT COUNT(*) n FROM daily_kline
                        WHERE date >= (SELECT MAX(date) FROM daily_kline) - 30
                          AND (ex_close IS NULL OR lxr_fc_close IS NULL)""").fetchone()
    tot = conn.execute("""SELECT COUNT(*) n FROM daily_kline
                          WHERE date >= (SELECT MAX(date) FROM daily_kline) - 30""").fetchone()
    return r[0], tot[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH), timeout=300)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")

    if a.verify:
        n, tot = report_gap(conn)
        print(f'  近 30 日：{tot:,} 行，其中 ex/lxr_fc 有空值 {n:,} 行')
        r = conn.execute("""SELECT COUNT(*) FROM daily_kline_adj WHERE close IS NULL""").fetchone()[0]
        print(f'  视图 close 为空：{r:,} 行（仅 ETF 属正常，应为 1,300）')
        conn.close()
        return 0

    t0 = time.time()
    print('=' * 74)
    print('1) ex_* 回填（从 open/high/low/close，等口径）')
    print('=' * 74)
    n1 = conn.execute("""SELECT COUNT(*) n FROM daily_kline
                         WHERE ex_close IS NULL AND close IS NOT NULL""").fetchone()[0]
    print(f'  待回填 {n1:,} 行')
    if n1 and a.apply:
        with conn:
            conn.execute("""UPDATE daily_kline
                            SET ex_open=open, ex_high=high, ex_low=low, ex_close=close
                            WHERE ex_close IS NULL AND close IS NOT NULL""")
        print(f'  ✅ ex_* 已回填 {n1:,} 行')
    elif not n1:
        print('  无需处理')

    print()
    print('=' * 74)
    print('2) lxr_fc_* 递推（change_pct 累积，无 API 调用）')
    print('=' * 74)
    # 基准：每只股票最后一个有 lxr_fc 的交易日
    base = {r['stock_code']: r for r in conn.execute("""
        SELECT k.stock_code, k.date, k.lxr_fc_close, k.ex_close
        FROM daily_kline k
        JOIN (SELECT stock_code, MAX(date) d FROM daily_kline
              WHERE lxr_fc_close IS NOT NULL GROUP BY stock_code) m
          ON m.stock_code = k.stock_code AND m.d = k.date""")}
    print(f'  基准股票 {len(base):,} 只')

    jobs, skipped = [], 0
    for code, b in base.items():
        rows = conn.execute("""SELECT date, ex_open, ex_high, ex_low, ex_close, change_pct
                               FROM daily_kline
                               WHERE stock_code=? AND date>? AND ex_close IS NOT NULL
                               ORDER BY date""", (code, b['date'])).fetchall()
        if not rows:
            skipped += 1
            continue
        jobs.append((code, b['lxr_fc_close'], rows))
    print(f'  需递推的股票 {len(jobs):,} 只，无新增的 {skipped:,} 只')
    print(f'  待写入行数 {sum(len(j[2]) for j in jobs):,}')

    if not a.apply:
        print('\n  （干跑模式，加 --apply 执行）')
        conn.close()
        return 0

    total, errs = 0, 0
    with conn:
        for code, base_lxr, rows in jobs:
            cum = 1.0
            payload = []
            for r in rows:
                cp = r['change_pct']
                if cp is None or cp <= -1:
                    errs += 1
                    continue
                cum *= (1.0 + cp)
                new_close = base_lxr * cum
                ex_c = r['ex_close']
                if not ex_c:
                    errs += 1
                    continue
                k = new_close / ex_c
                payload.append((
                    (r['ex_open'] * k) if r['ex_open'] else None,
                    (r['ex_high'] * k) if r['ex_high'] else None,
                    (r['ex_low'] * k) if r['ex_low'] else None,
                    new_close, code, r['date']))
            conn.executemany("""UPDATE daily_kline
                                SET lxr_fc_open=?, lxr_fc_high=?, lxr_fc_low=?, lxr_fc_close=?
                                WHERE stock_code=? AND date=?""", payload)
            total += len(payload)
    print(f'\n  ✅ lxr_fc_* 已递推写入 {total:,} 行'
          f'{f"，跳过异常 {errs:,} 行" if errs else ""}')

    print()
    n, tot = report_gap(conn)
    print(f'  近 30 日剩余空值：{n:,} / {tot:,} 行')
    r = conn.execute("SELECT COUNT(*) FROM daily_kline_adj WHERE close IS NULL").fetchone()[0]
    print(f'  视图 close 为空：{r:,} 行（1,300 为 ETF，属正常）')
    print(f'  耗时 {time.time()-t0:.1f}s')
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
