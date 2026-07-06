"""
精查 60-100 分区间，找自然断裂点
"""
import sqlite3, numpy as np
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

# 复用之前的计分逻辑（简化版，只查库）
rows = c.execute("""
    SELECT br.stock_code, br.signal_date as b1_date, br.net_ret_pct, br.is_win,
           m.score, m.confidence, m.b2_date
    FROM backtest_results br
    JOIN mw_signal_daily m ON br.stock_code=m.stock_code AND br.signal_date=m.b1_date
    WHERE br.combo_label='MW_B1' AND br.hold_days=20 AND br.entry_method='T+1_O'
      AND br.pool_mode='full' AND br.signal_date >= '2024-01-01' AND br.signal_date <= '2026-06-22'
""").fetchall()

c.execute("SELECT stock_code, date, adj_close, high, low, close, volume FROM daily_kline WHERE date >= '2023-06-01' AND date <= '2026-06-22' ORDER BY stock_code, date")
kline_by_code = defaultdict(list); kline_idx = {}
for r in c.fetchall():
    kline_by_code[r['stock_code']].append({'date':r['date'],'adj_close':r['adj_close'],'high':r['high'],'low':r['low'],'close':r['close'],'volume':r['volume']})
for code,kls in kline_by_code.items():
    for i,kl in enumerate(kls): kline_idx[(code,kl['date'])]=(i,kl)

c.execute("SELECT stock_code,date,rps_20,rps_60,rps_250 FROM stock_rs_daily WHERE date>='2024-01-01' AND date<='2026-06-22'")
rs_dict = {(r['stock_code'],r['date']):(r['rps_20']or 0,r['rps_60']or 0,r['rps_250']or 0) for r in c.fetchall()}
db.close()

def ma(arr,p): return np.mean(arr[-p:]) if len(arr)>=p else None

def quick_score(code, b1_date):
    ii = kline_idx.get((code,b1_date))
    if not ii: return None
    idx,kl = ii
    if idx<250: return None
    kls = kline_by_code[code]
    cl = np.array([k['adj_close'] for k in kls[max(0,idx-260):idx+1]], dtype=np.float64)
    cn = cl[-1]
    sc = 0
    m20=ma(cl,20); m50=ma(cl,50); m250=ma(cl,250); m60=ma(cl,60)
    if m20 and m20>0:
        p=(cn-m20)/m20*100; sc+=15 if p<=5 else (12 if p<=10 else (8 if p<=15 else (4 if p<=25 else 0)))
    if m50 and m50>0:
        p=(cn-m50)/m50*100; sc+=15 if p<=8 else (10 if p<=15 else (5 if p<=25 else 0))
    if m250 and m250>0:
        p=(cn-m250)/m250*100; sc+=15 if p<=15 else (10 if p<=25 else (5 if p<=35 else 0))
    if m60 and m60>0:
        b=(cn-m60)/m60*100; sc+=10 if b<=8 else (7 if b<=15 else (3 if b<=25 else 0))
    rs=rs_dict.get((code,b1_date))
    r20=rs[0] if rs else 0; r60=rs[1] if rs else 0; r250=rs[2] if rs else 0
    sc+=10 if 40<=r20<=75 else (6 if 30<=r20<40 or 75<r20<=85 else (2 if r20>85 else 4))
    sc+=10 if 40<=r60<=70 else (6 if 30<=r60<40 or 70<r60<=80 else (2 if r60>80 else 4))
    sc+=5 if 50<=r250<=70 else (3 if r250>70 else 2)
    if len(cl)>=26:
        e12=cn;e26=cn;k12=2/13;k26=2/27
        for i in range(len(cl)-2,max(0,len(cl)-27),-1): e12=cl[i]*k12+e12*(1-k12); e26=cl[i]*k26+e26*(1-k26)
        dif=e12-e26
        sc+=15 if dif>0 and dif<cn*0.02 else (12 if dif>0 else (8 if dif>cn*-0.01 else 3))
    if len(cl)>=9:
        hi=np.array([k['high'] for k in kls[max(0,idx-8):idx+1]],dtype=np.float64)
        lo=np.array([k['low'] for k in kls[max(0,idx-8):idx+1]],dtype=np.float64)
        if hi.max()>lo.min():
            kv=(cn-lo.min())/(hi.max()-lo.min())*100*2/3+50/3
            sc+=5 if kv<=75 else (3 if kv<=85 else 0)
    return sc

print('计算中...')
scored = []
for i,r in enumerate(rows):
    if i%4000==0: print(f'  {i}/{len(rows)}')
    s = quick_score(r['stock_code'], r['b1_date'])
    if s is not None:
        scored.append({'score':s, 'net_ret':r['net_ret_pct'], 'is_win':r['is_win'], 'has_b2':r['b2_date'] is not None, 'orig_conf':r['confidence']})

print(f'\n有效: {len(scored)}')
print(f'\n{"=" * 70}')
print(f'精查 50-100 分区间（5分为步长）')
print(f'{"=" * 70}')
print(f'{"区间":<10s} {"数量":>7s} {"胜率":>8s} {"收益":>8s} {"B2率":>7s}')
print('-' * 45)

for lo in range(50, 100, 5):
    bucket = [s for s in scored if lo <= s['score'] < lo+5]
    if len(bucket) < 10: continue
    n = len(bucket)
    wr = np.mean([s['is_win'] for s in bucket]) * 100
    avg = np.mean([s['net_ret'] for s in bucket])
    b2 = np.mean([s['has_b2'] for s in bucket]) * 100
    bar = '█' * int(wr/2) if wr > 40 else '░' * int(wr/2)
    print(f'{lo}-{lo+4:<5d} {n:>7d} {wr:>7.1f}% {avg:>7.2f}% {b2:>6.1f}%  {bar}')

# 建议断裂点
print(f'\n{"=" * 70}')
print('建议分层')
print(f'{"=" * 70}')

# 方案: 根据自然断裂点
for label, lo, hi in [('⭐⭐⭐ 极高', 85, 100), ('⭐⭐ 很高', 75, 84), ('⭐ 高', 65, 74), ('中', 50, 64), ('低', 0, 49)]:
    bucket = [s for s in scored if lo <= s['score'] < hi]
    if not bucket: continue
    n = len(bucket); wr = np.mean([s['is_win'] for s in bucket])*100
    avg = np.mean([s['net_ret'] for s in bucket])
    pct = n/len(scored)*100
    print(f'  {label}: {lo}-{hi-1}分  {n:>5d}条({pct:.1f}%)  胜率={wr:.1f}%  收益={avg:+.2f}%')
