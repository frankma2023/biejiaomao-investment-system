#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补拉指定指数的历史基本面（PE/PB/股息率 + 10 年分位），用于扩展息差图。

多数指数的 index_fundamental_daily 从 2018/2024 才开始，而 K 线回溯更早，
导致「股息率 − 10Y国债」息差图被迫截断。本脚本按与
fetch_index_fundamental.py 相同的指标口径补拉。

用法:
    python scripts/fetch_index_fundamental_range.py --code 000510 --start 2016-01-01
    python scripts/fetch_index_fundamental_range.py --code 000510 --start 2016-01-01 --dry-run
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
API_PATH = '/index/fundamental'

METRICS = [
    'mc', 'tv', 'ta', 'to_r',
    'pe_ttm.mcw', 'pe_ttm.y10.mcw.cvpos',
    'pb.mcw', 'pb.y10.mcw.cvpos',
    'dyr.mcw', 'dyr.y10.mcw.cvpos',
]

FIELDS = ['mc', 'tv', 'ta', 'to_r', 'pe_ttm', 'pe_ttm_pct',
          'pb', 'pb_pct', 'dyr', 'dyr_pct']


def dig(d, path):
    # 该接口返回**扁平键**（如 pe_ttm.mcw），不是嵌套字典。
    # 先按扁平取，取不到再按嵌套兜底。
    if path in d:
        return d[path]
    cur = d
    for p in path.split('.'):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def chunks(start, end, years=9):
    """接口限制单次跨度 <=10 年。"""
    from datetime import date
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out = []
    while s < e:
        nxt = date(min(s.year + years, 9999), s.month, s.day)
        seg_end = min(nxt, e)
        out.append((s.isoformat(), seg_end.isoformat()))
        if seg_end >= e:
            break
        s = seg_end
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--code', required=True)
    ap.add_argument('--start', default='2016-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    end = a.end or datetime.now().date().isoformat()

    con = sqlite3.connect(DB)
    cols = [r[1] for r in con.execute('PRAGMA table_info(index_fundamental_daily)')]
    missing = [f for f in FIELDS if f not in cols]
    if missing:
        print(f'  ✗ 表缺字段: {missing}')
        return 1

    total = 0
    for s0, s1 in chunks(a.start, end):
        print(f'  拉取 {a.code} {s0} ~ {s1}', end=' ... ')
        try:
            r = api_post(API_PATH, {
                'stockCodes': [a.code],
                'startDate': s0, 'endDate': s1,
                'metricsList': METRICS,
            })
        except Exception as e:
            print(f'失败 {str(e)[:90]}')
            continue
        rows = r if isinstance(r, list) else (r.get('data') or [])
        print(f'{len(rows)} 条')
        if not rows or a.dry_run:
            continue
        now = datetime.now().isoformat(timespec='seconds')
        recs = []
        for d in rows:
            dt = (d.get('date') or '')[:10]
            if not dt:
                continue
            recs.append(tuple(
                [a.code, dt] + [dig(d, m) for m in METRICS] + [now]))
        con.executemany(
            f"INSERT OR REPLACE INTO index_fundamental_daily "
            f"(stock_code,date,{','.join(FIELDS)},updated_at) "
            f"VALUES ({','.join('?' * (len(FIELDS) + 3))})", recs)
        con.commit()
        total += len(recs)

    r = con.execute(
        "SELECT COUNT(*),MIN(date),MAX(date) FROM index_fundamental_daily "
        "WHERE stock_code=?", (a.code,)).fetchone()
    dyr_n = con.execute(
        "SELECT COUNT(*) FROM index_fundamental_daily WHERE stock_code=? "
        "AND dyr IS NOT NULL", (a.code,)).fetchone()[0]
    con.close()
    print(f'  写入 {total} 行；现覆盖 {r[0]} 行 {r[1]} ~ {r[2]}（有股息率 {dyr_n} 行）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
