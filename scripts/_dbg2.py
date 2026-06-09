import sys,os;os.chdir("D:/hanako/investment-system")
SRC="D:/hanako/investment-system/src";SRC2="D:/hanako/investment-system/src/scanners"
sys.path[:0]=[SRC,SRC2]
import json,sqlite3
from base_breakout_v2 import get_hlc_from_chanlun,load_params,detect

db=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db");db.row_factory=sqlite3.Row
rows=db.execute("SELECT date,open,high,low,close,volume,amount FROM daily_kline WHERE stock_code='603683' AND date<='2026-06-01' AND date>=date('2026-06-01','-500 days') ORDER BY date").fetchall()
daily=[dict(r) for r in rows]
last=daily[-1]
print(f"K lines: {len(daily)}, last: {last['date']} C={last['close']}")

hlc=get_hlc_from_chanlun(daily,'603683')
print(f"HLC: {hlc['h_date'] if hlc else 'None'} {('L='+hlc['l_date']) if hlc else ''}")
if hlc:print(f"  decline={hlc['decline_pct']}% C={hlc['c_start_date']}~{hlc['c_end_date']}")

params=load_params()
params['drawdown_min']=0.02;params['drawdown_max']=0.99;params['rs_threshold']=0
sigs=detect(daily,params)
print(f"Sigs: {len(sigs)}")
for s in sigs:print(f"  {s['signal_date']} {s['breakout_gain_pct']}% vol={s['breakout_vol_ratio']}x")
db.close()
