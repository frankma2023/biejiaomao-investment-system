# -*- coding: utf-8 -*-
"""CANSLIM v3.6 回测：N 维度新旧定义对前瞻 60 日收益的区分力。

隔离变量：只换 N 的算法（N1 新高分档 + N2 形态窗口/衰减/上限），
其余维度不参与，避免 C/S/L/I 的噪声。

样本：2024-11 ~ 2026-11 每月最后一个交易日，全市场（过滤停牌/新股）。
指标：Spearman IC（逐月，再取均值）、十分位前瞻收益、多空差。
"""
import json
import sqlite3
from collections import defaultdict
from datetime import datetime

import numpy as np

DB = 'file:data/lixinger.db?mode=ro'
con = sqlite3.connect(DB, uri=True)
con.row_factory = sqlite3.Row

print('=== 1. 交易日历 ===')
dates = [r[0] for r in con.execute(
    "SELECT DISTINCT date FROM daily_kline_adj WHERE date>='2023-10-01' ORDER BY date")]
print(f'  {len(dates)} 个交易日 {dates[0]} ~ {dates[-1]}')

# 月末采样点（每月最后一个交易日），且后面还有 60 个交易日可算前瞻
FWD = 60
sample = []
seen = set()
for d in dates:
    ym = d[:7]
    if ym not in seen:
        seen.add(ym)
        sample.append(d)
sample = [d for d in sample if dates.index(d) + FWD < len(dates)]
# 逐月的月末
monthend = []
for i, d in enumerate(dates):
    nxt = dates[i + 1] if i + 1 < len(dates) else None
    if nxt is None or nxt[:7] != d[:7]:
        monthend.append(d)
monthend = [d for d in monthend if dates.index(d) + FWD < len(dates)]
print(f'  月末采样 {len(monthend)} 个：{monthend[0]} ~ {monthend[-1]}')

print()
print('=== 2. 载入行情（2023-11 起，预留 250 日预热）===')
px = defaultdict(dict)     # code -> {date: close}
hi = defaultdict(dict)
for r in con.execute("SELECT stock_code, date, close, high FROM daily_kline_adj "
                     "WHERE date>='2023-11-01'"):
    if r['close']:
        px[r['stock_code']][r['date']] = r['close']
    if r['high']:
        hi[r['stock_code']][r['date']] = r['high']
print(f'  {len(px):,} 只')

print()
print('=== 3. 载入形态信号（2024-10 起）===')
FAM = {'base_breakout_v2': ('base', 7), 'base_breakout': ('base', 3),
       'double_bottom': ('base', 2), 'flat_base': ('base', 2),
       'saucer_base': ('base', 2), 'cup_handle_v2': ('base', 2),
       'pocket_pivot_v2': ('pivot', 7), 'pocket_pivot': ('pivot', 5),
       'mw_signal': ('mw', 4), 'box_breakout': ('misc', 3),
       'cdl': ('misc', 2), 'cdl_bullish': ('misc', 2),
       'talib': ('misc', 1), 'talib_bullish': ('misc', 1)}
sig = defaultdict(list)     # code -> [(date, fam, base)]
KEYSET = {d: i for i, d in enumerate(dates)}
nrow = 0
for r in con.execute("SELECT stock_code, date, signals_json FROM pattern_scan_signals "
                     "WHERE date>='2024-10-01'"):
    nrow += 1
    try:
        arr = json.loads(r['signals_json'] or '[]')
    except Exception:
        continue
    if not isinstance(arr, list):
        continue
    best = {}
    for s in arr:
        if not isinstance(s, dict):
            continue
        if s.get('type') == 'bearish':
            continue
        src = s.get('source', '')
        if src not in FAM:
            continue
        fam, base = FAM[src]
        if fam not in best or base > best[fam]:
            best[fam] = base
    if best:
        sig[r['stock_code']].append(
            (r['date'], max(best.values()), len(best)))
print(f'  扫描 {nrow:,} 行，{len(sig):,} 只有可用信号')


def day_raw(base, fams):
    return min(base + min(fams - 1, 3), 10)


def n_old(code, d):
    """旧 N：N1 6/4/0 + N2 5 天窗、离散衰减、cap17。"""
    ds = sorted(px[code])
    idx = ds.index(d)
    if idx < 250:
        return None
    close = px[code][d]
    high52 = max(px[code][x] for x in ds[idx - 249:idx + 1])
    dist = (close - high52) / high52 * 100
    n1 = 6 if dist > -5 else 4 if dist > -15 else 0
    decay = [1.0, 0.8, 0.6, 0.4, 0.4]
    tot = 0.0
    for sd, base, fams in sig.get(code, []):
        if sd not in KEYSET:
            continue
        days = KEYSET[d] - KEYSET[sd]
        if days < 0 or days >= 5:
            continue
        tot += day_raw(base, fams) * decay[min(days, 4)]
    return n1 + min(tot, 17)


def n_new(code, d):
    """新 N：N1 10/8/5/2 + N2 15 天窗、连续衰减、top3、cap7 + 趋势延续底分。"""
    ds = sorted(px[code])
    idx = ds.index(d)
    if idx < 250:
        return None
    close = px[code][d]
    high52 = max(px[code][x] for x in ds[idx - 249:idx + 1])
    dist = (close - high52) / high52 * 100
    n1 = 10 if dist > -3 else 8 if dist > -5 else 5 if dist > -10 else 2 if dist > -15 else 0
    per_day = defaultdict(lambda: [0.0, 0])
    for sd, base, fams in sig.get(code, []):
        if sd not in KEYSET:
            continue
        days = KEYSET[d] - KEYSET[sd]
        if days < 0 or days >= 15:
            continue
        cur = per_day[sd]
        cur[0] = max(cur[0], day_raw(base, fams))
        cur[1] = days
    parts = sorted(per_day.items(), key=lambda kv: KEYSET[kv[0]])[-3:]
    tot = sum(v[0] * max(0.2, 1.0 - 0.08 * v[1]) for _, v in parts)
    tot = min(tot, 7)
    if idx >= 20:
        ma20 = sum(px[code][x] for x in ds[idx - 19:idx + 1]) / 20
        hh20 = max(hi[code].get(x, px[code][x]) for x in ds[idx - 19:idx + 1])
        if close >= ma20 and close >= hh20 * 0.95:
            tot = max(tot, 3.5)
    return n1 + tot


def spearman(a, b):
    if len(a) < 30:
        return None
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean()
    rb -= rb.mean()
    den = (ra @ ra) ** 0.5 * (rb @ rb) ** 0.5
    return float(ra @ rb / den) if den else None


print()
print('=== 4. 逐月截面 ===')
rows = []
for d in monthend:
    i = KEYSET[d]
    fd = dates[i + FWD] if i + FWD < len(dates) else None
    if not fd:
        continue
    o, n, y = [], [], []
    for code in px:
        if d not in px[code] or fd not in px[code]:
            continue
        a, b = n_old(code, d), n_new(code, d)
        if a is None:
            continue
        o.append(a)
        n.append(b)
        y.append(px[code][fd] / px[code][d] - 1)
    if len(y) < 100:
        continue
    rows.append({'d': d, 'cnt': len(y),
                 'ic_o': spearman(o, y), 'ic_n': spearman(n, y),
                 'o': o, 'n': n, 'y': y})
    print(f'  {d}  样本 {len(y):>5}  IC旧 {rows[-1]["ic_o"]:+.3f}  IC新 {rows[-1]["ic_n"]:+.3f}')

ico = [r['ic_o'] for r in rows if r['ic_o'] is not None]
icn = [r['ic_n'] for r in rows if r['ic_n'] is not None]
print()
print('=== 5. 汇总 ===')
print(f'  月数 {len(rows)} ｜ 平均样本 {np.mean([r["cnt"] for r in rows]):.0f} 只')
print(f'  IC 旧 {np.mean(ico):+.4f}（t={np.mean(ico) / (np.std(ico, ddof=1) / len(ico) ** 0.5):.2f}）')
print(f'  IC 新 {np.mean(icn):+.4f}（t={np.mean(icn) / (np.std(icn, ddof=1) / len(icn) ** 0.5):.2f}）')


def decile(key):
    """按分数分 10 组的平均前瞻收益。分数大量同分（离散），故用秩分组而非数值分位。"""
    allx, ally = [], []
    for r in rows:
        allx.extend(r[key])
        ally.extend(r['y'])
    allx, ally = np.array(allx, dtype=float), np.array(ally, dtype=float)
    order = np.argsort(np.argsort(allx, kind='stable'), kind='stable')
    bins = np.clip(order * 10 // len(order), 0, 9)
    return [ally[bins == k].mean() * 100 for k in range(10)], \
           [int((bins == k).sum()) for k in range(10)]


do, co = decile('o')
dn, cn = decile('n')
print()
print('  十分位平均前瞻 60 日收益（%）：')
print('   组   ' + ''.join(f'{i + 1:>7}' for i in range(10)))
print('   旧   ' + ''.join(f'{v:>7.2f}' for v in do))
print('   新   ' + ''.join(f'{v:>7.2f}' for v in dn))
print('   旧n  ' + ''.join(f'{v:>7}' for v in co))
print(f'  多空差（第10组−第1组）：旧 {do[-1] - do[0]:+.2f}pp ｜ 新 {dn[-1] - dn[0]:+.2f}pp')
print(f'  前10%平均：旧 {do[-1]:+.2f}% ｜ 新 {dn[-1]:+.2f}% ｜ 全样本 {np.mean([y for r in rows for y in r["y"]]) * 100:+.2f}%')
con.close()
