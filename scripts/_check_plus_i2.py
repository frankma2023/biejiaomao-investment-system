import sqlite3, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
from analytics.mw_backtest import *

DB = "D:/hanako/investment-system/data/lixinger.db"
start_date, end_date = '2026-01-01', '2026-06-05'

db = sqlite3.connect(DB); db.row_factory = sqlite3.Row
signals = [dict(r) for r in db.execute(
    "SELECT * FROM mw_signal_daily WHERE b2_date >= ? AND b2_date <= ?", 
    (start_date, end_date)).fetchall()]

old  = [s for s in signals if s['score']>=80 and s['score_d']==15 and s['score_i1']==15]
plus = [s for s in signals if s['score']>=80 and s['score_d']==15 and s['score_i1']==15 and s['score_i2']==15]

print(f"旧PLUS: {len(old)}")
print(f"新PLUS(+I2满分): {len(plus)}")
print(f"重叠: {len(set(s['id'] for s in plus) & set(s['id'] for s in old))}")

# Forward returns
codes = list(set(s['stock_code'] for s in signals))
pc = {}
for code in codes:
    rows = db.execute("SELECT date, close FROM daily_kline WHERE stock_code=? AND date >= ? ORDER BY date", (code, start_date)).fetchall()
    pc[code] = {r['date']: r['close'] for r in rows}

def rets(sigs, delay=0):
    r5, r10, r20 = [], [], []
    for s in sigs:
        code, b2 = s['stock_code'], s['b2_date']
        p = pc.get(code, {}); dates = sorted(p.keys())
        if b2 not in dates: continue
        idx = dates.index(b2) + delay
        if idx >= len(dates): continue
        entry = p[dates[idx]]
        for h in [5,10,20]:
            if idx+h < len(dates):
                ({5:r5,10:r10,20:r20}[h]).append((p[dates[idx+h]]-entry)/entry*100)
    return calc_stats(r5), calc_stats(r10), calc_stats(r20)

print("\n=== B2+2日开盘买入 ===")
for label, sigs in [("旧PLUS", old), ("新PLUS(+I2满分)", plus)]:
    s5,s10,s20 = rets(sigs, 2)
    print(f"\n{label}: {s5['n']}有效")
    print(f"  5d:  胜率{s5['win_rate']:.1f}% 中位{s5['median_return']:+.2f}%")
    print(f"  10d: 胜率{s10['win_rate']:.1f}% 中位{s10['median_return']:+.2f}%")
    print(f"  20d: 胜率{s20['win_rate']:.1f}% 中位{s20['median_return']:+.2f}%")

# 行业分布
from collections import Counter
ind = Counter(s.get('ind_name','未分类') for s in plus)
print(f"\n新PLUS行业分布:")
for k,v in ind.most_common(10):
    print(f"  {k}: {v}")

# 被淘汰的
lost = [s for s in old if s['id'] not in set(x['id'] for x in plus)]
print(f"\n被淘汰的旧PLUS: {len(lost)}")
for s in lost:
    print(f"  {s['stock_code']} {s['stock_name']:8s} 得分{s['score']} I2={s['score_i2']} h_rs250={s['h_rs250']} {s.get('ind_name','')}")

# 得分分布
from collections import Counter
sc = Counter(s['score'] for s in plus)
print(f"\n新PLUS得分分布: {dict(sorted(sc.items()))}")

db.close()
