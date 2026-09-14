#!/usr/bin/env python3
"""
scripts/snapshot_engine_baseline.py — 迁移前固化引擎输出基线

用途：迁移（改价格口径）之前先把引擎落盘结果导出来，改完重算后与之对照，
      才能判断是"修好了"还是"改坏了"。

用法
----
    python scripts/snapshot_engine_baseline.py --engine rs      # stock_rs_daily
    python scripts/snapshot_engine_baseline.py --engine cpa     # cpa_stage_daily
    python scripts/snapshot_engine_baseline.py --engine all
    python scripts/snapshot_engine_baseline.py --compare rs     # 与基线对照
"""

import argparse
import csv
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, 'scripts')
from common import DB_PATH  # noqa: E402

SPEC = {
    'rs': ('analysis/_baseline_rs.csv',
           "SELECT stock_code, date, rps_20, rps_60, rps_250, ret_20, ret_60, ret_120, ret_250 "
           "FROM stock_rs_daily WHERE date >= '2026-01-01' ORDER BY stock_code, date"),
    'cpa': ('analysis/_baseline_cpa.csv',
            "SELECT stock_code, date, stage FROM cpa_stage_daily "
            "WHERE date >= '2026-01-01' ORDER BY stock_code, date"),
}


def dump(key):
    path, sql = SPEC[key]
    conn = sqlite3.connect(str(DB_PATH), timeout=600)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql).fetchall()
    if not rows:
        print(f'  ⚠ {key}: 无数据，跳过')
        return
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(rows[0].keys())
        w.writerows([tuple(r) for r in rows])
    print(f'  ✅ {key}: 基线 {len(rows):,} 行 → {path}')
    conn.close()


def compare(key):
    path, sql = SPEC[key]
    if not os.path.exists(path):
        print(f'  ⚠ {key}: 找不到基线 {path}，先跑一次不带 --compare 的')
        return
    old = {}
    with open(path, encoding='utf-8') as f:
        rd = csv.DictReader(f)
        cols = [c for c in rd.fieldnames if c not in ('stock_code', 'date')]
        for r in rd:
            old[(r['stock_code'], r['date'])] = r
    conn = sqlite3.connect(str(DB_PATH), timeout=600)
    conn.row_factory = sqlite3.Row
    new = {(r['stock_code'], r['date']): r for r in conn.execute(sql)}
    print(f'  {key}: 基线 {len(old):,} 行 / 现表 {len(new):,} 行')
    only_old = set(old) - set(new)
    only_new = set(new) - set(old)
    if only_old:
        print(f'    仅基线有（消失）{len(only_old):,} 行，例：{list(only_old)[:3]}')
    if only_new:
        print(f'    仅现表有（新增）{len(only_new):,} 行，例：{list(only_new)[:3]}')
    if key == 'cpa':
        from collections import Counter
        co = Counter(old[k]['stage'] for k in old)
        cn = Counter(new[k]['stage'] for k in new)
        print(f'    {"阶段":<10}{"改前":>10}{"改后":>10}')
        for s in sorted(set(co) | set(cn)):
            print(f'    {str(s):<10}{co.get(s,0):>10,}{cn.get(s,0):>10,}')
    else:
        rps_moved = moved = 0
        common = set(old) & set(new)
        for k in common:
            code_, date_ = k
            o = old[k]
            n = {c: new[k][c] for c in cols}
            if str(o.get('rps_250')) != str(n.get('rps_250')):
                rps_moved += 1
            ov, nv = o.get('ret_250'), n.get('ret_250')
            if (ov is None) != (nv is None):
                moved += 1
            elif ov is not None and abs(float(ov) - float(nv)) > 1e-9:
                moved += 1
        if common:
            print(f'    rps_250 变化 {rps_moved:,} 行（{rps_moved/len(common)*100:.1f}%）')
            print(f'    ret_250 变化 {moved:,} 行（{moved/len(common)*100:.1f}%）')
    conn.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--engine', default='all')
    ap.add_argument('--compare', action='store_true')
    a = ap.parse_args()
    keys = list(SPEC) if a.engine == 'all' else [a.engine]
    for k in keys:
        (compare if a.compare else dump)(k)
