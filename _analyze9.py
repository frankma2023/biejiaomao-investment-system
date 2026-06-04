import json, sys
sys.path.insert(0, 'src')
from scanners.pattern_structure import analyze_structure

stocks = [
    ('002851', '2025-09-01', '2025-12-17', '麦格米特'),
    ('300308', '2025-12-01', '2026-04-10', '中际旭创'),
    ('300502', '2025-12-22', '2026-03-18', '新易盛'),
    ('601138', '2025-10-03', '2026-04-08', '工业富联'),
    ('601869', '2025-12-24', '2026-01-30', '601869'),
    ('603986', '2026-01-29', '2026-04-21', '603986'),
    ('600584', '2026-01-22', '2026-05-08', '600584'),
    ('600105', '2026-01-12', '2026-04-01', '600105'),
    ('300323', '2026-03-12', '2026-04-29', '300323'),
]

print('=' * 75)
print(f'{"代码":<8} {"名称":<10} {"B1日期":<12} {"B1涨幅":>6} {"B1量比":>6} {"B1均线":>5} {"B1新高":>4} {"B2日期":<12} {"B2涨幅":>6} {"B2收盘位":>6} {"B2均线":>5} {"B2跳空":>4}')
print('-' * 75)

for code, start, end, name in stocks:
    r = analyze_structure(code, start, end)
    n = r.get('nodes', {})
    f = r.get('features', {})
    
    b1_date = f.get('B1_date') or '—'
    b1_ret = f.get('B1_return_pct')
    b1_vol = f.get('B1_vol_ratio_vs_20d')
    b1_ma = f.get('B1_ma_break_count')
    b1_nh = f.get('B1_new_high_vs_C') or '—'
    b2_date = f.get('B2_date') or '—'
    b2_ret = f.get('B2_return_pct')
    b2_pos = f.get('B2_close_pos')
    b2_ma = f.get('B2_ma_break_count')
    b2_gap = f.get('B2_is_gap') or '—'
    
    b1r = f'{b1_ret:+.1f}%' if b1_ret is not None else '—'
    b1v = f'{b1_vol:.2f}' if b1_vol is not None else '—'
    b1m = str(b1_ma) if b1_ma is not None else '—'
    b2r = f'{b2_ret:+.1f}%' if b2_ret is not None else '—'
    b2p = f'{b2_pos:.0f}%' if b2_pos is not None else '—'
    b2m = str(b2_ma) if b2_ma is not None else '—'
    
    print(f'{code:<8} {name:<10} {b1_date:<12} {b1r:>6} {b1v:>6} {b1m:>5} {b1_nh:>4} {b2_date:<12} {b2r:>6} {b2p:>6} {b2m:>5} {b2_gap:>4}')

print()
print('=' * 75)
print('上下文特征')
print(f'{"代码":<8} {"跌幅":>7} {"跌天":>5} {"横盘天":>5} {"横盘振幅":>7} {"横盘缩量":>7} {"整理天":>5} {"整理回撤":>7} {"整理缩量":>7} {"B1→末":>7}')
print('-' * 75)

for code, start, end, name in stocks:
    r = analyze_structure(code, start, end)
    f = r.get('features', {})
    
    d1 = f'{f.get("D_decline_pct"):+.1f}%' if f.get("D_decline_pct") is not None else '—'
    d2 = str(f.get("D_days")) if f.get("D_days") is not None else '—'
    c1 = str(f.get("C_days")) if f.get("C_days") is not None else '—'
    c2 = f'{f.get("C_amplitude_pct"):.1f}%' if f.get("C_amplitude_pct") is not None else '—'
    c3 = f'{f.get("C_vol_vs_D"):.2f}' if f.get("C_vol_vs_D") is not None else '—'
    p1 = str(f.get("P_days")) if f.get("P_days") is not None else '—'
    p2 = f'{f.get("P_max_drawdown_pct"):+.1f}%' if f.get("P_max_drawdown_pct") is not None else '—'
    p3 = f'{f.get("P_vol_vs_B1"):.2f}' if f.get("P_vol_vs_B1") is not None else '—'
    tr = f'{f.get("total_return_B1_to_end"):+.1f}%' if f.get("total_return_B1_to_end") is not None else '—'
    
    print(f'{code:<8} {d1:>7} {d2:>5} {c1:>5} {c2:>7} {c3:>7} {p1:>5} {p2:>7} {p3:>7} {tr:>7}')
