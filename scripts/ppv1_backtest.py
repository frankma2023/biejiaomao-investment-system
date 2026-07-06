"""
PP_V1 核心回测
━━━━━━━━━━━━
1. PP_V1 单独表现（持有期/入场/环境）
2. PP_V1 质量分层（量比/RS强度）
3. PP_V1 × MW信号共现（精确窗口）
4. PP_V1 × 技术面评分
"""
import sqlite3, numpy as np
from collections import defaultdict
from datetime import datetime, timedelta

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 85)
print('PP_V1 核心回测')
print('=' * 85)

# ═══ 1. 加载 PP_V1 回测数据 ═══
rows = c.execute("""
    SELECT br.stock_code, br.signal_date, br.entry_method, br.hold_days,
           br.market_regime, br.net_ret_pct, br.is_win, br.excess_ret_pct,
           br.combo_label, br.signal_count
    FROM backtest_results br
    WHERE br.combo_label='PP_V1' AND br.pool_mode='full'
      AND br.signal_date >= '2024-01-01' AND br.signal_date <= '2026-06-22'
""").fetchall()
print(f'\n[1] PP_V1 回测记录: {len(rows)}')

# 按持有期+入场方式分组
def stats(items):
    if not items: return None
    n=len(items);rets=np.array([i['net_ret_pct'] for i in items]);wins=np.array([i['is_win'] for i in items])
    pos=rets[rets>0];neg=rets[rets<0]
    wr=wins.mean()*100;avg=rets.mean();med=np.median(rets)
    plr=pos.mean()/abs(neg.mean()) if len(neg)>0 and neg.mean()!=0 else 0
    kelly=max(0,wr/100-(1-wr/100)/plr)*100 if plr>0 else 0
    return {'n':n,'wr':wr,'avg':avg,'med':med,'plr':plr,'kelly':kelly}

# ═══ 2. PP_V1 基础表现 ═══
print(f'\n{"─" * 85}')
print('一、PP_V1 基础表现（T+1_O，2024-2026）')
print(f'{"─" * 85}')
print(f'  {"持有期":<6s} {"样本":>8s} {"胜率":>8s} {"净收益":>9s} {"盈亏比":>7s} {"凯利":>7s} {"超额":>7s}')
print(f'  {"─" * 60}')

for hd in [5,10,20,60]:
    subset = [r for r in rows if r['hold_days']==hd and r['entry_method']=='T+1_O']
    s = stats(subset)
    if s: print(f'  H{hd:<5d} {s["n"]:>8,d} {s["wr"]:>7.1f}% {s["avg"]:>8.2f}% {s["plr"]:>6.2f} {s["kelly"]:>6.1f}% {np.mean([r["excess_ret_pct"] for r in subset]):>7.2f}%')

# 入场方式
print(f'\n  入场方式对比（H20）:')
print(f'  {"入场":<8s} {"样本":>8s} {"胜率":>8s} {"净收益":>9s}')
for em in ['T+0_C','T+1_O','T+2_O']:
    subset = [r for r in rows if r['hold_days']==20 and r['entry_method']==em]
    s = stats(subset)
    if s: print(f'  {em:<8s} {s["n"]:>8,d} {s["wr"]:>7.1f}% {s["avg"]:>8.2f}%')

# 市场环境
print(f'\n  市场环境（H20/T+1_O）:')
for regime in ['bull','ranging','bear']:
    subset = [r for r in rows if r['hold_days']==20 and r['entry_method']=='T+1_O' and r['market_regime']==regime]
    s = stats(subset)
    if s: print(f'  {regime:<10s} {s["n"]:>8,d} {s["wr"]:>7.1f}% {s["avg"]:>8.2f}%')

# ═══ 3. PP_V1 质量分层 ═══
print(f'\n{"─" * 85}')
print('二、PP_V1 质量分层（量比 × RPS250，H20/T+1_O）')
print(f'{"─" * 85}')

# 加载 PP_V1 的因子数据
c.execute("""
    SELECT stock_code, date, vol_ratio, rps_250, gain_pct
    FROM pocket_pivot_daily WHERE engine_version='V1' AND date>='2024-01-01' AND date<='2026-06-22'
""")
pp_factors = {}
for r in c.fetchall():
    pp_factors[(r['stock_code'], r['date'])] = {
        'vol_ratio': r['vol_ratio'] or 0, 'rps_250': r['rps_250'] or 0, 'gain_pct': r['gain_pct'] or 0}

# 合并因子
merged_h20 = []
for r in rows:
    if r['hold_days']!=20 or r['entry_method']!='T+1_O': continue
    f = pp_factors.get((r['stock_code'], r['signal_date']))
    if f:
        merged_h20.append({**r, **f})

print(f'  有效信号（含因子）: {len(merged_h20)}')

# 量比分桶
print(f'\n  量比(vol_ratio)分层:')
print(f'  {"量比":<12s} {"样本":>8s} {"胜率":>8s} {"净收益":>9s}')
for lo, hi in [(0,1.0),(1.0,1.3),(1.3,1.5),(1.5,2.0),(2.0,3.0),(3.0,99)]:
    subset = [r for r in merged_h20 if lo <= r['vol_ratio'] < hi]
    s = stats(subset)
    if s: print(f'  {lo}-{hi:<7.0f} {s["n"]:>8,d} {s["wr"]:>7.1f}% {s["avg"]:>8.2f}%')

# RPS分桶
print(f'\n  RPS250分层:')
print(f'  {"RPS250":<12s} {"样本":>8s} {"胜率":>8s} {"净收益":>9s}')
for lo, hi in [(0,50),(50,60),(60,70),(70,80),(80,90),(90,100)]:
    subset = [r for r in merged_h20 if lo <= (r['rps_250'] or 0) < hi]
    s = stats(subset)
    if s: print(f'  {lo}-{hi:<7d} {s["n"]:>8,d} {s["wr"]:>7.1f}% {s["avg"]:>8.2f}%')

# 交叉
print(f'\n  量比 × RPS250 交叉:')
print(f'  {"条件":<20s} {"样本":>8s} {"胜率":>8s} {"净收益":>9s}')
for cond, fn in [
    ('vol≥1.5 & RPS≥70', lambda r: r['vol_ratio']>=1.5 and (r['rps_250'] or 0)>=70),
    ('vol≥1.5 & RPS≥80', lambda r: r['vol_ratio']>=1.5 and (r['rps_250'] or 0)>=80),
    ('vol≥2.0 & RPS≥70', lambda r: r['vol_ratio']>=2.0 and (r['rps_250'] or 0)>=70),
    ('vol≥2.0 & RPS≥80', lambda r: r['vol_ratio']>=2.0 and (r['rps_250'] or 0)>=80),
    ('vol≥1.3 & RPS≥85', lambda r: r['vol_ratio']>=1.3 and (r['rps_250'] or 0)>=85),
]:
    subset = [r for r in merged_h20 if fn(r)]
    s = stats(subset)
    if s: print(f'  {cond:<20s} {s["n"]:>8,d} {s["wr"]:>7.1f}% {s["avg"]:>8.2f}%')

# ═══ 4. PP_V1 × MW 共现 ═══
print(f'\n{"─" * 85}')
print('三、PP_V1 × MW B1 共现（PP_V1日买入，H20/T+1_O）')
print(f'{"─" * 85}')

# MW B1 信号
mw_b1_set = set()
for r in c.execute("SELECT stock_code, b1_date FROM mw_signal_daily WHERE b1_date>='2024-01-01' AND b1_date<='2026-06-22' AND stock_code!='_sentinel_'"):
    mw_b1_set.add((r['stock_code'], r['b1_date']))

# 对每个PP_V1信号，检测前后窗口内是否有MW B1
base_ppv1 = [r for r in merged_h20]  # 基准
for window_days in [3,5,10]:
    pp_with_mw_before = []
    pp_with_mw_after = []
    pp_with_mw_any = []
    pp_without_mw = []
    
    for r in merged_h20:
        code = r['stock_code']; pp_date = r['signal_date']
        pp_dt = datetime.strptime(pp_date, '%Y-%m-%d')
        has_before = has_after = False
        for offset in range(-window_days, window_days+1):
            wd = (pp_dt + timedelta(days=offset)).strftime('%Y-%m-%d')
            if (code, wd) in mw_b1_set:
                if offset < 0: has_before = True
                elif offset >= 0: has_after = True
        if has_before or has_after:
            pp_with_mw_any.append(r)
            if has_before: pp_with_mw_before.append(r)
            if has_after: pp_with_mw_after.append(r)
        else:
            pp_without_mw.append(r)
    
    s_base = stats(pp_without_mw)
    s_any = stats(pp_with_mw_any)
    s_before = stats(pp_with_mw_before)
    s_after = stats(pp_with_mw_after)
    
    print(f'\n  ±{window_days}天窗口:')
    print(f'    无MW B1共现:          {s_base["n"]:>6,d} {s_base["wr"]:>5.1f}% {s_base["avg"]:>+6.2f}%')
    if s_any: print(f'    有MW B1共现(任意):     {s_any["n"]:>6,d} {s_any["wr"]:>5.1f}% {s_any["avg"]:>+6.2f}%')
    if s_before: print(f'    MW B1在PP_V1之前:     {s_before["n"]:>6,d} {s_before["wr"]:>5.1f}% {s_before["avg"]:>+6.2f}%')
    if s_after: print(f'    MW B1在PP_V1之后:     {s_after["n"]:>6,d} {s_after["wr"]:>5.1f}% {s_after["avg"]:>+6.2f}%')

# ═══ 5. PP_V1 技术面评分 ═══
print(f'\n{"─" * 85}')
print('四、PP_V1 技术面评分（复用 B1 9因子，满分100）')
print(f'{"─" * 85}')

c.execute("SELECT stock_code,date,adj_close,high,low,close,volume FROM daily_kline WHERE date>='2023-06-01' AND date<='2026-06-22' ORDER BY stock_code,date")
kline_by_code=defaultdict(list);kline_idx={}
for r in c.fetchall():
    kline_by_code[r['stock_code']].append({'date':r['date'],'adj_close':r['adj_close'],'high':r['high'],'low':r['low'],'close':r['close'],'volume':r['volume']})
for code,kls in kline_by_code.items():
    for i,kl in enumerate(kls):kline_idx[(code,kl['date'])]=(i,kl)

c.execute("SELECT stock_code,date,rps_20,rps_60,rps_250 FROM stock_rs_daily WHERE date>='2024-01-01' AND date<='2026-06-22'")
rs_dict={(r['stock_code'],r['date']):(r['rps_20']or 0,r['rps_60']or 0,r['rps_250']or 0) for r in c.fetchall()}

def ma(arr,p): return np.mean(arr[-p:]) if len(arr)>=p else None
def tech_score(code,sig_date):
    ii=kline_idx.get((code,sig_date))
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

# 计算所有H20/T+1_O PP_V1的技术分
print('  计算中...')
for i, r in enumerate(merged_h20):
    if i % 10000 == 0: print(f'  {i}/{len(merged_h20)}')
    r['tech_score'] = tech_score(r['stock_code'], r['signal_date'])

scored = [r for r in merged_h20 if r.get('tech_score') is not None]
print(f'  有效: {len(scored)}')

# 分层验证
print(f'\n  技术评分分层验证:')
print(f'  {"评分区间":<10s} {"样本":>8s} {"胜率":>8s} {"净收益":>9s}')
for lo, hi in [(0,30),(30,40),(40,50),(50,60),(60,70),(70,80),(80,100)]:
    subset = [r for r in scored if lo <= r['tech_score'] < hi]
    s = stats(subset)
    if s and s['n']>=5: print(f'  {lo}-{hi:<7d} {s["n"]:>8,d} {s["wr"]:>7.1f}% {s["avg"]:>8.2f}%')

# PP_V1 质量 × 技术评分 × MW共现
print(f'\n{"─" * 85}')
print('五、PP_V1 最优组合（H20/T+1_O）')
print(f'{"─" * 85}')
print(f'  {"组合":<35s} {"样本":>7s} {"胜率":>8s} {"收益":>8s}')
print(f'  {"─" * 65}')

combos = [
    ('PP_V1 全量', lambda r: True),
    ('PP_V1 + vol≥1.5', lambda r: r['vol_ratio']>=1.5),
    ('PP_V1 + vol≥1.5 + RPS≥70', lambda r: r['vol_ratio']>=1.5 and (r['rps_250'] or 0)>=70),
    ('PP_V1 + vol≥1.5 + RPS≥80', lambda r: r['vol_ratio']>=1.5 and (r['rps_250'] or 0)>=80),
    ('PP_V1 + tech≥70', lambda r: (r.get('tech_score') or 0)>=70),
    ('PP_V1 + tech≥70 + vol≥1.5', lambda r: (r.get('tech_score') or 0)>=70 and r['vol_ratio']>=1.5),
    ('PP_V1 + tech≥70 + RPS≥70', lambda r: (r.get('tech_score') or 0)>=70 and (r['rps_250'] or 0)>=70),
    ('PP_V1 + tech≥70 + vol≥1.5 + RPS≥70', lambda r: (r.get('tech_score') or 0)>=70 and r['vol_ratio']>=1.5 and (r['rps_250'] or 0)>=70),
]

for label, fn in combos:
    subset = [r for r in scored if fn(r)]
    s = stats(subset)
    if s: print(f'  {label:<35s} {s["n"]:>7,d} {s["wr"]:>7.1f}% {s["avg"]:>7.2f}%')

db.close()
print('\n完成。')
