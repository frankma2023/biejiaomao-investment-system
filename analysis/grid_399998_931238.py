# -*- coding: utf-8 -*-
"""
399998 中证煤炭 / 931238 SSH黄金股票 网格投资适配性分析
=====================================================
方法论对齐 grid_all_indices.py：10格等额网格，全周期+分年度 vs 持有
结论框架：超额与趋势强度负相关（横盘指数网格赚、趋势指数网格亏）
"""
import sys, os, sqlite3, math
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row

INDICES = [
    ('399998', '中证煤炭', None, '2009-01-01'),
    ('931238', 'SSH黄金股票', None, '2014-12-31'),
    # 对照组（已知结论）
    ('000015', '红利指数(对照)', None, '2018-01-01'),
    ('000922', '中证红利(对照)', 'H00922', '2018-01-01'),
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
    def g(p): return max(0, min(n, int((p-low)/step)))
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

def ann_return(closes):
    return (closes[-1]/closes[0])**(252/len(closes))-1 if closes else 0

# 1. 基础特征 + 全周期网格
print('=== 基础特征 ===')
print(f'{"指数":<16}{"区间":<22}{"年化波动":<10}{"20日振幅中位":<13}{"年化收益(持有)"}')
for code, name, tri, start in INDICES:
    data = load_price(code, tri, start)
    if len(data) < 300: continue
    closes = [r['close'] for r in data]
    dates = [r['date'] for r in data]
    rets = [closes[i]/closes[i-1]-1 for i in range(1, len(closes))]
    vol = (sum((r - sum(rets)/len(rets))**2 for r in rets)/(len(rets)-1))**0.5 * math.sqrt(252) * 100
    rng = []
    for i in range(20, len(closes)):
        w = closes[i-19:i+1]
        rng.append((max(w)-min(w))/min(w)*100)
    rng_s = sorted(rng)
    med_amp = rng_s[len(rng_s)//2]
    ann = ann_return(closes)
    print(f'{name:<16}{dates[0]+" ~ "+dates[-1]:<22}{vol:<9.1f}%{med_amp:<12.1f}%{ann*100:>+6.1f}%')

# 2. 全周期网格 vs 持有（含最近8年对齐窗口）
print('\n=== 网格(10格) vs 持有 ===')
print(f'{"指数":<16}{"窗口":<12}{"持有":<9}{"网格":<9}{"超额":<9}{"交易次数"}')
for code, name, tri, start in INDICES:
    for win, ws in [(f'{start[:4]}起', start), ('2018起', '2018-01-01')]:
        data = load_price(code, tri, ws)
        if len(data) < 300: continue
        closes = [r['close'] for r in data]
        low, high = min(closes)*0.95, max(closes)*1.05
        r = pure_grid(closes, low, high)
        hold = 100000/closes[0]*closes[-1]
        print(f'{name:<16}{win:<12}{(hold/100000-1)*100:>+7.1f}%  {(r[0]/100000-1)*100:>+7.1f}%  {(r[0]/hold-1)*100:>+7.1f}%  {r[1]}')

# 3. 分年度：指数涨跌 + 网格超额
print('\n=== 分年度：指数涨跌% | 网格超额(pp) ===')
years = ['2016','2017','2018','2019','2020','2021','2022','2023','2024','2025','2026']
for code, name, tri, start in INDICES:
    data = load_price(code, tri, start)
    if len(data) < 300: continue
    closes = [r['close'] for r in data]
    dates = [r['date'] for r in data]
    print(f'--- {name} ---')
    for y in years:
        idx = [i for i,d in enumerate(dates) if d.startswith(y)]
        if len(idx) < 80:
            print(f'  {y}: 数据不足')
            continue
        seg = closes[idx[0]:idx[-1]+1]
        lo, hi = min(seg)*0.95, max(seg)*1.05
        r = pure_grid(seg, lo, hi)
        hold = 100000/seg[0]*seg[-1]
        idx_ret = (seg[-1]/seg[0]-1)*100
        excess = (r[0]/hold-1)*100
        trend = '↑' if idx_ret > 10 else ('→' if abs(idx_ret) <= 10 else '↓')
        print(f'  {y}: {idx_ret:>+7.1f}% {trend}  网格超额 {excess:>+6.1f}pp')

db.close()
