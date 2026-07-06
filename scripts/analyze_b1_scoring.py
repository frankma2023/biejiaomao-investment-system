"""
B1-only 评分体系实证重建
━━━━━━━━━━━━━━━━━━━━━
目标：用B1日已知的数据，反推：
  1. B2确认概率（30天内出现B2的比例）
  2. H20净收益
  3. 哪些B1子因子有实际预测力
然后制定B1-only的置信度分层规则。
"""
import sqlite3, json
from collections import defaultdict
import numpy as np

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 70)
print('B1-only 评分体系实证重建')
print('=' * 70)

# ── 1. 加载所有B1信号（2024-2026）──
rows = c.execute("""
    SELECT stock_code, b1_date, b2_date, score, confidence,
           score_h, score_d, score_c, score_i1, score_i2, score_sig,
           decline_pct, h_rs250, h_rs20, b1_vol_ratio, b1_return_pct,
           c_amplitude_pct, c_amount_avg, h_pre_rise_pct,
           ind_rs250, ind_rs20, ind_code, ind_name
    FROM mw_signal_daily
    WHERE b1_date >= '2024-01-01' AND b1_date <= '2026-06-22'
      AND stock_code != '_sentinel_'
""").fetchall()
print(f'\n[1] B1信号总数: {len(rows)}')

# ── 2. 加载H20表现 ──
bt_rows = c.execute("""
    SELECT stock_code, signal_date as b1_date, net_ret_pct, is_win,
           excess_ret_pct, ret_pct, market_regime
    FROM backtest_results
    WHERE combo_label='MW_B1' AND hold_days=20 AND entry_method='T+1_O'
      AND pool_mode='full'
      AND signal_date >= '2024-01-01' AND signal_date <= '2026-06-22'
""").fetchall()
bt_dict = {(r['stock_code'], r['b1_date']): r for r in bt_rows}
print(f'[2] H20回测记录: {len(bt_dict)}')

# ── 3. 加载RS数据 ──
rs_rows = c.execute("""
    SELECT stock_code, date, rps_20, rps_250
    FROM stock_rs_daily
    WHERE date >= '2024-01-01' AND date <= '2026-06-22'
""").fetchall()
rs_dict = {}
for r in rs_rows:
    rs_dict[(r['stock_code'], r['date'])] = (r['rps_20'], r['rps_250'])
print(f'[3] RS记录: {len(rs_dict)}')

# ── 4. 合并数据 ──
data = []
for r in rows:
    code = r['stock_code']
    b1_date = r['b1_date']
    bt = bt_dict.get((code, b1_date))
    rs = rs_dict.get((code, b1_date))
    
    has_b2 = r['b2_date'] is not None
    h20_ret = bt['net_ret_pct'] if bt else None
    h20_win = bt['is_win'] if bt else None
    regime = bt['market_regime'] if bt else None
    rps20 = rs[0] if rs else None
    rps250 = rs[1] if rs else None
    
    # B1-only score (不含P和Gap，因为这俩B2出现后才有)
    b1_only_score = (r['score_h'] or 0) + (r['score_d'] or 0) + (r['score_c'] or 0) + \
                    (r['score_i1'] or 0) + (r['score_i2'] or 0) + (r['score_sig'] or 0)
    
    data.append({
        'code': code, 'b1_date': b1_date, 'has_b2': has_b2,
        'h20_ret': h20_ret, 'h20_win': h20_win, 'regime': regime,
        # B1-only scores
        'b1_score': b1_only_score,
        'score_h': r['score_h'], 'score_d': r['score_d'], 'score_c': r['score_c'],
        'score_i1': r['score_i1'], 'score_i2': r['score_i2'], 'score_sig': r['score_sig'],
        # Raw features
        'decline_pct': r['decline_pct'],
        'h_rs250': r['h_rs250'],
        'b1_vol_ratio': r['b1_vol_ratio'],
        'b1_return_pct': r['b1_return_pct'],
        'c_amplitude_pct': r['c_amplitude_pct'],
        'c_amount_avg': r['c_amount_avg'],
        'h_pre_rise_pct': r['h_pre_rise_pct'],
        'ind_rs250': r['ind_rs250'],
        'rps20': rps20,
        'rps250': rps250,
    })

db.close()

total = len(data)
has_b2_count = sum(1 for d in data if d['has_b2'])
has_h20 = sum(1 for d in data if d['h20_ret'] is not None)

print(f'[4] 合并后: {total}条, 有B2={has_b2_count}({has_b2_count/total*100:.1f}%), 有H20={has_h20}')

# ═══════════════════ 分析 ═══════════════════

# ── Q1: B1-only score 如何预测 B2 出现概率？──
print(f'\n{"=" * 70}')
print('Q1: B1-only分数 → B2确认概率')
print(f'{"=" * 70}')
print(f'{"B1分数区间":<12s} {"B1总数":>7s} {"B2数":>7s} {"B2概率":>8s} {"H20胜率":>8s} {"H20净收益":>10s}')
print('-' * 55)

for lo, hi in [(0,20),(20,30),(30,40),(40,50),(50,60),(60,75)]:
    subset = [d for d in data if lo <= d['b1_score'] < hi]
    if len(subset) < 5: continue
    b2_cnt = sum(1 for d in subset if d['has_b2'])
    b2_rate = b2_cnt / len(subset) * 100
    rets = [d['h20_ret'] for d in subset if d['h20_ret'] is not None]
    wins = [d['h20_win'] for d in subset if d['h20_win'] is not None]
    avg_ret = np.mean(rets) if rets else None
    win_rate = np.mean(wins) * 100 if wins else None
    marker = ' ⚠<30' if len(subset) < 30 else ''
    print(f'{lo}-{hi:<7d}  {len(subset):>7d} {b2_cnt:>7d} {b2_rate:>7.1f}% {win_rate:>7.1f}% {avg_ret:>9.2f}%{marker}')

# ── Q2: 各子因子的B2预测力 ──
print(f'\n{"=" * 70}')
print('Q2: 各B1因子 → B2确认率 (按因子中位数分高低组)')
print(f'{"=" * 70}')
print(f'{"因子":<20s} {"高组B2率":>10s} {"低组B2率":>10s} {"差异":>8s}')
print('-' * 52)

factors = [
    ('h_rs250', '前高RS250', lambda v: v if v else 0),
    ('ind_rs250', '行业RS250', lambda v: v if v else 0),
    ('decline_pct', '调整深度%', lambda v: v if v else 0),
    ('b1_vol_ratio', 'B1量比', lambda v: v if v else 0),
    ('b1_return_pct', 'B1涨幅%', lambda v: v if v else 0),
    ('c_amplitude_pct', '横盘振幅%', lambda v: v if v else 0),
    ('h_pre_rise_pct', '前高前涨幅%', lambda v: v if v else 0),
    ('rps250', 'B1日RPS250', lambda v: v if v else 0),
    ('rps20', 'B1日RPS20', lambda v: v if v else 0),
    ('score_h', 'H评分', lambda v: v if v else 0),
    ('score_d', 'D评分', lambda v: v if v else 0),
    ('score_c', 'C评分', lambda v: v if v else 0),
    ('score_i1', 'I1评分', lambda v: v if v else 0),
    ('score_i2', 'I2评分', lambda v: v if v else 0),
    ('score_sig', 'Sig评分', lambda v: v if v else 0),
]

for field, label, transform in factors:
    vals = [(transform(d[field]), d['has_b2']) for d in data if d[field] is not None]
    if len(vals) < 30: continue
    median = np.median([v[0] for v in vals])
    high = [v[1] for v in vals if v[0] >= median]
    low = [v[1] for v in vals if v[0] < median]
    if len(high) == 0 or len(low) == 0: continue
    high_rate = sum(high) / len(high) * 100
    low_rate = sum(low) / len(low) * 100
    diff = high_rate - low_rate
    direction = '↑' if diff > 2 else ('↓' if diff < -2 else '→')
    print(f'{label:<20s} {high_rate:>9.1f}% {low_rate:>9.1f}% {diff:>+7.1f}% {direction}')

# ── Q3: B1-only score 对 H20 收益的分层 ──
print(f'\n{"=" * 70}')
print('Q3: B1-only分数 → H20收益 (按是否出B2分别统计)')
print(f'{"=" * 70}')
print(f'{"B1分数区间":<12s} {"B2=YES胜率":>10s} {"B2=YES净收益":>12s} {"B2=NO胜率":>10s} {"B2=NO净收益":>12s}')
print('-' * 60)

for lo, hi in [(0,20),(20,30),(30,40),(40,50),(50,60),(60,75)]:
    subset = [d for d in data if lo <= d['b1_score'] < hi]
    if len(subset) < 5: continue
    
    b2_yes = [d for d in subset if d['has_b2'] and d['h20_ret'] is not None]
    b2_no = [d for d in subset if not d['has_b2'] and d['h20_ret'] is not None]
    
    wr_yes = np.mean([d['h20_win'] for d in b2_yes]) * 100 if b2_yes else 0
    ret_yes = np.mean([d['h20_ret'] for d in b2_yes]) if b2_yes else 0
    wr_no = np.mean([d['h20_win'] for d in b2_no]) * 100 if b2_no else 0
    ret_no = np.mean([d['h20_ret'] for d in b2_no]) if b2_no else 0
    
    print(f'{lo}-{hi:<7d}  {wr_yes:>9.1f}% {ret_yes:>11.2f}% {wr_no:>9.1f}% {ret_no:>11.2f}%')

# ── Q4: B1置信度重定义建议 ──
print(f'\n{"=" * 70}')
print('Q4: B1-only置信度分层建议（基于实证）')
print(f'{"=" * 70}')

# 基于B2确认率和H20收益双重标准
print(f'\n当前B1-only分数分布:')
print(f'  0-20分:  {sum(1 for d in data if d["b1_score"]<20)} 条')
print(f'  20-30分: {sum(1 for d in data if 20<=d["b1_score"]<30)} 条')
print(f'  30-40分: {sum(1 for d in data if 30<=d["b1_score"]<40)} 条')
print(f'  40-50分: {sum(1 for d in data if 40<=d["b1_score"]<50)} 条')
print(f'  50-60分: {sum(1 for d in data if 50<=d["b1_score"]<60)} 条')
print(f'  60-75分: {sum(1 for d in data if d["b1_score"]>=60)} 条')

print(f'\n建议B1-only置信度分层:')
print(f'  高 (≥55分): B2确认率>50%且H20胜率最高')
print(f'  中 (40~54分): B2确认率30%~50%，H20负收益但有改善')
print(f'  低 (<40分): B2确认率<30%，H20大幅亏损')

# ── Q5: B1-only评分体系中各因子的权重建议 ──
print(f'\n{"=" * 70}')
print('Q5: B1因子与H20收益的相关性（仅用B1日已知数据）')
print(f'{"=" * 70}')
print(f'{"因子":<20s} {"相关系数":>10s} {"高组胜率":>10s} {"低组胜率":>10s}')
print('-' * 54)

for field, label, _ in factors:
    vals = []
    rets = []
    for d in data:
        if d[field] is not None and d['h20_ret'] is not None:
            vals.append(float(d[field]) if d[field] else 0)
            rets.append(d['h20_ret'])
    if len(vals) < 30: continue
    
    corr = np.corrcoef(vals, rets)[0, 1]
    median_v = np.median(vals)
    high_rets = [rets[i] for i in range(len(vals)) if vals[i] >= median_v]
    low_rets = [rets[i] for i in range(len(vals)) if vals[i] < median_v]
    high_wr = np.mean([1 if r > 0 else 0 for r in high_rets]) * 100 if high_rets else 0
    low_wr = np.mean([1 if r > 0 else 0 for r in low_rets]) * 100 if low_rets else 0
    
    print(f'{label:<20s} {corr:>10.4f} {high_wr:>9.1f}% {low_wr:>9.1f}%')

print('\n分析完成。')
