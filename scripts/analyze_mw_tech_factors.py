"""
MW信号 × 技术面因子 综合分析
━━━━━━━━━━━━━━━━━━━━━━━━━━
对所有MW B1信号计算：
  MA均线: MA20/50/120/250 位置、斜率、排列
  成交量: B1日量/MA20量、量/MA50量
  MACD: DIF/DEA/柱
  KDJ: K/D/J值
  BIAS: (收盘-MA60)/MA60
  换手率: B1日换手率
  RS: RPS20/60/250

按H20收益分组（高/中/低），对比各因子差异
"""
import sqlite3, numpy as np
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 80)
print('MW 信号 × 技术面因子 综合分析')
print('=' * 80)

# ═══ 1. 加载 MW B1 + H20 表现 ═══
print('\n[1] 加载 MW B1 + H20...')
rows = c.execute("""
    SELECT br.stock_code, br.signal_date as b1_date, br.net_ret_pct, br.is_win,
           m.score, m.confidence, m.b2_date, m.is_plus,
           m.decline_pct, m.h_rs250, m.b1_vol_ratio, m.b1_return_pct
    FROM backtest_results br
    JOIN mw_signal_daily m ON br.stock_code=m.stock_code AND br.signal_date=m.b1_date
    WHERE br.combo_label='MW_B1' AND br.hold_days=20 AND br.entry_method='T+1_O'
      AND br.pool_mode='full'
      AND br.signal_date >= '2024-01-01' AND br.signal_date <= '2026-06-22'
""").fetchall()
print(f'  {len(rows)} 条 B1 信号')

# 分组: 高收益(Top25%), 中收益(Mid50%), 低收益(Bot25%)
sorted_rows = sorted(rows, key=lambda r: r['net_ret_pct'])
n = len(sorted_rows)
bot25 = sorted_rows[:n//4]      # 最差25%
mid50 = sorted_rows[n//4:3*n//4] # 中间50%
top25 = sorted_rows[3*n//4:]    # 最好25%

print(f'  Top25%: net_ret={top25[0]["net_ret_pct"]:.1f}%~{top25[-1]["net_ret_pct"]:.1f}% (n={len(top25)})')
print(f'  Mid50%: net_ret={mid50[0]["net_ret_pct"]:.1f}%~{mid50[-1]["net_ret_pct"]:.1f}% (n={len(mid50)})')
print(f'  Bot25%: net_ret={bot25[0]["net_ret_pct"]:.1f}%~{bot25[-1]["net_ret_pct"]:.1f}% (n={len(bot25)})')

# ═══ 2. 预加载K线 ═══
print('\n[2] 预加载K线...')
c.execute("""
    SELECT stock_code, date, open, high, low, close, volume, amount, turnover_rate, adj_close
    FROM daily_kline WHERE date >= '2023-06-01' AND date <= '2026-06-22'
    ORDER BY stock_code, date
""")
kline_raw = c.fetchall()
kline_by_code = defaultdict(list)
for r in kline_raw:
    kline_by_code[r['stock_code']].append({
        'date': r['date'], 'open': r['open'], 'high': r['high'],
        'low': r['low'], 'close': r['close'], 'volume': r['volume'],
        'amount': r['amount'], 'turnover_rate': r['turnover_rate'],
        'adj_close': r['adj_close'],
    })
# 建索引
kline_idx = {}
for code, kls in kline_by_code.items():
    for i, kl in enumerate(kls):
        kline_idx[(code, kl['date'])] = (i, kl)
print(f'  {len(kline_by_code)} 只股票')

# ═══ 3. 加载 RS ═══
print('[3] 加载 RS...')
c.execute("SELECT stock_code, date, rps_20, rps_60, rps_250 FROM stock_rs_daily WHERE date >= '2024-01-01' AND date <= '2026-06-22'")
rs_dict = {}
for r in c.fetchall():
    rs_dict[(r['stock_code'], r['date'])] = (r['rps_20'] or 0, r['rps_60'] or 0, r['rps_250'] or 0)
print(f'  {len(rs_dict)} 条')

db.close()

# ═══ 4. 计算技术指标 ═══
def compute_factors(code, b1_date):
    """对单个B1信号计算全部技术因子"""
    kls = kline_by_code.get(code)
    if not kls: return None
    
    # 找到B1日在K线中的位置
    idx_info = kline_idx.get((code, b1_date))
    if not idx_info: return None
    idx, kl = idx_info
    
    if idx < 250: return None  # 需要至少250个交易日历史
    
    f = {}
    
    # ── MA均线 ──
    closes = np.array([k['adj_close'] for k in kls[max(0,idx-260):idx+1]], dtype=np.float64)
    volumes = np.array([k['volume'] for k in kls[max(0,idx-260):idx+1]], dtype=np.float64)
    
    def ma(arr, period):
        if len(arr) < period: return None
        return np.mean(arr[-period:])
    
    ma20 = ma(closes, 20)
    ma50 = ma(closes, 50)
    ma120 = ma(closes, 120)
    ma250 = ma(closes, 250)
    close_now = closes[-1]
    
    if ma20 and ma50 and ma120 and ma250:
        f['ma20'] = round(ma20, 2)
        f['ma50'] = round(ma50, 2)
        f['ma120'] = round(ma120, 2)
        f['ma250'] = round(ma250, 2)
        f['close'] = round(close_now, 2)
        
        # 价格相对MA的位置
        f['pct_to_ma20'] = round((close_now - ma20) / ma20 * 100, 1)
        f['pct_to_ma50'] = round((close_now - ma50) / ma50 * 100, 1)
        f['pct_to_ma250'] = round((close_now - ma250) / ma250 * 100, 1)
        
        # MA250 斜率（20日）
        if len(closes) >= 270:
            ma250_20d_ago = np.mean(closes[-270:-20])
            f['ma250_slope_20d'] = round((ma250 - ma250_20d_ago) / ma250_20d_ago * 100, 2)
        
        # MA250 斜率（60日）
        if len(closes) >= 310:
            ma250_60d_ago = np.mean(closes[-310:-60])
            f['ma250_slope_60d'] = round((ma250 - ma250_60d_ago) / ma250_60d_ago * 100, 2)
        
        # 均线排列：MA20>MA50>MA120>MA250 = 多头排列
        alignment = 0
        if close_now > ma20: alignment |= 8
        if ma20 > ma50: alignment |= 4
        if ma50 > ma120: alignment |= 2
        if ma120 > ma250: alignment |= 1
        f['ma_alignment'] = alignment  # 0~15, 15=完美多头
        
        # 站上几条均线
        above_count = sum([close_now > ma20, close_now > ma50, close_now > ma120, close_now > ma250])
        f['ma_above_count'] = above_count
    
    # ── 成交量 ──
    vol_now = volumes[-1]
    vol_ma20 = np.mean(volumes[-21:-1]) if len(volumes) >= 21 else None
    vol_ma50 = np.mean(volumes[-51:-1]) if len(volumes) >= 51 else None
    
    if vol_ma20 and vol_ma20 > 0:
        f['vol_vs_ma20'] = round(vol_now / vol_ma20, 2)
    if vol_ma50 and vol_ma50 > 0:
        f['vol_vs_ma50'] = round(vol_now / vol_ma50, 2)
    
    # ── 换手率 ──
    f['turnover_rate'] = round(kl['turnover_rate'] or 0, 2)
    
    # ── MACD ──
    if len(closes) >= 26:
        ema12 = closes[-1]; ema26 = closes[-1]
        k12 = 2/13; k26 = 2/27
        for i in range(len(closes)-2, -1, -1):
            ema12 = closes[i] * k12 + ema12 * (1-k12)
            ema26 = closes[i] * k26 + ema26 * (1-k26)
            if i <= len(closes) - 27:
                break
        dif = ema12 - ema26
        
        # DEA (9-day EMA of DIF) - simplified: use the last 9 values avg
        dea = dif * 0.2 + dif * 0.8  # rough approximation
        
        f['macd_dif'] = round(dif, 3)
        f['macd_hist'] = round((dif - dea) * 2, 3)
        f['macd_dif_sign'] = 1 if dif > 0 else 0
        f['macd_hist_sign'] = 1 if (dif - dea) > 0 else 0
        # 金叉/死叉（简化：DIF上穿DEA）
        if idx >= 2:
            prev_ema12_2 = closes[-3]; prev_ema26_2 = closes[-3]
            for i in range(len(closes)-4, -1, -1):
                prev_ema12_2 = closes[i] * k12 + prev_ema12_2 * (1-k12)
                prev_ema26_2 = closes[i] * k26 + prev_ema26_2 * (1-k26)
                if i <= len(closes) - 29: break
            prev_dif_2 = prev_ema12_2 - prev_ema26_2
            prev_dea_2 = prev_dif_2 * 0.2 + prev_dif_2 * 0.8
            f['macd_golden_cross'] = 1 if (dif > dea) and (prev_dif_2 <= prev_dea_2) else 0
    
    # ── KDJ (9,3,3) ──
    if len(closes) >= 9:
        highs = np.array([k['high'] for k in kls[max(0,idx-8):idx+1]], dtype=np.float64)
        lows = np.array([k['low'] for k in kls[max(0,idx-8):idx+1]], dtype=np.float64)
        h9 = np.max(highs); l9 = np.min(lows)
        rsv = (close_now - l9) / (h9 - l9) * 100 if h9 > l9 else 50
        # 简化：用RSV近似K值
        f['kdj_k'] = round(rsv * 2/3 + 50 * 1/3, 1)
        f['kdj_d'] = round(f['kdj_k'] * 2/3 + 50 * 1/3, 1)
        f['kdj_j'] = round(3 * f['kdj_k'] - 2 * f['kdj_d'], 1)
        f['kdj_k_zone'] = '高(>80)' if f['kdj_k'] > 80 else ('低(<20)' if f['kdj_k'] < 20 else '中(20-80)')
    
    # ── BIAS (收盘 vs MA60) ──
    ma60_val = ma(closes, 60)
    if ma60_val and ma60_val > 0:
        f['bias'] = round((close_now - ma60_val) / ma60_val * 100, 1)
    
    # ── RS ──
    rs = rs_dict.get((code, b1_date))
    if rs:
        f['rps20'] = rs[0]
        f['rps60'] = rs[1]
        f['rps250'] = rs[2]
    
    return f

# ═══ 5. 计算所有信号的因子 ═══
print('\n[4] 计算技术因子...')
all_factors = []
for i, r in enumerate(rows):
    if i % 2000 == 0:
        print(f'  {i}/{len(rows)}...')
    f = compute_factors(r['stock_code'], r['b1_date'])
    if f:
        f['net_ret'] = r['net_ret_pct']
        f['is_win'] = r['is_win']
        f['score'] = r['score']
        f['has_b2'] = r['b2_date'] is not None
        f['group'] = 'top25' if r in top25 else ('bot25' if r in bot25 else 'mid50')
        all_factors.append(f)

print(f'  有效信号: {len(all_factors)}')

# ═══ 6. 分组统计 ═══
def safe_mean(arr):
    arr = [x for x in arr if x is not None]
    return np.mean(arr) if arr else None

def safe_pct(arr, condition_fn):
    arr = [x for x in arr if x is not None]
    return sum(1 for x in arr if condition_fn(x)) / len(arr) * 100 if arr else None

def analyze(factors, group_name):
    n = len(factors)
    if n == 0: return {}
    
    a = {}
    
    # MA
    for key in ['pct_to_ma20', 'pct_to_ma50', 'pct_to_ma250', 'ma250_slope_20d', 'ma250_slope_60d']:
        a[key] = safe_mean([f.get(key) for f in factors])
    
    a['ma_alignment'] = safe_mean([f.get('ma_alignment') for f in factors])
    a['ma_above_count'] = safe_mean([f.get('ma_above_count') for f in factors])
    
    # MA250走势
    a['ma250_flat'] = safe_pct([f.get('ma250_slope_20d') for f in factors], 
                                lambda x: abs(x) < 2.0)  # 斜率<2%视为走平
    a['ma250_up'] = safe_pct([f.get('ma250_slope_20d') for f in factors], 
                              lambda x: x >= 2.0)
    a['ma250_down'] = safe_pct([f.get('ma250_slope_20d') for f in factors], 
                                lambda x: x <= -2.0)
    
    # 成交量
    for key in ['vol_vs_ma20', 'vol_vs_ma50', 'turnover_rate']:
        a[key] = safe_mean([f.get(key) for f in factors])
    
    a['vol_expand_30pct'] = safe_pct([f.get('vol_vs_ma20') for f in factors],
                                      lambda x: x >= 1.3)
    a['vol_expand_50pct'] = safe_pct([f.get('vol_vs_ma20') for f in factors],
                                      lambda x: x >= 1.5)
    a['vol_expand_100pct'] = safe_pct([f.get('vol_vs_ma20') for f in factors],
                                       lambda x: x >= 2.0)
    
    # MACD
    a['macd_dif_sign'] = safe_pct([f.get('macd_dif_sign') for f in factors], lambda x: x == 1)
    a['macd_hist_sign'] = safe_pct([f.get('macd_hist_sign') for f in factors], lambda x: x == 1)
    a['macd_golden_cross'] = safe_pct([f.get('macd_golden_cross') for f in factors], lambda x: x == 1)
    a['macd_dif'] = safe_mean([f.get('macd_dif') for f in factors])
    
    # KDJ
    a['kdj_k'] = safe_mean([f.get('kdj_k') for f in factors])
    a['kdj_k_over80'] = safe_pct([f.get('kdj_k') for f in factors], lambda x: x > 80)
    a['kdj_k_under20'] = safe_pct([f.get('kdj_k') for f in factors], lambda x: x < 20)
    a['kdj_j'] = safe_mean([f.get('kdj_j') for f in factors])
    
    # BIAS
    a['bias'] = safe_mean([f.get('bias') for f in factors])
    
    # RS
    for key in ['rps20', 'rps60', 'rps250']:
        a[key] = safe_mean([f.get(key) for f in factors])
    
    # 交叉特征
    a['above_ma250_and_vol30'] = safe_pct(factors, 
        lambda f: (f.get('pct_to_ma250') or -999) > 0 and (f.get('vol_vs_ma20') or 0) >= 1.3)
    
    return a

top_a = analyze([f for f in all_factors if f['group'] == 'top25'], 'Top25%')
mid_a = analyze([f for f in all_factors if f['group'] == 'mid50'], 'Mid50%')
bot_a = analyze([f for f in all_factors if f['group'] == 'bot25'], 'Bot25%')

# ═══ 7. 输出 ═══
def print_compare(label, top_val, bot_val, fmt='.2f', unit='', reverse=False):
    if top_val is None or bot_val is None: return
    diff = top_val - bot_val
    arrow = '↑' if (diff > 0 and not reverse) or (diff < 0 and reverse) else \
            ('↓' if diff != 0 else '→')
    if fmt == '.1f':
        print(f'  {label:<35s} Top={top_val:>8.1f}{unit}  Bot={bot_val:>8.1f}{unit}  {arrow} 差{diff:+.1f}')
    elif fmt == '.2f':
        print(f'  {label:<35s} Top={top_val:>8.2f}{unit}  Bot={bot_val:>8.2f}{unit}  {arrow} 差{diff:+.2f}')
    elif fmt == '.0f':
        print(f'  {label:<35s} Top={top_val:>7.0f}{unit}  Bot={bot_val:>7.0f}{unit}  {arrow} 差{diff:+.0f}')

print(f'\n{"=" * 80}')
print(f'Top25%(收益 {top25[0]["net_ret_pct"]:.0f}~{top25[-1]["net_ret_pct"]:.0f}%) vs Bot25%(收益 {bot25[0]["net_ret_pct"]:.0f}~{bot25[-1]["net_ret_pct"]:.0f}%)')
print(f'{"=" * 80}')

print(f'\n── MA均线 ──')
print_compare('价格距MA20(%)', top_a.get('pct_to_ma20'), bot_a.get('pct_to_ma20'), '.2f', '%')
print_compare('价格距MA50(%)', top_a.get('pct_to_ma50'), bot_a.get('pct_to_ma50'), '.2f', '%')
print_compare('价格距MA250(%)', top_a.get('pct_to_ma250'), bot_a.get('pct_to_ma250'), '.2f', '%')
print_compare('MA250斜率(20日%)', top_a.get('ma250_slope_20d'), bot_a.get('ma250_slope_20d'), '.2f', '%')
print_compare('MA250斜率(60日%)', top_a.get('ma250_slope_60d'), bot_a.get('ma250_slope_60d'), '.2f', '%')
print_compare('均线排列得分(0-15)', top_a.get('ma_alignment'), bot_a.get('ma_alignment'), '.1f')
print_compare('站上均线条数(0-4)', top_a.get('ma_above_count'), bot_a.get('ma_above_count'), '.1f')
print_compare('MA250走平(|斜率|<2%)', top_a.get('ma250_flat'), bot_a.get('ma250_flat'), '.1f', '%')
print_compare('MA250上升(斜率≥2%)', top_a.get('ma250_up'), bot_a.get('ma250_up'), '.1f', '%')
print_compare('MA250下降(斜率≤-2%)', top_a.get('ma250_down'), bot_a.get('ma250_down'), '.1f', '%')

print(f'\n── 成交量 ──')
print_compare('B1日量/MA20量', top_a.get('vol_vs_ma20'), bot_a.get('vol_vs_ma20'), '.2f')
print_compare('B1日量/MA50量', top_a.get('vol_vs_ma50'), bot_a.get('vol_vs_ma50'), '.2f')
print_compare('放量≥30%(vs MA20)', top_a.get('vol_expand_30pct'), bot_a.get('vol_expand_30pct'), '.1f', '%')
print_compare('放量≥50%(vs MA20)', top_a.get('vol_expand_50pct'), bot_a.get('vol_expand_50pct'), '.1f', '%')
print_compare('放量≥100%(vs MA20)', top_a.get('vol_expand_100pct'), bot_a.get('vol_expand_100pct'), '.1f', '%')
print_compare('换手率(%)', top_a.get('turnover_rate'), bot_a.get('turnover_rate'), '.2f', '%')

print(f'\n── MACD ──')
print_compare('DIF>0占比', top_a.get('macd_dif_sign'), bot_a.get('macd_dif_sign'), '.1f', '%')
print_compare('MACD柱>0占比', top_a.get('macd_hist_sign'), bot_a.get('macd_hist_sign'), '.1f', '%')
print_compare('MACD金叉占比', top_a.get('macd_golden_cross'), bot_a.get('macd_golden_cross'), '.1f', '%')
print_compare('DIF均值', top_a.get('macd_dif'), bot_a.get('macd_dif'), '.2f')

print(f'\n── KDJ ──')
print_compare('K值均值', top_a.get('kdj_k'), bot_a.get('kdj_k'), '.1f')
print_compare('K>80(超买)占比', top_a.get('kdj_k_over80'), bot_a.get('kdj_k_over80'), '.1f', '%')
print_compare('K<20(超卖)占比', top_a.get('kdj_k_under20'), bot_a.get('kdj_k_under20'), '.1f', '%')

print(f'\n── BIAS ──')
print_compare('BIAS(收盘vs MA60%)', top_a.get('bias'), bot_a.get('bias'), '.2f', '%')

print(f'\n── RS强度 ──')
print_compare('RPS20均值', top_a.get('rps20'), bot_a.get('rps20'), '.1f')
print_compare('RPS60均值', top_a.get('rps60'), bot_a.get('rps60'), '.1f')
print_compare('RPS250均值', top_a.get('rps250'), bot_a.get('rps250'), '.1f')

print(f'\n── 交叉特征 ──')
print_compare('站上MA250+放量≥30%', top_a.get('above_ma250_and_vol30'), bot_a.get('above_ma250_and_vol30'), '.1f', '%')

# ═══ 8. 因子重要性排序 ═══
print(f'\n{"=" * 80}')
print('因子区分力排序（Top vs Bot 差异幅度）')
print(f'{"=" * 80}')

diffs = []
for key, label in [
    ('ma250_slope_20d', 'MA250斜率20日'),
    ('vol_vs_ma20', '量/MA20量'),
    ('vol_expand_30pct', '放量≥30%占比'),
    ('vol_expand_50pct', '放量≥50%占比'),
    ('pct_to_ma250', '价格距MA250'),
    ('rps250', 'RPS250'),
    ('rps60', 'RPS60'),
    ('bias', 'BIAS'),
    ('macd_dif_sign', 'MACD DIF>0占比'),
    ('ma250_flat', 'MA250走平占比'),
    ('ma_alignment', '均线排列'),
    ('kdj_k_over80', 'KDJ超买占比'),
    ('turnover_rate', '换手率'),
    ('macd_golden_cross', 'MACD金叉占比'),
    ('rps20', 'RPS20'),
    ('above_ma250_and_vol30', 'MA250上+放量30%'),
]:
    tv = top_a.get(key)
    bv = bot_a.get(key)
    if tv is not None and bv is not None:
        diffs.append((label, abs(tv - bv), tv, bv))

diffs.sort(key=lambda x: -x[1])
for label, diff, tv, bv in diffs:
    arrow = 'Top更高' if tv > bv else 'Bot更高'
    print(f'  {label:<25s} 差异={diff:.2f}  ({arrow})')

print('\n分析完成。')
