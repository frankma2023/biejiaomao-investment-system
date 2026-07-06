"""
MW信号全维度回测 v2
━━━━━━━━━━━━━━━━━
维度：
  1. B1+B2 组合表现 (已有)
  2. B1为T日，T+1/T+2/T+3 买入
  3. B2为T日，T+1/T+2/T+3 买入
  4. B1前后窗口共现PP_V1/PP_V2/BO_V2 → 胜率影响
  5. B2前后窗口共现PP_V1/PP_V2/BO_V2 → 胜率影响
  6. 市场环境分层
  7. 行业RS分层
"""
import sqlite3
from collections import defaultdict
import numpy as np

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 85)
print('MW 信号全维度回测 v2')
print('=' * 85)

# ═══════════════ 加载数据 ═══════════════
# MW signals
mw = {}
for r in c.execute("""
    SELECT stock_code, b1_date, b2_date, score, confidence, is_plus,
           h_rs250, ind_rs250, ind_code, ind_name, decline_pct
    FROM mw_signal_daily WHERE stock_code!='_sentinel_'
      AND b1_date >= '2024-01-01' AND b1_date <= '2026-06-22'
"""):
    mw[(r['stock_code'], r['b1_date'])] = r

# Backtest results - MW_B1 and MW_B2
bt_b1 = defaultdict(list)
bt_b2 = defaultdict(list)
for r in c.execute("""
    SELECT stock_code, signal_date, combo_label, entry_method, hold_days,
           market_regime, net_ret_pct, is_win, ret_pct, excess_ret_pct
    FROM backtest_results
    WHERE combo_label IN ('MW_B1','MW_B2') AND pool_mode='full'
      AND signal_date >= '2024-01-01' AND signal_date <= '2026-06-22'
"""):
    key = (r['stock_code'], r['signal_date'], r['entry_method'], r['hold_days'])
    if r['combo_label'] == 'MW_B1':
        bt_b1[key] = r
    else:
        bt_b2[key] = r

# Signal events for co-occurrence
sig_events = defaultdict(lambda: {'mask': 0, 'pp_v1': False, 'pp_v2': False, 'bo_v2': False})
for r in c.execute("""
    SELECT stock_code, date, signal_mask FROM signal_events
    WHERE date >= '2023-12-20' AND date <= '2026-06-25'
"""):
    mask = r['signal_mask']
    sig_events[(r['stock_code'], r['date'])] = {
        'mask': mask,
        'pp_v1': bool(mask & (1 << 3)),
        'pp_v2': bool(mask & (1 << 4)),
        'bo_v2': bool(mask & (1 << 5)),
    }

# Industry RS
ind_rs = {}
for r in c.execute("""
    SELECT stock_code as idx_code, date, rs_250 FROM index_rs_daily
    WHERE date >= '2024-01-01' AND date <= '2026-06-22'
"""):
    ind_rs[(r['idx_code'], r['date'])] = r['rs_250']

# Trading dates for T+3 calc
trading_dates = [r[0] for r in c.execute("""
    SELECT DISTINCT date FROM daily_kline 
    WHERE date >= '2024-01-01' AND date <= '2026-07-20' ORDER BY date
""")]
date_idx = {d: i for i, d in enumerate(trading_dates)}

# K-line for T+3 pricing
kline = {}
for r in c.execute("""
    SELECT stock_code, date, adj_open, adj_close FROM daily_kline
    WHERE date >= '2024-01-01' AND date <= '2026-07-20'
"""):
    kline[(r['stock_code'], r['date'])] = (r['adj_open'], r['adj_close'])

db.close()

COST = 0.003

def stats(items):
    if not items: return None
    n = len(items)
    rets = np.array(items)
    wins = rets > 0
    pos = rets[rets > 0]
    neg = rets[rets < 0]
    wr = wins.mean() * 100
    avg = rets.mean()
    med = np.median(rets)
    plr = pos.mean() / abs(neg.mean()) if len(neg) > 0 and neg.mean() != 0 else 0
    kelly = max(0, wr/100 - (1-wr/100)/plr) * 100 if plr > 0 else 0
    return {'n': n, 'wr': wr, 'avg': avg, 'med': med, 'plr': plr, 'kelly': kelly}

def pr(tier, st, w=22):
    if st is None: return
    mark = ' ⚠' if st['n'] < 30 else (' ~' if st['n'] < 100 else '  ')
    print(f'  {tier:<{w}s} {st["n"]:>6,d}  {st["wr"]:>6.1f}%  {st["avg"]:>7.2f}%  {st["plr"]:>5.2f}  {st["kelly"]:>5.1f}%{mark}')

# ═══════════════ 1. B1+B2 组合表现 ═══════════════
print(f'\n{"─"*85}')
print('一、B1+B2 组合表现（H20/T+1_O，按置信度）')
print(f'{"─"*85}')
print(f'  {"类型":<22s} {"样本":>7s} {"胜率":>7s} {"净收益":>8s} {"盈亏比":>7s} {"凯利":>7s}')
print(f'  {"─"*60}')

for conf in ['高', '中', '低']:
    rets = []
    for (code, b1d), m in mw.items():
        if m['confidence'] != conf or m['b2_date'] is None:
            continue
        b = bt_b1.get((code, b1d, 'T+1_O', 20))
        if b:
            rets.append(b['net_ret_pct'])
    st = stats(rets)
    pr(f'B1+B2 ({conf}置信)', st)

plus_rets = []
for (code, b1d), m in mw.items():
    if m['is_plus'] != 1 or m['b2_date'] is None:
        continue
    b = bt_b1.get((code, b1d, 'T+1_O', 20))
    if b:
        plus_rets.append(b['net_ret_pct'])
pr('B1+B2 (PLUS)', stats(plus_rets))

# ═══════════════ 2. B1为T日，T+1/T+2/T+3买入 ═══════════════
print(f'\n{"─"*85}')
print('二、B1=T日，延时买入胜率（H20，B1+B2信号，按置信度）')
print(f'{"─"*85}')
print(f'  {"置信度":<8s} {"T+1_O":>15s} {"T+2_O":>15s} {"T+3_O":>15s}')
print(f'  {"─"*58}')

for conf in ['高', '中', '低']:
    row = f'  {conf:<8s}'
    for offset, em in [(1, 'T+1_O'), (2, 'T+2_O')]:
        rets = []
        for (code, b1d), m in mw.items():
            if m['confidence'] != conf or m['b2_date'] is None:
                continue
            b = bt_b1.get((code, b1d, em, 20))
            if b:
                rets.append(b['net_ret_pct'])
        st = stats(rets)
        if st:
            row += f'  {st["wr"]:.0f}%/{st["avg"]:.1f}% n={st["n"]}'
        else:
            row += f'  {"—":>15s}'
    
    # T+3: compute manually
    t3_rets = []
    for (code, b1d), m in mw.items():
        if m['confidence'] != conf or m['b2_date'] is None:
            continue
        idx = date_idx.get(b1d)
        if idx is None or idx + 3 >= len(trading_dates):
            continue
        entry_date = trading_dates[idx + 3]
        exit_idx = idx + 3 + 19  # H20
        if exit_idx >= len(trading_dates):
            continue
        exit_date = trading_dates[exit_idx]
        entry_kl = kline.get((code, entry_date))
        exit_kl = kline.get((code, exit_date))
        if entry_kl and exit_kl and entry_kl[0] and exit_kl[1]:
            ret = (exit_kl[1] - entry_kl[0]) / entry_kl[0] - COST
            t3_rets.append(ret * 100)
    st3 = stats(t3_rets)
    if st3:
        row += f'  {st3["wr"]:.0f}%/{st3["avg"]:.1f}% n={st3["n"]}'
    print(row)

# ═══════════════ 3. B2为T日，T+1/T+2/T+3买入 ═══════════════
print(f'\n{"─"*85}')
print('三、B2=T日，延时买入胜率（H20，按置信度）')
print(f'{"─"*85}')
print(f'  {"置信度":<8s} {"T+1_O(B2)":>15s} {"T+2_O(B2)":>15s} {"T+3_O(B2)":>15s}')
print(f'  {"─"*58}')

for conf in ['高', '中', '低']:
    row = f'  {conf:<8s}'
    for offset, em in [(1, 'T+1_O'), (2, 'T+2_O')]:
        rets = []
        for (code, b1d), m in mw.items():
            if m['confidence'] != conf or m['b2_date'] is None:
                continue
            b2d = m['b2_date']
            b = bt_b2.get((code, b2d, em, 20))
            if b:
                rets.append(b['net_ret_pct'])
        st = stats(rets)
        if st:
            row += f'  {st["wr"]:.0f}%/{st["avg"]:.1f}% n={st["n"]}'
        else:
            row += f'  {"—":>15s}'
    
    # T+3 for B2
    t3_rets = []
    for (code, b1d), m in mw.items():
        if m['confidence'] != conf or m['b2_date'] is None:
            continue
        b2d = m['b2_date']
        idx = date_idx.get(b2d)
        if idx is None or idx + 3 >= len(trading_dates):
            continue
        entry_date = trading_dates[idx + 3]
        exit_idx = idx + 3 + 19
        if exit_idx >= len(trading_dates):
            continue
        exit_date = trading_dates[exit_idx]
        entry_kl = kline.get((code, entry_date))
        exit_kl = kline.get((code, exit_date))
        if entry_kl and exit_kl and entry_kl[0] and exit_kl[1]:
            ret = (exit_kl[1] - entry_kl[0]) / entry_kl[0] - COST
            t3_rets.append(ret * 100)
    st3 = stats(t3_rets)
    if st3:
        row += f'  {st3["wr"]:.0f}%/{st3["avg"]:.1f}% n={st3["n"]}'
    print(row)

# ═══════════════ 4. B1共现信号 ═══════════════
print(f'\n{"─"*85}')
print('四、B1=T日，前后窗口共现买入信号对胜率的影响（B1+B2，H20/T+1_O）')
print(f'{"─"*85}')
print(f'  {"窗口":<12s} {"基准(无共现)":>20s} {"+PP_V1":>20s} {"+PP_V2":>20s} {"+BO_V2":>20s}')
print(f'  {"─"*92}')

# Baseline: B1+B2 signals WITHOUT co-occurrence
from datetime import datetime, timedelta

for window_days in [3, 5, 10]:
    base_rets = []
    ppv1_rets = {'before': [], 'same': [], 'after': [], 'any': []}
    ppv2_rets = {'before': [], 'same': [], 'after': [], 'any': []}
    bov2_rets = {'before': [], 'same': [], 'after': [], 'any': []}
    
    for (code, b1d), m in mw.items():
        if m['b2_date'] is None:  # B1-only skip
            continue
        b = bt_b1.get((code, b1d, 'T+1_O', 20))
        if b is None: continue
        
        b1dt = datetime.strptime(b1d, '%Y-%m-%d')
        has_ppv1 = has_ppv2 = has_bov2 = False
        ppv1_before = ppv1_same = ppv1_after = False
        ppv2_before = ppv2_same = ppv2_after = False
        bov2_before = bov2_same = bov2_after = False
        
        for offset in range(-window_days, window_days + 1):
            d = (b1dt + timedelta(days=offset)).strftime('%Y-%m-%d')
            se = sig_events.get((code, d))
            if se is None: continue
            
            if se['pp_v1']:
                has_ppv1 = True
                if offset < 0: ppv1_before = True
                elif offset == 0: ppv1_same = True
                else: ppv1_after = True
            if se['pp_v2']:
                has_ppv2 = True
                if offset < 0: ppv2_before = True
                elif offset == 0: ppv2_same = True
                else: ppv2_after = True
            if se['bo_v2']:
                has_bov2 = True
                if offset < 0: bov2_before = True
                elif offset == 0: bov2_same = True
                else: bov2_after = True
        
        ret = b['net_ret_pct']
        if not has_ppv1 and not has_ppv2 and not has_bov2:
            base_rets.append(ret)
        if ppv1_before: ppv1_rets['before'].append(ret)
        if ppv1_same: ppv1_rets['same'].append(ret)
        if ppv1_after: ppv1_rets['after'].append(ret)
        if has_ppv1: ppv1_rets['any'].append(ret)
        if ppv2_before: ppv2_rets['before'].append(ret)
        if ppv2_same: ppv2_rets['same'].append(ret)
        if ppv2_after: ppv2_rets['after'].append(ret)
        if has_ppv2: ppv2_rets['any'].append(ret)
        if bov2_before: bov2_rets['before'].append(ret)
        if bov2_same: bov2_rets['same'].append(ret)
        if bov2_after: bov2_rets['after'].append(ret)
        if has_bov2: bov2_rets['any'].append(ret)
    
    base_st = stats(base_rets)
    pv1_st = stats(ppv1_rets['any'])
    pv2_st = stats(ppv2_rets['any'])
    bv2_st = stats(bov2_rets['any'])
    
    print(f'  ±{window_days}天窗口:')
    b = f'wr={base_st["wr"]:.0f}% av={base_st["avg"]:.1f}% n={base_st["n"]}' if base_st else '—'
    p1 = f'wr={pv1_st["wr"]:.0f}% av={pv1_st["avg"]:.1f}% n={pv1_st["n"]}' if pv1_st else '—'
    p2 = f'wr={pv2_st["wr"]:.0f}% av={pv2_st["avg"]:.1f}% n={pv2_st["n"]}' if pv2_st else '—'
    bo = f'wr={bv2_st["wr"]:.0f}% av={bv2_st["avg"]:.1f}% n={bv2_st["n"]}' if bv2_st else '—'
    print(f'  {"":12s} {b:>20s} {p1:>20s} {p2:>20s} {bo:>20s}')

# ═══════════════ 5. B2共现信号 ═══════════════
print(f'\n{"─"*85}')
print('五、B2=T日，前后窗口共现买入信号对胜率的影响（H20/T+1_O）')
print(f'{"─"*85}')
print(f'  {"窗口":<12s} {"基准(无共现)":>20s} {"+PP_V1":>20s} {"+PP_V2":>20s} {"+BO_V2":>20s}')
print(f'  {"─"*92}')

for window_days in [3, 5, 10]:
    base_rets = []
    ppv1_rets = {'any': []}
    ppv2_rets = {'any': []}
    bov2_rets = {'any': []}
    
    for (code, b1d), m in mw.items():
        if m['b2_date'] is None: continue
        b2d = m['b2_date']
        b = bt_b2.get((code, b2d, 'T+1_O', 20))
        if b is None: continue
        
        b2dt = datetime.strptime(b2d, '%Y-%m-%d')
        has_ppv1 = has_ppv2 = has_bov2 = False
        
        for offset in range(-window_days, window_days + 1):
            d = (b2dt + timedelta(days=offset)).strftime('%Y-%m-%d')
            se = sig_events.get((code, d))
            if se is None: continue
            if se['pp_v1']: has_ppv1 = True
            if se['pp_v2']: has_ppv2 = True
            if se['bo_v2']: has_bov2 = True
        
        ret = b['net_ret_pct']
        if not has_ppv1 and not has_ppv2 and not has_bov2:
            base_rets.append(ret)
        if has_ppv1: ppv1_rets['any'].append(ret)
        if has_ppv2: ppv2_rets['any'].append(ret)
        if has_bov2: bov2_rets['any'].append(ret)
    
    bs = stats(base_rets)
    ps1 = stats(ppv1_rets['any'])
    ps2 = stats(ppv2_rets['any'])
    bv = stats(bov2_rets['any'])
    
    print(f'  ±{window_days}天窗口:')
    b = f'wr={bs["wr"]:.0f}% av={bs["avg"]:.1f}% n={bs["n"]}' if bs else '—'
    p1 = f'wr={ps1["wr"]:.0f}% av={ps1["avg"]:.1f}% n={ps1["n"]}' if ps1 else '—'
    p2 = f'wr={ps2["wr"]:.0f}% av={ps2["avg"]:.1f}% n={ps2["n"]}' if ps2 else '—'
    bo = f'wr={bv["wr"]:.0f}% av={bv["avg"]:.1f}% n={bv["n"]}' if bv else '—'
    print(f'  {"":12s} {b:>20s} {p1:>20s} {p2:>20s} {bo:>20s}')

# ═══════════════ 6. 市场环境 ═══════════════
print(f'\n{"─"*85}')
print('六、市场环境分层（B1+B2，H20/T+1_O）')
print(f'{"─"*85}')
print(f'  {"环境":<10s} {"高置信":>18s} {"中置信":>18s} {"低置信":>18s} {"PLUS":>18s}')
print(f'  {"─"*82}')

for regime in ['bull', 'ranging', 'bear']:
    row = f'  {regime:<10s}'
    for filter_fn, label in [
        (lambda m: m['confidence']=='高' and m['b2_date'], '高'),
        (lambda m: m['confidence']=='中' and m['b2_date'], '中'),
        (lambda m: m['confidence']=='低' and m['b2_date'], '低'),
        (lambda m: m['is_plus']==1, 'PLUS'),
    ]:
        rets = []
        for (code, b1d), m in mw.items():
            if not filter_fn(m): continue
            b = bt_b1.get((code, b1d, 'T+1_O', 20))
            if b and b['market_regime'] == regime:
                rets.append(b['net_ret_pct'])
        st = stats(rets)
        if st and st['n'] >= 3:
            row += f'  {st["n"]:>3d}/{st["wr"]:.0f}%/{st["avg"]:.1f}%'
        else:
            row += f'  {"—":>18s}'
    print(row)

# ═══════════════ 7. 行业RS ═══════════════
print(f'\n{"─"*85}')
print('七、行业RS分层（B1+B2，H20/T+1_O，按B2日行业RS250）')
print(f'{"─"*85}')
print(f'  {"行业RS":<12s} {"高置信":>18s} {"中置信":>18s} {"低置信":>18s} {"PLUS":>18s}')
print(f'  {"─"*82}')

for irs_lo, irs_hi, label in [(0,60,'<60'), (60,75,'60~75'), (75,85,'75~85'), (85,100,'≥85')]:
    row = f'  {label:<12s}'
    for filter_fn, _ in [
        (lambda m: m['confidence']=='高' and m['b2_date'], '高'),
        (lambda m: m['confidence']=='中' and m['b2_date'], '中'),
        (lambda m: m['confidence']=='低' and m['b2_date'], '低'),
        (lambda m: m['is_plus']==1, 'PLUS'),
    ]:
        rets = []
        for (code, b1d), m in mw.items():
            if not filter_fn(m): continue
            # Get industry RS on B2 date
            b2d = m['b2_date']
            irs_val = None
            if m['ind_code']:
                irs_val = ind_rs.get((m['ind_code'], b2d))
            if irs_val is None:
                continue
            if irs_lo <= irs_val < irs_hi:
                b = bt_b1.get((code, b1d, 'T+1_O', 20))
                if b:
                    rets.append(b['net_ret_pct'])
        st = stats(rets)
        if st and st['n'] >= 3:
            row += f'  {st["n"]:>3d}/{st["wr"]:.0f}%/{st["avg"]:.1f}%'
        else:
            row += f'  {"—":>18s}'
    print(row)

# ═══════════════ 8. 最终汇总 ═══════════════
print(f'\n{"─"*85}')
print('八、B1 vs B2 入场时机对比（同信号，不同入场日）')
print(f'{"─"*85}')
print(f'  {"信号":<12s} {"T+1_O":>18s} {"T+2_O":>18s} {"T+3_O(手动)":>18s}')
print(f'  {"─"*72}')

for label, date_field, bt_dict in [
    ('B1入场', 'b1_date', bt_b1),
    ('B2入场', 'b2_date', bt_b2),
]:
    for m_filter, flabel in [
        (lambda m: m['confidence']=='高' and (date_field=='b2_date' or m['b2_date']), '高'),
        (lambda m: m['confidence']=='中' and (date_field=='b2_date' or m['b2_date']), '中'),
    ]:
        row = f'  {label}({flabel})'
        for offset, em in [(1,'T+1_O'),(2,'T+2_O')]:
            rets = []
            for (code, b1d), m in mw.items():
                if not m_filter(m): continue
                sig_date = m[date_field]
                if sig_date is None: continue
                b = bt_dict.get((code, sig_date, em, 20))
                if b: rets.append(b['net_ret_pct'])
            st = stats(rets)
            row += f'  wr={st["wr"]:.0f}% av={st["avg"]:.1f}% n={st["n"]}' if st else f'  {"—":>18s}'
        
        # T+3
        t3_rets = []
        for (code, b1d), m in mw.items():
            if not m_filter(m): continue
            sig_date = m[date_field]
            if sig_date is None: continue
            idx = date_idx.get(sig_date)
            if idx is None or idx + 3 >= len(trading_dates): continue
            entry_date = trading_dates[idx + 3]
            exit_idx = idx + 3 + 19
            if exit_idx >= len(trading_dates): continue
            exit_date = trading_dates[exit_idx]
            ek = kline.get((code, entry_date))
            xk = kline.get((code, exit_date))
            if ek and xk and ek[0] and xk[1]:
                t3_rets.append(((xk[1] - ek[0]) / ek[0] - COST) * 100)
        st3 = stats(t3_rets)
        row += f'  wr={st3["wr"]:.0f}% av={st3["avg"]:.1f}% n={st3["n"]}' if st3 else '  —'
        print(row)

print(f'\n报告完成。')
