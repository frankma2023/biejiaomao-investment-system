"""
B1(tech≥75) + PP_V1(tech≥70) in 5 days → 精确胜率
"""
import sqlite3, numpy as np
from collections import defaultdict
from datetime import datetime, timedelta

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB); db.row_factory = sqlite3.Row; c = db.cursor()

b1_rows = c.execute("SELECT stock_code,signal_date as b1_date,net_ret_pct,is_win FROM backtest_results WHERE combo_label='MW_B1' AND hold_days=20 AND entry_method='T+1_O' AND pool_mode='full' AND signal_date>='2024-01-01' AND signal_date<='2026-06-22'").fetchall()

ppv1_dates = defaultdict(set)
for r in c.execute("SELECT stock_code,date FROM pocket_pivot_daily WHERE engine_version='V1' AND date>='2024-01-01' AND date<='2026-07-05'"):
    ppv1_dates[r['stock_code']].add(r['date'])

c.execute("SELECT stock_code,date,adj_close,high,low,close,volume FROM daily_kline WHERE date>='2023-06-01' AND date<='2026-06-22' ORDER BY stock_code,date")
kline_by_code=defaultdict(list);kline_idx={}
for r in c.fetchall(): kline_by_code[r['stock_code']].append({'date':r['date'],'adj_close':r['adj_close'],'high':r['high'],'low':r['low'],'close':r['close'],'volume':r['volume']})
for code,kls in kline_by_code.items():
    for i,kl in enumerate(kls):kline_idx[(code,kl['date'])]=(i,kl)

c.execute("SELECT stock_code,date,rps_20,rps_60,rps_250 FROM stock_rs_daily WHERE date>='2024-01-01' AND date<='2026-06-22'")
rs_dict={(r['stock_code'],r['date']):(r['rps_20']or 0,r['rps_60']or 0,r['rps_250']or 0) for r in c.fetchall()}

c.execute("SELECT stock_code,date,vol_ratio,gain_pct FROM pocket_pivot_daily WHERE engine_version='V1' AND date>='2024-01-01' AND date<='2026-06-22'")
pp_factors={(r['stock_code'],r['date']):(r['vol_ratio']or 0,r['gain_pct']or 0) for r in c.fetchall()}
db.close()

def ma(arr,p): return np.mean(arr[-p:]) if len(arr)>=p else None

def tech_b1(code,date):
    ii=kline_idx.get((code,date)); 
    if not ii: return None
    idx,kl=ii
    if idx<250: return None
    kls=kline_by_code[code];cl=np.array([k['adj_close'] for k in kls[max(0,idx-260):idx+1]],dtype=np.float64);cn=cl[-1];sc=0
    m20=ma(cl,20);m50=ma(cl,50);m250=ma(cl,250);m60=ma(cl,60)
    if m20 and m20>0: p=(cn-m20)/m20*100;sc+=15 if p<=5 else(12 if p<=10 else(8 if p<=15 else(4 if p<=25 else 0)))
    if m50 and m50>0: p=(cn-m50)/m50*100;sc+=15 if p<=8 else(10 if p<=15 else(5 if p<=25 else 0))
    if m250 and m250>0: p=(cn-m250)/m250*100;sc+=15 if p<=15 else(10 if p<=25 else(5 if p<=35 else 0))
    if m60 and m60>0: b=(cn-m60)/m60*100;sc+=10 if b<=8 else(7 if b<=15 else(3 if b<=25 else 0))
    rs=rs_dict.get((code,date));r20=rs[0]if rs else 0;r60=rs[1]if rs else 0;r250=rs[2]if rs else 0
    sc+=10 if 40<=r20<=75 else(6 if 30<=r20<40 or 75<r20<=85 else(2 if r20>85 else 4))
    sc+=10 if 40<=r60<=70 else(6 if 30<=r60<40 or 70<r60<=80 else(2 if r60>80 else 4))
    sc+=5 if 50<=r250<=70 else(3 if r250>70 else 2)
    if len(cl)>=26:
        e12=cn;e26=cn;k12=2/13;k26=2/27
        for i in range(len(cl)-2,max(0,len(cl)-27),-1):e12=cl[i]*k12+e12*(1-k12);e26=cl[i]*k26+e26*(1-k26)
        dif=e12-e26;sc+=15 if dif>0 and dif<cn*.02 else(12 if dif>0 else(8 if dif>cn*-.01 else 3))
    if len(cl)>=9:
        hi=np.array([k['high'] for k in kls[max(0,idx-8):idx+1]],dtype=np.float64)
        lo=np.array([k['low'] for k in kls[max(0,idx-8):idx+1]],dtype=np.float64)
        if hi.max()>lo.min():kv=(cn-lo.min())/(hi.max()-lo.min())*100*2/3+50/3;sc+=5 if kv<=75 else(3 if kv<=85 else 0)
    return sc

def tech_ppv1(code,date):
    ii=kline_idx.get((code,date));
    if not ii: return None
    idx,kl=ii
    if idx<250: return None
    kls=kline_by_code[code];cl=np.array([k['adj_close'] for k in kls[max(0,idx-260):idx+1]],dtype=np.float64);cn=cl[-1];sc=0
    m20=ma(cl,20);m50=ma(cl,50);m250=ma(cl,250);m60=ma(cl,60)
    if m20 and m20>0: p=(cn-m20)/m20*100;sc+=12 if p<=5 else(9 if p<=10 else(6 if p<=15 else(3 if p<=25 else 0)))
    if m50 and m50>0: p=(cn-m50)/m50*100;sc+=12 if p<=10 else(8 if p<=18 else(4 if p<=30 else 0))
    if m250 and m250>0: p=(cn-m250)/m250*100;sc+=10 if p<=20 else(7 if p<=30 else(3 if p<=45 else 0))
    if m60 and m60>0: b=(cn-m60)/m60*100;sc+=8 if b<=10 else(5 if b<=18 else(2 if b<=30 else 0))
    pf=pp_factors.get((code,date));vr=pf[0]if pf else 0;gp=pf[1]if pf else 0
    sc+=15 if 1.5<=vr<=2.0 else(10 if 1.3<=vr<1.5 or 2.0<vr<=3.0 else(5 if vr>3.0 else 3))
    sc+=8 if 2<=gp<=4 else(6 if 4<gp<=6 else(4 if 6<gp<=8 else 2))
    rs=rs_dict.get((code,date));r20=rs[0]if rs else 0;r60=rs[1]if rs else 0
    sc+=8 if 40<=r20<=75 else(5 if 30<=r20<40 or 75<r20<=85 else(2 if r20>85 else 3))
    sc+=8 if 40<=r60<=70 else(5 if 30<=r60<40 or 70<r60<=80 else(2 if r60>80 else 3))
    if len(cl)>=26:
        e12=cn;e26=cn;k12=2/13;k26=2/27
        for i in range(len(cl)-2,max(0,len(cl)-27),-1):e12=cl[i]*k12+e12*(1-k12);e26=cl[i]*k26+e26*(1-k26)
        dif=e12-e26;sc+=12 if dif>0 and dif<cn*.02 else(9 if dif>0 else(6 if dif>cn*-.01 else 3))
    if len(cl)>=9:
        hi=np.array([k['high'] for k in kls[max(0,idx-8):idx+1]],dtype=np.float64)
        lo=np.array([k['low'] for k in kls[max(0,idx-8):idx+1]],dtype=np.float64)
        if hi.max()>lo.min():kv=(cn-lo.min())/(hi.max()-lo.min())*100*2/3+50/3;sc+=7 if kv<=60 else(5 if kv<=80 else(2 if kv<=90 else 0))
    return sc

def stats(items):
    if not items: return None
    n=len(items);rets=np.array(items);wr=np.mean(rets>0)*100;avg=np.mean(rets);med=np.median(rets)
    return {'n':n,'wr':wr,'avg':avg,'med':med}

print('计算中...')
g_none = []   # B1≥75, 无PP_V1后出
g_any = []    # B1≥75, PP_V1后出(任意技术分)
g_both = []   # B1≥75, PP_V1后出且≥70

for i,r in enumerate(b1_rows):
    if i%4000==0: print(f'  {i}/{len(b1_rows)}')
    ts = tech_b1(r['stock_code'], r['b1_date'])
    if ts is None or ts < 75: continue
    
    b1dt = datetime.strptime(r['b1_date'], '%Y-%m-%d')
    ppv1_set = ppv1_dates.get(r['stock_code'], set())
    found = False; found_tech = 0
    for offset in range(1,6):
        wd = (b1dt + timedelta(days=offset)).strftime('%Y-%m-%d')
        if wd in ppv1_set:
            pts = tech_ppv1(r['stock_code'], wd)
            if pts is not None:
                found = True; found_tech = max(found_tech, pts)
    
    if found:
        g_any.append(r['net_ret_pct'])
        if found_tech >= 70:
            g_both.append(r['net_ret_pct'])
    else:
        g_none.append(r['net_ret_pct'])

print(f'\n{"=" * 70}')
print(f'B1(tech≥75) + PP_V1后5天 分层结果')
print(f'{"=" * 70}')

for label, group in [('无PP_V1', g_none), ('PP_V1任意分', g_any), ('PP_V1且tech≥70', g_both)]:
    st = stats(group)
    if st: print(f'  {label:<20s} {st["n"]:>6d}条  胜率={st["wr"]:.1f}%  收益={st["avg"]:+.2f}%')

# 全量B1基准
all_rets = [r['net_ret_pct'] for r in b1_rows]
ab = stats(all_rets)
print(f'\n  全量B1基准:  {ab["n"]:,d}条  胜率={ab["wr"]:.1f}%  收益={ab["avg"]:+.2f}%')
print(f'  B1(tech≥75)基准: {len(g_none)+len(g_any)}条  其中{len(g_any)}({len(g_any)/(len(g_none)+len(g_any))*100:.0f}%)后出PP_V1')
