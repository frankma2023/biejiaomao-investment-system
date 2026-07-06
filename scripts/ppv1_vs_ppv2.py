"""
PP_V1 vs PP_V2 对比诊断：为什么"更精确"的V2表现不如V1
"""
import sqlite3, numpy as np
from collections import defaultdict
from datetime import datetime, timedelta

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB); db.row_factory = sqlite3.Row; c = db.cursor()

print('=' * 80)
print('PP_V1 vs PP_V2 对比诊断')
print('=' * 80)

# ═══ 1. 基础数据 ═══
c.execute("SELECT engine_version, COUNT(*), MIN(date), MAX(date) FROM pocket_pivot_daily WHERE date>='2024-01-01' AND date<='2026-06-22' GROUP BY 1")
for r in c.fetchall():
    print(f'[信号量] PP_{r[0]}: {r[1]:,d} 条, {r[2]}~{r[3]}')

# ═══ 2. 因子分布对比 ═══
print(f'\n{"─" * 80}')
print('因子分布对比（有H20回测数据且有因子值的信号）')
print(f'{"─" * 80}')

# 获取有H20回测的PP_V1和PP_V2
c.execute("""
    SELECT br.stock_code, br.signal_date, pp.engine_version, pp.vol_ratio, pp.gain_pct, pp.rps_250, 
           br.net_ret_pct, br.is_win
    FROM backtest_results br
    JOIN pocket_pivot_daily pp ON br.stock_code=pp.stock_code AND br.signal_date=pp.date
    WHERE br.combo_label IN ('PP_V1','PP_V2') AND br.hold_days=20 AND br.entry_method='T+1_O'
      AND br.pool_mode='full' AND br.signal_date>='2024-01-01' AND br.signal_date<='2026-06-22'
""")
rows = c.fetchall()
v1 = [r for r in rows if r['engine_version']=='V1']
v2 = [r for r in rows if r['engine_version']=='V2']

print(f'  PP_V1 有效信号: {len(v1)}, PP_V2: {len(v2)}')

for label, data in [('PP_V1', v1), ('PP_V2', v2)]:
    vol_r = [r['vol_ratio'] for r in data if r['vol_ratio']]
    gain = [r['gain_pct'] for r in data if r['gain_pct']]
    rets = [r['net_ret_pct'] for r in data]
    wins = [r['is_win'] for r in data]
    print(f'\n  {label}:')
    print(f'    vol_ratio: median={np.median(vol_r):.2f} mean={np.mean(vol_r):.2f}')
    print(f'    gain_pct:  median={np.median(gain):.2f}% mean={np.mean(gain):.2f}%')
    print(f'    H20胜率: {np.mean(wins)*100:.1f}%  收益: {np.mean(rets):+.2f}%')

# ═══ 3. PP_V1和PP_V2 在同一天同一只股票上同时出现的情况 ═══
print(f'\n{"─" * 80}')
print('PP_V1 和 PP_V2 同日同股共现分析')
print(f'{"─" * 80}')

c.execute("""
    SELECT v1.stock_code, v1.date, v1.vol_ratio as v1_vr, v1.gain_pct as v1_gain,
           v2.vol_ratio as v2_vr, v2.gain_pct as v2_gain
    FROM pocket_pivot_daily v1
    JOIN pocket_pivot_daily v2 ON v1.stock_code=v2.stock_code AND v1.date=v2.date
    WHERE v1.engine_version='V1' AND v2.engine_version='V2'
      AND v1.date>='2024-01-01' AND v1.date<='2026-06-22'
""")
co_dates = c.fetchall()
print(f'  同日同股共现: {len(co_dates)} 次')

# 共现信号的H20表现
co_set = {(r['stock_code'], r['date']) for r in co_dates}
co_rets = [r['net_ret_pct'] for r in v2 if (r['stock_code'], r['signal_date']) in co_set]
non_co_rets = [r['net_ret_pct'] for r in v2 if (r['stock_code'], r['signal_date']) not in co_set]

print(f'  PP_V2中同日有PP_V1共现的: {len(co_rets)}条  胜率={np.mean([1 if r>0 else 0 for r in co_rets])*100:.1f}%  收益={np.mean(co_rets):+.2f}%')
print(f'  PP_V2中无PP_V1共现的:     {len(non_co_rets)}条  胜率={np.mean([1 if r>0 else 0 for r in non_co_rets])*100:.1f}%  收益={np.mean(non_co_rets):+.2f}%')

# ═══ 4. 时序分析：PP_V1和PP_V2谁先触发 ═══
print(f'\n{"─" * 80}')
print('时序分析：同一股票上 PP_V1 和 PP_V2 的触发顺序')
print(f'{"─" * 80}')

# 找所有同一股票在15天内同时触发过V1和V2的情况
c.execute("SELECT DISTINCT stock_code FROM pocket_pivot_daily WHERE engine_version='V2' AND date>='2024-01-01' AND date<='2026-06-22'")
v2_codes = {r['stock_code'] for r in c.fetchall()}

v1_first = 0; v2_first = 0; same_day = 0
v1_only_win = []; v2_only_win = []
v1_before_v2_ret = []; v2_before_v1_ret = []

# 对每只V2股票，找最近的V1
for code in list(v2_codes)[:500]:
    c.execute("SELECT date FROM pocket_pivot_daily WHERE stock_code=? AND engine_version='V2' AND date>='2024-01-01' AND date<='2026-06-22' ORDER BY date", (code,))
    v2_dates = [r['date'] for r in c.fetchall()]
    for v2d_str in v2_dates:
        v2d = datetime.strptime(v2d_str, '%Y-%m-%d')
        c.execute("SELECT date FROM pocket_pivot_daily WHERE stock_code=? AND engine_version='V1' AND date>=? AND date<=? ORDER BY date", 
                  (code, (v2d-timedelta(days=30)).strftime('%Y-%m-%d'), (v2d+timedelta(days=30)).strftime('%Y-%m-%d')))
        nearby_v1 = [r['date'] for r in c.fetchall()]
        for v1d_str in nearby_v1:
            v1d = datetime.strptime(v1d_str, '%Y-%m-%d')
            if v1d < v2d: v1_first += 1
            elif v1d > v2d: v2_first += 1
            else: same_day += 1

print(f'  PP_V1先于PP_V2: {v1_first} 次')
print(f'  PP_V2先于PP_V1: {v2_first} 次')
print(f'  同日: {same_day} 次')

# ═══ 5. PP_V2单独 vs PP_V2+PP_V1附近的胜率 ═══
print(f'\n{"─" * 80}')
print('PP_V2 附近有/无 PP_V1 的胜率差异')
print(f'{"─" * 80}')

mw_b1_set = set()
for r in c.execute("SELECT stock_code, b1_date FROM mw_signal_daily WHERE b1_date>='2024-01-01' AND b1_date<='2026-06-22' AND stock_code!='_sentinel_'"):
    mw_b1_set.add((r['stock_code'], r['b1_date']))

# 对每个PP_V2，检查前后是否有PP_V1或MW_B1
for label, window_days in [('±3天', 3), ('±5天', 5), ('±10天', 10)]:
    v2_alone = []; v2_with_v1 = []; v2_with_b1 = []
    for r in v2:
        code = r['stock_code']; pp_date = r['signal_date']
        pp_dt = datetime.strptime(pp_date, '%Y-%m-%d')
        has_v1 = False; has_b1 = False
        for offset in range(-window_days, window_days+1):
            wd = (pp_dt + timedelta(days=offset)).strftime('%Y-%m-%d')
            if (code, wd) in co_set: has_v1 = True  # PP_V1同位置
            if (code, wd) in mw_b1_set: has_b1 = True
        if not has_v1 and not has_b1: v2_alone.append(r['net_ret_pct'])
        if has_v1: v2_with_v1.append(r['net_ret_pct'])
        if has_b1: v2_with_b1.append(r['net_ret_pct'])
    
    wr_a = np.mean([1 if r>0 else 0 for r in v2_alone])*100 if v2_alone else 0
    wr_v = np.mean([1 if r>0 else 0 for r in v2_with_v1])*100 if v2_with_v1 else 0
    wr_b = np.mean([1 if r>0 else 0 for r in v2_with_b1])*100 if v2_with_b1 else 0
    print(f'  {label}: V2单独={wr_a:.1f}%(n={len(v2_alone)})  V2+PP_V1={wr_v:.1f}%(n={len(v2_with_v1)})  V2+B1={wr_b:.1f}%(n={len(v2_with_b1)})')

db.close()
print('\n诊断完成。')
