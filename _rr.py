import sqlite3,sys,traceback
sys.path.insert(0,'src')
from scanners.mw_signal import scan_stock, get_klines
c=sqlite3.connect('data/lixinger.db')
c.row_factory=sqlite3.Row
k=get_klines(c,'600584','2018-01-01','2026-05-08')
try:
    p,r=scan_stock(k,'2026-05-08','600584',c)
    print(f'OK: passed={p}')
    if p: print(f'H={r["h_date"]} B1={r["b1_date"]} B2={r["b2_date"]}')
except Exception as e:
    print(f'CRASH: {e}')
    traceback.print_exc()
c.close()
