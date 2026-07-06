"""
PP_V1 技术评分验证：<50分 vs ≥50分
"""
import sqlite3, numpy as np
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

# 加载 PP_V1 H20
rows = c.execute("""
    SELECT br.stock_code, br.signal_date, br.net_ret_pct, br.is_win
    FROM backtest_results br
    WHERE br.combo_label='PP_V1' AND br.hold_days=20 AND br.entry_method='T+1_O'
      AND br.pool_mode='full' AND br.signal_date >= '2024-01-01' AND br.signal_date <= '2026-06-22'
""").fetchall()

# K线和RS
c.execute("SELECT stock_code,date,adj_close,high,low,close,volume FROM daily_kline WHERE date>='2023-06-01' AND date<='2026-06-22' ORDER BY stock_code,date")
kline_by_code=defaultdict(list);kline_idx={}
for r in c.fetchall():
    kline_by_code[r['stock_code']].append({'date':r['date'],'adj_close':r['adj_close'],'high':r['high'],'low':r['low'],'close':r['close'],'volume':r['volume']})
for code,kls in kline_by_code.items():
    for i,kl in enumerate(kls):kline_idx[(code,kl['date'])]=(i,kl)

c.execute("SELECT stock_code,date,rps_20,rps_60,rps_250 FROM stock_rs_daily WHERE date>='2024-01-01' AND date<='2026-06-22'")
rs_dict={(r['stock_code'],r['date']):(r['rps_20']or 0,r['rps_60']or 0,r['rps_250']or 0) for r in c.fetchall()}

c.execute("SELECT stock_code, date, vol_ratio, gain_pct FROM pocket_pivot_daily WHERE engine_version='V1' AND date>='2024-01-01' AND date<='2026-06-22'")
pp_factors = {(r['stock_code'],r['date']):(r['vol_ratio']or 0,r['gain_pct']or 0) for r in c.fetchall()}

# MW B1 co-occurrence (±5天)
mw_b1_dates = defaultdict(set)
for r in c.execute("SELECT stock_code, b1_date FROM mw_signal_daily WHERE b1_date>='2024-01-01' AND b1_date<='2026-06-22' AND stock_code!='_sentinel_'"):
    mw_b1_dates[r['stock_code']].add(r['b1_date'])

db.close()

def ma(arr,p): return np.mean(arr[-p:]) if len(arr)>=p else None

def ppv1_tech_score(code, sig_date):
    ii = kline_idx.get((code, sig_date))
    if not ii: return None
    idx, kl = ii
    if idx < 250: return None
    kls = kline_by_code[code]
    cl = np.array([k['adj_close'] for k in kls[max(0,idx-260):idx+1]], dtype=np.float64)
    cn = cl[-1]; sc = 0; detail = {}
    
    # 1. 距MA20 (12分)
    m20 = ma(cl,20)
    if m20 and m20>0:
        p = (cn-m20)/m20*100
        if p<=5: s=12
        elif p<=10: s=9
        elif p<=15: s=6
        elif p<=25: s=3
        else: s=0
        sc+=s; detail['距MA20']=f'{p:.1f}%→{s}分'
    
    # 2. 距MA50 (12分)
    m50 = ma(cl,50)
    if m50 and m50>0:
        p = (cn-m50)/m50*100
        if p<=10: s=12
        elif p<=18: s=8
        elif p<=30: s=4
        else: s=0
        sc+=s; detail['距MA50']=f'{p:.1f}%→{s}分'
    
    # 3. 距MA250 (10分)
    m250 = ma(cl,250)
    if m250 and m250>0:
        p = (cn-m250)/m250*100
        if p<=20: s=10
        elif p<=30: s=7
        elif p<=45: s=3
        else: s=0
        sc+=s; detail['距MA250']=f'{p:.1f}%→{s}分'
    
    # 4. BIAS (8分)
    m60 = ma(cl,60)
    if m60 and m60>0:
        b = (cn-m60)/m60*100
        if b<=10: s=8
        elif b<=18: s=5
        elif b<=30: s=2
        else: s=0
        sc+=s; detail['BIAS']=f'{b:.1f}%→{s}分'
    
    # 5. PP量比 (15分)
    pf = pp_factors.get((code, sig_date))
    vr = pf[0] if pf else 0
    if vr>=1.5 and vr<=2.0: s=15
    elif vr>=1.3 and vr<1.5: s=10
    elif vr>2.0 and vr<=3.0: s=10
    elif vr>3.0: s=5
    else: s=3
    sc+=s; detail['PP量比']=f'{vr:.1f}→{s}分'
    
    # 6. PP涨幅 (8分)
    gp = pf[1] if pf else 0
    if 2<=gp<=4: s=8
    elif 4<gp<=6: s=6
    elif 6<gp<=8: s=4
    else: s=2
    sc+=s; detail['PP涨幅']=f'{gp:.1f}%→{s}分'
    
    # 7. RPS20 (8分)
    rs = rs_dict.get((code, sig_date))
    r20 = rs[0] if rs else 0
    if 40<=r20<=75: s=8
    elif 30<=r20<40 or 75<r20<=85: s=5
    elif r20>85: s=2
    else: s=3
    sc+=s; detail['RPS20']=f'{r20}→{s}分'
    
    # 8. RPS60 (8分)
    r60 = rs[1] if rs else 0
    if 40<=r60<=70: s=8
    elif 30<=r60<40 or 70<r60<=80: s=5
    elif r60>80: s=2
    else: s=3
    sc+=s; detail['RPS60']=f'{r60}→{s}分'
    
    # 9. MACD (12分)
    if len(cl)>=26:
        e12=cn;e26=cn;k12=2/13;k26=2/27
        for j in range(len(cl)-2,max(0,len(cl)-27),-1):e12=cl[j]*k12+e12*(1-k12);e26=cl[j]*k26+e26*(1-k26)
        dif=e12-e26
        if dif>0 and dif<cn*.02: s=12
        elif dif>0: s=9
        elif dif>cn*-.01: s=6
        else: s=3
        sc+=s; detail['MACD']=f'DIF={dif:.3f}→{s}分'
    
    # 10. KDJ (7分)
    if len(cl)>=9:
        hi=np.array([k['high'] for k in kls[max(0,idx-8):idx+1]],dtype=np.float64)
        lo=np.array([k['low'] for k in kls[max(0,idx-8):idx+1]],dtype=np.float64)
        if hi.max()>lo.min():
            kv=(cn-lo.min())/(hi.max()-lo.min())*100*2/3+50/3
            if kv<=60: s=7
            elif kv<=80: s=5
            elif kv<=90: s=2
            else: s=0
            sc+=s; detail['KDJ']=f'K={kv:.0f}→{s}分'
    
    return sc, detail

print('计算PP_V1技术评分...')
scored = []
for i, r in enumerate(rows):
    if i % 15000 == 0: print(f'  {i}/{len(rows)}')
    result = ppv1_tech_score(r['stock_code'], r['signal_date'])
    if result:
        sc, detail = result
        scored.append({'net_ret': r['net_ret_pct'], 'is_win': r['is_win'],
                       'code': r['stock_code'], 'date': r['signal_date'],
                       'score': sc, 'detail': detail})

print(f'有效: {len(scored)}')

# ═══ 验证 ═══
def stats(items):
    if not items: return None
    n=len(items);rets=[i['net_ret'] for i in items];wins=[i['is_win'] for i in items]
    return {'n':n,'wr':np.mean(wins)*100,'avg':np.mean(rets),'med':np.median(rets)}

print(f'\n{"=" * 80}')
print('评分表验证（5分步长）')
print(f'{"=" * 80}')
print(f'  {"分数":<10s} {"数量":>7s} {"胜率":>8s} {"收益":>8s}')
print(f'  {"─" * 38}')

for lo in range(25, 95, 5):
    hi = lo+5
    bucket = [s for s in scored if lo <= s['score'] < hi]
    if len(bucket)<10: continue
    s = stats(bucket)
    bar = '█' * int(s['wr']/3) if s['wr']>45 else '░' * int(s['wr']/3)
    print(f'  {lo}-{hi-1:<5d} {s["n"]:>7,d} {s["wr"]:>7.1f}% {s["avg"]:>7.2f}%  {bar}')

# ═══ <50 vs ≥50 ═══
low = [s for s in scored if s['score'] < 50]
high = [s for s in scored if s['score'] >= 50]
sl = stats(low); sh = stats(high)

print(f'\n{"=" * 80}')
print(f'<50 分 vs ≥50 分')
print(f'{"=" * 80}')
print(f'  <50分: {sl["n"]:>7,d}条 ({sl["n"]/len(scored)*100:.0f}%)  胜率={sl["wr"]:.1f}%  收益={sl["avg"]:.2f}%')
print(f'  ≥50分: {sh["n"]:>7,d}条 ({sh["n"]/len(scored)*100:.0f}%)  胜率={sh["wr"]:.1f}%  收益={sh["avg"]:.2f}%')

# ═══ 实例 ═══
print(f'\n{"=" * 80}')
print(f'评分示例（高低各1条）')
print(f'{"=" * 80}')

scored.sort(key=lambda x: x['score'])
lo_ex = scored[0]
hi_ex = scored[-1]
for label, ex in [('最低分', lo_ex), ('最高分', hi_ex)]:
    print(f'\n  {label}: {ex["code"]} {ex["date"]}  总分={ex["score"]}/100  实际收益={ex["net_ret"]:+.1f}%')
    for k, v in ex['detail'].items():
        print(f'    {k}: {v}')
