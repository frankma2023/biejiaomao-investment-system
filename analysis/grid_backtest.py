# -*- coding: utf-8 -*-
"""
中证红利 网格策略回测
=====================
策略模型（经典网格）：
  - 设定价格区间 [low, high]，等分 N 格
  - 价格每跌一格买入 1 份，每涨一格卖出 1 份（赚一格价差）
  - 底仓：区间中间价买入半仓，跌到底部满仓
  - 收益 = 网格价差收益 + 持仓浮盈/浮亏 + 分红（用全收益价格）

对比：
  1. 纯持有（买入不动）
  2. 网格区间 5%/10% 间距
  3. 估值锚定网格（PB 分位映射区间）vs 固定区间
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
    """
    经典网格回测（全收益价格，含分红再投资效果）
    - 区间 [low, high] 等分 n_grids 格
    - 网格间距 = (high-low)/n_grids
    - 起始：价格落在中间格时，建半仓（现金一半买，一半留）
    - 每格：跌一格买 1 份（用剩余现金/n_grids），涨一格卖 1 份
    - 越界：跌破 low 满仓持有等回升；涨破 high 清仓
    """
    step = (high - low) / n_grids
    if step <= 0:
        return None
    # 每份金额 = 初始现金 / (n_grids + 1)（留一格余量）
    per = initial_cash / (n_grids + 1)
    cash = initial_cash
    shares = 0.0
    grid_trades = 0
    grid_profit = 0.0

    # 起始档位：当前价最近的格
    def grid_of(price):
        g = round((price - low) / step)
        return max(0, min(n_grids, g))

    cur_grid = grid_of(closes[0])
    # 起始建半仓：买 (n_grids+1)/2 份？
    # 简化：起始买 1/3 仓位
    buy_amount = per * (n_grids // 3 + 1)
    shares = buy_amount / closes[0]
    cash -= buy_amount

    for i in range(1, len(closes)):
        p = closes[i]
        g = grid_of(p)
        # 跌一格买
        if g < cur_grid:
            for _ in range(cur_grid - g):
                if cash > per and shares * p + cash > 0:
                    shares += per / p
                    cash -= per
                    grid_trades += 1
            cur_grid = g
        # 涨一格卖
        elif g > cur_grid:
            for _ in range(g - cur_grid):
                if shares > 0:
                    sell_amt = min(shares * p, per)
                    shares -= sell_amt / p
                    cash += sell_amt
                    grid_trades += 1
            cur_grid = g

    total = cash + shares * closes[-1]
    buy_hold = initial_cash / closes[0] * closes[-1]
    return {
        'final': total,
        'buy_hold': buy_hold,
        'grid_trades': grid_trades,
        'grid_outperform': (total / buy_hold - 1) * 100,
        'total_return': (total / initial_cash - 1) * 100,
        'hold_return': (buy_hold / initial_cash - 1) * 100,
    }


# 参数扫描
print('=== 网格回测（H00922 全收益，2018-2026，初始 10 万）===')
print('\n--- 固定区间 [4000, 5800]（近8年 P10~P90）---')
for n_grids in [5, 8, 10, 12]:
    r = grid_backtest(tri_closes, 4000, 5800, n_grids)
    if r:
        print(f'  {n_grids} 格(间距{(5800-4000)/n_grids:.0f}点): 终值 {r["final"]:.0f} 收益率 {r["total_return"]:.1f}% vs 持有 {r["hold_return"]:.1f}% | 超额 {r["grid_outperform"]:+.1f}% | 交易 {r["grid_trades"]} 次')

print('\n--- 宽区间 [3650, 5977]（近8年全区间）---')
for n_grids in [8, 10, 12]:
    r = grid_backtest(tri_closes, 3650, 5977, n_grids)
    if r:
        print(f'  {n_grids} 格(间距{(5977-3650)/n_grids:.0f}点): 终值 {r["final"]:.0f} 收益率 {r["total_return"]:.1f}% vs 持有 {r["hold_return"]:.1f}% | 超额 {r["grid_outperform"]:+.1f}% | 交易 {r["grid_trades"]} 次')

print('\n--- 百分比网格（5% 间距，区间 = 当前价 ±30%）---')
# 用 2018 起点价做固定区间
start_p = tri_closes[0]
for pct in [0.03, 0.05, 0.08]:
    low = start_p * (1 - 0.30)
    high = start_p * (1 + 0.30)
    n = int(round((high - low) / (low * pct)))
    r = grid_backtest(tri_closes, low, high, max(n, 4))
    if r:
        print(f'  {pct*100:.0f}% 间距({n}格): 终值 {r["final"]:.0f} 收益率 {r["total_return"]:.1f}% vs 持有 {r["hold_return"]:.1f}% | 超额 {r["grid_outperform"]:+.1f}% | 交易 {r["grid_trades"]} 次')

# 持有基准
hold = 100000 / tri_closes[0] * tri_closes[-1]
print(f'\n=== 基准 ===')
print(f'纯持有: 终值 {hold:.0f} 收益率 {(hold/100000-1)*100:.1f}%')
print(f'全收益 2018-2026 累计涨幅: {(tri_closes[-1]/tri_closes[0]-1)*100:.1f}%')
