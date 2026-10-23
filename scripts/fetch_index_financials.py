#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取指数财报数据（理杏仁 /api/cn/index/fs/non_financial）→ index_financials_annual。

关键点（踩过的坑）：
  - 端点必须是 /index/fs/non_financial，不是 /fs/hybrid（后者对指数返回空）
  - 返回是**顶层列表**，不是 {'data': [...]}
  - 单次时间跨度 ≤10 年，需分段
  - 年频用 y. 前缀，字段形如 y.ps.toi.t

用法:
    python scripts/fetch_index_financials.py --code 399998
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

from common import api_post  # noqa: E402

DB = str(ROOT / 'data' / 'lixinger.db')

FIELDS = [
    ('revenue', 'y.ps.toi.t'),            # 营业总收入
    ('operating_profit', 'y.ps.op.t'),    # 营业利润
    ('net_profit', 'y.ps.npatoshopc.t'),  # 归母净利润
    ('roe', 'y.m.roe.t'),                 # ROE（小数）
    ('eps', 'y.m.npatoshopc_ps.t'),       # 每股收益
    ('net_margin', 'y.m.np_s_r.t'),       # 净利润率（小数）
    ('op_margin', 'y.ps.op_s_r.t'),       # 营业利润率（小数）
    ('dividend_amount', 'y.ps.da_om.t'),  # A股分红金额
]

SEGMENTS = [('2013-01-01', '2022-12-31'), ('2023-01-01', '2026-12-31')]


def dig(d, path):
    cur = d
    for p in path.split('.'):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _table_exists(con, name):
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--code', required=True)
    ap.add_argument('--endpoint', default='non_financial',
                    choices=['non_financial', 'security', 'bank', 'insurance',
                             'other_financial', 'hybrid'],
                    help='按指数所属行业选择端点：煤炭等产业用 non_financial，券商用 security')
    a = ap.parse_args()

    metrics = [c for _, c in FIELDS]
    all_rows = []
    for s0, s1 in SEGMENTS:
        try:
            r = api_post('/index/fs/' + a.endpoint, {
                'startDate': s0, 'endDate': s1,
                'stockCodes': [a.code], 'metricsList': metrics,
            })
        except Exception as e:
            print(f'  ✗ {s0}~{s1}: {e}')
            continue
        rows = r if isinstance(r, list) else r.get('data', [])
        print(f'  {s0}~{s1}: {len(rows)} 条')
        all_rows.extend(rows)

    if not all_rows:
        print('  ✗ 无数据')
        return 1

    by_date = {}
    for d in all_rows:
        by_date[(d.get('date') or '')[:10]] = d
    dates = sorted(by_date)
    print(f'  合并后 {len(dates)} 个报告期：{dates[0]} ~ {dates[-1]}')

    con = sqlite3.connect(DB)
    con.execute("DELETE FROM index_financials_annual WHERE stock_code=?", (a.code,)) if _table_exists(con, 'index_financials_annual') else None
    con.execute("""
        CREATE TABLE IF NOT EXISTS index_financials_annual (
            stock_code TEXT NOT NULL, date TEXT NOT NULL,
            revenue REAL, operating_profit REAL, net_profit REAL,
            roe REAL, eps REAL, net_margin REAL, op_margin REAL,
            dividend_amount REAL, updated_at TEXT,
            PRIMARY KEY (stock_code, date))
    """)
    now = datetime.now().isoformat(timespec='seconds')
    out = []
    filled = {k: 0 for k, _ in FIELDS}
    for dt in dates:
        d = by_date[dt]
        rec = [a.code, dt]
        for k, code in FIELDS:
            v = dig(d, code)
            if v is not None:
                filled[k] += 1
            rec.append(v)
        rec.append(now)
        out.append(tuple(rec))
    con.executemany(
        "INSERT OR REPLACE INTO index_financials_annual VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?)", out)
    con.commit()
    con.close()

    print(f'  写入 index_financials_annual {len(out)} 行')
    for k, _ in FIELDS:
        print(f'    {k:18} {filled[k]}/{len(out)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
