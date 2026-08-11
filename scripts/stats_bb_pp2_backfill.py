# -*- coding: utf-8 -*-
"""回填表口径共振统计：market_breakout_v2_daily + pocket_pivot_daily(V2) 同日共振 → 未来收益
这是防未来数据的正确口径（回填逐日设置 target_date 读当日快照）
"""
import sqlite3, json, os
from collections import Counter

DB = r'D:\hanako\investment-system\data\lixinger.db'
START, END = '2023-08-01', '2026-08-07'
HORIZONS = (10, 20, 30, 60)

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# 1. 共振对
rows = db.execute('''
SELECT b.date, b.stock_code FROM market_breakout_v2_daily b
JOIN pocket_pivot_daily p ON p.date=b.date AND p.stock_code=b.stock_code AND p.engine_version='V2'
WHERE b.date >= ? AND b.date <= ?
''', (START, END)).fetchall()
pairs = [(r['date'], r['stock_code']) for r in rows]
print(f'回填表共振: {len(pairs)} 组')
years = Counter(d[:4] for d, _ in pairs)
print('按年:', dict(years))

# 2. 加载 K 线（前复权）
codes = sorted(set(c for _, c in pairs))
klines = {}
for code in codes:
    rs = db.execute("""SELECT date, close, change_pct FROM daily_kline
        WHERE stock_code=? AND date>=? AND date<=? ORDER BY date""",
        (code, '2021-01-01', END)).fetchall()
    if not rs: continue
    n = len(rs)
    adj = [None]*n
    adj[n-1] = rs[n-1]['close']
    for i in range(n-2, -1, -1):
        chg = rs[i+1]['change_pct']
        adj[i] = adj[i+1]/(1+chg) if chg is not None else adj[i+1]
    klines[code] = [(rs[i]['date'], adj[i]) for i in range(n)]

# 3. 收益
stats = {h: [] for h in HORIZONS}
for sig_date, code in pairs:
    kl = klines.get(code)
    if not kl: continue
    dates = [d for d, _ in kl]
    if sig_date not in dates: continue
    i0 = dates.index(sig_date)
    base = kl[i0][1]
    if not base: continue
    for h in HORIZONS:
        i1 = i0 + h
        if i1 < len(kl):
            stats[h].append((kl[i1][1]/base - 1)*100)

print(f'\n{"="*70}')
print(f'回填表口径共振 → 未来收益（{START} ~ {END}）')
print(f'{"="*70}')
out = {'total': len(pairs), 'horizons': {}}
for h in HORIZONS:
    vals = stats[h]
    if not vals: continue
    vs = sorted(vals)
    n = len(vs)
    mean = sum(vs)/n
    median = vs[n//2]
    win = sum(1 for v in vs if v > 0)/n*100
    out['horizons'][str(h)] = {'n': n, 'mean': round(mean,2), 'median': round(median,2), 'win_rate': round(win,1),
        'p10': round(vs[int(n*.1)],2), 'p25': round(vs[int(n*.25)],2), 'p75': round(vs[int(n*.75)],2), 'p90': round(vs[int(n*.9)],2)}
    print(f'{h}日 (n={n}): 均值{mean:+.2f}% 中位{median:+.2f}% 胜率{win:.1f}% | P10={vs[int(n*.1)]:+.1f}% P90={vs[int(n*.9)]:+.1f}%')

with open(r'D:\hanako\investment-system\analysis\bb_pp2_backfill_stats.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print('\n已保存 analysis/bb_pp2_backfill_stats.json')
db.close()
