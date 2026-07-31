"""
MW B1 实战卖出策略 · 组合规则 · 分层统计
入场: T+1开盘 | 卖出规则组合: MA10卖50% + MA20清仓 + -7%全清 + +25%锁半仓
"""
import json, os, numpy as np
from datetime import datetime
from collections import defaultdict
import sqlite3

DB = 'D:/hanako/investment-system/data/lixinger.db'
WIDE = 'D:/hanako/investment-system/config/strategy/mw_backtest_wide.json'
OUT = 'D:/hanako/investment-system/config/strategy/mw_b1_exit_combined.json'

t0 = datetime.now()
print("=" * 60)
print("MW B1 实战卖出策略")
print("=" * 60)

# ── 1. 加载宽表 ──
with open(WIDE, 'r') as f:
    wide = json.load(f)
print(f"[1] 加载 {len(wide)} 条信号")

# ── 2. 加载 K 线（只取 B1 日后 60 天）──
print("[2] 加载K线...", end=' ', flush=True)
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

needed = defaultdict(list)
for r in wide:
    needed[r['stock_code']].append(r['b1_date'])

# 取所有需要的 K 线（B1 日到 B1+60 天）
all_codes = list(needed.keys())
klines_cache = {}
chunk_size = 500

for bi in range(0, len(all_codes), chunk_size):
    batch = all_codes[bi:bi+chunk_size]
    ph = ','.join('?' * len(batch))
    rows = conn.execute(
        f"SELECT stock_code, date, open, high, low, close FROM daily_kline WHERE stock_code IN ({ph}) AND date >= '2016-01-01' AND date <= '2026-09-30' ORDER BY stock_code, date",
        batch
    ).fetchall()
    
    batch_data = defaultdict(list)
    for r in rows:
        batch_data[r['stock_code']].append((r['date'], r['open'], r['high'], r['low'], r['close']))
    
    for code, data in batch_data.items():
        dates = [d[0] for d in data]
        closes = np.array([d[4] for d in data])
        lows = np.array([d[3] for d in data])
        highs = np.array([d[2] for d in data])
        opens = np.array([d[1] for d in data])
        ma10 = np.full(len(closes), np.nan)
        ma20 = np.full(len(closes), np.nan)
        for i in range(9, len(closes)): ma10[i] = np.mean(closes[i-9:i+1])
        for i in range(19, len(closes)): ma20[i] = np.mean(closes[i-19:i+1])
        klines_cache[code] = (dates, opens, highs, lows, closes, ma10, ma20)

print(f'{len(klines_cache)} 只 ({(datetime.now()-t0).total_seconds():.0f}s)')

# ── 3. 组合卖出模拟 ──
print("[3] 模拟组合卖出...", end=' ', flush=True)

def simulate_combined_exit(code, b1_date, tier, has_b2, decline, h_rs250, tech_score):
    """
    规则（按优先级）：
    1. 亏损 ≥ 7% → 全部清仓
    2. 盈利 ≥ 25% → 卖出 50%（锁利），剩余继续
    3. 跌破 MA10 → 卖出剩余仓位的 50%（如有锁利仓，则卖剩下的一半）
    4. 跌破 MA20 → 全部清仓
    5. 60 天到期 → 按收盘价清仓
    
    返回: (exit_days, final_return_pct, exit_reason)
    """
    cache = klines_cache.get(code)
    if not cache: return None
    dates, opens, highs, lows, closes, ma10, ma20 = cache
    try: idx = dates.index(b1_date)
    except ValueError: return None
    if idx + 1 >= len(closes): return None
    
    entry = opens[idx + 1]
    if entry <= 0: return None
    
    pc = closes[idx+1:]   # post-entry closes
    pl = lows[idx+1:]     
    ph = highs[idx+1:]
    pm10 = ma10[idx+1:]
    pm20 = ma20[idx+1:]
    
    max_days = min(60, len(pc) - 1)
    if max_days < 1: return None
    
    shares = 1.0          # 初始仓位（标准化为1，收益直接是比例）
    locked_profit = 0.0    # 已锁定的利润（比例）
    exit_days = max_days
    exit_reason = '60天到期'
    
    # 追踪 MA 是否已触发过
    ma10_triggered = False
    ma20_triggered = False
    
    for day in range(max_days):
        current_price = pc[day]
        current_ret = current_price / entry - 1
        
        # 规则1: 亏损7%全清
        if current_ret <= -0.07:
            locked_profit += shares * current_ret
            shares = 0
            exit_days = day + 1
            exit_reason = '亏损7%止损'
            break
        
        # 规则2: 盈利25%锁半仓
        if current_ret >= 0.25 and shares >= 0.99:
            half = shares * 0.5
            locked_profit += half * current_ret
            shares -= half
            exit_reason = '盈利25%锁半仓'
        
        # 规则3: 跌破MA10 → 卖剩余的一半（只触发一次）
        if not ma10_triggered and day < len(pm10) and not np.isnan(pm10[day]) and pl[day] < pm10[day]:
            if shares > 0.01:
                sell_frac = shares * 0.5
                locked_profit += sell_frac * (pm10[day] / entry - 1)
                shares -= sell_frac
                ma10_triggered = True
                if exit_reason in ('60天到期','盈利25%锁半仓'):
                    exit_reason = '跌破MA10'
        
        # 规则4: 跌破MA20 → 全清
        if not ma20_triggered and day < len(pm20) and not np.isnan(pm20[day]) and pl[day] < pm20[day]:
            if shares > 0.01:
                locked_profit += shares * (pm20[day] / entry - 1)
                shares = 0
            ma20_triggered = True
            exit_days = day + 1
            if ma10_triggered or '锁半仓' in exit_reason:
                exit_reason = 'MA10→MA20'
            else:
                exit_reason = '跌破MA20'
            break
    
    # 60天到期 → 剩余仓位按收盘价清
    if shares > 0.01:
        last_price = pc[min(max_days - 1, len(pc) - 1)]
        locked_profit += shares * (last_price / entry - 1)
    
    return (exit_days, locked_profit, exit_reason)

# 分层模拟
tiers = [
    ('全量', lambda r: True),
    ('极高≥80', lambda r: r['tech_score'] >= 80),
    ('高65~79', lambda r: 65 <= r['tech_score'] <= 79),
    ('关注50~64', lambda r: 50 <= r['tech_score'] <= 64),
    ('一般<50', lambda r: r['tech_score'] < 50),
    ('有B2', lambda r: r['has_b2']),
    ('有B2+极高', lambda r: r['has_b2'] and r['tech_score'] >= 80),
    ('浅调<20%', lambda r: r['decline_pct'] < 20),
    ('深调≥35%', lambda r: r['decline_pct'] >= 35),
    ('行业RS≥90', lambda r: (r.get('ind_rs20') or 0) >= 90),
]

results_by_tier = {}
for tier_name, fn in tiers:
    subset = [r for r in wide if fn(r)]
    returns = []
    reasons = defaultdict(int)
    exit_days_list = []
    
    for i, r in enumerate(subset):
        res = simulate_combined_exit(
            r['stock_code'], r['b1_date'], tier_name,
            r.get('has_b2'), r.get('decline_pct'),
            r.get('h_rs250'), r.get('tech_score')
        )
        if res:
            days, ret, reason = res
            returns.append(ret)
            reasons[reason] += 1
            exit_days_list.append(days)
    
    if returns:
        arr = np.array(returns)
        results_by_tier[tier_name] = {
            'n': len(arr),
            'win_rate': round((arr > 0).mean() * 100, 1),
            'median': round(np.median(arr) * 100, 2),
            'mean': round(arr.mean() * 100, 2),
            'max_dd': round(arr.min() * 100, 2),
            'best': round(arr.max() * 100, 1),
            'avg_days': round(np.mean(exit_days_list), 1),
            'exit_reasons': dict(reasons),
        }
    if len(subset) % 5000 == 0:
        pass  # silent

print(f'({(datetime.now()-t0).total_seconds():.0f}s)')

# ── 4. 输出 ──
print("\n" + "=" * 80)
print("MW B1 实战卖出策略 · 组合规则")
print("规则: T+1开盘入场 | -7%全清 | +25%锁半仓 | MA10卖剩余一半 | MA20全清")
print("=" * 80)
print(f"{'分层':<16} {'N':>7} {'胜率':>7} {'中位':>7} {'均值':>7} {'最大亏损':>8} {'均持':>6}  {'退出原因'}")
print("-" * 85)
for name in ['全量','极高≥80','高65~79','关注50~64','一般<50','有B2','有B2+极高','浅调<20%','深调≥35%','行业RS≥90']:
    s = results_by_tier.get(name)
    if not s: continue
    top_reasons = sorted(s['exit_reasons'].items(), key=lambda x: -x[1])[:2]
    reason_str = ' → '.join(f'{r[0]}({r[1]/s["n"]*100:.0f}%)' for r in top_reasons)
    print(f"{name:<16} {s['n']:>7,} {s['win_rate']:>6.1f}% {s['median']:>6.2f}% {s['mean']:>6.2f}% {s['max_dd']:>7.2f}% {s['avg_days']:>5.1f}d {reason_str}")

# ── 对比：纯持有10日 vs 组合卖出 ──
print("\n" + "-" * 85)
print("策略对比: 纯持有10日 vs 组合卖出")
print("-" * 85)
hold10 = np.array([r.get('ret_b1_10d') for r in wide if r.get('ret_b1_10d') is not None and not (isinstance(r.get('ret_b1_10d'),float) and np.isnan(r.get('ret_b1_10d')))])
combo = results_by_tier.get('全量', {})
print(f"  纯持有10日: 胜率={(hold10>0).mean()*100:.1f}% 中位={np.median(hold10)*100:+.2f}% 均值={hold10.mean()*100:+.2f}% 最大亏损={hold10.min()*100:+.2f}%")
if combo:
    print(f"  组合卖出:   胜率={combo['win_rate']}% 中位={combo['median']:+.2f}% 均值={combo['mean']:+.2f}% 最大亏损={combo['max_dd']:+.2f}%")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(results_by_tier, f, ensure_ascii=False, indent=2)
print(f"\n→ {OUT} ({ (datetime.now()-t0).total_seconds():.0f}s)")
conn.close()
