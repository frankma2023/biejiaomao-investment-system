"""
MW 信号全维度分层回测报告
━━━━━━━━━━━━━━━━━━━━━
维度：B1置信度(B1-only) × B2置信度(B1+B2) × PLUS × 入场方式 × 持有期 × 市场环境
"""
import sqlite3, json
from collections import defaultdict
import numpy as np

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 80)
print('MW 信号全维度分层回测报告')
print(f'期间: 2024-01-01 ~ 2026-06-22 | 成本: 0.3% | 价格: 前复权')
print('=' * 80)

# ── 加载数据 ──
mw = {}
for r in c.execute("""
    SELECT stock_code, b1_date, b2_date, score, confidence, is_plus,
           score_h, score_d, score_c, score_i1, score_i2, score_sig, score_p, score_gap,
           decline_pct, h_rs250, b1_vol_ratio, b1_return_pct, b2_return_pct, b2_is_gap
    FROM mw_signal_daily WHERE stock_code!='_sentinel_'
      AND b1_date >= '2024-01-01' AND b1_date <= '2026-06-22'
"""):
    mw[(r['stock_code'], r['b1_date'])] = r

bt = {}
for r in c.execute("""
    SELECT stock_code, signal_date as b1_date, combo_label, entry_method, hold_days,
           market_regime, net_ret_pct, is_win, ret_pct, excess_ret_pct,
           peak_ret_pct, trough_ret_pct
    FROM backtest_results
    WHERE combo_label IN ('MW_B1','MW_B2','MW_PLUS')
      AND pool_mode='full'
      AND signal_date >= '2024-01-01' AND signal_date <= '2026-06-22'
"""):
    bt[(r['stock_code'], r['b1_date'], r['entry_method'], r['hold_days'])] = r

db.close()

# ── 合并 ──
data = []
for (code, b1d), m in mw.items():
    for em in ['T+0_C', 'T+1_O', 'T+2_O']:
        for hd in [5, 10, 20, 60]:
            b = bt.get((code, b1d, em, hd))
            if b:
                data.append({
                    'code': code, 'b1_date': b1d,
                    'is_b1_only': m['b2_date'] is None,
                    'has_b2': m['b2_date'] is not None,
                    'is_plus': m['is_plus'] == 1,
                    'b1_confidence': m['confidence'],
                    'score': m['score'],
                    'entry_method': em, 'hold_days': hd,
                    'market_regime': b['market_regime'],
                    'net_ret': b['net_ret_pct'], 'is_win': b['is_win'],
                    'excess_ret': b['excess_ret_pct'],
                    'combo_label': b['combo_label'],
                })

print(f'\n合并后总记录: {len(data):,}')

def stats(items, label=''):
    if not items: return None
    n = len(items)
    rets = np.array([d['net_ret'] for d in items])
    wins = np.array([d['is_win'] for d in items])
    pos = rets[rets > 0]
    neg = rets[rets < 0]
    
    wr = wins.mean()
    avg = rets.mean()
    med = np.median(rets)
    std = rets.std()
    plr = pos.mean() / abs(neg.mean()) if len(neg) > 0 and neg.mean() != 0 else 0
    kelly = max(0, wr - (1-wr)/plr) if plr > 0 else 0
    excess = np.mean([d['excess_ret'] for d in items])
    
    # Top/Bottom
    sorted_rets = sorted(rets)
    worst_1pct = np.mean(sorted_rets[:max(1, int(n*0.01))])
    best_1pct = np.mean(sorted_rets[-max(1, int(n*0.01)):])
    
    return {
        'n': n, 'wr': wr*100, 'avg': avg, 'med': med, 'std': std,
        'plr': plr, 'kelly': kelly*100, 'excess': excess,
        'worst1': worst_1pct, 'best1': best_1pct,
    }

def print_row(tier, st, extra=''):
    if st is None: return
    marker = ' ⚠' if st['n'] < 30 else (' ~' if st['n'] < 100 else '  ')
    print(f'  {tier:<22s} {st["n"]:>7,d}  {st["wr"]:>6.1f}%  {st["avg"]:>7.2f}%  {st["med"]:>7.2f}%  {st["plr"]:>5.2f}  {st["kelly"]:>5.1f}%  {st["excess"]:>7.2f}%{marker}{extra}')

# ═══════════════════ 1. B1-only by confidence ═══════════════════
print(f'\n{"─"*80}')
print('一、B1-only 按置信度分层（H20 / T+1_O）')
print(f'{"─"*80}')
print(f'  {"置信度":<12s} {"B1-only分":>8s} {"样本":>7s} {"胜率":>7s} {"净收益":>8s} {"盈亏比":>7s} {"凯利":>7s}')
print(f'  {"─"*60}')

b1_only = [d for d in data if d['is_b1_only'] and d['hold_days']==20 and d['entry_method']=='T+1_O']
for conf in ['高', '中', '低']:
    subset = [d for d in b1_only if d['b1_confidence'] == conf]
    st = stats(subset)
    if st:
        print(f'  {conf:<12s} {"≥55/40~54/<40":>8s} {st["n"]:>7,d}  {st["wr"]:>6.1f}%  {st["avg"]:>7.2f}%  {st["plr"]:>6.2f}  {st["kelly"]:>6.1f}%')

# ── B1-only all hold periods ──
print(f'\n  B1-only 各持有期 vs 置信度:')
print(f'  {"持有期":<6s} {"置信度":<6s} {"样本":>7s} {"胜率":>7s} {"净收益":>8s} {"盈亏比":>7s}')
for hd in [5, 10, 20, 60]:
    for conf in ['高', '中', '低']:
        subset = [d for d in data if d['is_b1_only'] and d['hold_days']==hd and d['entry_method']=='T+1_O' and d['b1_confidence']==conf]
        st = stats(subset)
        if st and st['n'] >= 5:
            print(f'  H{hd:<4d} {conf:<6s} {st["n"]:>7,d}  {st["wr"]:>6.1f}%  {st["avg"]:>7.2f}%  {st["plr"]:>6.2f}')

# ═══════════════════ 2. B1+B2 by confidence ═══════════════════
print(f'\n{"─"*80}')
print('二、B1+B2 按置信度分层（H20 / T+1_O）')
print(f'{"─"*80}')
print(f'  {"置信度":<12s} {"样本":>7s} {"胜率":>7s} {"净收益":>8s} {"超额":>7s} {"盈亏比":>7s} {"凯利":>7s} {"最好1%":>8s} {"最差1%":>8s}')
print(f'  {"─"*85}')

b1b2 = [d for d in data if d['has_b2'] and d['hold_days']==20 and d['entry_method']=='T+1_O']
for conf in ['高', '中', '低']:
    subset = [d for d in b1b2 if d['b1_confidence'] == conf]
    st = stats(subset)
    if st:
        print(f'  {conf:<12s} {st["n"]:>7,d}  {st["wr"]:>6.1f}%  {st["avg"]:>7.2f}%  {st["excess"]:>6.2f}%  {st["plr"]:>6.2f}  {st["kelly"]:>6.1f}%  {st["best1"]:>7.2f}%  {st["worst1"]:>7.2f}%')

# ── B1+B2 all hold periods ──
print(f'\n  B1+B2 各持有期 vs 置信度:')
print(f'  {"持有期":<6s} {"置信度":<6s} {"样本":>7s} {"胜率":>7s} {"净收益":>8s} {"盈亏比":>7s} {"凯利":>7s}')
for hd in [5, 10, 20, 60]:
    for conf in ['高', '中', '低']:
        subset = [d for d in data if d['has_b2'] and d['hold_days']==hd and d['entry_method']=='T+1_O' and d['b1_confidence']==conf]
        st = stats(subset)
        if st and st['n'] >= 5:
            print(f'  H{hd:<4d} {conf:<6s} {st["n"]:>7,d}  {st["wr"]:>6.1f}%  {st["avg"]:>7.2f}%  {st["plr"]:>6.2f}  {st["kelly"]:>6.1f}%')

# ═══════════════════ 3. PLUS ═══════════════════
print(f'\n{"─"*80}')
print('三、PLUS vs 非PLUS 对比（B1+B2信号，H20 / T+1_O）')
print(f'{"─"*80}')
plus = [d for d in b1b2 if d['is_plus']]
non_plus = [d for d in b1b2 if not d['is_plus']]

sp = stats(plus)
snp = stats(non_plus)
print(f'  {"PLUS":<12s} {sp["n"]:>7,d}  {sp["wr"]:>6.1f}%  {sp["avg"]:>7.2f}%  {sp["excess"]:>6.2f}%  {sp["plr"]:>6.2f}  {sp["kelly"]:>6.1f}%  {sp["best1"]:>7.2f}%  {sp["worst1"]:>7.2f}%')
print(f'  {"非PLUS":<12s} {snp["n"]:>7,d}  {snp["wr"]:>6.1f}%  {snp["avg"]:>7.2f}%  {snp["excess"]:>6.2f}%  {snp["plr"]:>6.2f}  {snp["kelly"]:>6.1f}%  {snp["best1"]:>7.2f}%  {snp["worst1"]:>7.2f}%')

# PLUS by hold periods
print(f'\n  PLUS 各持有期:')
for hd in [5, 10, 20, 60]:
    subset = [d for d in data if d['is_plus'] and d['hold_days']==hd and d['entry_method']=='T+1_O']
    st = stats(subset)
    if st and st['n'] >= 3:
        print(f'  H{hd:<4d} {st["n"]:>7,d}  {st["wr"]:>6.1f}%  {st["avg"]:>7.2f}%  {st["kelly"]:>6.1f}%')

# ═══════════════════ 4. 双重分层 ═══════════════════
print(f'\n{"─"*80}')
print('四、B1置信度 × B2置信度 双重分层（B1+B2信号，H20 / T+1_O）')
print(f'{"─"*80}')
print(f'  {"B1\\B2":<12s} {"高":>10s} {"中":>10s} {"低":>10s}')
print(f'  {"─"*35}')

for b1conf in ['高', '中', '低']:
    row_parts = [f'  {b1conf:<12s}']
    for b2conf in ['高', '中', '低']:
        # Note: B2 confidence field - for B1+B2 signals the full score is used
        # But we need the B2 confidence from the MW table
        subset = []
        for d in b1b2:
            if d['b1_confidence'] != b1conf: continue
            m = mw.get((d['code'], d['b1_date']))
            if m and m['confidence'] == b2conf:
                subset.append(d)
        st = stats(subset)
        if st and st['n'] >= 3:
            row_parts.append(f'{st["n"]:>3d}/{st["wr"]:.0f}%/{st["avg"]:.1f}%')
        else:
            row_parts.append(f'{"—":>10s}')
    print(' '.join(row_parts))

# ═══════════════════ 5. 入场方式对比 ═══════════════════
print(f'\n{"─"*80}')
print('五、入场方式对比（B1+B2信号，H20，按置信度）')
print(f'{"─"*80}')
print(f'  {"置信度":<8s} {"T+0_C":>12s} {"T+1_O":>12s} {"T+2_O":>12s}')
print(f'  {"─"*48}')
for conf in ['高', '中', '低']:
    row = f'  {conf:<8s}'
    for em in ['T+0_C', 'T+1_O', 'T+2_O']:
        subset = [d for d in data if d['has_b2'] and d['hold_days']==20 and d['entry_method']==em and d['b1_confidence']==conf]
        st = stats(subset)
        if st:
            row += f'  wr={st["wr"]:.0f}% av={st["avg"]:.1f}%'
        else:
            row += f'  {"—":>12s}'
    print(row)

# ═══════════════════ 6. 市场环境 ═══════════════════
print(f'\n{"─"*80}')
print('六、市场环境分层（B1+B2，H20 / T+1_O）')
print(f'{"─"*80}')
print(f'  {"环境":<10s} {"高置信":>10s} {"中置信":>10s} {"低置信":>10s} {"PLUS":>10s}')
print(f'  {"─"*52}')

for regime in ['bull', 'ranging', 'bear']:
    row = f'  {regime:<10s}'
    for filter_key, filter_fn in [
        ('高', lambda d: d['b1_confidence']=='高'),
        ('中', lambda d: d['b1_confidence']=='中'),
        ('低', lambda d: d['b1_confidence']=='低'),
        ('PLUS', lambda d: d['is_plus']),
    ]:
        subset = [d for d in data if d['has_b2'] and d['hold_days']==20 and d['entry_method']=='T+1_O' and d['market_regime']==regime and filter_fn(d)]
        st = stats(subset)
        if st and st['n'] >= 3:
            row += f'{st["n"]:>3d}/{st["wr"]:.0f}% '
        else:
            row += f'{"—":>10s}'
    print(row)

# ═══════════════════ 7. 汇总矩阵 ═══════════════════
print(f'\n{"─"*80}')
print('七、最终汇总：MW信号投资决策矩阵（H20 / T+1_O）')
print(f'{"─"*80}')
print(f'  {"信号类型":<18s} {"置信度":<8s} {"样本":>7s} {"胜率":>7s} {"净收益":>8s} {"超额":>7s} {"盈亏比":>7s} {"凯利":>7s}')
print(f'  {"─"*78}')

# B1-only
print(f'  {"── B1-only (无B2) ──":<18s}')
for conf in ['高', '中', '低']:
    subset = [d for d in data if d['is_b1_only'] and d['hold_days']==20 and d['entry_method']=='T+1_O' and d['b1_confidence']==conf]
    st = stats(subset)
    if st: print_row(f'  B1-only', st, f' ({conf})' if conf else '')

# B1+B2
print(f'  {"── B1+B2 (有确认) ──":<18s}')
for conf in ['高', '中', '低']:
    subset = [d for d in data if d['has_b2'] and d['hold_days']==20 and d['entry_method']=='T+1_O' and d['b1_confidence']==conf]
    st = stats(subset)
    if st: print_row(f'  B1+B2', st, f' ({conf})')

# PLUS
print(f'  {"── PLUS ──":<18s}')
subset = [d for d in data if d['is_plus'] and d['hold_days']==20 and d['entry_method']=='T+1_O']
st = stats(subset)
if st: print_row(f'  PLUS', st)

print(f'\n报告完成。')
