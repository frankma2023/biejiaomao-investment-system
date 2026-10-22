#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日指数 CPA 扫描：对全部指数跑八阶段状态机，把最新阶段写入 index_cpa_daily。

表结构（主键 stock_code + date，保留历史便于追踪阶段变化）：
  stock_code, date, stage, stage_name, action, regime, close,
  days_in_stage, since_date, close_position（价在 EMA20 的 ATR 倍数）,
  index_name, updated_at

用法：
    python scripts/scan_index_cpa_daily.py                # 扫描今天
    python scripts/scan_index_cpa_daily.py --date 2026-10-19
    python scripts/scan_index_cpa_daily.py --no-save      # 只打印
"""
import argparse
import sqlite3
import sys
from datetime import date as _date
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from scanners.cpa_index import ACTION, STAGE_NAME, load, run_state_machine  # noqa: E402

DB = str(ROOT / 'data' / 'lixinger.db')
REGIME_NAME = {'UP': '上行', 'DOWN': '下行', 'RANGE': '箱体'}


def ensure_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS index_cpa_daily (
            stock_code    TEXT NOT NULL,
            date          TEXT NOT NULL,
            stage         INTEGER,
            stage_name    TEXT,
            action        TEXT,
            regime        TEXT,
            close         REAL,
            days_in_stage INTEGER,
            since_date    TEXT,
            dev20         REAL,
            index_name    TEXT,
            updated_at    TEXT,
            PRIMARY KEY (stock_code, date))
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_cpa_date ON index_cpa_daily(date)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_cpa_stage ON index_cpa_daily(stage)")
    con.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='截止日期，默认最新')
    ap.add_argument('--no-save', action='store_true')
    a = ap.parse_args()

    con = sqlite3.connect(DB)
    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT stock_code FROM index_daily_kline "
        "WHERE kline_type='normal' GROUP BY stock_code HAVING COUNT(*)>="
        "(SELECT 400)")]
    names = {}
    try:
        for c, n in con.execute(
                "SELECT DISTINCT index_code, index_name FROM stock_index "
                "WHERE index_name IS NOT NULL AND index_name<>''"):
            names.setdefault(c, n)
    except sqlite3.Error:
        pass

    if not a.no_save:
        ensure_table(con)

    now = datetime.now().isoformat(timespec='seconds')
    cutoff = a.date
    stage_count = {}
    rows_out = []
    done = 0

    for code in codes:
        rows = load(con, code)
        if not rows:
            continue
        if cutoff:
            rows = [r for r in rows if r[0] <= cutoff]
            if len(rows) < 400:
                continue
        dates = [r[0] for r in rows]
        o = [r[1] for r in rows]
        h = [r[2] for r in rows]
        lo = [r[3] for r in rows]
        c = [r[4] for r in rows]
        v = [r[5] if r[5] else 0.0 for r in rows]

        stage, _, ind = run_state_machine(o, h, lo, c, v)
        last = None
        for k in range(len(stage) - 1, -1, -1):
            if stage[k] is not None:
                last = k
                break
        if last is None:
            continue

        st = stage[last]
        k = last
        while k - 1 >= 0 and stage[k - 1] == st:
            k -= 1
        dev = None
        if ind['a20'][last]:
            dev = round((c[last] - ind['e20'][last]) / ind['a20'][last], 2)

        rec = (code, dates[last], st, STAGE_NAME[st], ACTION[st],
               ind['regime'][last], round(c[last], 2), last - k + 1,
               dates[k], dev, names.get(code), now)
        rows_out.append(rec)
        stage_count[st] = stage_count.get(st, 0) + 1
        done += 1

    if not a.no_save and rows_out:
        con.executemany(
            "INSERT OR REPLACE INTO index_cpa_daily VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?)", rows_out)
        con.commit()

    print(f'  扫描 {done} 个指数'
          + (f'，写入 index_cpa_daily {len(rows_out)} 行' if not a.no_save else '（未写库）'))
    for st in sorted(stage_count):
        print(f'    阶段{st} {STAGE_NAME[st][:26]:<28} {stage_count[st]:>4} 个')

    if a.no_save:
        print()
        print('  最新明细（前 20）：')
        for r in sorted(rows_out, key=lambda x: x[2])[:20]:
            print(f'    {r[0]} {(r[10] or "")[:10]:<11} 阶段{r[2]} '
                  f'{r[7]:>4}日  {(r[5] or ""):<5} {r[4]}')
    con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
