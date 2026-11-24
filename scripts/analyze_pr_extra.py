# -*- coding: utf-8 -*-
"""PR 的增量信息检验：方差分解 + 同 PE 内双重排序 + 剔除金融地产。"""
import json
import os
from collections import defaultdict

import numpy as np

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(PROJECT, 'docs', 'report', 'pr_backtest', 'data', 'pr_raw.json')
D = json.load(open(RAW, encoding='utf-8'))

FIN = set()
import sqlite3
con = sqlite3.connect(os.path.join(PROJECT, 'data', 'lixinger.db'), timeout=60)
for r in con.execute("""SELECT stock_code, name FROM stock_basic
                        WHERE name LIKE '%银行%' OR name LIKE '%保险%'
                           OR name LIKE '%证券%' OR name LIKE '%地产%'
                           OR name LIKE '%信托%' OR name LIKE '%人寿%'
                           OR name LIKE '%财险%'"""):
    FIN.add(r[0])
print('金融地产样本池', len(FIN))

lpe = np.log([r['pe'] for r in D])
lpb = np.log([r['pb'] for r in D])
lpr = np.log([r['pr'] for r in D])

print('\n=== 一、log(PR) 的方差分解 ===')
print(f'  var(log PR) = {lpr.var():.4f}')
print(f'  var(2·log PE) = {lpe.var() * 4:.4f}   贡献 {lpe.var() * 4 / lpr.var() * 100:.1f}%')
print(f'  var(log PB)  = {lpb.var():.4f}   贡献 {lpb.var() / lpr.var() * 100:.1f}%')
print(f'  corr(log PR, log PE) = {np.corrcoef(lpr, lpe)[0, 1]:+.3f}')
print(f'  corr(log PR, log PB) = {np.corrcoef(lpr, lpb)[0, 1]:+.3f}')
roe = np.array([r['roe'] for r in D])
print(f'  corr(log PR, ROE)    = {np.corrcoef(lpr, roe)[0, 1]:+.3f}')
# 单变量回归解释力
A = np.vstack([np.ones(len(lpe)), lpe]).T
coef, *_ = np.linalg.lstsq(A, lpr, rcond=None)
res = lpr - A @ coef
r2 = 1 - res.var() / lpr.var()
print(f'  仅用 log PE 解释 log PR 的 R² = {r2:.3f}')

# 基准
base = {}
for h in (250,):
    per = defaultdict(list)
    for r in D:
        v = r.get(f'r{h}')
        if v is not None:
            per[r['date']].append(v)
    base = {d: float(np.mean(v)) for d, v in per.items()}


def stat(rows, h=250):
    v = np.array([r[f'r{h}'] for r in rows if r.get(f'r{h}') is not None], dtype=float)
    if len(v) < 20:
        return None
    exc = np.mean([r['r250'] - base[r['date']] for r in rows if r.get('r250') is not None])
    return len(v), v.mean(), exc, (v > 0).mean() * 100


print('\n=== 二、同 PE 内双重排序：控制 PE 后 PR 还有增量吗 ===')
by_date = defaultdict(list)
for r in D:
    by_date[r['date']].append(r)

rows_out = []
print(f"{'PE 五分位':<12}{'PR 三分位':<12}{'样本':>8}{'250日均值':>11}{'超额':>9}{'胜率':>8}")
for pi in range(5):
    buckets = defaultdict(list)
    for d, rows in by_date.items():
        rows = sorted(rows, key=lambda x: x['pe'])
        n = len(rows)
        seg = rows[int(n * pi / 5):int(n * (pi + 1) / 5)]
        seg = sorted(seg, key=lambda x: x['pr'])
        m = len(seg)
        for ti in range(3):
            buckets[ti].extend(seg[int(m * ti / 3):int(m * (ti + 1) / 3)])
    for ti in range(3):
        s = stat(buckets[ti])
        if not s:
            continue
        print(f'P{pi + 1:<11}T{ti + 1:<11}{s[0]:>8,}{s[1]:>11.2f}{s[2]:>+9.2f}{s[3]:>7.1f}%')
        rows_out.append(dict(pe_q=pi + 1, pr_t=ti + 1, n=s[0], m250=s[1], exc=s[2], win=s[3]))
    print()

print('\n=== 三、剔除金融地产 ===')
sub = [r for r in D if r['code'] not in FIN]
print(f'  样本 {len(sub):,} / {len(D):,}')
print(f"  {'PR 区间':<12}{'样本':>8}{'250日均值':>11}{'超额':>9}")
for lo, hi in [(0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0), (1.0, 1.5),
               (1.5, 2.0), (2.0, 4.0), (4.0, 99)]:
    rows = [r for r in sub if lo <= r['pr'] < hi]
    s = stat(rows)
    if not s:
        continue
    print(f'  {lo:.1f}~{hi:.1f}     {s[0]:>8,}{s[1]:>11.2f}{s[2]:>+9.2f}')

print('\n=== 四、只用「ROE>15% 且 PR<1」的严格组合 ===')
strict = [r for r in D if r['roe'] >= 0.15 and r['pr'] < 1.0]
s = stat(strict)
print(f'  样本 {s[0]:,}  250日均值 {s[1]:.2f}%  超额 {s[2]:+.2f}pp  胜率 {s[3]:.1f}%')
strict2 = [r for r in D if r['roe'] >= 0.15 and r['pr'] < 0.6]
s2 = stat(strict2)
print(f'  ROE>15% 且 PR<0.6：样本 {s2[0]:,}  250日均值 {s2[1]:.2f}%  超额 {s2[2]:+.2f}pp  胜率 {s2[3]:.1f}%')
hi_roe = [r for r in D if r['roe'] >= 0.15]
s3 = stat(hi_roe)
print(f'  仅 ROE>15%：样本 {s3[0]:,}  250日均值 {s3[1]:.2f}%  超额 {s3[2]:+.2f}pp  胜率 {s3[3]:.1f}%')

json.dump(dict(double_sort=rows_out,
               strict=dict(n=s[0], m250=s[1], exc=s[2], win=s[3]),
               strict06=dict(n=s2[0], m250=s2[1], exc=s2[2], win=s2[3]),
               hi_roe=dict(n=s3[0], m250=s3[1], exc=s3[2], win=s3[3])),
          open(os.path.join(PROJECT, 'docs', 'report', 'pr_backtest', 'data',
                            'pr_extra.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
