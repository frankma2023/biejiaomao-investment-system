import sys,os;os.chdir("D:/hanako/investment-system");sys.path.insert(0,"D:/hanako/investment-system/src");sys.path.insert(0,"D:/hanako/investment-system/src/scanners")
import sqlite3,json
from base_breakout_v2 import get_hlc_from_chanlun,load_params,detect,_chanlun_cache

db=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db");db.row_factory=sqlite3.Row
rows=db.execute("SELECT date,open,high,low,close,volume,amount FROM daily_kline WHERE stock_code='603683' AND date<='2026-06-01' AND date>=date('2026-06-01','-500 days') ORDER BY date").fetchall()
daily=[dict(r) for r in rows]
print(f"K lines: {len(daily)}, last date: {daily[-1]['date']}")

params=load_params();params['stock_code']='603683'
hlc=get_hlc_from_chanlun(daily,'603683')
print(f"HLC: {hlc is not None}")
if hlc:print(f"  H={hlc['h_date']} ¥{hlc['h_price']} L={hlc['l_date']} decline={hlc['decline_pct']}% C={hlc['c_start_date']}~{hlc['c_end_date']}")

# Check cache
print(f"Cache has 603683: {'603683' in _chanlun_cache}")
if '603683' in _chanlun_cache:print(f"  bi_list len: {len(_chanlun_cache['603683'])}")

params['drawdown_min']=0.02;params['drawdown_max']=0.99;params['rs_threshold']=0
sigs=detect(daily,params)
print(f"Sigs: {len(sigs)}")
db.close()
