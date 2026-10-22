"""RSI20 强买点在不同类型指数上的表现：红利 vs 宽基 vs 成长。

**统一用价格指数**（不用全收益），因为全收益数据只有红利系列，
用它会让红利占便宜。这里比的是「相对各自基准的超额」，与分红无关。

用法： python analysis/rsi20_whydividend.py
"""
import os
import sqlite3
import sys

import numpy as np

ROOT = os.getcwd()
sys.path.insert(0, ROOT)
from src.detectors.divergence import compute_rsi  # noqa: E402

DB = os.path.join(ROOT, 'data', 'lixinger.db')
START, END = '2016-09-28', '2026-09-28'
BT = [40, 35, 28]
ST = [67, 73, 78]

GROUPS = [
    ('红利类', [('000922', '中证红利'), ('000015', '红利指数'), ('H30269', '红利低波'),
                ('930955', '红利低波100'), ('931468', '红利质量'), ('931848', '800红利低波')]),
    ('宽基类', [('000300', '沪深300'), ('000905', '中证500'), ('000016', '上证50'),
                ('000852', '中证1000'), ('399006', '创业板指'), ('000688', '科创50')]),
]


def load(con, code):
    rows = con.execute(
        "SELECT date, close FROM index_daily_kline WHERE stock_code=? AND kline_type='normal' "
        "AND date>=? AND date<=? ORDER BY date", (code, START, END)).fetchall()
    return [r[0] for r in rows], [float(r[1]) for r in rows]


def tier(v, thr, buy):
    if buy:
        return 3 if v < thr[2] else 2 if v < thr[1] else 1 if v < thr[0] else 0
    return 3 if v > thr[2] else 2 if v > thr[1] else 1 if v > thr[0] else 0


def events(rsi, thr, buy):
    """段口径：每段连续超买/超卖里每档只触发一次。"""
    outer = thr[0]
    ev, fired = [], set()
    for i, v in enumerate(rsi):
        if v is None:
            continue
        if (v < outer) if buy else (v > outer):
            t = tier(v, thr, buy)
            if t and t not in fired:
                fired.add(t)
                ev.append((i, t))
        else:
            fired = set()
    return ev


con = sqlite3.connect(DB)
print(f'{"指数":<12}{"年数":>6}{"基准60日胜率":>13}'
      f'{"强买点胜率":>12}{"超额":>9}{"强买点后60日均值":>18}')
print('-' * 74)
for gname, codes in GROUPS:
    print(f'【{gname}】')
    for code, name in codes:
        ds, px = load(con, code)
        if len(px) < 400:
            print(f'  {name:<10} 数据不足（{len(px)} 天）')
            continue
        rsi = compute_rsi(px, 20)
        base = [px[i + 60] / px[i] - 1 for i in range(len(px) - 60)]
        bw = sum(1 for x in base if x > 0) / len(base) * 100
        ev = [(i, t) for i, t in events(rsi, BT, True) if t == 2 and i + 60 < len(px)]
        if not ev:
            continue
        v = [px[i + 60] / px[i] - 1 for i, _t in ev]
        w = sum(1 for x in v if x > 0) / len(v) * 100
        yrs = len(px) / 244.0
        print(f'  {name:<10}{yrs:>6.1f}{bw:>12.1f}%{w:>11.1f}%{w - bw:>+8.1f}'
              f'{np.mean(v) * 100:>17.2f}%  (n={len(ev)})')
con.close()
