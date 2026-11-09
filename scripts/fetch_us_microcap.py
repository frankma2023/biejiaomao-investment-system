"""Fetch US micro-cap peers for the 400/100 microcap index (index-research §1.6).

Peers chosen:
  IWC  iShares Micro-Cap ETF       —— 直接同类（美股微盘）
  IWM  iShares Russell 2000 ETF    —— 美股小盘基准（同类的上一档）
  SPY  SPDR S&P 500 ETF            —— 美国当地宽基基准
  IJR  iShares S&P SmallCap 600    —— 补充小盘对照

Source ladder (§1.6.2):
  1 local db          -> no US series, skipped by design
  2 ak.stock_us_daily(symbol=..., adjust='qfq')   <- primary, sina
  2b ak.stock_us_hist(symbol='106.IWC', ...)      <- fallback, eastmoney
Output: .scratch/us_microcap_peers.json  (symbol -> [[date, close], ...])
Also prints the |daily move| > 35% scan required by §1.6.5.
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUT = r'D:\hanako\investment-system\.scratch\us_microcap_peers.json'
SYMBOLS = ['IWC', 'IWM', 'IJR', 'SPY']

import akshare as ak


def via_sina(sym):
    df = ak.stock_us_daily(symbol=sym, adjust='qfq')
    rows = []
    for _, r in df.iterrows():
        d = str(r['date'])[:10]
        try:
            c = float(r['close'])
        except (TypeError, ValueError):
            continue
        if c > 0:
            rows.append([d, c])
    rows.sort(key=lambda x: x[0])
    return rows, 'akshare.stock_us_daily(sina,qfq)'


def via_em(sym):
    df = ak.stock_us_hist(symbol='106.' + sym, period='daily',
                          start_date='19900101', end_date='20991231', adjust='qfq')
    rows = []
    for _, r in df.iterrows():
        d = str(r['日期'])[:10]
        c = float(r['收盘'])
        if c > 0:
            rows.append([d, c])
    rows.sort(key=lambda x: x[0])
    return rows, 'akshare.stock_us_hist(em,qfq)'


def scan_bad(rows):
    bad = []
    for i in range(1, len(rows)):
        a, b = rows[i - 1][1], rows[i][1]
        if a > 0:
            mv = b / a - 1
            if abs(mv) > 0.35:
                bad.append((rows[i][0], round(mv * 100, 1)))
    return bad


def main():
    src = {}
    data = {}
    for s in SYMBOLS:
        for name, fn in (('sina', via_sina), ('em', via_em)):
            try:
                t0 = time.time()
                rows, how = fn(s)
                if len(rows) < 250:
                    print('%s via %s 只有 %d 行, 换源' % (s, name, len(rows)))
                    continue
                data[s] = rows
                src[s] = how
                bad = scan_bad(rows)
                print('%-4s %-38s %4d 行  %s ~ %s  |涨跌|>35%%: %d 处  %.0fs'
                      % (s, how, len(rows), rows[0][0], rows[-1][0], len(bad), time.time() - t0))
                if bad:
                    print('     坏点:', bad[:12])
                break
            except Exception as e:
                print('%s via %s 失败: %s' % (s, name, e))
    json.dump({'source': src, 'data': data}, open(OUT, 'w', encoding='utf-8'))
    print('wrote', OUT, len(data), 'symbols')


if __name__ == '__main__':
    main()
