import sqlite3
from datetime import datetime

db = sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
db.row_factory = sqlite3.Row

# 21 PLUS signals
signals = db.execute("""
    SELECT stock_code, stock_name, b2_date, score, confidence, 
           score_h, score_d, score_p, score_i1, score_i2, score_sig, score_gap,
           h_rs250, decline_pct, ind_name
    FROM mw_signal_daily WHERE is_plus=1 ORDER BY b2_date
""").fetchall()

# Load price data
codes = list(set(s['stock_code'] for s in signals))
pc = {}
for code in codes:
    rows = db.execute("SELECT date, close FROM daily_kline WHERE stock_code=? AND date >= '2026-01-01' ORDER BY date", (code,)).fetchall()
    pc[code] = {r['date']: r['close'] for r in rows}

print(f"{'代码':<8}{'名称':<10}{'得分':>4}{'B2日':>12}{'5d':>8}{'10d':>8}{'20d':>8}{'至06-05':>9}  {'行业'}")
print("-"*90)

for s in signals:
    code = s['stock_code']
    b2 = s['b2_date']
    prices = pc.get(code, {})
    dates = sorted(prices.keys())
    if b2 not in dates: continue
    entry = prices[b2]
    try: idx = dates.index(b2)
    except: continue
    
    rets = {}
    for h, label in [(5,'5d'),(10,'10d'),(20,'20d')]:
        fut = idx + h
        if fut < len(dates):
            rets[label] = (prices[dates[fut]] - entry) / entry * 100
        else:
            rets[label] = None
    
    # 到06-05
    if '2026-06-05' in dates:
        to_end = (prices['2026-06-05'] - entry) / entry * 100
    else:
        # last available
        last_date = dates[-1]
        to_end = (prices[last_date] - entry) / entry * 100
    
    r5 = f"{rets['5d']:+.1f}%" if rets['5d'] is not None else "  —"
    r10 = f"{rets['10d']:+.1f}%" if rets['10d'] is not None else "  —"
    r20 = f"{rets['20d']:+.1f}%" if rets['20d'] is not None else "  —"
    rend = f"{to_end:+.1f}%"
    
    print(f"{code:<8}{s['stock_name']:<10}{s['score']:>4}{b2:>12}{r5:>8}{r10:>8}{r20:>8}{rend:>9}  {s['ind_name'] or ''}")

db.close()
