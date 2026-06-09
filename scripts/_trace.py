import sys,os,json,sqlite3
os.chdir("D:/hanako/investment-system");sys.path[:0]=["D:/hanako/investment-system/src","D:/hanako/investment-system/src/scanners"]

db=sqlite3.connect("data/lixinger.db");db.row_factory=sqlite3.Row
rows=db.execute("SELECT date,open,high,low,close,volume FROM daily_kline WHERE stock_code='603683' AND date<='2026-06-01' AND date>=date('2026-06-01','-500 days') ORDER BY date").fetchall()
klines=[dict(r) for r in rows]
dates=[k['date'] for k in klines];n=len(klines)
print(f"K lines: {n}, {dates[0]} ~ {dates[-1]}")

# Load bi_list
r=db.execute("SELECT bi_json FROM chanlun_scan_daily WHERE stock_code='603683' ORDER BY scan_date DESC LIMIT 1").fetchone()
bi_list=json.loads(r[0]) if r and r[0] else []
tops=[(b['sdt'][:10],b['high']) for b in bi_list if b['direction']=='向下']
tops.sort(key=lambda x:x[0],reverse=True)
print(f"Tops: {len(tops)}")
for t in tops[-3:]:print(f"  {t[0]} ¥{t[1]}")

# Try to find H
for top_date,top_price in tops:
    if top_date>klines[-1]['date']:continue
    try:top_idx=dates.index(top_date)
    except:continue
    if top_idx+1>=n:continue
    future_low=min(klines[j]['close'] for j in range(top_idx+1,n))
    decline=(top_price-future_low)/top_price if top_price>0 else 0
    pre60_start=max(0,top_idx-60)
    pre60_low=min(klines[j]['close'] for j in range(pre60_start,top_idx)) if pre60_start<top_idx else top_price
    pre_rise=(top_price-pre60_low)/pre60_low if pre60_low>0 else 0
    print(f"Top {top_date}: future_low={future_low:.2f} decline={decline:.1%} pre_low={pre60_low:.2f} pre_rise={pre_rise:.1%}")
    if decline>=0.10 and pre_rise>=0.20:
        print(f"  → H FOUND!")
        break
    else:
        print(f"  → SKIP (decline≥10%={decline>=0.10}, pre_rise≥20%={pre_rise>=0.20})")
db.close()
