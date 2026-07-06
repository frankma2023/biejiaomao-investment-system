"""
PP_V1 技术因子预测力分析 → 构建专属评分体系
"""
import sqlite3, numpy as np
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 80)
print('PP_V1 技术因子预测力分析')
print('=' * 80)

# ═══ 1. 加载 PP_V1 H20 ═══
rows = c.execute("""
    SELECT br.stock_code, br.signal_date, br.net_ret_pct, br.is_win
    FROM backtest_results br
    WHERE br.combo_label='PP_V1' AND br.hold_days=20 AND br.entry_method='T+1_O'
      AND br.pool_mode='full' AND br.signal_date >= '2024-01-01' AND br.signal_date <= '2026-06-22'
""").fetchall()
print(f'[1] PP_V1 H20: {len(rows)} 条')

# ═══ 2. PP_V1 因子 ═══
c.execute("SELECT stock_code, date, vol_ratio, rps_250, gain_pct FROM pocket_pivot_daily WHERE engine_version='V1' AND date>='2024-01-01' AND date<='2026-06-22'")
pp_factors = {}
for r in c.fetchall():
    pp_factors[(r['stock_code'], r['date'])] = {
        'vol_ratio': r['vol_ratio'] or 0, 'rps_250': r['rps_250'] or 0, 'gain_pct': r['gain_pct'] or 0}

# ═══ 3. K线 ═══
c.execute("SELECT stock_code,date,adj_close,high,low,close,volume FROM daily_kline WHERE date>='2023-06-01' AND date<='2026-06-22' ORDER BY stock_code,date")
kline_by_code=defaultdict(list);kline_idx={}
for r in c.fetchall():
    kline_by_code[r['stock_code']].append({'date':r['date'],'adj_close':r['adj_close'],'high':r['high'],'low':r['low'],'close':r['close'],'volume':r['volume']})
for code,kls in kline_by_code.items():
    for i,kl in enumerate(kls):kline_idx[(code,kl['date'])]=(i,kl)

# ═══ 4. RS ═══
c.execute("SELECT stock_code,date,rps_20,rps_60,rps_250 FROM stock_rs_daily WHERE date>='2024-01-01' AND date<='2026-06-22'")
rs_dict={(r['stock_code'],r['date']):(r['rps_20']or 0,r['rps_60']or 0,r['rps_250']or 0) for r in c.fetchall()}

db.close()

def ma(arr,p): return np.mean(arr[-p:]) if len(arr)>=p else None

# ═══ 5. 计算全部因子 ═══
print('[2] 计算因子...')
factors_list = []
for i, r in enumerate(rows):
    if i % 15000 == 0: print(f'  {i}/{len(rows)}')
    code, sig_date = r['stock_code'], r['signal_date']
    ii = kline_idx.get((code, sig_date))
    if not ii: continue
    idx, kl = ii
    if idx < 250: continue
    
    kls = kline_by_code[code]
    cl = np.array([k['adj_close'] for k in kls[max(0,idx-260):idx+1]], dtype=np.float64)
    cn = cl[-1]
    vols = np.array([k['volume'] for k in kls[max(0,idx-60):idx+1]], dtype=np.float64)
    
    f = {'net_ret': r['net_ret_pct'], 'is_win': r['is_win']}
    
    # PP_V1 自身因子
    pf = pp_factors.get((code, sig_date))
    if pf:
        f['pp_vol_ratio'] = pf['vol_ratio']
        f['pp_gain_pct'] = pf['gain_pct']
    
    # MA 距离
    m20=ma(cl,20);m50=ma(cl,50);m250=ma(cl,250);m60=ma(cl,60)
    if m20 and m20>0: f['pct_ma20'] = (cn-m20)/m20*100
    if m50 and m50>0: f['pct_ma50'] = (cn-m50)/m50*100
    if m250 and m250>0: f['pct_ma250'] = (cn-m250)/m250*100
    if m60 and m60>0: f['bias'] = (cn-m60)/m60*100
    
    # 成交量
    vol_now = vols[-1]
    if len(vols)>=21:
        vol_ma20 = np.mean(vols[-21:-1])
        if vol_ma20>0: f['vol_vs_ma20'] = vol_now/vol_ma20
    if len(vols)>=51:
        vol_ma50 = np.mean(vols[-51:-1])
        if vol_ma50>0: f['vol_vs_ma50'] = vol_now/vol_ma50
    
    # RS
    rs = rs_dict.get((code, sig_date))
    if rs:
        f['rps20'] = rs[0]; f['rps60'] = rs[1]; f['rps250'] = rs[2]
    
    # MACD
    if len(cl)>=26:
        e12=cn;e26=cn;k12=2/13;k26=2/27
        for j in range(len(cl)-2,max(0,len(cl)-27),-1):e12=cl[j]*k12+e12*(1-k12);e26=cl[j]*k26+e26*(1-k26)
        f['macd_dif'] = e12-e26; f['macd_dif_sign'] = 1 if f['macd_dif']>0 else 0
    
    # KDJ
    if len(cl)>=9:
        hi=np.array([k['high'] for k in kls[max(0,idx-8):idx+1]],dtype=np.float64)
        lo=np.array([k['low'] for k in kls[max(0,idx-8):idx+1]],dtype=np.float64)
        if hi.max()>lo.min(): f['kdj_k'] = (cn-lo.min())/(hi.max()-lo.min())*100*2/3+50/3
    
    # 涨幅
    if idx>0 and kls[idx-1]['close']>0:
        f['day_gain'] = (kl['close']-kls[idx-1]['close'])/kls[idx-1]['close']*100
    
    # 均线排列
    if m20 and m50 and m250:
        align = 0
        if cn>m20: align|=8
        if m20>m50: align|=4
        if m50>m250: align|=2
        f['ma_align'] = align
    
    factors_list.append(f)

print(f'  有效: {len(factors_list)}')

# ═══ 6. 因子相关性分析 ═══
print(f'\n{"=" * 80}')
print('因子与 H20 净收益的相关性')
print(f'{"=" * 80}')
print(f'  {"因子":<20s} {"相关系数":>10s} {"高组胜率":>10s} {"低组胜率":>10s}')
print(f'  {"─" * 52}')

factor_names = [
    ('pct_ma20', '距MA20(%)'),
    ('pct_ma50', '距MA50(%)'),
    ('pct_ma250', '距MA250(%)'),
    ('bias', 'BIAS(MA60)'),
    ('vol_vs_ma20', '量/MA20量'),
    ('pp_vol_ratio', 'PP量比(引擎)'),
    ('pp_gain_pct', 'PP涨幅%(引擎)'),
    ('day_gain', '当日涨幅%'),
    ('rps20', 'RPS20'),
    ('rps60', 'RPS60'),
    ('rps250', 'RPS250'),
    ('macd_dif', 'MACD DIF'),
    ('macd_dif_sign', 'DIF>0占比'),
    ('kdj_k', 'KDJ K值'),
    ('ma_align', '均线排列'),
]

for key, label in factor_names:
    vals = []; rets = []
    for f in factors_list:
        if key in f and f[key] is not None:
            vals.append(f[key]); rets.append(f['net_ret'])
    if len(vals) < 100: continue
    
    corr = np.corrcoef(vals, rets)[0,1]
    median_v = np.median(vals)
    high_rets = [rets[i] for i in range(len(vals)) if vals[i] >= median_v]
    low_rets = [rets[i] for i in range(len(vals)) if vals[i] < median_v]
    high_wr = np.mean([1 if r>0 else 0 for r in high_rets])*100
    low_wr = np.mean([1 if r>0 else 0 for r in low_rets])*100
    
    arrow = '↑' if abs(corr) > 0.03 else ('→' if abs(corr) > 0.01 else '')
    print(f'  {label:<20s} {corr:>10.4f} {high_wr:>9.1f}% {low_wr:>9.1f}%  {arrow}')

# ═══ 7. PP_V1 专属评分设计 ═══
print(f'\n{"=" * 80}')
print('PP_V1 专属评分设计')
print(f'{"=" * 80}')

print(f'''
基于因子分析，PP_V1 与 B1 的核心差异：
  1. PP_V1 有 vol_ratio 数据，量比本身就是信号的一部分
  2. RPS 数据在 PP_V1 表中大量缺失，但可从 stock_rs_daily 获取
  3. PP_V1 的 gain_pct 一般比 B1 小（口袋支点不要求大涨）
  4. 均线排列在 PP_V1 中预测力更强

建议评分表（满分 100）：
''')

print(f'  {"#":<4s} {"因子":<18s} {"最优区间":<15s} {"满分":<6s} {"规则":<50s}')
print(f'  {"─" * 90}')

rules = [
    (1, '距MA20', '≤5%', 12, '≤5%(12) 5-10%(9) 10-15%(6) 15-25%(3) >25%(0)'),
    (2, '距MA50', '≤10%', 12, '≤10%(12) 10-18%(8) 18-30%(4) >30%(0)'),
    (3, '距MA250', '≤20%', 10, '≤20%(10) 20-30%(7) 30-45%(3) >45%(0)'),
    (4, 'BIAS(MA60)', '≤10%', 8, '≤10%(8) 10-18%(5) 18-30%(2) >30%(0)'),
    (5, 'PP量比', '1.3-2.0', 15, '1.3-1.5(10) 1.5-2.0(15) 2.0-3.0(10) >3.0(5) <1.3(3)'),
    (6, 'PP涨幅%', '2%-6%', 8, '2-4%(8) 4-6%(6) 6-8%(4) <2%或>8%(2)'),
    (7, 'RPS20', '40-75', 8, '40-75(8) 30-40或75-85(5) >85(2) <30(3)'),
    (8, 'RPS60', '40-70', 8, '40-70(8) 30-40或70-80(5) >80(2) <30(3)'),
    (9, 'MACD DIF', '>0', 12, 'DIF>0且<2%(12) DIF>0(9) 近零轴(6) 深负(3)'),
    (10, 'KDJ K值', '≤80', 7, '≤60(7) 60-80(5) 80-90(2) >90(0)'),
]

for num, name, opt, score, rule in rules:
    print(f'  {num:<4d} {name:<18s} {opt:<15s} {score:<6d} {rule:<50s}')

print(f'\n  PP_V1 与 B1 评分差异:')
print(f'    新增 PP量比(15分)和 PP涨幅(8分) — PP_V1自身信号特征')
print(f'    距MA权重略降 — PP_V1 信号量更大，覆盖更多价位')
print(f'    移除 RPS250 — 数据覆盖率低')
print(f'    MACD + KDJ 权重略降 — 给 PP_V1 自身因子让路')
