"""
高置信度口袋支点回测：B1=口袋支点日 → 次日开盘买入
"""
import sqlite3, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from collections import defaultdict

DB = "D:/hanako/investment-system/data/lixinger.db"
db = sqlite3.connect(DB); db.row_factory = sqlite3.Row

# 1. 找出所有 B1 = pocket_pivot 的重合信号
pairs = db.execute("""
    SELECT m.stock_code, m.stock_name, m.b1_date as pp_date, m.b2_date, m.score,
           pp.pivot_type, pp.gain_pct, pp.vol_ratio, pp.c_days
    FROM mw_signal_daily m
    INNER JOIN pocket_pivot_daily pp ON m.stock_code = pp.stock_code AND m.b1_date = pp.date
    WHERE m.b2_date >= '2023-06-01' AND m.b2_date <= '2026-06-05'
    ORDER BY m.b1_date
""").fetchall()
print(f"B1=口袋支点 重合信号: {len(pairs)}")

if not pairs:
    print("无数据"); db.close(); sys.exit(1)

# 2. 批量加载K线（次日开盘价 → forward returns）
codes = list(set(p['stock_code'] for p in pairs))
pc = {}
for code in codes:
    rows = db.execute("SELECT date, open, close FROM daily_kline WHERE stock_code=? AND date >= '2023-06-01' AND date <= '2026-07-31' ORDER BY date", (code,)).fetchall()
    pc[code] = [{r['date']: {'o': r['open'], 'c': r['close']}} for r in rows]
    # Convert to dict
    pc[code] = {}
    for r in rows:
        pc[code][r['date']] = {'o': r['open'], 'c': r['close']}

# 3. 计算 forward returns
results = {5: [], 10: [], 20: []}
trades = []

for p in pairs:
    code = p['stock_code']
    pp_date = p['pp_date']
    prices = pc.get(code, {})
    dates = sorted(prices.keys())
    
    if pp_date not in dates: continue
    idx = dates.index(pp_date)
    
    # 次日开盘买入
    next_idx = idx + 1
    if next_idx >= len(dates): continue
    entry_date = dates[next_idx]
    entry_price = prices[entry_date]['o']
    if entry_price <= 0: continue
    
    trade = {
        'code': code, 'name': p['stock_name'],
        'pp_date': pp_date, 'entry_date': entry_date,
        'entry_price': entry_price, 'pivot_type': p['pivot_type'],
        'score': p['score'], 'c_days': p['c_days']
    }
    
    for h in [5, 10, 20]:
        fut = next_idx + h
        if fut < len(dates):
            ret = (prices[dates[fut]]['c'] - entry_price) / entry_price * 100
            results[h].append(ret)
            trade[f'ret_{h}d'] = ret
        else:
            trade[f'ret_{h}d'] = None
    
    trades.append(trade)

# 4. 统计
from analytics.mw_backtest import calc_stats

print(f"\n有效交易: {len(trades)}")
print(f"{'='*70}")
print(f"  高置信度口袋支点回测")
print(f"  条件: B1日 = 口袋支点日")
print(f"  入场: 信号次日开盘价")
print(f"  区间: 2023-06-01 ~ 2026-06-05")
print(f"{'='*70}")

for h in [5, 10, 20]:
    r = results[h]
    if not r: continue
    s = calc_stats(r)
    wins = sum(1 for v in r if v > 0)
    print(f"\n  {h}日持有:  {len(r)}笔")
    print(f"    胜率:    {s['win_rate']:.1f}%")
    print(f"    中位:    {s['median_return']:+.2f}%")
    print(f"    平均:    {s['avg_return']:+.2f}%")
    
    # 收益分布
    buckets = [('>20%', lambda v: v>20), ('10~20%', lambda v: 10<v<=20),
               ('5~10%', lambda v: 5<v<=10), ('0~5%', lambda v: 0<v<=5),
               ('-5~0%', lambda v: -5<v<=0), ('-10~-5%', lambda v: -10<v<=-5),
               ('<-10%', lambda v: v<=-10)]
    print(f"    分布:")
    for label, fn in buckets:
        cnt = sum(1 for v in r if fn(v))
        bar = '█' * int(cnt / max(1, len(r)) * 40)
        print(f"      {label}: {cnt:>4} ({cnt/len(r)*100:>5.1f}%) {bar}")

# 5. 按 pivot_type 分组
print(f"\n  按类型分组 (10日):")
for pt in ['base', 'continuation', '10ma_bounce']:
    r10 = [t['ret_10d'] for t in trades if t['pivot_type'] == pt and t['ret_10d'] is not None]
    if r10:
        s = calc_stats(r10)
        print(f"    {pt}: {len(r10)}笔 胜率{s['win_rate']:.1f}% 中位{s['median_return']:+.2f}%")

# 6. 月度分布
monthly = defaultdict(lambda: {'count': 0, 'wins_10': 0})
for t in trades:
    m = t['pp_date'][:7]
    monthly[m]['count'] += 1
    if t.get('ret_10d') and t['ret_10d'] > 0:
        monthly[m]['wins_10'] += 1

print(f"\n  月度分布 (Top 10):")
for m in sorted(monthly, key=lambda x: -monthly[x]['count'])[:12]:
    d = monthly[m]
    wr = d['wins_10']/d['count']*100 if d['count'] else 0
    print(f"    {m}: {d['count']:>3}笔 10d胜率{wr:.0f}%")

db.close()
