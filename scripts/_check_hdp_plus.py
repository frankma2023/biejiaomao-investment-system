import sqlite3, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
from analytics.mw_backtest import *

DB = "D:/hanako/investment-system/data/lixinger.db"
start_date = '2026-01-01'
end_date = '2026-06-05'

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# 1. 全量信号
signals_raw = db.execute("""
    SELECT * FROM mw_signal_daily WHERE b2_date >= ? AND b2_date <= ? ORDER BY b2_date
""", (start_date, end_date)).fetchall()
signals = [dict(r) for r in signals_raw]

# 2. 新PLUS: score>=80 AND H=15 AND D=15 AND P=15
new_plus = [s for s in signals if s['score'] >= 80 and s['score_h'] == 15 and s['score_d'] == 15 and s['score_p'] == 15]
old_plus = [s for s in signals if s['score'] >= 80 and s['score_d'] == 15 and s['score_i1'] == 15]

print(f"旧PLUS (D+I1): {len(old_plus)}")
print(f"新PLUS (HDP): {len(new_plus)}")
print(f"重叠: {len(set(s['id'] for s in new_plus) & set(s['id'] for s in old_plus))}")

# 3. Forward returns
codes = list(set(s['stock_code'] for s in signals))
price_cache = {}
for code in codes:
    rows = db.execute("SELECT date, close FROM daily_kline WHERE stock_code=? AND date >= ? ORDER BY date", (code, start_date)).fetchall()
    price_cache[code] = {r['date']: r['close'] for r in rows}

def get_rets(sig_list):
    rets_5, rets_10, rets_20 = [], [], []
    for sig in sig_list:
        code = sig['stock_code']
        b2 = sig['b2_date']
        prices = price_cache.get(code, {})
        dates = sorted(prices.keys())
        if b2 not in prices: continue
        entry = prices[b2]
        try: idx = dates.index(b2)
        except: continue
        for h in [5,10,20]:
            fut = idx + h
            if fut < len(dates):
                r = (prices[dates[fut]] - entry) / entry * 100
                {5: rets_5, 10: rets_10, 20: rets_20}[h].append(r)
    return rets_5, rets_10, rets_20

print("\n=== B2次日买入 ===")
for label, sigs in [("旧PLUS(D+I1)", old_plus), ("新PLUS(HDP)", new_plus)]:
    r5, r10, r20 = get_rets(sigs)
    s5, s10, s20 = calc_stats(r5), calc_stats(r10), calc_stats(r20)
    print(f"\n{label}: {len(r5)}有效")
    print(f"  5d:  胜率{s5['win_rate']:.1f}% 中位{s5['median_return']:+.2f}% 平均{s5['avg_return']:+.2f}%")
    print(f"  10d: 胜率{s10['win_rate']:.1f}% 中位{s10['median_return']:+.2f}% 平均{s10['avg_return']:+.2f}%")
    print(f"  20d: 胜率{s20['win_rate']:.1f}% 中位{s20['median_return']:+.2f}% 平均{s20['avg_return']:+.2f}%")

# 4. B2+2 延迟入场
def get_delayed_rets(sig_list, delay):
    rets_5, rets_10, rets_20 = [], [], []
    for sig in sig_list:
        code = sig['stock_code']
        b2 = sig['b2_date']
        prices = price_cache.get(code, {})
        dates = sorted(prices.keys())
        if b2 not in dates: continue
        try: idx = dates.index(b2)
        except: continue
        entry_idx = idx + delay
        if entry_idx >= len(dates): continue
        entry = price_cache[code].get(dates[entry_idx], 0)
        if not entry: continue
        for h in [5,10,20]:
            fut = entry_idx + h
            if fut < len(dates):
                r = (prices[dates[fut]] - entry) / entry * 100
                {5: rets_5, 10: rets_10, 20: rets_20}[h].append(r)
    return rets_5, rets_10, rets_20

print("\n=== B2+2日开盘买入 ===")
for label, sigs in [("旧PLUS(D+I1)", old_plus), ("新PLUS(HDP)", new_plus)]:
    r5, r10, r20 = get_delayed_rets(sigs, 2)
    s5, s10, s20 = calc_stats(r5), calc_stats(r10), calc_stats(r20)
    print(f"\n{label}: {len(r5)}有效")
    print(f"  5d:  胜率{s5['win_rate']:.1f}% 中位{s5['median_return']:+.2f}%")
    print(f"  10d: 胜率{s10['win_rate']:.1f}% 中位{s10['median_return']:+.2f}%")
    print(f"  20d: 胜率{s20['win_rate']:.1f}% 中位{s20['median_return']:+.2f}%")

# 5. 新PLUS行业分布
new_plus_codes = list(set(s['stock_code'] for s in new_plus))
ind_count = {}
for s in new_plus:
    ind = s.get('ind_name', '未分类')
    ind_count[ind] = ind_count.get(ind, 0) + 1
print(f"\n=== 新PLUS行业分布 (Top10) ===")
for ind, cnt in sorted(ind_count.items(), key=lambda x: -x[1])[:10]:
    print(f"  {ind}: {cnt}")

# 6. 被新PLUS淘汰的旧PLUS
dropped = [s for s in old_plus if s['id'] not in set(x['id'] for x in new_plus)]
new_added = [s for s in new_plus if s['id'] not in set(x['id'] for x in old_plus)]
print(f"\n被淘汰的旧PLUS: {len(dropped)}")
for s in dropped[:5]:
    print(f"  {s['stock_code']} {s['stock_name']} 得分{s['score']} H={s['score_h']} D={s['score_d']} P={s['score_p']} I1={s['score_i1']}")
print(f"新增的PLUS: {len(new_added)}")
for s in new_added[:5]:
    print(f"  {s['stock_code']} {s['stock_name']} 得分{s['score']} H={s['score_h']} D={s['score_d']} P={s['score_p']} I1={s['score_i1']}")

db.close()
