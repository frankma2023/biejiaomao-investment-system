# -*- coding: utf-8 -*-
"""fetch_gold_daily.py — 抓伦敦金与上海金日线，落库 gold_daily。

数据源一：伦敦金（XAU/USD，美元/盎司）—— 新浪外盘期货日线
    https://stock.finance.sina.com.cn/futures/api/jsonp.php/var%20t=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=XAU
返回 JSONP，含 2006-11-21 以来全部日线（约 5,180 根）。

数据源二：上海金 Au99.99（人民币/克）—— akshare `spot_hist_sge`，约 2,400 根。
两者一起才能拆出「国际金价」与「汇率 + 国内溢价」各自贡献了多少：
    隐含人民币汇率 ≈ 上海金(元/克) × 31.1035 ÷ 伦敦金(美元/盎司)

为什么要这张表：A 股复盘经常要拿金价对照（避险/流动性）。本库原来没有任何
黄金序列，159934 这只 ETF 也只有价格、没有参照物。

用法：
    python scripts/fetch_gold_daily.py                 # 两个源都增量
    python scripts/fetch_gold_daily.py --full          # 全量重写
    python scripts/fetch_gold_daily.py --sources xau   # 只抓伦敦金
"""
import argparse
import datetime as dt
import json
import os
import socket
import sqlite3
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'data', 'lixinger.db')
URL = ('https://stock.finance.sina.com.cn/futures/api/jsonp.php/var%20t=/'
       'GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=XAU')
UA = {'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                     '(KHTML, like Gecko) Chrome/131.0 Safari/537.36'),
      'Referer': 'https://finance.sina.com.cn/'}

DDL = """CREATE TABLE IF NOT EXISTS gold_daily(
    source TEXT NOT NULL,          -- xau = 伦敦金现货（美元/盎司）；sge = 上海金 Au99.99（元/克）
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    updated_at TEXT,
    PRIMARY KEY(source, date))"""


def fetch_xau():
    r = requests.get(URL, headers=UA, timeout=30)
    r.raise_for_status()
    txt = r.text
    body = txt[txt.index('(') + 1: txt.rindex(')')]
    rows = json.loads(body)
    out = []
    for x in rows:
        try:
            out.append((str(x['date'])[:10], float(x['open']), float(x['high']),
                        float(x['low']), float(x['close'])))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda t: t[0])
    return out


def fetch_sge():
    """上海金 Au99.99，akshare。列：date/open/close/low/high。"""
    import akshare as ak
    df = ak.spot_hist_sge(symbol='Au99.99')
    out = []
    for r in df.to_dict('records'):
        try:
            out.append((str(r['date'])[:10], float(r['open']), float(r['high']),
                        float(r['low']), float(r['close'])))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda t: t[0])
    return out


FETCHERS = {'xau': fetch_xau, 'sge': fetch_sge}


def save(con, source, rows, full):
    if not rows:
        print(f'[FAIL] {source} 未取到任何数据')
        return 1
    last = None
    if not full:
        r = con.execute('SELECT MAX(date) FROM gold_daily WHERE source=?', (source,)).fetchone()
        last = r[0] if r else None
        if last:
            rows = [t for t in rows if t[0] > last]
        if not rows:
            print(f'[OK] {source} 无新数据（库内最新 {last}）')
            return 0

    now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    con.executemany(
        'INSERT OR REPLACE INTO gold_daily(source,date,open,high,low,close,updated_at)'
        ' VALUES(?,?,?,?,?,?,?)',
        [(source, d, o, h, l, c, now) for d, o, h, l, c in rows])
    con.commit()
    n = con.execute('SELECT COUNT(*), MIN(date), MAX(date) FROM gold_daily WHERE source=?',
                    (source,)).fetchone()
    print(f'[OK] {source} {"全量" if full or not last else "增量"}写入 {len(rows)} 行'
          f'（{rows[0][0]} ~ {rows[-1][0]}）；表内共 {n[0]} 行（{n[1]} ~ {n[2]}）')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=DB)
    ap.add_argument('--full', action='store_true')
    ap.add_argument('--sources', default='xau,sge', help='逗号分隔：xau,sge')
    ap.add_argument('--source', default=None, help='（兼容旧参数，等同 --sources）')
    a = ap.parse_args()
    socket.setdefaulttimeout(30)

    names = (a.source or a.sources).split(',')
    con = sqlite3.connect(a.db)
    con.execute(DDL)
    con.commit()

    bad = 0
    for s in [x.strip() for x in names if x.strip()]:
        fn = FETCHERS.get(s)
        if not fn:
            print(f'[SKIP] 未知源 {s}')
            continue
        try:
            bad |= save(con, s, fn(), a.full)
        except Exception as e:
            bad |= 1
            print(f'[FAIL] {s} {type(e).__name__}: {str(e)[:120]}')
    con.close()
    return bad


if __name__ == '__main__':
    sys.exit(main())
