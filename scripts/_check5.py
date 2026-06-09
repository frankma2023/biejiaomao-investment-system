import sqlite3,sys,os
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..','src'))
db=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db");db.row_factory=sqlite3.Row

# 5 PLUS with PP in B1~B2
plus_pp=db.execute("""
    SELECT * FROM mw_signal_daily
    WHERE b2_date>='2023-06-01' AND b2_date<='2026-06-05'
    AND score>=80 AND score_d=15 AND score_i1=15 AND score_i2=15
    AND stock_code IN ('002428','300666','688295','688387','000657')
    ORDER BY b2_date
""").fetchall()

pc={}
for code in set(p['stock_code'] for p in plus_pp):
    rows=db.execute("SELECT date,open,close FROM daily_kline WHERE stock_code=? AND date>='2023-01-01' AND date<='2026-07-31' ORDER BY date",(code,)).fetchall()
    pc[code]={'dates':[r['date'] for r in rows],'prices':{r['date']:{'o':r['open'],'c':r['close']} for r in rows}}

def nth(dates,base,n):
    try:i=dates.index(base);t=i+n
    except:return None
    return dates[t] if t<len(dates) else None

ret={5:[],10:[],20:[],30:[],60:[]}
for p in plus_pp:
    code=p['stock_code'];b2=p['b2_date']
    d=pc[code]['dates'];pr=pc[code]['prices']
    ed=nth(d,b2,2)
    if not ed or ed not in pr:continue
    ep=pr[ed]['o']
    if ep<=0:continue
    try:i=d.index(ed)
    except:continue
    for h in [5,10,20,30,60]:
        f=i+h
        if f<len(d):
            ret[h].append((pr[d[f]]['c']-ep)/ep*100)
        else:
            ret[h].append(None)

from analytics.mw_backtest import calc_stats
for h in [5,10,20,30,60]:
    r=[v for v in ret[h] if v is not None]
    if r:
        s=calc_stats(r)
        print(f"{h}d: {len(r)}笔 胜率{s['win_rate']:.0f}% 中位{s['median_return']:+.1f}% 平均{s['avg_return']:+.1f}%")
db.close()
