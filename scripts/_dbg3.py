import sys,os;sys.path.insert(0,"D:/hanako/investment-system/src");sys.path.insert(0,"D:/hanako/investment-system/src/scanners")
import sqlite3,json,traceback
from scanners.chanlun import analyze

db=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
r=db.execute("SELECT COUNT(*) FROM chanlun_scan_daily WHERE stock_code='603683'").fetchone()
print(f"chanlun_scan_daily for 603683: {r[0]} rows")

r=db.execute("SELECT bi_json FROM chanlun_scan_daily WHERE stock_code='603683' ORDER BY scan_date DESC LIMIT 1").fetchone()
if r and r[0]:
    bi=json.loads(r[0])
    tops=[b for b in bi if b['direction']=='向下']
    bots=[b for b in bi if b['direction']=='向上']
    print(f"BI list: {len(bi)} total, {len(tops)} tops, {len(bots)} bots")
    if tops: print(f"  Last top: {tops[-1]['sdt'][:10]} ¥{tops[-1]['high']}")
    if bots: print(f"  Last bot: {bots[-1]['sdt'][:10]} ¥{bots[-1]['low']}")
else:
    print("No bi_json in cache, running analyze()...")
    try:
        r=analyze('603683','D',500,data_mode='stock')
        bi=r.get('bi_list',[])
        print(f"  Result: {len(bi)} bi items")
    except Exception as e:
        traceback.print_exc()
db.close()
