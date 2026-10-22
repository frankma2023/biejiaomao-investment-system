"""检验「红利 RSI 超额」到底来自哪个机制。

测两件事：
  A. 强买点事件在月份上是否聚集在 12 月（调样生效月）？
     若来自调样，事件应扎堆在调样前后；若均匀分布，则调样不是月度机制。
  B. 把强买点事件按当时的股息率十年分位拆开，高分红率 vs 低分红率谁更准？
     若来自「股息率锚/配置盘」，分红率高（=跌得便宜）时应该更准。

用法： python analysis/rsi20_mechanism.py
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
DIV = ['000922', '000015', 'H30269', '930955', '931468', '931848']


def load(con, code):
    rows = con.execute(
        "SELECT date, close FROM index_daily_kline WHERE stock_code=? AND kline_type='normal' "
        "AND date>=? AND date<=? ORDER BY date", (code, START, END)).fetchall()
    return [r[0] for r in rows], [float(r[1]) for r in rows]


def strong_buy_events(rsi):
    """段口径：强买点档（RSI20 < 35）每段只触发一次。"""
    ev, fired = [], set()
    for i, v in enumerate(rsi):
        if v is None:
            continue
        if v < BT[0]:
            t = 3 if v < BT[2] else 2 if v < BT[1] else 1
            if t and t not in fired:
                fired.add(t)
                if t == 2:
                    ev.append(i)
        else:
            fired = set()
    return ev


con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

# ── A. 月份分布 ──
print('A. 强买点事件的月份分布（合并 6 只红利指数）')
mon = [0] * 13
tot = 0
for code in DIV:
    ds, px = load(con, code)
    rsi = compute_rsi(px, 20)
    for i in strong_buy_events(rsi):
        if i + 60 >= len(px):
            continue
        mon[int(ds[i][5:7])] += 1
        tot += 1
for m in range(1, 13):
    bar = '█' * mon[m]
    print(f'   {m:>2}月 {mon[m]:>3} 次  {bar}')
print(f'   合计 {tot} 次。12 月 {mon[12]} 次，占 {mon[12] / tot * 100:.0f}%'
      f'（若与调样有关，12 月应明显偏高）')

# ── B. 按股息率分位拆 ──
print('\nB. 强买点事件按「当时股息率十年分位」拆开')
hi, lo = [], []
for code in DIV:
    ds, px = load(con, code)
    dyr = {r['date']: r['dyr_pct'] for r in con.execute(
        "SELECT date, dyr_pct FROM index_fundamental_daily WHERE stock_code=? "
        "AND dyr_pct IS NOT NULL", (code,))}
    if len(dyr) < 200:
        print(f'   （{code} 无股息率分位数据，跳过对照）')
        continue
    rsi = compute_rsi(px, 20)
    for i in strong_buy_events(rsi):
        if i + 60 >= len(px) or ds[i] not in dyr:
            continue
        r = px[i + 60] / px[i] - 1
        (hi if dyr[ds[i]] >= 0.5 else lo).append(r)


def st(v, lab):
    if not v:
        print(f'   {lab}: 无样本')
        return
    w = sum(1 for x in v if x > 0) / len(v) * 100
    print(f'   {lab:<28} n={len(v):>3}  60日胜率 {w:5.1f}%  均值 {np.mean(v) * 100:+.2f}%')


st(hi, '股息率分位 ≥50%（跌得便宜）')
st(lo, '股息率分位 <50%（不算便宜）')
st(hi + lo, '全部')
con.close()
