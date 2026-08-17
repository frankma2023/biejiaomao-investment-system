# -*- coding: utf-8 -*-
"""H00922 全收益口径公平对比：定投 vs 网格 vs 持有（都含分红）"""
import sys, os, sqlite3
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row

rows = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code='H00922' AND date>='2018-01-01' ORDER BY date").fetchall()
dates = [r['date'] for r in rows]
closes = [r['close'] for r in rows]
print('H00922 全收益:', len(closes), '天', dates[0], '~', dates[-1])

# 1. 普通定投（每月1万）
def dca(mode):
    shares, invested = 0.0, 0.0
    prev = None
    for i, d in enumerate(dates):
        m = d[:7]
        if m == prev: continue
        prev = m
        amt = 10000.0
        if mode == 'dd' and i >= 250:
            hi = max(closes[i-249:i+1])
            dd = (hi - closes[i]) / hi * 100
            if dd >= 15: amt = 30000.0
            elif dd >= 10: amt = 20000.0
        shares += amt / closes[i]
        invested += amt
    return invested, shares * closes[-1]

inv_d, fin_d = dca('plain')
inv_dd, fin_dd = dca('dd')

# 2. 网格（8%间距，全收益口径，初始10万）
def grid(cash=100000):
    c, s, low = cash, 0.0, min(closes) * 0.95
    s = (cash / 3) / closes[0]; c -= cash / 3
    cg = int((closes[0] - low) / (low * 0.08))
    per = cash / 10
    for i in range(1, len(closes)):
        p = closes[i]
        g = int((p - low) / (low * 0.08))
        if g < cg:
            for _ in range(cg - g):
                if c > per: s += per / p; c -= per
            cg = g
        elif g > cg:
            for _ in range(g - cg):
                if s > 0:
                    amt = min(s * p, per); s -= amt / p; c += amt
            cg = g
    return c + s * closes[-1]

g = grid()
# 3. 持有（期初10万）
hold = 100000 / closes[0] * closes[-1]
years = len(closes) / 244

print('\n=== H00922 全收益（含分红再投资）公平对比 ===')
print(f'{"策略":<10}{"总收益":>10}{"年化":>8}{"说明"}')
print(f'{"持有":<10}{hold/100000*100-100:>+9.1f}%{(hold/100000)**(1/years)-1:>+7.1%}  期初10万一次性')
print(f'{"普通定投":<10}{fin_d/inv_d*100-100:>+9.1f}%{(fin_d/inv_d)**(1/years)-1:>+7.1%}  每月1万×{len(dates)//22}个月')
print(f'{"回撤加码":<10}{fin_dd/inv_dd*100-100:>+9.1f}%{(fin_dd/inv_dd)**(1/years)-1:>+7.1%}  回撤≥10%双倍/≥15%三倍')
print(f'{"网格8%":<10}{g/100000*100-100:>+9.1f}%{(g/100000)**(1/years)-1:>+7.1%}  期初10万，含持仓分红')
# 网格 vs 定投的资金效率：定投总投入 vs 网格总投入
print(f'\n资金投入对比: 定投投入{inv_d:,.0f}元 vs 网格初始{100000:,}元')
print(f'定投终值{fin_d:,.0f} / 网格终值{g:,.0f}')
db.close()
