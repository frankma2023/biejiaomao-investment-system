# -*- coding: utf-8 -*-
"""
红利指数买点依据全面回测（H00922 全收益口径，2018-2026）
========================================================
验证用户提出的 4 类买点依据：
  A. 股债息差（低息差=贵=卖点? 高息差=便宜=买点?）
  B. 估值绝对值（PE/PB/股息率）——用分位替代（绝对值无历史锚）
  C. 估值分位（PE分位低/PB分位低/股息率分位高 = 买点）
  D. 250日回撤 ≥10%（对照组，已知 32次/75%）

口径：次日收盘买入，20/60 日收益；20日去重合并（相邻 20 日内只算第一次触发）
"""
import sys, os, sqlite3
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row

# 全收益收盘
tri = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code='H00922' AND date>='2018-01-01' ORDER BY date").fetchall()
tri = [dict(r) for r in tri]
dates = [r['date'] for r in tri]
closes = [r['close'] for r in tri]
n = len(closes)

# 估值分位
fund = db.execute("SELECT date, pe_ttm_pct, pb_pct, dyr_pct FROM index_fundamental_daily WHERE stock_code='000922' AND date>='2018-01-01' ORDER BY date").fetchall()
fmap = {r['date']: r for r in fund}

# 国债 10Y
bond = db.execute("SELECT date, y10 FROM bond_yield_daily WHERE date>='2018-01-01' AND y10 IS NOT NULL ORDER BY date").fetchall()
bmap = {r['date']: r['y10'] for r in bond}

# dyr 序列（息差）
dyr_map = {r['date']: (r['dyr_pct'] and 0 or 0) for r in fund}  # placeholder

# 构造逐日信号
def build_signals():
    sig = []
    for i, d in enumerate(dates):
        row = {'date': d, 'i': i}
        f = fmap.get(d)
        row['pe_pct'] = f['pe_ttm_pct'] * 100 if f and f['pe_ttm_pct'] is not None else None
        row['pb_pct'] = f['pb_pct'] * 100 if f and f['pb_pct'] is not None else None
        row['dyr_pct'] = f['dyr_pct'] * 100 if f and f['dyr_pct'] is not None else None
        # 息差：dyr 需要从 fund 拿（dyr 字段没查，重新查）
        sig.append(row)
    return sig

# 息差序列（dyr×100 - y10）
dyr_all = db.execute("SELECT date, dyr FROM index_fundamental_daily WHERE stock_code='000922' AND date>='2018-01-01' AND dyr IS NOT NULL ORDER BY date").fetchall()
dyr_map = {r['date']: r['dyr'] * 100 for r in dyr_all}
dates_all = sorted(set(list(dyr_map.keys()) + list(bmap.keys())))
spread_map = {}
for d in dates_all:
    if d in dyr_map and d in bmap:
        spread_map[d] = dyr_map[d] - bmap[d]
spreads = sorted(spread_map.values())

def pct_all(v):
    return sum(1 for x in spreads if x <= v) / len(spreads) * 100 if spreads else 50

def analyze(signal_fn, label, cooldown=20):
    """signal_fn(i, d, row) → bool。次日收盘买入，20日去重"""
    events = []
    last_trigger = -999
    for i, d in enumerate(dates):
        if i < 250 or i >= n - 61:
            continue
        if i - last_trigger < cooldown:
            continue
        row = {'date': d, 'i': i}
        f = fmap.get(d)
        row['pe_pct'] = f['pe_ttm_pct'] * 100 if f and f['pe_ttm_pct'] is not None else None
        row['pb_pct'] = f['pb_pct'] * 100 if f and f['pb_pct'] is not None else None
        row['dyr_pct'] = f['dyr_pct'] * 100 if f and f['dyr_pct'] is not None else None
        row['spread'] = spread_map.get(d)
        if not signal_fn(i, d, row):
            continue
        last_trigger = i
        buy = closes[i + 1]
        r20 = closes[i + 21] / buy - 1 if i + 21 < n else None
        r60 = closes[i + 61] / buy - 1 if i + 61 < n else None
        events.append((r20, r60))
    wins20 = [e[0] for e in events if e[0] is not None]
    wins60 = [e[1] for e in events if e[1] is not None]
    import statistics
    if len(wins20) < 3:
        return f'{label:<28} 触发{len(wins20):>3}次 样本不足'
    return (f'{label:<28} 触发{len(events):>3}次 | 20日胜率{sum(1 for v in wins20 if v > 0)/len(wins20)*100:>5.1f}% 中位{statistics.median(wins20)*100:>+6.2f}%'
            f' | 60日胜率{sum(1 for v in wins60 if v > 0)/len(wins60)*100:>5.1f}% 中位{statistics.median(wins60)*100:>+6.2f}%')

# 随机基准
import random
random.seed(42)
def rand_sig(i, d, row):
    return random.random() < 0.05

print('=== 红利买点依据回测（H00922 全收益 · 次日收盘买入 · 20日去重）===')
print()
# D. 回撤≥10%（对照组）
def dd10(i, d, row):
    seg = closes[i-249:i+1]
    return (max(seg) - closes[i]) / max(seg) * 100 >= 10
print(analyze(dd10, 'D. 250日回撤≥10% (对照)'))
print(analyze(dd10, 'D. 250日回撤≥8%', cooldown=20))
print()
# A. 息差
print('--- A. 股债息差（dyr−10Y国债）---')
print(analyze(lambda i, d, r: r['spread'] is not None and r['spread'] <= 2.5, 'A1. 息差 ≤2.5%'))
print(analyze(lambda i, d, r: r['spread'] is not None and r['spread'] >= 3.0, 'A2. 息差 ≥3.0%'))
print(analyze(lambda i, d, r: r['spread'] is not None and pct_all(r['spread']) <= 30, 'A3. 息差分位 ≤30'))
print(analyze(lambda i, d, r: r['spread'] is not None and pct_all(r['spread']) >= 70, 'A4. 息差分位 ≥70'))
print()
print('--- B/C. 估值分位 ---')
print(analyze(lambda i, d, r: r['pe_pct'] is not None and r['pe_pct'] <= 30, 'C1. PE分位 ≤30%'))
print(analyze(lambda i, d, r: r['pb_pct'] is not None and r['pb_pct'] <= 30, 'C2. PB分位 ≤30%'))
print(analyze(lambda i, d, r: r['dyr_pct'] is not None and r['dyr_pct'] >= 70, 'C3. 股息率分位 ≥70%'))
print(analyze(lambda i, d, r: r['pe_pct'] is not None and r['pe_pct'] <= 30 and r['dyr_pct'] is not None and r['dyr_pct'] >= 70, 'C4. PE分位≤30 且 股息率分位≥70'))
print()
print('--- 组合：回撤+估值双确认 ---')
print(analyze(lambda i, d, r: (max(closes[i-249:i+1]) - closes[i]) / max(closes[i-249:i+1]) * 100 >= 8 and r['dyr_pct'] is not None and r['dyr_pct'] >= 60, 'D+C. 回撤≥8% 且 股息率分位≥60'))
print(analyze(rand_sig, '随机基准 (5%概率)'))
db.close()
