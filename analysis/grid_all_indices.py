# -*- coding: utf-8 -*-
"""
全部红利指数 + 自由现金流 网格适配性对比
========================================
数据：index_daily_kline 价格指数（000922 用 H00922 全收益）
统一方法论：10 格网格（当年区间），分年度 + 全周期对比持有
"""
import sys, os, sqlite3
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row

INDICES = [
    ('000922', '中证红利', 'H00922'),   # 有全收益
    ('H30269', '红利低波', None),
    ('931468', '红利质量', None),
    ('000015', '红利指数', None),
    ('931848', '800红利低波', None),
    ('980092', '自由现金流', None),
]

def load_price(code, tri_code=None, start='2018-01-01'):
    if tri_code:
        rows = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code=? AND date>=? ORDER BY date", (tri_code, start)).fetchall()
    else:
        rows = db.execute("SELECT date, close FROM index_daily_kline WHERE stock_code=? AND kline_type='normal' AND date>=? ORDER BY date", (code, start)).fetchall()
    return [dict(r) for r in rows]

def pure_grid(closes, low, high, n=10, cash=100000):
    step = (high-low)/n
    if step <= 0: return None
    per = cash/(n+1)
    c, s = cash, 0.0
    def g(p): return max(0, min(n, round((p-low)/step)))
    cg = g(closes[0])
    s = per*(n//3+1)/closes[0]; c -= per*(n//3+1)
    trades = 0
    for i in range(1, len(closes)):
        p = closes[i]; gg = g(p)
        if gg < cg:
            for _ in range(cg-gg):
                if c > per: s += per/p; c -= per; trades += 1
            cg = gg
        elif gg > cg:
            for _ in range(gg-cg):
                if s > 0:
                    amt = min(s*p, per); s -= amt/p; c += amt; trades += 1
            cg = gg
    return c + s*closes[-1], trades

# 1. 波动特征 + 全周期网格 vs 持有
print('=== 各指数波动特征 + 全周期（2018起）网格(10格) vs 持有 ===')
print(f'{"指数":<10}{"年化波动":<10}{"20日振幅中位":<12}{"持有":<10}{"网格":<10}{"超额":<10}{"交易次数"}')
for code, name, tri in INDICES:
    data = load_price(code, tri)
    if len(data) < 300: continue
    closes = [r['close'] for r in data]
    # 年化波动
    rets = [closes[i]/closes[i-1]-1 for i in range(1, len(closes))]
    import math
    vol = (sum((r - sum(rets)/len(rets))**2 for r in rets)/(len(rets)-1))**0.5 * math.sqrt(252) * 100
    # 20日振幅中位
    rng = []
    for i in range(20, len(closes)):
        w = closes[i-19:i+1]
        rng.append((max(w)-min(w))/min(w)*100)
    rng_s = sorted(rng)
    med_amp = rng_s[len(rng_s)//2]
    # 全周期网格（当年区间不行，用全周期区间）
    low, high = min(closes)*0.95, max(closes)*1.05
    r = pure_grid(closes, low, high)
    hold = 100000/closes[0]*closes[-1]
    print(f'{name:<10}{vol:<9.1f}%{med_amp:<11.1f}%{(hold/100000-1)*100:>6.1f}%  {(r[0]/100000-1)*100:>6.1f}%  {(r[0]/hold-1)*100:>+6.1f}%  {r[1]}')

# 2. 分年度：网格 vs 持有（各指数）
print('\n=== 分年度超额（网格-持有，%）===')
years = ['2019','2020','2021','2022','2023','2024','2025','2026']
print(f'{"指数":<10}' + ''.join(f'{y:>8}' for y in years))
for code, name, tri in INDICES:
    data = load_price(code, tri)
    if len(data) < 300: continue
    closes = [r['close'] for r in data]
    dates = [r['date'] for r in data]
    row = f'{name:<10}'
    for y in years:
        idx = [i for i,d in enumerate(dates) if d.startswith(y)]
        if len(idx) < 80:
            row += f'{"—":>8}'
            continue
        seg = closes[idx[0]:idx[-1]+1]
        lo, hi = min(seg)*0.95, max(seg)*1.05
        r = pure_grid(seg, lo, hi)
        hold = 100000/seg[0]*seg[-1]
        excess = (r[0]/hold-1)*100
        row += f'{excess:>+7.1f}% '
    print(row)
db.close()
