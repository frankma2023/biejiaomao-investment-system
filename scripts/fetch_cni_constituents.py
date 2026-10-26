#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 akshare 补齐国证系列（98xxxx）指数的成分股，并据此聚合指数级财务。

背景：理杏仁的指数财报端点（non_financial / security / bank / ...）不覆盖国证系列，
表现为返回 0 条。但 akshare 的 index_detail_cni 能取到国证指数成分与权重，
据此可以用本地个股财报（stock_financials_annual）加权聚合出指数级财务。

用法:
    python scripts/fetch_cni_constituents.py --code 980081
    python scripts/fetch_cni_constituents.py --code 980081 --aggregate
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = str(ROOT / 'data' / 'lixinger.db')


def fetch_constituents(code):
    import akshare as ak
    df = ak.index_detail_cni(symbol=code)
    if df is None or df.empty:
        return []
    latest = df['日期'].max()
    df = df[df['日期'] == latest]
    return [{'date': str(r['日期'])[:10], 'stock_code': str(r['样本代码']).zfill(6),
             'name': r['样本简称'], 'industry': r['所属行业'],
             'mcap': float(r['总市值']), 'weight': float(r['权重'])}
            for _, r in df.iterrows()]


def save_constituents(con, code, rows):
    con.execute("""
        CREATE TABLE IF NOT EXISTS index_constituents_cni (
            index_code TEXT NOT NULL, date TEXT NOT NULL,
            stock_code TEXT NOT NULL, stock_name TEXT, industry TEXT,
            mcap REAL, weight REAL, updated_at TEXT,
            PRIMARY KEY (index_code, date, stock_code))
    """)
    now = datetime.now().isoformat(timespec='seconds')
    con.executemany(
        "INSERT OR REPLACE INTO index_constituents_cni VALUES (?,?,?,?,?,?,?,?)",
        [(code, r['date'], r['stock_code'], r['name'], r['industry'],
          r['mcap'], r['weight'], now) for r in rows])
    con.commit()


def aggregate_financials(con, code, rows):
    """按权重把个股年报加权聚合成指数级财务。

    口径：weight 为百分比，先归一化；
    营收/利润/分红为「加权合计」（单位元），ROE/利润率为「加权平均」。
    """
    names = [r['stock_code'] for r in rows]
    wmap = {r['stock_code']: r['weight'] for r in rows}
    tot_w = sum(wmap.values()) or 1.0
    q = ("SELECT stock_code, report_date, revenue, net_profit, roe, "
         "revenue, net_profit FROM stock_financials_annual "
         "WHERE stock_code IN (%s)" % ','.join('?' * len(names)))
    try:
        data = con.execute(q, names).fetchall()
    except sqlite3.Error as e:
        print(f'  ✗ 个股财报查询失败: {e}')
        return 0
    if not data:
        print('  ✗ 成分股在本库无年报数据')
        return 0

    by_year = {}
    for sc, rd, rev, np_, roe, _r2, _n2 in data:
        y = (rd or '')[:4]
        if not y or not y.isdigit():
            continue
        w = wmap.get(sc, 0.0) / tot_w
        d = by_year.setdefault(y, {'rev': 0.0, 'np': 0.0, 'roe_w': 0.0, 'w_sum': 0.0})
        if rev:
            d['rev'] += rev * w
        if np_:
            d['np'] += np_ * w
        if roe is not None:
            d['roe_w'] += roe * w
            d['w_sum'] += w

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
    for y in sorted(by_year):
        d = by_year[y]
        roe = d['roe_w'] / d['w_sum'] if d['w_sum'] else None
        nm = (d['np'] / d['rev']) if d['rev'] else None
        out.append((code, f'{y}-12-31', d['rev'] or None, None, d['np'] or None,
                    roe, None, nm, None, None, now))
    con.execute("DELETE FROM index_financials_annual WHERE stock_code=?", (code,))
    con.executemany(
        "INSERT OR REPLACE INTO index_financials_annual VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?)", out)
    con.commit()
    print(f'  聚合出 {len(out)} 个年度：{out[0][1][:4] if out else "—"} ~ {out[-1][1][:4] if out else "—"}')
    return len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--code', required=True)
    ap.add_argument('--aggregate', action='store_true', help='顺便聚合财务')
    a = ap.parse_args()

    print(f'  akshare 拉取 {a.code} 成分股')
    try:
        rows = fetch_constituents(a.code)
    except Exception as e:
        print(f'  ✗ 失败: {str(e)[:150]}')
        return 1
    if not rows:
        print('  ✗ 无成分数据')
        return 1
    print(f'  取到 {len(rows)} 只成分（{rows[0]["date"]}），权重合计 {sum(r["weight"] for r in rows):.2f}%')
    ind = {}
    for r in rows:
        ind[r['industry']] = ind.get(r['industry'], 0) + r['weight']
    print('  行业分布（前 5）:', sorted(ind.items(), key=lambda x: -x[1])[:5])

    con = sqlite3.connect(DB)
    save_constituents(con, a.code, rows)
    print(f'  写入 index_constituents_cni {len(rows)} 行')

    if a.aggregate:
        aggregate_financials(con, a.code, rows)
    con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
