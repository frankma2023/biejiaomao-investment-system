import sqlite3,sys,os
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..','src'))
from analytics.mw_backtest import *
from collections import defaultdict

db=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db");db.row_factory=sqlite3.Row
sigs=[dict(r) for r in db.execute("SELECT * FROM mw_signal_daily WHERE b2_date>='2026-01-01' AND b2_date<='2026-06-05' AND score>=80 AND score_d=15 AND score_i1=15 AND score_i2=15").fetchall()]
print(f"New PLUS signals: {len(sigs)}")

codes=list(set(s['stock_code'] for s in sigs))
pc={}
for c in codes:
    rows=db.execute("SELECT date,open,close FROM daily_kline WHERE stock_code=? AND date>='2025-12-01' ORDER BY date",(c,)).fetchall()
    pc[c]={'dates':[r['date'] for r in rows],'prices':{r['date']:{'o':r['open'],'c':r['close']} for r in rows}}

pending=[]
for s in sigs:
    c=s['stock_code'];b2=s['b2_date'];p=pc.get(c)
    if not p: continue
    try: idx=p['dates'].index(b2)
    except: continue
    eidx=idx+2;xidx=eidx+10
    if xidx>=len(p['dates']): continue
    pending.append((p['dates'][eidx],c,p['dates'][xidx]))

buys=defaultdict(list)
for ed,cd,xd in pending: buys[ed].append((cd,xd))
all_dates=sorted(set(d for p in pc.values() for d in p['dates']))
all_dates=[d for d in all_dates if '2025-12-15'<=d<='2026-07-31']

cash=1000000;positions=[];peak=1000000;max_dd=0;trades=[]
for today in all_dates:
    for pos in positions[:]:
        if pos['xd']<=today:
            epx=pc[pos['c']]['prices'].get(today,{}).get('c',pos['ep'])
            cash+=pos['sh']*epx
            trades.append((epx-pos['ep'])/pos['ep']*100)
            positions.remove(pos)
    if today in buys:
        for cd,xd in buys[today]:
            ep=pc[cd]['prices'].get(today,{}).get('o',0)
            if not ep: continue
            inv=cash*0.05
            if inv<5000: continue
            cash-=inv;positions.append({'c':cd,'sh':inv/ep,'ep':ep,'xd':xd})
    pv=sum(pos['sh']*pc[pos['c']]['prices'].get(today,{}).get('c',pos['ep']) for pos in positions)
    tv=cash+pv;peak=max(peak,tv);max_dd=min(max_dd,(tv-peak)/peak*100)

wr=sum(1 for t in trades if t>0)/len(trades)*100
ret=(tv-1000000)/1000000*100
median=sorted(trades)[len(trades)//2]
avg=sum(trades)/len(trades)
print(f"模拟盘: {len(trades)}笔 胜率{wr:.1f}% 收益{ret:+.2f}% 中位{median:+.2f}% 平均{avg:+.2f}% 最大回撤{max_dd:.2f}%")

# 月度
monthly=defaultdict(lambda:{'trades':0,'wins':0,'ret':0})
for i,t in enumerate(trades):
    # find entry month from pending
    m='?'
    for s in sigs:
        c=s['stock_code'];p=pc.get(c)
        if not p: continue
        try: idx=p['dates'].index(s['b2_date'])
        except: continue
        eidx=idx+2
        if eidx<len(p['dates']):
            monthly[p['dates'][eidx][:7]]['trades']+=1
            if t>0: monthly[p['dates'][eidx][:7]]['wins']+=1
            monthly[p['dates'][eidx][:7]]['ret']+=t
        break
print("月度:")
for m in sorted(monthly):
    d=monthly[m]
    wr2=d['wins']/d['trades']*100 if d['trades'] else 0
    print(f"  {m}: {d['trades']}笔 胜率{wr2:.1f}% 累计{d['ret']:+.1f}%")

db.close()
