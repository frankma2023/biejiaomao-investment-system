import sys,os;os.chdir("D:/hanako/investment-system");sys.path.insert(0,"D:/hanako/investment-system/src")
import sqlite3,json
sys.path.insert(0,"D:/hanako/investment-system/src/scanners")
from base_breakout_v2 import get_hlc_from_chanlun,load_params,detect

db=sqlite3.connect('data/lixinger.db');db.row_factory=sqlite3.Row
rows=db.execute("SELECT date,open,high,low,close,volume,amount FROM daily_kline WHERE stock_code='603683' AND date<='2026-06-01' AND date>=date('2026-06-01','-500 days') ORDER BY date").fetchall()
daily=[dict(r) for r in rows]
print(f"K lines: {len(daily)}")

hlc=get_hlc_from_chanlun(daily,'603683')
if hlc:
    print(f"H: {hlc['h_date']} ¥{hlc['h_price']} L: {hlc['l_date']} ¥{hlc['l_price']} decline={hlc['decline_pct']}%")
    print(f"C: {hlc['c_start_date']}~{hlc['c_end_date']} ({hlc['c_end_idx']-hlc['c_start_idx']+1} days)")
else:
    print("HLC: None")

params=load_params()
params['drawdown_min'] = 0.01
params['drawdown_max'] = 0.99
params['rs_threshold'] = 0
sigs=detect(daily,params)
print(f"Signals: {len(sigs)}")
for s in sigs:
    print(f"  {s['signal_date']} BO¥{s['breakout_close']} +{s['breakout_gain_pct']}% vol={s['breakout_vol_ratio']}x")
db.close()
