"""
技术置信度 × PP_V1 精确窗口分析
"""
import sqlite3, numpy as np
from collections import defaultdict
from datetime import datetime, timedelta

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 85)
print('技术置信度 × PP_V1 精确窗口分析')
print('=' * 85)

# ═══ 1. 加载数据 ═══
rows = c.execute("""
    SELECT br.stock_code, br.signal_date as b1_date, br.net_ret_pct, br.is_win
    FROM backtest_results br
    WHERE br.combo_label='MW_B1' AND br.hold_days=20 AND br.entry_method='T+1_O'
      AND br.pool_mode='full' AND br.signal_date >= '2024-01-01' AND br.signal_date <= '2026-06-22'
""").fetchall()

c.execute("SELECT stock_code,date,adj_close,high,low,close,volume FROM daily_kline WHERE date>='2023-06-01' AND date<='2026-06-22' ORDER BY stock_code,date")
kline_by_code=defaultdict(list);kline_idx={}
for r in c.fetchall():
    kline_by_code[r['stock_code']].append({'date':r['date'],'adj_close':r['adj_close'],'high':r['high'],'low':r['low'],'close':r['close'],'volume':r['volume']})
for code,kls in kline_by_code.items():
    for i,kl in enumerate(kls):kline_idx[(code,kl['date'])]=(i,kl)

c.execute("SELECT stock_code,date,rps_20,rps_60,rps_250 FROM stock_rs_daily WHERE date>='2024-01-01' AND date<='2026-06-22'")
rs_dict={(r['stock_code'],r['date']):(r['rps_20']or 0,r['rps_60']or 0,r['rps_250']or 0) for r in c.fetchall()}

# PP_V1 精确到日
c.execute("SELECT stock_code, date FROM pocket_pivot_daily WHERE engine_version='V1' AND date>='2023-12-20' AND date<='2026-06-25'")
ppv1_dates = defaultdict(set)
for r in c.fetchall():
    ppv1_dates[r['stock_code']].add(r['date'])

db.close()

def ma(arr,p): return np.mean(arr[-p:]) if len(arr)>=p else None
def tech_score(code,b1_date):
    ii=kline_idx.get((code,b1_date))
    if not ii: return None
    idx,kl=ii
    if idx<250: return None
    kls=kline_by_code[code]
    cl=np.array([k['adj_close'] for k in kls[max(0,idx-260):idx+1]],dtype=np.float64)
    cn=cl[-1];sc=0
    m20=ma(cl,20);m50=ma(cl,50);m250=ma(cl,250);m60=ma(cl,60)
    if m20 and m20>0: p=(cn-m20)/m20*100;sc+=15 if p<=5 else(12 if p<=10 else(8 if p<=15 else(4 if p<=25 else 0)))
    if m50 and m50>0: p=(cn-m50)/m50*100;sc+=15 if p<=8 else(10 if p<=15 else(5 if p<=25 else 0))
    if m250 and m250>0: p=(cn-m250)/m250*100;sc+=15 if p<=15 else(10 if p<=25 else(5 if p<=35 else 0))
    if m60 and m60>0: b=(cn-m60)/m60*100;sc+=10 if b<=8 else(7 if b<=15 else(3 if b<=25 else 0))
    rs=rs_dict.get((code,b1_date))
    r20=rs[0]if rs else 0;r60=rs[1]if rs else 0;r250=rs[2]if rs else 0
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

# ═══ 2. 计算 ═══
print('[2] 计算中...')
merged = []
for i, r in enumerate(rows):
    if i % 4000 == 0: print(f'  {i}/{len(rows)}')
    code, b1d = r['stock_code'], r['b1_date']
    ts = tech_score(code, b1d)
    if ts is None: continue
    
    b1dt = datetime.strptime(b1d, '%Y-%m-%d')
    ppv1_set = ppv1_dates.get(code, set())
    
    # 精确窗口检测
    ppv1 = {
        'same': False,          # 同日
        'before_1_3': False,    # 前1-3天
        'before_4_5': False,    # 前4-5天
        'after_1_3': False,     # 后1-3天
        'after_4_5': False,     # 后4-5天
        'before_1_5': False,    # 前1-5天
        'after_1_5': False,     # 后1-5天
        'any_5': False,         # ±5天内任意
        'any_10': False,        # ±10天内任意
    }
    
    for offset in range(-10, 11):
        wd = (b1dt + timedelta(days=offset)).strftime('%Y-%m-%d')
        if wd in ppv1_set:
            if offset == 0: ppv1['same'] = True
            if -3 <= offset <= -1: ppv1['before_1_3'] = True
            if -5 <= offset <= -4: ppv1['before_4_5'] = True
            if 1 <= offset <= 3: ppv1['after_1_3'] = True
            if 4 <= offset <= 5: ppv1['after_4_5'] = True
            if -5 <= offset <= -1: ppv1['before_1_5'] = True
            if 1 <= offset <= 5: ppv1['after_1_5'] = True
            if -5 <= offset <= 5: ppv1['any_5'] = True
            if -10 <= offset <= 10: ppv1['any_10'] = True
    
    merged.append({
        'net_ret': r['net_ret_pct'], 'is_win': r['is_win'],
        'tech_score': ts,
        'tech_tier': '极高' if ts>=85 else ('很高' if ts>=75 else ('高' if ts>=65 else ('中' if ts>=50 else '低'))),
        'ppv1': ppv1,
    })

print(f'  有效: {len(merged)}')

# ═══ 3. 输出 ═══
def stats(items):
    if not items: return None
    n=len(items);rets=[i['net_ret'] for i in items];wins=[i['is_win'] for i in items]
    return {'n':n,'wr':np.mean(wins)*100,'avg':np.mean(rets),'med':np.median(rets)}

def bucket(items, tier, ppv1_key):
    subset = [m for m in items if m['tech_tier']==tier]
    total = len(subset)
    with_sig = [m for m in subset if m['ppv1'][ppv1_key]]
    base = [m for m in subset if not m['ppv1']['any_5']]  # 基准 = 完全无PP_V1
    return stats(with_sig), stats(base), total

tiers = ['极高','很高','高']
windows = [
    ('same','同日'),
    ('before_1_3','B1前1-3天'),
    ('before_4_5','B1前4-5天'),
    ('after_1_3','B1后1-3天'),
    ('after_4_5','B1后4-5天'),
    ('before_1_5','B1前1-5天'),
    ('after_1_5','B1后1-5天'),
    ('any_5','±5天任意'),
    ('any_10','±10天任意'),
]

print(f'\n{"=" * 85}')
print(f'PP_V1 精确窗口 × 技术层 胜率矩阵')
print(f'{"=" * 85}')

for win_key, win_label in windows:
    print(f'\n── PP_V1 {win_label} ──')
    print(f'  {"技术层":<8s} {"基准(无PP_V1)":>24s} {"有PP_V1":>24s} {"差异":>10s}')
    print(f'  {"─" * 70}')
    for tier in tiers:
        ws, bs, total = bucket(merged, tier, win_key)
        if not ws or not bs or ws['n'] < 5: continue
        diff_wr = ws['wr'] - bs['wr']
        diff_ret = ws['avg'] - bs['avg']
        arrow = '↑' if diff_wr > 2 else ('↓' if diff_wr < -2 else '→')
        print(f'  {tier:<8s} {bs["wr"]:>5.1f}%/+{bs["avg"]:.1f}% n={bs["n"]:<5d}  {ws["wr"]:>5.1f}%/+{ws["avg"]:.1f}% n={ws["n"]:<5d}  {arrow} wr{diff_wr:+.0f}pp ret{diff_ret:+.1f}%')

# ═══ 4. 综合总结 ═══
print(f'\n{"=" * 85}')
print('总结：PP_V1 最优窗口推荐')
print(f'{"=" * 85}')

# 极高+很高合并，找最优窗口
top_items = [m for m in merged if m['tech_tier'] in ('极高','很高')]
base_all = stats([m for m in top_items if not m['ppv1']['any_5']])
print(f'\n  极高+很高基准(无PP_V1): {base_all["n"]}条, {base_all["wr"]:.1f}%胜率, +{base_all["avg"]:.2f}%收益')

best = []
for win_key, win_label in windows:
    s = stats([m for m in top_items if m['ppv1'][win_key]])
    if s and s['n'] >= 50:
        best.append((win_label, s['n'], s['wr'], s['avg'], s['wr']-base_all['wr'], s['avg']-base_all['avg']))

best.sort(key=lambda x: -x[3])  # 按收益排序
print(f'\n  {"窗口":<16s} {"信号数":>6s} {"胜率":>7s} {"收益":>7s} {"胜率提升":>9s} {"收益提升":>9s}')
for b in best:
    print(f'  {b[0]:<16s} {b[1]:>6d} {b[2]:>6.1f}% {b[3]:>6.2f}% {b[4]:>+7.0f}pp {b[5]:>+8.2f}%')

print(f'\n  建议: 极高+很高 + PP_V1同日或后1-3天 = 最强组合')
