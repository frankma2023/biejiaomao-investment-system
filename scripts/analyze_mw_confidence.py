"""
MW 信号置信度/评分分层分析
━━━━━━━━━━━━━━━━━━━━━
B1-only: 用 score 分桶（0-20/20-40/40-60/60-75），不做confidence
B1+B2:  用 confidence（高/中/低）分桶
对比各层在H20持有期下的表现
"""
import sqlite3, json
from collections import defaultdict, Counter
import numpy as np

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 70)
print('MW 信号分层回测（H20 / T+1_O / 2024-2026）')
print('=' * 70)

# ── 加载 backtest_results for MW signals ──
rows = c.execute("""
    SELECT br.stock_code, br.signal_date as b1_date, br.net_ret_pct, br.is_win,
           br.combo_label, br.market_regime, br.excess_ret_pct, br.ret_pct
    FROM backtest_results br
    WHERE br.hold_days=20 AND br.entry_method='T+1_O' AND br.pool_mode='full'
      AND br.signal_date >= '2024-01-01' AND br.signal_date <= '2026-06-22'
      AND br.combo_label IN ('MW_B1','MW_B2','MW_PLUS')
""").fetchall()
print(f'\nbacktest_results: {len(rows)} 条')

# ── 加载 MW 信号细节 ──
mw_rows = c.execute("""
    SELECT stock_code, b1_date, b2_date, score, score_v2, confidence, confidence_v2,
           is_plus, decline_pct, h_rs250, h_rs20, b1_vol_ratio, b1_return_pct,
           b2_return_pct, b2_is_gap, score_h, score_d, score_c, score_p,
           score_i1, score_i2, score_sig, score_gap, ind_rs250, ind_rs20,
           ind_code, ind_name, c_amplitude_pct, c_amount_avg
    FROM mw_signal_daily
    WHERE b1_date >= '2024-01-01' AND b1_date <= '2026-06-22'
      AND stock_code != '_sentinel_'
""").fetchall()
mw_dict = {(r['stock_code'], r['b1_date']): r for r in mw_rows}
print(f'mw_signal_daily: {len(mw_dict)} 条')

db.close()

# ── 合并数据 ──
merged = []
for r in rows:
    mw = mw_dict.get((r['stock_code'], r['b1_date']))
    if mw:
        merged.append({**r, **{f'mw_{k}': mw[k] for k in mw.keys()}})

print(f'合并后: {len(merged)} 条\n')

# ── 分类 ──
def score_bucket(score):
    if score is None: return 'N/A'
    if score <= 20: return '0-20'
    if score <= 40: return '20-40'
    if score <= 60: return '40-60'
    return '60-75'

b1_only = [m for m in merged if m['mw_b2_date'] is None]
b1_b2 = [m for m in merged if m['mw_b2_date'] is not None]
mw_plus = [m for m in merged if m['mw_is_plus'] == 1]

print(f'B1-only: {len(b1_only)}')
print(f'B1+B2:  {len(b1_b2)}')
print(f'MW PLUS: {len(mw_plus)}')

# ── B1-only 按 score 分桶 ──
print(f'\n{"=" * 70}')
print('B1-only 按 score 分桶（无B2确认，H20持有）')
print(f'{"=" * 70}')
buckets = defaultdict(list)
for m in b1_only:
    buckets[score_bucket(m['mw_score'])].append(m)

print(f'{"Score桶":<10s} {"样本":>6s} {"胜率":>8s} {"净收益":>8s} {"超额":>8s} {"中位":>8s} {"盈亏比":>8s}')
print('-' * 65)
for bucket in ['0-20', '20-40', '40-60', '60-75']:
    items = buckets[bucket]
    if not items: continue
    n = len(items)
    wr = np.mean([m['is_win'] for m in items]) * 100
    net = np.mean([m['net_ret_pct'] for m in items])
    med = np.median([m['net_ret_pct'] for m in items])
    excess = np.mean([m['excess_ret_pct'] for m in items])
    pos = [m['net_ret_pct'] for m in items if m['net_ret_pct'] > 0]
    neg = [m['net_ret_pct'] for m in items if m['net_ret_pct'] < 0]
    plr = np.mean(pos) / abs(np.mean(neg)) if neg else 0
    print(f'{bucket:<10s} {n:>6d} {wr:>7.1f}% {net:>7.2f}% {excess:>7.2f}% {med:>7.2f}% {plr:>7.2f}')

# ── B1+B2 按 confidence 分桶 ──
print(f'\n{"=" * 70}')
print('B1+B2 按 confidence 分桶（有B2确认，H20持有）')
print(f'{"=" * 70}')
conf_buckets = defaultdict(list)
for m in b1_b2:
    conf_buckets[m['mw_confidence']].append(m)

print(f'{"置信度":<8s} {"样本":>6s} {"胜率":>8s} {"净收益":>8s} {"超额":>8s} {"中位":>8s} {"盈亏比":>8s} {"B2收益":>8s}')
print('-' * 75)
for conf in ['高', '中', '低']:
    items = conf_buckets.get(conf, [])
    if not items: continue
    n = len(items)
    wr = np.mean([m['is_win'] for m in items]) * 100
    net = np.mean([m['net_ret_pct'] for m in items])
    med = np.median([m['net_ret_pct'] for m in items])
    excess = np.mean([m['excess_ret_pct'] for m in items])
    pos = [m['net_ret_pct'] for m in items if m['net_ret_pct'] > 0]
    neg = [m['net_ret_pct'] for m in items if m['net_ret_pct'] < 0]
    plr = np.mean(pos) / abs(np.mean(neg)) if neg else 0
    b2rets = [m['mw_b2_return_pct'] for m in items if m['mw_b2_return_pct'] is not None]
    b2ret_avg = np.mean(b2rets) if b2rets else 0
    print(f'{conf:<8s} {n:>6d} {wr:>7.1f}% {net:>7.2f}% {excess:>7.2f}% {med:>7.2f}% {plr:>7.2f} {b2ret_avg:>7.2f}%')

# ── 双重分层：B1 score × B2 confidence ──
print(f'\n{"=" * 70}')
print('双重分层: B1 score桶 × B2 confidence (H20持有)')
print(f'{"=" * 70}')
print(f'{"B1Score":<8s} {"Conf":<8s} {"样本":>6s} {"胜率":>8s} {"净收益":>8s} {"盈亏比":>8s}')
print('-' * 55)
for bucket in ['0-20', '20-40', '40-60', '60-75']:
    for conf in ['高', '中', '低']:
        items = [m for m in b1_b2 if score_bucket(m['mw_score']) == bucket and m['mw_confidence'] == conf]
        if len(items) < 5: continue
        n = len(items)
        wr = np.mean([m['is_win'] for m in items]) * 100
        net = np.mean([m['net_ret_pct'] for m in items])
        pos = [m['net_ret_pct'] for m in items if m['net_ret_pct'] > 0]
        neg = [m['net_ret_pct'] for m in items if m['net_ret_pct'] < 0]
        plr = np.mean(pos) / abs(np.mean(neg)) if neg else 0
        marker = ' ⚠<30' if n < 30 else ''
        print(f'{bucket:<8s} {conf:<8s} {n:>6d} {wr:>7.1f}% {net:>7.2f}% {plr:>7.2f}{marker}')

# ── MW PLUS ──
if mw_plus:
    print(f'\n{"=" * 70}')
    print(f'MW PLUS (is_plus=1, H20持有)')
    print(f'{"=" * 70}')
    n = len(mw_plus)
    wr = np.mean([m['is_win'] for m in mw_plus]) * 100
    net = np.mean([m['net_ret_pct'] for m in mw_plus])
    med = np.median([m['net_ret_pct'] for m in mw_plus])
    excess = np.mean([m['excess_ret_pct'] for m in mw_plus])
    print(f'  样本={n} 胜率={wr:.1f}% 净收益={net:.2f}% 中位={med:.2f}% 超额={excess:.2f}%')

# ── 评分子项分解 ──
print(f'\n{"=" * 70}')
print('评分子项与H20收益的相关性（B1+B2信号）')
print(f'{"=" * 70}')
score_fields = [
    ('score_h', 'H前高趋势'), ('score_d', 'D调整深度'), ('score_c', 'C横盘质量'),
    ('score_p', 'P整理回撤'), ('score_i1', 'I1行业RS'), ('score_i2', 'I2个股RS'),
    ('score_sig', 'Sig信号共振'), ('score_gap', 'Gap跳空'),
    ('decline_pct', '调整深度(%)'), ('h_rs250', '前高RS250'),
    ('b1_vol_ratio', 'B1量比'), ('b1_return_pct', 'B1涨幅(%)'),
    ('b2_return_pct', 'B2涨幅(%)'), ('b2_is_gap', 'B2跳空'),
]
print(f'{"因子":<20s} {"与净收益相关性":>15s} {"高分组胜率":>12s} {"低分组胜率":>12s}')
print('-' * 62)
for field, label in score_fields:
    vals = []
    rets = []
    for m in b1_b2:
        v = m.get(f'mw_{field}')
        if v is None: continue
        vals.append(v)
        rets.append(m['net_ret_pct'])
    if len(vals) < 30: continue
    
    corr = np.corrcoef(vals, rets)[0, 1]
    
    # 高分组 vs 低分组
    median_v = np.median(vals)
    high = [rets[i] for i in range(len(vals)) if vals[i] >= median_v]
    low = [rets[i] for i in range(len(vals)) if vals[i] < median_v]
    high_wr = np.mean([1 if r > 0 else 0 for r in high]) * 100 if high else 0
    low_wr = np.mean([1 if r > 0 else 0 for r in low]) * 100 if low else 0
    
    print(f'{label:<20s} {corr:>15.4f} {high_wr:>11.1f}% {low_wr:>11.1f}%')

print('\n分析完成。')
