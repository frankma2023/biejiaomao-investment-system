# -*- coding: utf-8 -*-
"""市赚率分档检验：分档前瞻收益、横截面 IC、阈值扫描、分年。"""
import json
import os
from collections import defaultdict

import numpy as np

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(PROJECT, 'docs', 'report', 'pr_backtest', 'data', 'pr_raw.json')
OUT = os.path.join(PROJECT, 'docs', 'report', 'pr_backtest', 'data')
D = json.load(open(RAW, encoding='utf-8'))

BANDS = [(0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.8),
         (0.8, 1.0), (1.0, 1.2), (1.2, 1.5), (1.5, 2.0), (2.0, 99)]


def stats(rows, h):
    v = np.array([r[f'r{h}'] for r in rows if r.get(f'r{h}') is not None], dtype=float)
    if len(v) < 10:
        return None
    return dict(n=len(v), mean=v.mean(), median=np.median(v),
                win=(v > 0).mean() * 100, p25=np.percentile(v, 25),
                p75=np.percentile(v, 75))


# 每月基准（同月全体等权）
base = defaultdict(dict)
for h in (60, 120, 250):
    per = defaultdict(list)
    for r in D:
        v = r.get(f'r{h}')
        if v is not None:
            per[r['date']].append(v)
    for d, arr in per.items():
        base[h][d] = float(np.mean(arr))

print('=' * 96)
print('一、按市赚率分档（全样本，月度截面，等权）')
print('=' * 96)
print(f"{'PR 区间':<12}{'样本':>8}{'60日均值':>10}{'120日均值':>11}"
      f"{'250日均值':>11}{'250日超额':>11}{'250日胜率':>10}{'跑赢基准':>10}")
band_out = []
for lo, hi in BANDS:
    rows = [r for r in D if lo <= r['pr'] < hi]
    s250 = stats(rows, 250)
    if not s250:
        continue
    s60, s120 = stats(rows, 60), stats(rows, 120)
    exc = np.mean([r['r250'] - base[250][r['date']] for r in rows
                   if r.get('r250') is not None])
    beat = np.mean([1 if (r.get('r250') is not None
                          and r['r250'] > base[250][r['date']]) else 0 for r in rows]) * 100
    lab = f'{lo:.1f}~{hi:.1f}'
    print(f'{lab:<12}{len(rows):>8,}{s60["mean"]:>10.2f}{s120["mean"]:>11.2f}'
          f'{s250["mean"]:>11.2f}{exc:>+11.2f}{s250["win"]:>9.1f}%{beat:>9.1f}%')
    band_out.append(dict(lo=lo, hi=hi, n=len(rows), m60=s60['mean'], m120=s120['mean'],
                         m250=s250['mean'], exc=exc, win=s250['win'], beat=beat))

print()
print('=' * 96)
print('二、横截面十分位（每月按 PR 排序切十组，等权平均）')
print('=' * 96)
print(f"{'分位':<8}{'PR 中位':>10}{'60日':>10}{'120日':>10}{'250日':>10}{'250日超额':>12}")
dec_out = []
by_date = defaultdict(list)
for r in D:
    by_date[r['date']].append(r)
for k in range(10):
    picks = []
    for d, rows in by_date.items():
        rows = sorted(rows, key=lambda x: x['pr'])
        n = len(rows)
        seg = rows[int(n * k / 10):int(n * (k + 1) / 10)]
        picks.extend(seg)
    s60, s120, s250 = stats(picks, 60), stats(picks, 120), stats(picks, 250)
    exc = np.mean([r['r250'] - base[250][r['date']] for r in picks
                   if r.get('r250') is not None])
    prm = np.median([r['pr'] for r in picks])
    print(f'D{k + 1:<7}{prm:>10.2f}{s60["mean"]:>10.2f}{s120["mean"]:>10.2f}'
          f'{s250["mean"]:>10.2f}{exc:>+12.2f}')
    dec_out.append(dict(decile=k + 1, pr=prm, m60=s60['mean'], m120=s120['mean'],
                        m250=s250['mean'], exc=exc))

print()
print('=' * 96)
print('三、阈值扫描：PR 低于阈值的组合，250 日前瞻')
print('=' * 96)
print(f"{'阈值':<8}{'样本':>8}{'250日均值':>11}{'超额':>10}{'胜率':>9}{'最大单月占比':>14}")
scan = []
for thr in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5, 2.0, 99]:
    rows = [r for r in D if r['pr'] < thr]
    s = stats(rows, 250)
    if not s:
        continue
    exc = np.mean([r['r250'] - base[250][r['date']] for r in rows
                   if r.get('r250') is not None])
    share = len(rows) / len(D) * 100
    print(f'{thr:<8.1f}{len(rows):>8,}{s["mean"]:>11.2f}{exc:>+10.2f}'
          f'{s["win"]:>8.1f}%{share:>13.1f}%')
    scan.append(dict(thr=thr, n=len(rows), m250=s['mean'], exc=exc,
                     win=s['win'], share=share))

print()
print('=' * 96)
print('四、分层：高 ROE 子集里 PR 还有用吗')
print('=' * 96)
for lo, hi in [(0.0, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.30), (0.30, 0.80)]:
    sub = [r for r in D if lo <= r['roe'] < hi]
    if len(sub) < 100:
        continue
    print(f'\n  ROE {lo * 100:.0f}%~{hi * 100:.0f}%  样本 {len(sub):,}')
    print(f"    {'PR 区间':<12}{'样本':>7}{'250日均值':>11}{'超额':>9}")
    for plo, phi in [(0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0), (1.0, 99)]:
        rows = [r for r in sub if plo <= r['pr'] < phi]
        s = stats(rows, 250)
        if not s or s['n'] < 30:
            continue
        exc = np.mean([r['r250'] - base[250][r['date']] for r in rows
                       if r.get('r250') is not None])
        print(f'    {plo:.1f}~{phi:.1f}    {s["n"]:>7,}{s["mean"]:>11.2f}{exc:>+9.2f}')

print()
print('=' * 96)
print('五、分年（250 日前瞻，PR<0.6 组合 vs 全体）')
print('=' * 96)
print(f"{'建仓年':<8}{'样本':>8}{'PR<0.6 均值':>13}{'全体均值':>10}{'超额':>9}")
yr_out = []
for y in range(2016, 2026):
    rows = [r for r in D if r['date'][:4] == str(y)]
    if not rows:
        continue
    low = [r for r in rows if r['pr'] < 0.6]
    a = np.mean([r['r250'] for r in low if r.get('r250') is not None])
    b = np.mean([r['r250'] for r in rows if r.get('r250') is not None])
    print(f'{y:<8}{len(low):>8,}{a:>13.2f}{b:>10.2f}{a - b:>+9.2f}')
    yr_out.append(dict(year=y, n=len(low), low=a, all=b, exc=a - b))

# 横截面 IC
print()
print('=' * 96)
print('六、横截面 Spearman IC（每月 PR 与前瞻收益的秩相关）')
print('=' * 96)
from statistics import mean


def rank(x):
    order = sorted(range(len(x)), key=lambda i: x[i])
    r = [0.0] * len(x)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


ic_out = {}
for h in (60, 120, 250):
    ics = []
    for d, rows in by_date.items():
        rows = [r for r in rows if r.get(f'r{h}') is not None]
        if len(rows) < 100:
            continue
        a = rank([r['pr'] for r in rows])
        b = rank([r[f'r{h}'] for r in rows])
        ma, mb = mean(a), mean(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
        if den:
            ics.append(num / den)
    ics = np.array(ics)
    t = ics.mean() / (ics.std(ddof=1) / len(ics) ** 0.5)
    print(f'  {h:>3} 日：均值 IC {ics.mean():+.4f}   t={t:+.2f}   月数 {len(ics)}')
    ic_out[h] = dict(ic=float(ics.mean()), t=float(t), n=int(len(ics)))

json.dump(dict(bands=band_out, deciles=dec_out, scan=scan, year=yr_out, ic=ic_out,
               base={h: float(np.mean(list(base[h].values()))) for h in (60, 120, 250)},
               n=len(D)),
          open(os.path.join(OUT, 'pr_result.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print('\n写出 pr_result.json')
