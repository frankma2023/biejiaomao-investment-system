# -*- coding: utf-8 -*-
"""
scripts/fetch_bond_yield.py — 中国国债收益率拉取（akshare bond_zh_us_rate）
=================================================================
- 数据源：akshare bond_zh_us_rate（中债估值口径，日频）
- 表：bond_yield_daily（date 主键，y2/y5/y10/y30/spread_10_2）
- 模式：全量（--full 2018-01-01 起）/ 增量（表内 MAX(date) 起）
- 挂载：daily_update.py 盘后步骤 5.6
"""
import os
import sys
import sqlite3
import argparse
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')
FULL_START = '2018-01-01'

CREATE_SQL = """CREATE TABLE IF NOT EXISTS bond_yield_daily (
    date     TEXT PRIMARY KEY,
    y2  REAL,
    y5  REAL,
    y10 REAL,
    y30 REAL,
    spread_10_2 REAL,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
)"""

# 列名映射（akshare 中文列名，模糊匹配防上游改名）
COL_MAP = {
    'date': ['日期'],
    'y2': ['中国国债收益率2年'],
    'y5': ['中国国债收益率5年'],
    'y10': ['中国国债收益率10年'],
    'y30': ['中国国债收益率30年'],
    'spread_10_2': ['中国国债收益率10年-2年'],
}


def _match_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None


def fetch(start_date):
    """akshare 拉取，返回 [(date, y2, y5, y10, y30, spread), ...]"""
    import akshare as ak
    df = ak.bond_zh_us_rate(start_date=start_date)

    cols = {}
    for key, candidates in COL_MAP.items():
        c = _match_col(df, candidates)
        if c is None:
            print(f'⚠️ 列 {key} 未找到（候选 {candidates}），实际列: {list(df.columns)}', file=sys.stderr)
            return None
        cols[key] = c

    rows = []
    for _, r in df.iterrows():
        d = str(r[cols['date']])
        if not d or d < start_date:
            continue
        def g(k):
            v = r.get(cols[k])
            return float(v) if v is not None and v == v else None
        rows.append((d, g('y2'), g('y5'), g('y10'), g('y30'), g('spread_10_2')))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--full', action='store_true', help='全量（2018-01-01 起）')
    ap.add_argument('--date', help='指定拉取起始日期（YYYY-MM-DD）')
    args = ap.parse_args()

    db = sqlite3.connect(DB_PATH)
    try:
        db.execute(CREATE_SQL)

        if args.date:
            start = args.date
        elif args.full:
            start = FULL_START
        else:
            r = db.execute("SELECT MAX(date) FROM bond_yield_daily").fetchone()
            start = r[0] if r and r[0] else FULL_START
            if start:
                # 增量时从最后一条往前补 10 天（防最新日期缺失）
                start = (datetime.strptime(start, '%Y-%m-%d') - timedelta(days=10)).strftime('%Y-%m-%d')

        print(f'拉取国债收益率: {start} 起 ...')
        rows = fetch(start)
        if rows is None:
            print('❌ 数据源列名不匹配，中止（避免坏数据落库）')
            return 1
        if not rows:
            print('无新数据')
            return 0

        db.executemany("""INSERT OR REPLACE INTO bond_yield_daily
            (date, y2, y5, y10, y30, spread_10_2) VALUES (?,?,?,?,?,?)""", rows)
        db.commit()
        cnt = db.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM bond_yield_daily").fetchone()
        print(f'入库 {len(rows)} 条 | 表内共 {cnt[0]} 条 ({cnt[1]} ~ {cnt[2]})')
        return 0
    finally:
        db.close()


if __name__ == '__main__':
    sys.exit(main())
