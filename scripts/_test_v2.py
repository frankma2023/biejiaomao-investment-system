import sys,os;os.chdir("D:/hanako/investment-system");sys.path.insert(0,"D:/hanako/investment-system/src")
import sqlite3,json
db=sqlite3.connect("data/lixinger.db");db.row_factory=sqlite3.Row

# Simulate what pattern-scan API does
rows=db.execute("SELECT date,open,high,low,close,volume FROM daily_kline WHERE stock_code='002384' AND date<='2026-06-05' AND date>=date('2026-06-05','-750 days') ORDER BY date").fetchall()
daily=[dict(r) for r in rows]
print(f"K lines: {len(daily)}")

from scanners.base_breakout_v2 import detect,load_params
params=load_params();params['stock_code']='002384'
try:
    sigs=detect(daily,params)
    print(f"Signals: {len(sigs)}")
    for s in sigs[:5]:print(f"  {s.get('signal_date','?')} {s.get('breakout_close','?')}")
except Exception as e:
    import traceback;traceback.print_exc()

db.close()
