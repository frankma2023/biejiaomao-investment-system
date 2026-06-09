import sys,os,json,sqlite3
os.chdir("D:/hanako/investment-system");sys.path[:0]=["D:/hanako/investment-system/src","D:/hanako/investment-system/src/scanners"]

db=sqlite3.connect("data/lixinger.db");db.row_factory=sqlite3.Row
rows=db.execute("SELECT date,open,high,low,close,volume FROM daily_kline WHERE stock_code='603683' AND date<='2026-06-01' AND date>=date('2026-06-01','-500 days') ORDER BY date").fetchall()
klines=[dict(r) for r in rows]
dates=[k['date'] for k in klines];n=len(klines)

r=db.execute("SELECT bi_json FROM chanlun_scan_daily WHERE stock_code='603683' ORDER BY scan_date DESC LIMIT 1").fetchone()
bi_list=json.loads(r[0]) if r and r[0] else []

# Find H (copy from engine)
tops=[(b['sdt'][:10],b['high']) for b in bi_list if b['direction']=='向下']
tops.sort(key=lambda x:x[0],reverse=True)
h_idx=None
for top_date,top_price in tops:
    if top_date>klines[-1]['date']:continue
    try:top_idx=dates.index(top_date)
    except:continue
    if top_idx+1>=n:continue
    future_low=min(klines[j]['close'] for j in range(top_idx+1,n))
    decline=(top_price-future_low)/top_price if top_price>0 else 0
    if decline<0.10:continue
    pre60_start=max(0,top_idx-60)
    pre60_low=min(klines[j]['close'] for j in range(pre60_start,top_idx)) if pre60_start<top_idx else top_price
    pre_rise=(top_price-pre60_low)/pre60_low if pre60_low>0 else 0
    if pre_rise>=0.20:h_idx=top_idx;h_price=top_price;h_date=top_date;break

print(f"H: idx={h_idx} date={h_date} price={h_price}")

# Find L
bots=[(b['sdt'][:10],b['low']) for b in bi_list if b['direction']=='向上']
l_idx=None
for bot_date,bot_price in bots:
    if bot_date>h_date:
        try:l_idx=dates.index(bot_date);l_price=bot_price
        except:pass
        break
print(f"L: idx={l_idx} date={dates[l_idx] if l_idx else 'None'} price={l_price if l_idx else 'None'}")

# Find C
if l_idx:
    c_start=l_idx;c_end=l_idx
    for i in range(l_idx,min(l_idx+30,n)):
        seg=[klines[j]['close'] for j in range(l_idx,i+1)]
        seg_min,seg_max=min(seg),max(seg)
        amp=(seg_max-seg_min)/seg_min if seg_min>0 else 999
        if amp<=0.10:c_end=i
        elif i-l_idx>=3:break
    print(f"C: {c_start}~{c_end} ({dates[c_start]}~{dates[c_end]}) amp check done")

db.close()
