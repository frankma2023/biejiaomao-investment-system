"""
精确组合分析: B1(tech≥75) + 后5天 PP_V1(tech≥70)
"""
import sqlite3, numpy as np
from collections import defaultdict
from datetime import datetime, timedelta

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 80)
print('B1(tech≥75) + PP_V1(tech≥70) in 5 days → 胜率')
print('=' * 80)

# ═══ 加载数据 ═══
# B1 + H20
b1_rows = c.execute("""
    SELECT br.stock_code, br.signal_date as b1_date, br.net_ret_pct, br.is_win
    FROM backtest_results br
    WHERE br.combo_label='MW_B1' AND br.hold_days=20 AND br.entry_method='T+1_O'
      AND br.pool_mode='full' AND br.signal_date >= '2024-01-01' AND br.signal_date <= '2026-06-22'
""").fetchall()

# PP_V1 dates
ppv1_dates = defaultdict(set)
for r in c.execute("SELECT stock_code, date FROM pocket_pivot_daily WHERE engine_version='V1' AND date>='2024-01-01' AND date<='2026-07-05'"):
    ppv1_dates[r['stock_code']].add(r['date'])

# K线 + RS（复用之前的快速评分）
c.execute("SELECT stock_code,date,adj_close,high,low,close,volume FROM daily_kline WHERE date>='2023-06-01' AND date<='2026-06-22' ORDER BY stock_code,date")
kline_by_code=defaultdict(list);kline_idx={}
for r in c.fetchall():
    kline_by_code[r['stock_code']].append({'date':r['date'],'adj_close':r['adj_close'],'high':r['high'],'low':r['low'],'close':r['close'],'volume':r['volume']})
for code,kls in kline_by_code.items():
    for i,kl in enumerate(kls):kline_idx[(code,kl['date'])]=(i,kl)

c.execute("SELECT stock_code,date,rps_20,rps_60,rps_250 FROM stock_rs_daily WHERE date>='2024-01-01' AND date<='2026-06-22'")
rs_dict={(r['stock_code'],r['date']):(r['rps_20']or 0,r['rps_60']or 0,r['rps_250']or 0) for r in c.fetchall()}

# PP_V1因子
c.execute("SELECT stock_code, date, vol_ratio, gain_pct FROM pocket_pivot_daily WHERE engine_version='V1' AND date>='2024-01-01' AND date<='2026-06-22'")
pp_factors = {(r['stock_code'],r['date']):(r['vol_ratio']or 0,r['gain_pct']or 0) for r in c.fetchall()}

db.close()

def ma(arr,p): return np.mean(arr[-p:]) if len(arr)>=p else None

def tech_score_b1(code, sig_date):
    """B1 技术评分（复用之前的逻辑）"""
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

def tech_score_ppv1(code, sig_date):
    """PP_V1 技术评分"""
    ii=kline_idx.get((code,sig_date))
    if not ii: return None
    idx,kl=ii
    if idx<250: return None
    kls=kline_by_code[code]
    cl=np.array([k['adj_close'] for k in kls[max(0,idx-260):idx+1]],dtype=np.float64)
    cn=cl[-1];sc=0
    m20=ma(cl,20);m50=ma(cl,50);m250=ma(cl,250);m60=ma(cl,60)
    if m20 and m20>0: p=(cn-m20)/m20*100;sc+=12 if p<=5 else(9 if p<=10 else(6 if p<=15 else(3 if p<=25 else 0)))
    if m50 and m50>0: p=(cn-m50)/m50*100;sc+=12 if p<=10 else(8 if p<=18 else(4 if p<=30 else 0))
    if m250 and m250>0: p=(cn-m250)/m250*100;sc+=10 if p<=20 else(7 if p<=30 else(3 if p<=45 else 0))
    if m60 and m60>0: b=(cn-m60)/m60*100;sc+=8 if b<=10 else(5 if b<=18 else(2 if b<=30 else 0))
    pf=pp_factors.get((code,sig_date));vr=pf[0]if pf else 0;gp=pf[1]if pf else 0
    sc+=15 if 1.5<=vr<=2.0 else(10 if 1.3<=vr<1.5 or 2.0<vr<=3.0 else(5 if vr>3.0 else 3))
    sc+=8 if 2<=gp<=4 else(6 if 4<gp<=6 else(4 if 6<gp<=8 else 2))
    rs=rs_dict.get((code,sig_date));r20=rs[0]if rs else 0;r60=rs[1]if rs else 0
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

# ═══ 计算B1技术分 ═══
print('[1] 计算 B1 技术分...')
b1_scored = {}
for r in b1_rows:
    ts = tech_score_b1(r['stock_code'], r['b1_date'])
    if ts is not None:
            'net_ret': r['net_ret_pct'],
            'b1_tech': ts,
        }

# ═══ 检查PP_V1 ═══
print('[2] 检查 PP_V1 共现...')
results = []
for (code, b1_date), b1_info in b1_scored.items():
    if b1_info['b1_tech'] < 75: continue  # B1技术分≥75
    b1dt = datetime.strptime(b1_date, '%Y-%m-%d')
    ppv1_set = ppv1_dates.get(code, set())
    
    found_ppv1 = None
    for offset in range(1, 6):  # B1后1-5天
        wd = (b1dt + timedelta(days=offset)).strftime('%Y-%m-%d')
        if wd in ppv1_set:
            pp_ts = tech_score_ppv1(code, wd)
            if pp_ts is not None and pp_ts >= 70:
                found_ppv1 = {'date': wd, 'tech': pp_ts}
                break  # 取第一个满足条件的PP_V1
            elif pp_ts is not None:
                if found_ppv1 is None:
                    found_ppv1 = {'date': wd, 'tech': pp_ts}  # 记录但不满足≥70
    
    results.append({
        'b1_net_ret': b1_info['net_ret'],
        'b1_is_win': b1_info['is_win'],
        'b1_tech': b1_info['b1_tech'],
        'has_ppv1': found_ppv1 is not None,
        'ppv1_tech': found_ppv1['tech'] if found_ppv1 else None,
        'ppv1_meets': found_ppv1 is not None and found_ppv1['tech'] >= 70 if found_ppv1 else False,
        'pp_any_tech': found_ppv1['tech'] if found_ppv1 else None,
    })

# ═══ 统计 ═══
def s(items, label=''):
    if not items: return None
    n=len(items);rets=[i['b1_net_ret'] for i in items];wins=[i['b1_is_win'] for i in items]
    pos=sum(1 for i in items if i['b1_net_ret']>0)
    return {'n':n,'wr':pos/n*100,'avg':np.mean(rets),'med':np.median(rets)}

total_b1_tech75 = len(results)
total_with_ppv1_any = sum(1 for r in results if r['has_ppv1'])
total_with_ppv1_meets = sum(1 for r in results if r['ppv1_meets'])

print(f'\nB1(tech≥75) 总数: {total_b1_tech75}')
print(f'  其中后5天有PP_V1(任意技术分): {total_with_ppv1_any} ({total_with_ppv1_any/total_b1_tech75*100:.0f}%)')
print(f'  其中后5天有PP_V1(tech≥70):    {total_with_ppv1_meets} ({total_with_ppv1_meets/total_b1_tech75*100:.0f}%)')

# 分组
g1 = [r for r in results if not r['has_ppv1']]          # 无PP_V1
g2 = [r for r in results if r['has_ppv1'] and not r['ppv1_meets']]  # 有PP_V1但技术分<70
g3 = [r for r in results if r['ppv1_meets']]             # 有PP_V1且技术分≥70

print(f'\n{"=" * 80}')
print(f'B1(tech≥75) + PP_V1后5天 分层对比')
print(f'{"=" * 80}')
print(f'  {"组合":<30s} {"数量":>6s} {"胜率":>7s} {"收益":>7s}')
print(f'  {"─" * 52}')

for label, group in [('无PP_V1', g1), ('PP_V1但tech<70', g2), ('PP_V1且tech≥70', g3)]:
    st = s(group)
    if st: print(f'  {label:<30s} {st["n"]:>6d} {st["wr"]:>6.1f}% {st["avg"]:>6.2f}%')

# 全量B1基准
all_b1 = s([{'b1_net_ret': r['net_ret_pct'], 'b1_is_win': r['is_win']} for r in b1_rows]))
print(f'\n  全量B1基准: {all_b1["n"]:,d}条, {all_b1["wr"]:.1f}%胜率, {all_b1["avg"]:+.2f}%收益')
