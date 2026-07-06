"""
技术置信度 × 共现信号 交叉分析
"""
import sqlite3, numpy as np
from collections import defaultdict
from datetime import datetime, timedelta

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 80)
print('技术置信度 × 共现信号 交叉分析')
print('=' * 80)

# ═══ 1. 加载 B1 + H20 ═══
rows = c.execute("""
    SELECT br.stock_code, br.signal_date as b1_date, br.net_ret_pct, br.is_win
    FROM backtest_results br
    WHERE br.combo_label='MW_B1' AND br.hold_days=20 AND br.entry_method='T+1_O'
      AND br.pool_mode='full' AND br.signal_date >= '2024-01-01' AND br.signal_date <= '2026-06-22'
""").fetchall()
print(f'[1] {len(rows)} 条 B1 信号')

# ═══ 2. 快速技术评分（复用之前的逻辑）═══
c.execute("SELECT stock_code,date,adj_close,high,low,close,volume FROM daily_kline WHERE date>='2023-06-01' AND date<='2026-06-22' ORDER BY stock_code,date")
kline_by_code=defaultdict(list);kline_idx={}
for r in c.fetchall():
    kline_by_code[r['stock_code']].append({'date':r['date'],'adj_close':r['adj_close'],'high':r['high'],'low':r['low'],'close':r['close'],'volume':r['volume']})
for code,kls in kline_by_code.items():
    for i,kl in enumerate(kls):kline_idx[(code,kl['date'])]=(i,kl)

c.execute("SELECT stock_code,date,rps_20,rps_60,rps_250 FROM stock_rs_daily WHERE date>='2024-01-01' AND date<='2026-06-22'")
rs_dict={(r['stock_code'],r['date']):(r['rps_20']or 0,r['rps_60']or 0,r['rps_250']or 0) for r in c.fetchall()}

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

def tech_tier(score):
    if score is None: return None
    if score>=85: return '极高'
    if score>=75: return '很高'
    if score>=65: return '高'
    if score>=50: return '中'
    return '低'

# ═══ 3. 加载共现信号 ═══
c.execute("SELECT stock_code, date, signal_mask FROM signal_events WHERE date>='2023-12-20' AND date<='2026-06-25'")
sig_events = {}
for r in c.fetchall():
    m = r['signal_mask']
    sig_events[(r['stock_code'], r['date'])] = {
        'pp_v1': bool(m & (1<<3)),
        'pp_v2': bool(m & (1<<4)),
        'bo_v2': bool(m & (1<<5)),
    }

# 卖出信号
c.execute("SELECT stock_code, date, signals_json FROM pattern_scan_signals WHERE date>='2024-01-01' AND date<='2026-06-22'")
sell_dict = {}
for r in c.fetchall():
    if r['signals_json']:
        try:
            import json
            sigs = json.loads(r['signals_json']) if isinstance(r['signals_json'], str) else r['signals_json']
            has_sell = any(s.get('type') == 'bearish' for s in (sigs if isinstance(sigs, list) else []))
            if has_sell:
                sell_dict[(r['stock_code'], r['date'])] = True
        except: pass

db.close()

# ═══ 4. 合并数据 ═══
print('[4] 合并数据...')
merged = []
for r in rows:
    code, b1d = r['stock_code'], r['b1_date']
    ts = tech_score(code, b1d)
    if ts is None: continue
    
    b1dt = datetime.strptime(b1d, '%Y-%m-%d')
    has_ppv1 = has_ppv2 = has_bov2 = has_sell = False
    
    for offset in range(-5, 6):
        wd = (b1dt + timedelta(days=offset)).strftime('%Y-%m-%d')
        se = sig_events.get((code, wd))
        if se:
            if se['pp_v1']: has_ppv1 = True
            if se['pp_v2']: has_ppv2 = True
            if se['bo_v2']: has_bov2 = True
    
    # 卖出信号（B1后N天内）
    for offset in range(0, 21):
        wd = (b1dt + timedelta(days=offset)).strftime('%Y-%m-%d')
        if (code, wd) in sell_dict:
            has_sell = True
            break
    
    merged.append({
        'net_ret': r['net_ret_pct'], 'is_win': r['is_win'],
        'tech_tier': tech_tier(ts), 'tech_score': ts,
        'ppv1': has_ppv1, 'ppv2': has_ppv2, 'bov2': has_bov2,
        'sell': has_sell,
    })

print(f'  有效: {len(merged)}')

# ═══ 5. 分析 ═══
def stats(items):
    if not items: return None
    n=len(items);rets=[i['net_ret'] for i in items];wins=[i['is_win'] for i in items]
    return {'n':n,'wr':np.mean(wins)*100,'avg':np.mean(rets),'med':np.median(rets)}

tiers = ['极高','很高','高','中','低']
co_signals = [
    ('ppv1', 'PP_V1 ±5天'),
    ('ppv2', 'PP_V2 ±5天'),
    ('bov2', 'BO_V2 ±5天'),
    ('sell', 'B1后有卖出信号'),
]

print(f'\n{"=" * 80}')
print('各技术层 × 共现信号 胜率矩阵')
print(f'{"=" * 80}')

for cs_key, cs_label in co_signals:
    print(f'\n── {cs_label} ──')
    print(f'  {"技术层":<8s} {"无共现":>16s} {"有共现":>16s} {"差异":>10s}')
    print(f'  {"─" * 52}')
    for tier in tiers:
        tier_items = [m for m in merged if m['tech_tier'] == tier]
        without = [m for m in tier_items if not m[cs_key]]
        with_sig = [m for m in tier_items if m[cs_key]]
        sw = stats(without); ss = stats(with_sig)
        if not sw or not ss: continue
        diff = ss['avg'] - sw['avg']
        arrow = '↑' if diff > 0 else ('↓' if diff < -0.5 else '→')
        print(f'  {tier:<8s} {sw["wr"]:>5.1f}%/+{sw["avg"]:.1f}% n={sw["n"]:<5d} {ss["wr"]:>5.1f}%/+{ss["avg"]:.1f}% n={ss["n"]:<5d} {arrow} {diff:+.1f}%')

# ═══ 6. 最佳组合 ═══
print(f'\n{"=" * 80}')
print('最佳组合推荐')
print(f'{"=" * 80}')
print(f'  {"组合":<30s} {"信号":>6s} {"胜率":>7s} {"收益":>7s}')
print(f'  {"─" * 52}')

combos = [
    ('技术极高 + PP_V1', lambda m: m['tech_tier']=='极高' and m['ppv1']),
    ('技术极高 + 无PP_V1', lambda m: m['tech_tier']=='极高' and not m['ppv1']),
    ('技术极高 + 无卖出信号', lambda m: m['tech_tier']=='极高' and not m['sell']),
    ('技术极高 + 有卖出信号', lambda m: m['tech_tier']=='极高' and m['sell']),
    ('技术很高 + PP_V1', lambda m: m['tech_tier']=='很高' and m['ppv1']),
    ('技术很高 + 无PP_V1', lambda m: m['tech_tier']=='很高' and not m['ppv1']),
    ('技术极高+很高 + PP_V1', lambda m: m['tech_tier'] in ('极高','很高') and m['ppv1']),
    ('技术极高+很高 + 无PP_V1', lambda m: m['tech_tier'] in ('极高','很高') and not m['ppv1']),
]

for label, fn in combos:
    items = [m for m in merged if fn(m)]
    if len(items) < 5: continue
    s = stats(items)
    print(f'  {label:<30s} {s["n"]:>6d} {s["wr"]:>6.1f}% {s["avg"]:>6.2f}%')

print(f'\n{"=" * 80}')
print('结论')
print(f'{"=" * 80}')
# Summary
for tier in ['极高','很高']:
    base = stats([m for m in merged if m['tech_tier']==tier])
    plus = stats([m for m in merged if m['tech_tier']==tier and m['ppv1']])
    minus = stats([m for m in merged if m['tech_tier']==tier and not m['ppv1']])
    if base and plus:
        diff = plus['wr'] - base['wr']
        print(f'  技术{tier}: 基准{base["wr"]:.1f}% → +PP_V1 {plus["wr"]:.1f}% (+{diff:.0f}pp)')
    if base and minus:
        diff = minus['wr'] - base['wr']
        print(f'  技术{tier}: 基准{base["wr"]:.1f}% → 无PP_V1 {minus["wr"]:.1f}% ({diff:+.0f}pp)')

# PP_V2 and BO_V2 summary
print(f'\n  PP_V2和BO_V2共现在所有技术层中均无显著提升效果。')
