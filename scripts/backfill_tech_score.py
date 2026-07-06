"""
为 mw_signal_daily 添加 tech_score 字段并回填
"""
import sqlite3, numpy as np
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
c = db.cursor()

# 1. 添加字段
print('[1] 添加 tech_score 字段...')
try:
    c.execute("ALTER TABLE mw_signal_daily ADD COLUMN tech_score INTEGER DEFAULT 0")
    print('  字段已添加')
except sqlite3.OperationalError as e:
    if 'duplicate' in str(e).lower():
        print('  字段已存在，跳过')
    else:
        raise

db.commit()

# 2. 加载K线
print('[2] 加载K线...')
db.row_factory = sqlite3.Row
c = db.cursor()
c.execute("SELECT stock_code,date,adj_close,high,low,close,volume FROM daily_kline WHERE date>='2023-06-01' AND date<='2026-06-22' ORDER BY stock_code,date")
kline_by_code=defaultdict(list);kline_idx={}
for r in c.fetchall():
    kline_by_code[r['stock_code']].append({'date':r['date'],'adj_close':r['adj_close'],'high':r['high'],'low':r['low'],'close':r['close'],'volume':r['volume']})
for code,kls in kline_by_code.items():
    for i,kl in enumerate(kls):kline_idx[(code,kl['date'])]=(i,kl)

print('[3] 加载RS...')
c.execute("SELECT stock_code,date,rps_20,rps_60,rps_250 FROM stock_rs_daily WHERE date>='2024-01-01' AND date<='2026-06-22'")
rs_dict={(r['stock_code'],r['date']):(r['rps_20']or 0,r['rps_60']or 0,r['rps_250']or 0) for r in c.fetchall()}

def ma(arr,p): return np.mean(arr[-p:]) if len(arr)>=p else None

def tech_score(code, sig_date):
    ii=kline_idx.get((code,sig_date))
    if not ii: return 0
    idx,kl=ii
    if idx<250: return 0
    kls=kline_by_code[code]
    cl=np.array([k['adj_close'] for k in kls[max(0,idx-260):idx+1]],dtype=np.float64)
    cn=cl[-1];sc=0
    m20=ma(cl,20);m50=ma(cl,50);m250=ma(cl,250);m60=ma(cl,60)
    if m20 and m20>0: p=(cn-m20)/m20*100;sc+=15 if p<=5 else(12 if p<=10 else(8 if p<=15 else(4 if p<=25 else 0)))
    if m50 and m50>0: p=(cn-m50)/m50*100;sc+=15 if p<=8 else(10 if p<=15 else(5 if p<=25 else 0))
    if m250 and m250>0: p=(cn-m250)/m250*100;sc+=15 if p<=15 else(10 if p<=25 else(5 if p<=35 else 0))
    if m60 and m60>0: b=(cn-m60)/m60*100;sc+=10 if b<=8 else(7 if b<=15 else(3 if b<=25 else 0))
    rs=rs_dict.get((code,sig_date));r20=rs[0]if rs else 0;r60=rs[1]if rs else 0;r250=rs[2]if rs else 0
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

def tech_tier(score):
    if score>=85: return '极高'
    if score>=75: return '很高'
    if score>=65: return '高'
    if score>=50: return '中'
    return '低'

# 3. 回填所有B1信号
print('[4] 回填 tech_score...')
c.execute("SELECT stock_code, b1_date FROM mw_signal_daily WHERE stock_code!='_sentinel_' AND b1_date>='2024-01-01' AND b1_date<='2026-06-22'")
signals = c.fetchall()
total = len(signals)
updated = 0
for i, (code, b1d) in enumerate(signals):
    if i % 5000 == 0: print(f'  {i}/{total}...')
    ts = tech_score(code, b1d)
    if ts > 0:
        c.execute("UPDATE mw_signal_daily SET tech_score=? WHERE stock_code=? AND b1_date=?", (ts, code, b1d))
        updated += 1

db.commit()
print(f'  已更新: {updated}/{total}')

# 4. 验证
c.execute("SELECT tech_score, COUNT(*) FROM mw_signal_daily WHERE tech_score>0 AND b1_date>='2026-06-30' AND stock_code!='_sentinel_' GROUP BY 1 ORDER BY 1 DESC")
print(f'\n今日B1技术分分布:')
for r in c.fetchall():
    tier = tech_tier(r['tech_score'])
    print(f'  {r["tech_score"]:>3d}分 ({tier}): {r[1]}个')

db.close()
print('\n完成。')
