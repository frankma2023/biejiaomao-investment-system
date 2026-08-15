# -*- coding: utf-8 -*-
"""
估值锚定网格：只在低估/合理区间运行网格，高估区清仓
思路（结合回撤买点研究）：
  - PE分位 < 60%：网格运行区（低吸）
  - PE分位 > 80%：清仓观望（卖出警示规则）
  - 网格区间用 PB 分位映射价格（估值锚）
对比：纯网格 vs 估值锚定网格 vs 持有
"""
import sys, os, sqlite3
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row
tri = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code='H00922' ORDER BY date").fetchall()
tri_dates = [r['date'] for r in tri]
tri_closes = [r['close'] for r in tri]
# PE 分位（000922，按日期对齐）
fund = db.execute("SELECT date, pe_ttm_pct FROM index_fundamental_daily WHERE stock_code='000922' AND pe_ttm_pct IS NOT NULL ORDER BY date").fetchall()
fund_map = {}
for r in fund:
    fund_map[r['date']] = r['pe_ttm_pct']
db.close()

def pe_of(date):
    # 取 <= date 的最近 PE 分位
    best = None
    for d in sorted(fund_map.keys()):
        if d <= date:
            best = fund_map[d]
        else:
            break
    return best

def val_grid_backtest(closes, dates, low, high, n_grids, initial_cash=100000):
    """估值锚定网格：PE分位>0.8 时清仓，<0.6 恢复网格"""
    step = (high - low) / n_grids
    if step <= 0: return None
    per = initial_cash / (n_grids + 1)
    cash = initial_cash
    shares = 0.0
    trades = 0
    def grid_of(p):
        return max(0, min(n_grids, round((p - low) / step)))
    cur_grid = grid_of(closes[0])
    buy_amount = per * (n_grids // 3 + 1)
    shares = buy_amount / closes[0]
    cash -= buy_amount
    for i in range(1, len(closes)):
        p = closes[i]
        pe = pe_of(dates[i])
        # 高估清仓
        if pe is not None and pe > 0.80 and shares > 0:
            cash += shares * p
            shares = 0
            cur_grid = grid_of(p)
            continue
        # 低估恢复
        if pe is not None and pe <= 0.60 and shares == 0:
            buy_amount = per * (n_grids // 3 + 1)
            shares = buy_amount / p
            cash -= buy_amount
            cur_grid = grid_of(p)
            continue
        g = grid_of(p)
        if g < cur_grid:
            for _ in range(cur_grid - g):
                if cash > per:
                    shares += per / p; cash -= per; trades += 1
            cur_grid = g
        elif g > cur_grid:
            for _ in range(g - cur_grid):
                if shares > 0:
                    sell_amt = min(shares * p, per)
                    shares -= sell_amt / p; cash += sell_amt; trades += 1
            cur_grid = g
    total = cash + shares * closes[-1]
    hold = initial_cash / closes[0] * closes[-1]
    return {'grid': total, 'hold': hold, 'trades': trades,
            'grid_ret': (total/initial_cash-1)*100, 'hold_ret': (hold/initial_cash-1)*100,
            'excess': (total/hold-1)*100}

# 全周期 2018-2026
low, high = min(tri_closes)*0.92, max(tri_closes)*1.05
print('=== 全周期（2018-2026）估值锚定网格 vs 纯网格 vs 持有 ===')
for n in [8, 10, 12]:
    r = val_grid_backtest(tri_closes, tri_dates, low, high, n)
    print(f'  估值锚定 {n} 格: 收益 {r["grid_ret"]:.1f}% vs 持有 {r["hold_ret"]:.1f}% | 超额 {r["excess"]:+.1f}% | 交易 {r["trades"]} 次')

print('\n=== 对比：无估值锚的纯网格（同区间）===')
def pure_grid(closes, low, high, n, cash=100000):
    step = (high-low)/n
    per = cash/(n+1)
    c, s = cash, 0.0
    def g(p): return max(0, min(n, round((p-low)/step)))
    cg = g(closes[0])
    s = per*(n//3+1)/closes[0]; c -= per*(n//3+1)
    for i in range(1, len(closes)):
        p = closes[i]; gg = g(p)
        if gg < cg:
            for _ in range(cg-gg):
                if c > per: s += per/p; c -= per
            cg = gg
        elif gg > cg:
            for _ in range(gg-cg):
                if s > 0:
                    amt = min(s*p, per); s -= amt/p; c += amt
            cg = gg
    return (c + s*closes[-1])
for n in [8, 10, 12]:
    g = pure_grid(tri_closes, low, high, n)
    print(f'  纯网格 {n} 格: 收益 {(g/100000-1)*100:.1f}%')

# 分年度验证估值锚定
print('\n=== 估值锚定网格 分年度 ===')
periods = [('2022','2022-01-01','2022-12-31'), ('2023','2023-01-01','2023-12-31'),
           ('2024','2024-01-01','2024-12-31'), ('2025','2025-01-01','2025-12-31')]
for name, s, e in periods:
    idx = [i for i, d in enumerate(tri_dates) if s <= d <= e]
    if len(idx) < 100: continue
    seg_d = [tri_dates[i] for i in idx]
    seg_c = tri_closes[idx[0]:idx[-1]+1]
    lo, hi = min(seg_c)*0.95, max(seg_c)*1.05
    r = val_grid_backtest(seg_c, seg_d, lo, hi, 10)
    print(f'  {name}: 网格 {r["grid_ret"]:.1f}% vs 持有 {r["hold_ret"]:.1f}% | 超额 {r["excess"]:+.1f}%')
