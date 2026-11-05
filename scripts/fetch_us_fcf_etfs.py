#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取美股自由现金流 / 红利同类 ETF，并与国证自由现金流（980092）做同期对照。

数据源：akshare <stock_us_daily>（新浪美股，adjust='qfq' 前复权）。
产出：.scratch/fcf_intl.json（供 docs/report/make_fcf100_report.py 读取）

用法：python scripts/fetch_us_fcf_etfs.py
"""
import json
import os
import warnings
from datetime import datetime

import akshare as ak

warnings.filterwarnings('ignore')

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(PROJECT, '.scratch', 'fcf_intl.json')

# ticker -> (名称, 角色)
TICKERS = {
    'COWZ': ('Pacer US Cash Cows 100', '现金流旗舰'),
    'CALF': ('Pacer US Small Cap Cash Cows 100', '现金流·小盘'),
    'SCHD': ('Schwab US Dividend Equity', '红利对照'),
    'SPY': ('SPDR S&P 500', '美股基准'),
    'VOO': ('Vanguard S&P 500', '美股基准2'),
}


def fetch(sym):
    df = ak.stock_us_daily(symbol=sym, adjust='qfq')
    df = df.dropna(subset=['close'])
    # 新浪美股复权序列偶发 0 / 负值（停牌填充），必须先剔除，否则年化会变成复数
    out = {}
    for d, c in zip(df['date'], df['close']):
        try:
            v = float(c)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out[str(d)[:10]] = v
    return out


def cagr(s, ds):
    if len(ds) < 30:
        return None
    yrs = (len(ds) - 1) / 252
    return ((s[ds[-1]] / s[ds[0]]) ** (1 / yrs) - 1) * 100


def mdd(s, ds):
    peak, m = s[ds[0]], 0.0
    for d in ds:
        peak = max(peak, s[d])
        m = min(m, s[d] / peak - 1)
    return m * 100


def vol(s, ds):
    r = [s[ds[i]] / s[ds[i - 1]] - 1 for i in range(1, len(ds))]
    mu = sum(r) / len(r)
    return (sum((x - mu) ** 2 for x in r) / len(r)) ** 0.5 * (252 ** 0.5) * 100


def main():
    data = {}
    for sym in TICKERS:
        try:
            data[sym] = fetch(sym)
            ds = sorted(data[sym])
            print(f'  ✓ {sym:<5} {len(ds):>5} 行  {ds[0]} ~ {ds[-1]}')
        except Exception as e:
            print(f'  ✗ {sym} {type(e).__name__}: {str(e)[:80]}')

    # 窗口锚定在 COWZ 成立日：新浪 qfq 在 SPY(2002)/SCHD(2012) 早期段有坏数据
    # （单日 ±100%~345% 的跳变），裁掉后各序列才可比。
    anchor = sorted(data['COWZ'])[0] if 'COWZ' in data else min(
        sorted(v)[0] for v in data.values())
    win = {s: {d: v for d, v in data[s].items() if d >= anchor} for s in data}

    print()
    print(f'窗口起点 {anchor}（COWZ 成立日）—— 检查窗口内是否有异常跳变：')
    for s in win:
        ds = sorted(win[s])
        bad = [(ds[i], round((win[s][ds[i]] / win[s][ds[i - 1]] - 1) * 100, 1))
               for i in range(1, len(ds))
               if abs(win[s][ds[i]] / win[s][ds[i - 1]] - 1) > 0.35]
        print(f'  {s:<5} {len(ds):>5} 行  {ds[0]} ~ {ds[-1]}  '
              f'异常跳变 {len(bad)} 处' + (f' 例 {bad[:3]}' if bad else ''))

    base = 'COWZ'
    bd = sorted(win[base])

    print()
    print('=' * 80)
    print(f'同期对照（{bd[0]} ~ {bd[-1]}，{(len(bd) - 1) / 252:.2f} 年，前复权 qfq）')
    print('=' * 80)
    summary = {}
    for sym, (name, role) in TICKERS.items():
        if sym not in win:
            continue
        ds = sorted(set(win[sym]) & set(bd))
        if len(ds) < 100:
            continue
        summary[sym] = {'name': name, 'role': role,
                        'start': ds[0], 'end': ds[-1], 'n': len(ds),
                        'cagr': cagr(win[sym], ds), 'mdd': mdd(win[sym], ds),
                        'vol': vol(win[sym], ds),
                        'series': {d: win[sym][d] for d in ds}}
        print(f'  {sym:<5}{name[:36]:<38}年化 {summary[sym]["cagr"]:>6.2f}%  '
              f'回撤 {summary[sym]["mdd"]:>7.1f}%  波动 {summary[sym]["vol"]:>5.1f}%')

    print()
    print('=' * 80)
    print('COWZ 自身最长区间（其成立以来全部实盘，无回溯）')
    print('=' * 80)
    if 'COWZ' in data:
        ds = sorted(data['COWZ'])
        print(f'  {ds[0]} ~ {ds[-1]}  {(len(ds) - 1) / 252:.2f} 年  '
              f'年化 {cagr(data["COWZ"], ds):.2f}%  回撤 {mdd(data["COWZ"], ds):.1f}%  '
              f'波动 {vol(data["COWZ"], ds):.1f}%')

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'asof': datetime.now().strftime('%Y-%m-%d %H:%M'),
                   'source': 'akshare stock_us_daily(adjust=qfq)，新浪美股',
                   'anchor': anchor,
                   'tickers': {k: list(v) for k, v in TICKERS.items()},
                   'summary': {k: {kk: vv for kk, vv in v.items() if kk != 'series'}
                               for k, v in summary.items()},
                   'series': {k: v['series'] for k, v in summary.items()}},
                  f, ensure_ascii=False)
    print('\n已写出', OUT)


if __name__ == '__main__':
    main()
