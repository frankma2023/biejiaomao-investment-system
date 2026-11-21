# -*- coding: utf-8 -*-
"""fetch_gold_daily.py — 抓伦敦金（XAU/USD）日线，落库 gold_daily。

数据源：新浪外盘期货日线
    https://stock.finance.sina.com.cn/futures/api/jsonp.php/var%20t=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=XAU
返回 JSONP，含 2006-11-21 以来全部日线（约 5,180 根），字段 date/open/high/low/close。
单位：美元/盎司。

为什么要这张表：A 股复盘经常要拿金价对照（避险/流动性）。本库原来没有任何
黄金序列，159934 这只 ETF 也只有价格、没有参照物。

用法：
    python scripts/fetch_gold_daily.py            # 增量（从库内最大日期往后）
    python scripts/fetch_gold_daily.py --full     # 全量重写
"""
import argparse
import datetime as dt
import json
import os
import re
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
    source TEXT NOT NULL,          -- xau = 伦敦金现货（美元/盎司）
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    updated_at TEXT,
    PRIMARY KEY(source, date))"""


def fetch():
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=DB)
    ap.add_argument('--full', action='store_true')
    ap.add_argument('--source', default='xau')
    a = ap.parse_args()
    socket.setdefaulttimeout(30)

    con = sqlite3.connect(a.db)
    con.execute(DDL)
    con.commit()

    rows = fetch()
    if not rows:
        print('[FAIL] 未取到任何数据')
        return 1

    have = 0
    if not a.full:
        r = con.execute('SELECT MAX(date) FROM gold_daily WHERE source=?',
                        (a.source,)).fetchone()
        last = r[0] if r else None
        if last:
            rows = [t for t in rows if t[0] > last]
            have = 1
        if not rows:
            print(f'[OK] 无新数据（库内最新 {last}）')
            con.close()
            return 0

    now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    con.executemany(
        'INSERT OR REPLACE INTO gold_daily(source,date,open,high,low,close,updated_at)'
        ' VALUES(?,?,?,?,?,?,?)',
        [(a.source, d, o, h, l, c, now) for d, o, h, l, c in rows])
    con.commit()
    n = con.execute('SELECT COUNT(*), MIN(date), MAX(date) FROM gold_daily WHERE source=?',
                    (a.source,)).fetchone()
    first, last_new = rows[0][0], rows[-1][0]
    print(f'[OK] 伦敦金 {"增量" if have else "全量"}写入 {len(rows)} 行'
          f'（{first} ~ {last_new}）；表内共 {n[0]} 行（{n[1]} ~ {n[2]}）')
    con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
