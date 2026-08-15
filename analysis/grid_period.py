# -*- coding: utf-8 -*-
"""
网格 vs 持有：分阶段对比（识别网格适用的市场环境）
重点：2022-2024 震荡期 vs 2019-2021/2025-2026 趋势期
"""
import sys, os, sqlite3
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row
tri = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code='H00922' ORDER BY date").fetchall()
tri_dates = [r['date'] for r in tri]
tri_closes = [r['close'] for r in tri]
db.close()

def grid_backtest(closes, low, high, n_grids, initial_cash=100000):
    step = (high - low) / n_grids
    if step <= 0: return None
    per = initial_cash / (n_grids + 1)
    cash = initial_cash
    shares = 0.0
    grid_trades = 0
    def grid_of(price):
        g = round((price - low) / step)
        return max(0, min(n_grids, g))
    cur_grid = grid_of(closes[0])
    buy_amount = per * (n_grids // 3 + 1)
    shares = buy_amount / closes[0]
    cash -= buy_amount
    for i in range(1, len(closes)):
        p = closes[i]
        g = grid_of(p)
        if g < cur_grid:
            for _ in range(cur_grid - g):
                if cash > per:
                    shares += per / p; cash -= per; grid_trades += 1
            cur_grid = g
        elif g > cur_grid:
            for _ in range(g - cur_grid):
                if shares > 0:
                    sell_amt = min(shares * p, per)
                    shares -= sell_amt / p; cash += sell_amt; grid_trades += 1
            cur_grid = g
    total = cash + shares * closes[-1]
    buy_hold = initial_cash / closes[0] * closes[-1]
    return {'grid': total, 'hold': buy_hold, 'trades': grid_trades,
            'grid_ret': (total/initial_cash-1)*100, 'hold_ret': (buy_hold/initial_cash-1)*100,
            'excess': (total/buy_hold-1)*100}

# 分阶段（用当年价格区间做网格）
periods = [
    ('2019', '2019-01-01', '2019-12-31'),
    ('2020', '2020-01-01', '2020-12-31'),
    ('2021', '2021-01-01', '2021-12-31'),
    ('2022', '2022-01-01', '2022-12-31'),
    ('2023', '2023-01-01', '2023-12-31'),
    ('2024', '2024-01-01', '2024-12-31'),
    ('2025', '2025-01-01', '2025-12-31'),
    ('2026', '2026-01-01', '2026-08-14'),
]

print('=== 分年度：网格(10格,当年区间) vs 持有 ===')
print(f'{"年份":<6}{"持有收益":<10}{"网格收益":<10}{"超额":<10}{"交易次数":<8}{"市场特征"}')
for name, s, e in periods:
    idx = [i for i, d in enumerate(tri_dates) if s <= d <= e]
    if len(idx) < 100: continue
    seg = tri_closes[idx[0]:idx[-1]+1]
    low, high = min(seg)*0.95, max(seg)*1.05
    r = grid_backtest(seg, low, high, 10)
    if not r: continue
    trend = '上涨' if seg[-1] > seg[0] else '下跌'
    swing = (max(seg)-min(seg))/min(seg)*100
    print(f'{name:<6}{r["hold_ret"]:>6.1f}%  {r["grid_ret"]:>6.1f}%  {r["excess"]:>+6.1f}%  {r["trades"]:<8}{trend} 振幅{swing:.0f}%')

# 震荡期汇总：2022-2024
print('\n=== 震荡期（2022-2024）汇总 ===')
idx = [i for i, d in enumerate(tri_dates) if '2022-01-01' <= d <= '2024-12-31']
seg = tri_closes[idx[0]:idx[-1]+1]
low, high = min(seg)*0.95, max(seg)*1.05
r = grid_backtest(seg, low, high, 10)
print(f'区间: {tri_dates[idx[0]]} ~ {tri_dates[idx[-1]]}')
print(f'网格 10 格: 收益 {r["grid_ret"]:.1f}% vs 持有 {r["hold_ret"]:.1f}% | 超额 {r["excess"]:+.1f}% | 交易 {r["trades"]} 次')
for n in [5, 8, 12, 15]:
    r2 = grid_backtest(seg, low, high, n)
    print(f'  {n} 格: 超额 {r2["excess"]:+.1f}% 交易 {r2["trades"]} 次')
