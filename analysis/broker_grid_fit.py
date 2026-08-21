# -*- coding: utf-8 -*-
"""
399975 证券公司指数 网格适配性分析
=================================
方法论对齐 grid_step_sens.py：间距敏感性 + 分年度超额 + 波动特征
"""
import sys, os, sqlite3, math, statistics
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row

rows = db.execute("SELECT date, close FROM index_daily_kline WHERE stock_code='399975' AND kline_type='normal' AND date>='2018-01-01' ORDER BY date").fetchall()
dates = [r['date'] for r in rows]
closes = [r['close'] for r in rows]
print(f'399975 证券公司: {len(closes)} 天 ({dates[0]}~{dates[-1]})')

# 年化收益/波动
years = len(closes) / 244
ann = (closes[-1] / closes[0]) ** (1 / years) - 1
rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
vol = (sum((r - sum(rets) / len(rets)) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 * math.sqrt(252) * 100
rng = []
for i in range(20, len(closes)):
    w = closes[i - 19:i + 1]
    rng.append((max(w) - min(w)) / min(w) * 100)
rng_s = sorted(rng)
print(f'年化收益 {ann*100:+.1f}% | 年化波动 {vol:.1f}% | 20日振幅中位 {rng_s[len(rng_s)//2]:.1f}%')
print(f'对比: 煤炭波动34% / 红利17% → 券商波动等级: {"高" if vol > 30 else ("中" if vol > 20 else "低")}')

# 网格回测（百分比间距）
def grid(closes, step_pct, cash=100000):
    c, s, low = cash, 0.0, min(closes) * 0.95
    s = (cash / 3) / closes[0]; c -= cash / 3
    cg = int((closes[0] - low) / (low * step_pct / 100))
    per = cash / 10
    trades = 0
    for i in range(1, len(closes)):
        p = closes[i]
        g = int((p - low) / (low * step_pct / 100))
        if g < cg:
            for _ in range(cg - g):
                if c > per: s += per / p; c -= per; trades += 1
            cg = g
        elif g > cg:
            for _ in range(g - cg):
                if s > 0:
                    amt = min(s * p, per); s -= amt / p; c += amt; trades += 1
            cg = g
    return c + s * closes[-1], trades

hold = 100000 / closes[0] * closes[-1]
print(f'\n=== 全周期（2018起 8.6年）网格 vs 持有 ===')
print(f'持有: {(hold/100000-1)*100:+.1f}%')
for st in (5, 8, 10, 12):
    r = grid(closes, st)
    excess = (r[0] / hold - 1) * 100
    print(f'  {st}%: 网格 {(r[0]/100000-1)*100:+.1f}% | 超额 {excess:+.1f}pp | {r[1]}次交易')

# 分年度
print(f'\n=== 分年度（8%间距）===')
years_list = sorted(set(d[:4] for d in dates))
for y in years_list:
    idx = [i for i, d in enumerate(dates) if d.startswith(y)]
    seg = closes[idx[0]:idx[-1] + 1]
    r = grid(seg, 8)
    h = 100000 / seg[0] * seg[-1]
    idx_ret = (seg[-1] / seg[0] - 1) * 100
    excess = (r[0] / h - 1) * 100
    trend = '↑' if idx_ret > 15 else ('→' if abs(idx_ret) <= 15 else '↓')
    print(f'  {y}: {idx_ret:>+7.1f}% {trend} | 网格超额 {excess:>+6.1f}pp')

# 估值现状
f = db.execute("SELECT pe_ttm, pe_ttm_pct, pb, pb_pct, dyr, dyr_pct FROM index_fundamental_daily WHERE stock_code='399975' ORDER BY date DESC LIMIT 1").fetchone()
if f:
    print(f'\n=== 估值现状（{dates[-1]}）===')
    print(f'PE {f["pe_ttm"]:.1f} (分位 {f["pe_ttm_pct"]*100:.0f}%) | PB {f["pb"]:.2f} (分位 {f["pb_pct"]*100:.0f}%) | 股息率 {f["dyr"]*100:.2f}% (分位 {f["dyr_pct"]*100:.0f}%)')
# 当前回撤/位置
seg250 = closes[-250:]
dd = (max(seg250) - closes[-1]) / max(seg250) * 100
pos = (closes[-1] - min(seg250)) / (max(seg250) - min(seg250)) * 100
print(f'当前250日回撤 {dd:.1f}% | 250日位置 {pos:.0f}%')
db.close()
