# -*- coding: utf-8 -*-
"""399998/931238 网格间距敏感性：5% / 8% / 10% 对比（2018起）"""
import sys, os, sqlite3
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row

def load_price(code, start='2018-01-01'):
    rows = db.execute("SELECT date, close FROM index_daily_kline WHERE stock_code=? AND kline_type='normal' AND date>=? ORDER BY date", (code, start)).fetchall()
    return [r['close'] for r in rows]

def grid_with_step(closes, step_pct, cash=100000):
    """按价格百分比间距的网格：每 step_pct 一档"""
    c, s = cash, 0.0
    low = min(closes) * 0.95
    base = closes[0]
    # 初始买入 1/3
    s = (cash/3) / closes[0]; c -= cash/3
    cg = int((closes[0]-low)/(low*step_pct/100))
    trades = 0
    per = cash/10
    for i in range(1, len(closes)):
        p = closes[i]
        g = int((p-low)/(low*step_pct/100))
        if g < cg:
            for _ in range(cg-g):
                if c > per: s += per/p; c -= per; trades += 1
            cg = g
        elif g > cg:
            for _ in range(g-cg):
                if s > 0:
                    amt = min(s*p, per); s -= amt/p; c += amt; trades += 1
            cg = g
    return c + s*closes[-1], trades

print('=== 间距敏感性（2018起，网格 vs 持有）===')
print(f'{"指数":<16}{"间距":<8}{"持有":<9}{"网格":<9}{"超额":<9}{"交易次数"}')
for code, name in [('399998', '中证煤炭'), ('931238', 'SSH黄金股票'), ('000015', '红利指数(对照)')]:
    closes = load_price(code)
    if len(closes) < 300: continue
    hold = 100000/closes[0]*closes[-1]
    for step in [5, 8, 10]:
        r = grid_with_step(closes, step)
        print(f'{name:<16}{str(step)+"%":<8}{(hold/100000-1)*100:>+7.1f}%  {(r[0]/100000-1)*100:>+7.1f}%  {(r[0]/hold-1)*100:>+7.1f}%  {r[1]}')
db.close()
