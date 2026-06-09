"""
深度分析：为什么 B1=pocket_pivot 反而表现差？
"""
import sqlite3, sys, os
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from analytics.mw_backtest import calc_stats

DB = "D:/hanako/investment-system/data/lixinger.db"
db = sqlite3.connect(DB); db.row_factory = sqlite3.Row

# 1. 找出所有 B1 = pocket_pivot 的案例
pairs = db.execute("""
    SELECT m.stock_code, m.stock_name, m.b1_date, m.b2_date, m.score, m.confidence,
           m.h_date, m.l_date, m.decline_pct, m.h_rs250,
           pp.pivot_type, pp.gain_pct as pp_gain, pp.vol_ratio as pp_vol, pp.c_days
    FROM mw_signal_daily m
    INNER JOIN pocket_pivot_daily pp ON m.stock_code = pp.stock_code AND m.b1_date = pp.date
    WHERE m.b2_date >= '2025-06-01' AND m.b2_date <= '2026-06-05'
    ORDER BY m.b2_date
""").fetchall()
print(f"B1=pocket_pivot 重合案例: {len(pairs)}")

# 2. 查看几个典型案例
print("\n=== 典型重合案例（前10个）===")
for p in pairs[:10]:
    print(f"  {p['stock_code']} {p['stock_name']:8s} B1={p['b1_date']} B2={p['b2_date']} "
          f"得分{p['score']} 置信{p['confidence']} 跌幅{p['decline_pct']:.1f}% "
          f"PP类型={p['pivot_type']} PP涨幅={p['pp_gain']:.1f}% PP盘整{p['c_days']}天")

# 3. 分市场状态看
# 加载市场状态
market_states = {}
rows = db.execute("""
    SELECT date, close FROM index_daily_kline
    WHERE stock_code='000985' AND date >= '2025-01-01' ORDER BY date
""").fetchall()
closes = [r['close'] for r in rows]
dates = [r['date'] for r in rows]

def sma(v, n):
    if len(v) < n: return None
    return sum(v[-n:])/n

for i in range(50, len(closes)):
    ma20 = sma(closes[:i+1], 20)
    ma50 = sma(closes[:i+1], 50)
    if ma20 and ma50:
        if closes[i] > ma20 > ma50: market_states[dates[i]] = 'bull'
        elif closes[i] < ma20 < ma50: market_states[dates[i]] = 'bear'
        else: market_states[dates[i]] = 'sideways'

# 4. 分市场状态分组
for group_label, group_filter in [
    ("B1重合", "INNER"),
    ("无口袋支点", "NOT IN")
]:
    if group_filter == "INNER":
        sigs = db.execute("""
            SELECT m.* FROM mw_signal_daily m
            INNER JOIN pocket_pivot_daily pp ON m.stock_code=pp.stock_code AND m.b1_date=pp.date
            WHERE m.b2_date >= '2025-06-01' AND m.b2_date <= '2026-06-05'
        """).fetchall()
    else:
        # Get all codes+dates that have pocket pivot
        pp_set = set()
        for r in db.execute("SELECT stock_code, date FROM pocket_pivot_daily").fetchall():
            pp_set.add((r['stock_code'], r['date']))
        all_mw = db.execute("""
            SELECT * FROM mw_signal_daily WHERE b2_date >= '2025-06-01' AND b2_date <= '2026-06-05'
        """).fetchall()
        sigs = [r for r in all_mw if (r['stock_code'], r['b1_date']) not in pp_set]
    
    # Load K-line for forward returns
    codes = list(set(s['stock_code'] for s in sigs))
    pc = {}
    for code in codes:
        rows = db.execute("SELECT date, close FROM daily_kline WHERE stock_code=? AND date>='2025-06-01' ORDER BY date", (code,)).fetchall()
        pc[code] = {r['date']: r['close'] for r in rows}
    
    # Group by market state at B2
    by_market = defaultdict(list)
    for s in sigs:
        state = market_states.get(s['b2_date'], 'sideways')
        # Forward returns from B2
        prices = pc.get(s['stock_code'], {})
        dates_kl = sorted(prices.keys())
        if s['b2_date'] not in prices: continue
        entry = prices[s['b2_date']]
        try: idx = dates_kl.index(s['b2_date'])
        except: continue
        for h in [5,10,20]:
            fut = idx + h
            if fut < len(dates_kl):
                ret = (prices[dates_kl[fut]] - entry) / entry * 100
                by_market[(state, h)].append(ret)
    
    print(f"\n=== {group_label} 分市场状态 ===")
    for state in ['bull', 'bear', 'sideways']:
        for h in [5, 10, 20]:
            rets = by_market.get((state, h), [])
            if rets:
                s = calc_stats(rets)
                print(f"  {state} {h}d: {len(rets)}个 胜率{s['win_rate']:.1f}% 中位{s['median_return']:+.2f}%")
            else:
                print(f"  {state} {h}d: 0个")

# 5. 对比：用口袋支点日作为入场点（而非 B2）
print(f"\n=== 口袋支点作为入场信号（非B2）===")
pp_sigs = db.execute("""
    SELECT pp.*, m.b2_date as mw_b2_date, m.score as mw_score
    FROM pocket_pivot_daily pp
    LEFT JOIN mw_signal_daily m ON pp.stock_code=m.stock_code AND pp.date=m.b1_date
    WHERE pp.date >= '2025-06-01' AND pp.date <= '2026-06-05'
""").fetchall()

pp_codes = list(set(s['stock_code'] for s in pp_sigs))
pp_pc = {}
for code in pp_codes:
    rows = db.execute("SELECT date, close FROM daily_kline WHERE stock_code=? AND date>='2025-06-01' ORDER BY date", (code,)).fetchall()
    pp_pc[code] = {r['date']: r['close'] for r in rows}

# 口袋支点当天入场
pp_rets = {h: [] for h in [5, 10, 20]}
pp_b1_rets = {h: [] for h in [5, 10, 20]}  # 仅B1重合的
pp_no_b1_rets = {h: [] for h in [5, 10, 20]}  # B1不重合的

for s in pp_sigs:
    code = s['stock_code']
    prices = pp_pc.get(code, {})
    dates_kl = sorted(prices.keys())
    if s['date'] not in prices: continue
    entry = prices[s['date']]
    try: idx = dates_kl.index(s['date'])
    except: continue
    
    for h in [5,10,20]:
        fut = idx + h
        if fut < len(dates_kl):
            ret = (prices[dates_kl[fut]] - entry) / entry * 100
            pp_rets[h].append(ret)
            if s['mw_b2_date']:
                pp_b1_rets[h].append(ret)
            else:
                pp_no_b1_rets[h].append(ret)

print("口袋支点入场（全部）:")
for h in [5,10,20]:
    s = calc_stats(pp_rets[h])
    print(f"  {h}d: {len(pp_rets[h])}个 胜率{s['win_rate']:.1f}% 中位{s['median_return']:+.2f}%")

print("其中 B1重合口袋支点:")
for h in [5,10,20]:
    s = calc_stats(pp_b1_rets[h])
    print(f"  {h}d: {len(pp_b1_rets[h])}个 胜率{s['win_rate']:.1f}% 中位{s['median_return']:+.2f}%")

print("其中 无B1的口袋支点:")
for h in [5,10,20]:
    s = calc_stats(pp_no_b1_rets[h])
    print(f"  {h}d: {len(pp_no_b1_rets[h])}个 胜率{s['win_rate']:.1f}% 中位{s['median_return']:+.2f}%")

db.close()
