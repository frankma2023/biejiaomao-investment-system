# -*- coding: utf-8 -*-
"""抓美股低波红利类 ETF 日线（新浪 qfq 源），供 800红利低波 报告的国际同类对照。

标的：
  SPLV  Invesco S&P 500 Low Volatility ETF   —— 低波（无红利筛选）
  USMV  iShares MSCI USA Min Vol Factor ETF  —— 最小波动
  NOBL  ProShares S&P 500 Dividend Aristocrats —— 红利质量/连续分红
  SPY   SPDR S&P 500 ETF                     —— 基准
  DVY   iShares Select Dividend ETF          —— 高股息（最接近红利）
  SCHD  Schwab US Dividend Equity ETF        —— 红利质量+低波混合

输出 .scratch/us_divlow_peers.json，结构 {source, data:{symbol:[[date,close],...]}}
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / '.scratch' / 'us_divlow_peers.json'

SYMBOLS = ['SPLV', 'USMV', 'NOBL', 'SPY', 'DVY', 'SCHD']


def main():
    import akshare as ak
    out = {'source': 'akshare.stock_us_daily(sina)', 'data': {}, 'data_raw': {}}
    for s in SYMBOLS:
        for adj_key, adj in (('data', 'qfq'), ('data_raw', '')):
            try:
                df = ak.stock_us_daily(symbol=s, adjust=adj)
            except Exception as e:
                print(f'  {s} {adj_key}: FAIL {e}')
                continue
            if df is None or len(df) == 0:
                print(f'  {s} {adj_key}: empty')
                continue
            df = df.dropna(subset=['close'])
            recs = [[str(r['date'])[:10], float(r['close'])] for _, r in df.iterrows()]
            out[adj_key][s] = recs
            print(f'  {s} {adj_key}: {len(recs)} rows {recs[0][0]} ~ {recs[-1][0]}')
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
    print('saved', OUT, OUT.stat().st_size, 'bytes')


if __name__ == '__main__':
    sys.exit(main())
