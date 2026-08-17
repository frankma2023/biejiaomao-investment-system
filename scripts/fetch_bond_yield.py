# -*- coding: utf-8 -*-
"""
scripts/fetch_bond_yield.py — 中国国债收益率拉取（akshare bond_zh_us_rate）
=================================================================
- 数据源：akshare bond_zh_us_rate（中债估值口径，日频）
- 表：bond_yield_daily（date 主键，y2/y5/y10/y30/spread_10_2）
- 模式：全量（--full 2018-01-01 起）/ 增量（表内 MAX(date) 起）
- 挂载：daily_update.py 盘后步骤
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


def fetch(start_date):
    """akshare 拉取，返回 [(date, y2, y5, y10, y30, spread), ...]"""
    import akshare as ak
    df = ak.bond_zh_us_rate(start_date=start_date)
    rows = []
    for _, r in df.iterrows():
        d = str(r['日期'])
        if not d or d < start_date:
            continue
        def g(k):
            v = r.get(k)
            return float(v) if v is not None and v == v else None
        rows.append((d, g('中国国债收益率2年'), g('中国国债收益率5年'),
                     g('中国国债收益率10年'), g('中国国债收益率30年'),
                     g('中国国债收益率10年-2年')))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--full', action='store_true', help='全量（2018-01-01 起）')
    ap.add_argument('--date', help='指定拉取起始日期（YYYY-MM-DD）')
    args = ap.parse_args()

    db = sqlite3.connect(DB_PATH)
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
    if not rows:
        print('无新数据')
        db.close()
        return

    db.executemany("""INSERT OR REPLACE INTO bond_yield_daily
        (date, y2, y5, y10, y30, spread_10_2) VALUES (?,?,?,?,?,?)""", rows)
    db.commit()
    cnt = db.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM bond_yield_daily").fetchone()
    print(f'入库 {len(rows)} 条 | 表内共 {cnt[0]} 条 ({cnt[1]} ~ {cnt[2]})')
    db.close()


if __name__ == '__main__':
    main()
