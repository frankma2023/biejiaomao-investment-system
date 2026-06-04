import json, sys
sys.path.insert(0, 'src')
from scanners.pattern_structure import analyze_structure

stocks = [
    ('002851', '2025-09-01', '2025-12-17'),
    ('300308', '2025-12-01', '2026-04-10'),
    ('300502', '2025-12-22', '2026-03-18'),
    ('601138', '2025-10-03', '2026-04-08'),
    ('601869', '2025-12-24', '2026-01-30'),
    ('603986', '2026-01-29', '2026-04-21'),
    ('600584', '2026-01-22', '2026-05-08'),
    ('300323', '2026-03-12', '2026-04-30'),
]

all_features = []
for code, start, end in stocks:
    r = analyze_structure(code, start, end)
    f = r['features']
    f['_code'] = code
    all_features.append(f)

# ── H 前高段 ──
print('═══ H 前高段 ═══')
keys_h = ['H_price','H_is_n_day_high','H_rs20','H_rs120','H_rs250','H_sma50_slope','H_close_vs_sma50']
print(f'{"代码":<8} {"价格":>7} {"新高":>4} {"RS20":>5} {"RS120":>6} {"RS250":>6} {"SMA50斜率":>9} {"距SMA50":>7}')
for f in all_features:
    vals = [f.get(k) for k in keys_h]
    rs20 = f'{vals[2]:.0f}' if vals[2] is not None else '—'
    rs120 = f'{vals[3]:.0f}' if vals[3] is not None else '—'
    rs250 = f'{vals[4]:.0f}' if vals[4] is not None else '—'
    slope = f'{vals[5]:+.1f}%' if vals[5] is not None else '—'
    dist = f'{vals[6]:+.1f}%' if vals[6] is not None else '—'
    print(f'{f["_code"]:<8} ¥{vals[0]:>6.0f} {vals[1] or "—":>4} {rs20:>5} {rs120:>6} {rs250:>6} {slope:>9} {dist:>7}')

# ── D 调整段 ──
print('\n═══ D 调整段 ═══')
keys_d = ['D_decline_pct','D_days','D_max_daily_drop','D_vol_avg']
print(f'{"代码":<8} {"跌幅":>7} {"天数":>5} {"最大单日跌":>8} {"日均量":>10}')
for f in all_features:
    vals = [f.get(k) for k in keys_d]
    dd = f'{vals[0]:+.1f}%' if vals[0] is not None else '—'
    md = f'{vals[2]:+.1f}%' if vals[2] is not None else '—'
    av = f'{vals[3]:.0f}' if vals[3] is not None else '—'
    print(f'{f["_code"]:<8} {dd:>7} {vals[1] or "—":>5} {md:>8} {av:>10}')

# ── C 横盘段 ──
print('\n═══ C 横盘段 ═══')
keys_c = ['C_days','C_amplitude_pct','C_low_slope','C_vol_avg','C_vol_vs_D','C_ad_slope']
print(f'{"代码":<8} {"天数":>4} {"振幅":>6} {"低点斜率":>8} {"日均量":>10} {"缩量比":>6} {"AD斜率":>7}')
for f in all_features:
    vals = [f.get(k) for k in keys_c]
    amp = f'{vals[1]:.1f}%' if vals[1] is not None else '—'
    ls = f'{vals[2]:+.3f}%' if vals[2] is not None else '—'
    av = f'{vals[3]:.0f}' if vals[3] is not None else '—'
    cv = f'{vals[4]:.2f}' if vals[4] is not None else '—'
    ad = f'{vals[5]:+.4f}' if vals[5] is not None else '—'
    print(f'{f["_code"]:<8} {vals[0] or "—":>4} {amp:>6} {ls:>8} {av:>10} {cv:>6} {ad:>7}')

# ── P 整理段 ──
print('\n═══ P 整理段 ═══')
keys_p = ['P_days','P_max_drawdown_pct','P_held_b1_low','P_vol_avg','P_vol_vs_B1']
print(f'{"代码":<8} {"天数":>4} {"最大回撤":>7} {"守B1低点":>7} {"日均量":>10} {"缩量比":>5}')
for f in all_features:
    vals = [f.get(k) for k in keys_p]
    mr = f'{vals[1]:+.1f}%' if vals[1] is not None else '—'
    hl = vals[2] or '—'
    av = f'{vals[3]:.0f}' if vals[3] is not None else '—'
    pv = f'{vals[4]:.2f}' if vals[4] is not None else '—'
    print(f'{f["_code"]:<8} {vals[0] or "—":>4} {mr:>7} {hl:>7} {av:>10} {pv:>5}')
