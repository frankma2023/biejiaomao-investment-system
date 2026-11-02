# -*- coding: utf-8 -*-
"""大盘健康分与中证全指的匹配度诊断。

回答四个问题：
  1. 同向率：指数涨/跌时，健康分是否同向？
  2. 幅度匹配：健康分的波动是否与指数成比例？
  3. 领先滞后：健康分是领先、同步还是滞后？
  4. 背离：哪些时段两者方向相反？背离后指数怎么走？
"""
import math
import sqlite3

DB = 'D:/hanako/investment-system/data/lixinger.db'
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

# ── 取数 ──
h = {r['date']: r['total_score'] for r in con.execute(
    "SELECT date, total_score FROM market_health_daily WHERE date >= '2026-03-01' ORDER BY date")}
idx = {r['date']: r['close'] for r in con.execute(
    "SELECT date, close FROM index_daily_kline WHERE stock_code='000985' "
    "AND kline_type='normal' AND date >= '2026-03-01' ORDER BY date")}
ds = sorted(set(h) & set(idx))
print(f'  样本：{len(ds)} 个交易日  {ds[0]} ~ {ds[-1]}')

hv = [h[d] for d in ds]
iv = [idx[d] for d in ds]

# ── 日变动 ──
dh = [hv[i] - hv[i - 1] for i in range(1, len(hv))]
di = [iv[i] / iv[i - 1] - 1 for i in range(1, len(iv))]
dates = ds[1:]


def corr(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    ca = [x - ma for x in a]
    cb = [x - mb for x in b]
    num = sum(x * y for x, y in zip(ca, cb))
    da = math.sqrt(sum(x * x for x in ca))
    db = math.sqrt(sum(x * x for x in cb))
    return num / (da * db) if da and db else None


def std(a):
    n = len(a)
    m = sum(a) / n
    return math.sqrt(sum((x - m) ** 2 for x in a) / (n - 1))


# ══════════ 1) 同向率 ══════════
same = sum(1 for a, b in zip(dh, di) if (a > 0) == (b > 0) and a != 0 and b != 0)
valid = sum(1 for a, b in zip(dh, di) if a != 0 and b != 0)
print(f'\n【1. 同向率】')
print(f'  全部交易日：{same}/{valid} = {same/valid*100:.1f}%')

# 分档：按指数涨跌幅
BUCKETS = [('涨 >2%', lambda x: x > 0.02), ('涨 1~2%', lambda x: 0.01 < x <= 0.02),
           ('涨 0~1%', lambda x: 0 < x <= 0.01), ('跌 0~1%', lambda x: -0.01 <= x < 0),
           ('跌 1~2%', lambda x: -0.02 <= x < -0.01), ('跌 >2%', lambda x: x < -0.02)]
print(f'  {"指数情形":<12}{"天数":>5}{"健康分同向":>10}{"同向率":>8}{"健康分日均变动":>13}')
for label, fn in BUCKETS:
    sel = [(a, b) for a, b in zip(dh, di) if fn(b)]
    if not sel:
        continue
    up = sum(1 for a, b in sel if (a > 0) == (b > 0))
    avg = sum(a for a, b in sel) / len(sel)
    print(f'  {label:<12}{len(sel):>5}{up:>10}{up/len(sel)*100:>7.0f}%{avg:>+13.1f}')

# ══════════ 2) 幅度匹配 ══════════
print(f'\n【2. 幅度匹配】')
sh, si = std(hv), std(iv)
sh_d, si_d = std(dh), std(di)
print(f'  水平波动 健康分 σ={sh:.1f} 分  中证全指 σ={si:.1f} 点（{si/iv[0]*100:.1f}%）')
print(f'  日变动   健康分 σ={sh_d:.1f} 分  中证全指 σ={si_d*100:.2f}%')
print(f'  → 健康分日均波动约 {sh_d:.1f} 分，相当于 100 分制的 {sh_d:.1f}%；'
      f'指数日均波动 {si_d*100:.2f}%')
print(f'  → 放大倍数 ≈ {sh_d/(si_d*100):.1f}x')

# 极端日：指数涨跌 >1.5% 时健康分平均变动
big_up = [(a, b) for a, b in zip(dh, di) if b > 0.015]
big_dn = [(a, b) for a, b in zip(dh, di) if b < -0.015]
if big_up:
    print(f'  指数大涨日(>1.5%) {len(big_up)} 天：健康分平均 {sum(a for a,b in big_up)/len(big_up):+.1f} 分，'
          f'同向 {sum(1 for a,b in big_up if a>0)}/{len(big_up)}')
if big_dn:
    print(f'  指数大跌日(<-1.5%) {len(big_dn)} 天：健康分平均 {sum(a for a,b in big_dn)/len(big_dn):+.1f} 分，'
          f'同向 {sum(1 for a,b in big_dn if a<0)}/{len(big_dn)}')

# ══════════ 3) 领先滞后 ══════════
print(f'\n【3. 领先滞后】（健康分日变动 vs 指数日变动，平移 k 日）')
print(f'  {"平移":<10}{"相关系数":>10}')
for k in (-3, -2, -1, 0, 1, 2, 3):
    if k >= 0:
        a, b = dh[k:] if k else dh, (di[:len(di) - k] if k else di)
    else:
        a, b = dh[:len(dh) + k], di[-k:]
    n = min(len(a), len(b))
    c = corr(a[:n], b[:n])
    tag = ' ← 同步' if k == 0 else ('（健康分领先）' if k > 0 else '（健康分滞后）')
    print(f'  k={k:<8}{c:>10.3f} {tag}')

# ══════════ 4) 背离段 ══════════
print(f'\n【4. 显著背离段】（连续 ≥3 日方向相反）')
runs = []
cur = []
for i, (a, b) in enumerate(zip(dh, di)):
    if a != 0 and b != 0 and (a > 0) != (b > 0):
        cur.append(i)
    else:
        if len(cur) >= 3:
            runs.append(cur)
        cur = []
if len(cur) >= 3:
    runs.append(cur)
for r in runs:
    s, e = r[0], r[-1]
    dh_sum = sum(dh[s:e + 1])
    idx_s = iv[s]  # 起点（用前一日的收盘更准确，简化为起点）
    idx_e = iv[e + 1] if e + 1 < len(iv) else iv[e]
    chg = (idx_e / idx_s - 1) * 100
    # 背离后 10 日指数表现
    aft = min(e + 1 + 10, len(iv) - 1)
    post = (iv[aft] / idx_e - 1) * 100 if aft > e + 1 else None
    print(f'  {dates[s]} ~ {dates[e]} 共{len(r)}日：健康分累计 {dh_sum:+.1f} 分，'
          f'指数 {chg:+.2f}%'
          + (f'　→ 之后10日指数 {post:+.2f}%' if post is not None else ''))

con.close()
