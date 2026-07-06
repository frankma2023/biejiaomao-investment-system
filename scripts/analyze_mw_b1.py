"""
MW B1 信号特征反向挖掘
━━━━━━━━━━━━━━━━━━━━━━
目标：2024~2026年所有MW B1信号中，H20表现最好的前N只，反推其共性特征

分析维度：
  1. 共现信号：B1前后5天内出现的PP_V1/PP_V2/BO_V2/MW_B2
  2. 成交量：B1日量比、B1前后日均成交额变化
  3. 行业RS：B1日所属行业RS250
  4. 个股RS：B1日个股RPS
  5. MW结构：调整深度(decline_pct)、前高RS(h_rs250)、横盘振幅
  6. 价格形态：B1日收盘位置、B1前后涨跌
  7. 市场环境：B1日市场状态(bull/bear/ranging)
  8. B2出现：B1后是否出现B2确认、B2日表现
  9. 跳空特征
"""
import sqlite3, json, sys, os
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import numpy as np
import polars as pl

DB = 'D:/hanako/investment-system/data/lixinger.db'
PROJECT = 'D:/hanako/investment-system'
sys.path.insert(0, PROJECT)

# ═══════════════════ 数据加载 ═══════════════════
print('=' * 70)
print('MW B1 信号特征反向挖掘')
print('=' * 70)

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# ── 1. 加载所有 MW B1 信号及其 H20 表现 ──
print('\n[1] 加载 MW B1 信号 + H20 表现...')
rows = db.execute("""
    SELECT br.stock_code, br.signal_date as b1_date, br.net_ret_pct, br.is_win,
           br.market_regime, br.ret_pct as gross_ret,
           br.peak_ret_pct, br.trough_ret_pct,
           br.index_ret_pct, br.excess_ret_pct,
           e.mw_b1_decline_pct, e.mw_b1_h_rs250, e.mw_b1_vol_ratio,
           e.signal_mask, e.combo_label, e.signal_count
    FROM backtest_results br
    JOIN signal_events e ON br.stock_code=e.stock_code AND br.signal_date=e.date
    WHERE br.combo_label='MW_B1' AND br.hold_days=20 AND br.entry_method='T+1_O'
      AND br.pool_mode='full'
      AND br.signal_date >= '2024-01-01' AND br.signal_date <= '2026-06-22'
      AND br.stock_code != '_sentinel_'
""").fetchall()
print(f'  总信号数: {len(rows)}')

# 排序取Top
rows_sorted = sorted(rows, key=lambda r: r['net_ret_pct'], reverse=True)
N = min(2000, len(rows_sorted))
top = rows_sorted[:N]
bottom = rows_sorted[-N:]  # 最差的同等数量，做对比

print(f'  Top {N}: net_ret={top[0]["net_ret_pct"]:.1f}% ~ {top[-1]["net_ret_pct"]:.1f}%')
print(f'  Bottom {N}: net_ret={bottom[0]["net_ret_pct"]:.1f}% ~ {bottom[-1]["net_ret_pct"]:.1f}%')

top_set = {(r['stock_code'], r['b1_date']) for r in top}
bottom_set = {(r['stock_code'], r['b1_date']) for r in bottom}

# ── 2. 加载全量 MW 信号细节 ──
print('\n[2] 加载 MW 信号细节...')
mw_rows = db.execute("""
    SELECT stock_code, b1_date, b2_date, score, score_v2, decline_pct,
           h_rs250, h_rs20, c_amplitude_pct, c_amount_avg, b1_vol_ratio,
           b1_return_pct, b2_return_pct, b2_is_gap, b2_ma_count,
           score_h, score_d, score_c, score_p, score_i1, score_i2,
           score_sig, score_gap, is_plus, ind_rs250, ind_rs20,
           ind_code, ind_name, p_max_dd_pct, p_vol_ratio,
           h_pre_rise_pct
    FROM mw_signal_daily
    WHERE b1_date >= '2024-01-01' AND b1_date <= '2026-06-22'
      AND stock_code != '_sentinel_'
""").fetchall()
mw_dict = {(r['stock_code'], r['b1_date']): r for r in mw_rows}
print(f'  MW记录数: {len(mw_dict)}')

# ── 3. 加载个股RS ──
print('\n[3] 加载个股RS...')
rs_rows = db.execute("""
    SELECT stock_code, date, rps_20, rps_250
    FROM stock_rs_daily
    WHERE date >= '2023-12-01' AND date <= '2026-06-22'
""").fetchall()
rs_dict = defaultdict(dict)
for r in rs_rows:
    rs_dict[r['stock_code']][r['date']] = (r['rps_20'], r['rps_250'])
print(f'  RS记录数: {len(rs_rows)}')

# ── 4. 加载K线（用于量比和价格形态）──
print('\n[4] 加载K线数据...')
min_date = '2023-12-01'
max_date = '2026-06-22'
k_rows = db.execute("""
    SELECT stock_code, date, open, high, low, close, volume, amount, adj_close
    FROM daily_kline
    WHERE date >= ? AND date <= ?
    ORDER BY stock_code, date
""", (min_date, max_date)).fetchall()
kline_dict = defaultdict(list)
for r in k_rows:
    kline_dict[r['stock_code']].append({
        'date': r['date'], 'open': r['open'], 'high': r['high'],
        'low': r['low'], 'close': r['close'], 'volume': r['volume'],
        'amount': r['amount'], 'adj_close': r['adj_close'],
    })
# 建索引
kline_idx = defaultdict(dict)
for code, kls in kline_dict.items():
    for i, kl in enumerate(kls):
        kline_idx[code][kl['date']] = (i, kl)  # (index, kline)
print(f'  K线股票数: {len(kline_dict)}')

# ── 5. 加载其他信号（用于共现检测）──
print('\n[5] 加载共现信号...')

# PP V1
ppv1_dict = defaultdict(set)
for r in db.execute("""
    SELECT stock_code, date FROM pocket_pivot_daily
    WHERE engine_version='V1' AND date >= '2023-12-01' AND date <= '2026-06-22'
"""):
    ppv1_dict[r['stock_code']].add(r['date'])

# PP V2
ppv2_dict = defaultdict(set)
for r in db.execute("""
    SELECT stock_code, date FROM pocket_pivot_daily
    WHERE engine_version='V2' AND date >= '2023-12-01' AND date <= '2026-06-22'
"""):
    ppv2_dict[r['stock_code']].add(r['date'])

# BO V2
bo_dict = defaultdict(set)
for r in db.execute("""
    SELECT stock_code, date FROM market_breakout_v2_daily
    WHERE date >= '2023-12-01' AND date <= '2026-06-22'
"""):
    bo_dict[r['stock_code']].add(r['date'])

# MW B2 (来自mw_signal_daily)
mw_b2_dict = defaultdict(set)
for r in db.execute("""
    SELECT stock_code, b2_date FROM mw_signal_daily
    WHERE b2_date >= '2023-12-01' AND b2_date <= '2026-06-22'
"""):
    mw_b2_dict[r['stock_code']].add(r['b2_date'])

print(f'  PP_V1: {sum(len(v) for v in ppv1_dict.values())} 条')
print(f'  PP_V2: {sum(len(v) for v in ppv2_dict.values())} 条')
print(f'  BO_V2: {sum(len(v) for v in bo_dict.values())} 条')
print(f'  MW_B2: {sum(len(v) for v in mw_b2_dict.values())} 条')

# ── 6. 加载行业RS ──
print('\n[6] 加载行业RS...')
irs_rows = db.execute("""
    SELECT stock_code, date, rs_250, rs_20
    FROM index_rs_daily
    WHERE date >= '2024-01-01' AND date <= '2026-06-22'
""").fetchall()
irs_dict = {}
for r in irs_rows:
    irs_dict[(r['stock_code'], r['date'])] = (r['rs_250'], r['rs_20'])
print(f'  行业RS记录数: {len(irs_dict)}')

db.close()

# ═══════════════════ 特征分析 ═══════════════════

def analyze_group(rows_list, label, mw_dict, rs_dict, kline_idx, 
                  ppv1_dict, ppv2_dict, bo_dict, mw_b2_dict, irs_dict):
    """对一组B1信号做全面特征分析"""
    n = len(rows_list)
    if n == 0:
        return {}
    
    # ── 共现信号 ──
    # 窗口: [B1-5, B1+3]
    signal_co_occur = {
        'PP_V1_before': [], 'PP_V2_before': [], 'BO_V2_before': [], 'MW_B2_before': [],
        'PP_V1_same': [], 'PP_V2_same': [], 'BO_V2_same': [], 'MW_B2_same': [],
        'PP_V1_after': [], 'PP_V2_after': [], 'BO_V2_after': [], 'MW_B2_after': [],
        'PP_V1_any': [], 'PP_V2_any': [], 'BO_V2_any': [], 'MW_B2_any': [],
    }
    
    # ── 量价特征 ──
    vol_ratios = []
    amounts = []
    amount_ma20_ratios = []  # B1日成交额/20日均
    close_positions = []  # B1日收盘在K线中的位置
    b1_returns = []  # B1日涨跌幅
    pre_1d_returns = []  # B1前1日涨跌幅
    pre_5d_returns = []  # B1前5日涨跌幅
    post_3d_returns = []  # B1后3日涨跌幅
    gaps = []  # B1日是否跳空高开
    
    # ── RS特征 ──
    stock_rs20 = []
    stock_rs250 = []
    ind_rs250_list = []
    ind_rs20_list = []
    
    # ── MW结构特征 ──
    decline_pcts = []
    h_rs250s = []
    h_rs20s = []
    c_amplitudes = []
    c_amount_avgs = []
    b1_vol_ratios_mw = []
    p_max_dds = []
    h_pre_rises = []
    
    # ── MW评分 ──
    scores = []
    score_v2s = []
    score_hs = []
    score_ds = []
    score_cs = []
    score_ps = []
    score_i1s = []
    score_i2s = []
    score_gaps = []
    score_sigs = []
    is_plus_count = 0
    b2_confirmed = 0  # B1后出现B2的
    b2_is_gap_count = 0
    b2_returns = []
    
    # ── 市场环境 ──
    regimes = Counter()
    
    # ── 行业分布 ──
    industries = Counter()
    
    # ── B1日期分布 ──
    months = Counter()
    
    # ── 信号共现计数 ──
    signal_counts = Counter()
    
    for r in rows_list:
        code = r['stock_code']
        b1_date = r['b1_date']
        dt = datetime.strptime(b1_date, '%Y-%m-%d')
        
        # 共现信号窗口
        window_dates = set()
        for offset in range(-5, 4):  # -5 ~ +3
            d = (dt + timedelta(days=offset)).strftime('%Y-%m-%d')
            window_dates.add(d)
        
        # 检查各信号
        ppv1_set = ppv1_dict.get(code, set())
        ppv2_set = ppv2_dict.get(code, set())
        bo_set = bo_dict.get(code, set())
        b2_set = mw_b2_dict.get(code, set())
        
        def _check_window(signal_set):
            """返回 (before_count, same_count, after_count)"""
            before = same = after = 0
            for wd in window_dates:
                if wd in signal_set:
                    wdt = datetime.strptime(wd, '%Y-%m-%d')
                    if wdt < dt:
                        before += 1
                    elif wdt == dt:
                        same += 1
                    else:
                        after += 1
            return before, same, after
        
        b1, s1, a1 = _check_window(ppv1_set)
        b2, s2, a2 = _check_window(ppv2_set)
        b3, s3, a3 = _check_window(bo_set)
        b4, s4, a4 = _check_window(b2_set)
        
        signal_co_occur['PP_V1_before'].append(1 if b1 > 0 else 0)
        signal_co_occur['PP_V1_same'].append(1 if s1 > 0 else 0)
        signal_co_occur['PP_V1_after'].append(1 if a1 > 0 else 0)
        signal_co_occur['PP_V1_any'].append(1 if b1+s1+a1 > 0 else 0)
        
        signal_co_occur['PP_V2_before'].append(1 if b2 > 0 else 0)
        signal_co_occur['PP_V2_same'].append(1 if s2 > 0 else 0)
        signal_co_occur['PP_V2_after'].append(1 if a2 > 0 else 0)
        signal_co_occur['PP_V2_any'].append(1 if b2+s2+a2 > 0 else 0)
        
        signal_co_occur['BO_V2_before'].append(1 if b3 > 0 else 0)
        signal_co_occur['BO_V2_same'].append(1 if s3 > 0 else 0)
        signal_co_occur['BO_V2_after'].append(1 if a3 > 0 else 0)
        signal_co_occur['BO_V2_any'].append(1 if b3+s3+a3 > 0 else 0)
        
        signal_co_occur['MW_B2_before'].append(1 if b4 > 0 else 0)
        signal_co_occur['MW_B2_same'].append(1 if s4 > 0 else 0)
        signal_co_occur['MW_B2_after'].append(1 if a4 > 0 else 0)
        signal_co_occur['MW_B2_any'].append(1 if b4+s4+a4 > 0 else 0)
        
        # ── 量价 ──
        kls = kline_idx.get(code, {})
        kl = kls.get(b1_date)
        if kl:
            kld_idx, kld = kl  # kld_idx=index in kline list, kld=kline data
            vol_ratios.append(kld['volume'] or 0)
            amounts.append(kld['amount'] or 0)
            
            # 20日均成交额
            kld_list = kline_dict.get(code, [])
            if kld_idx >= 19:
                amt_20 = [kld_list[i]['amount'] for i in range(kld_idx-19, kld_idx) if kld_list[i].get('amount')]
                if amt_20:
                    amt_20_avg = sum(amt_20) / len(amt_20)
                    if amt_20_avg > 0:
                        amount_ma20_ratios.append(kld['amount'] / amt_20_avg)
            
            # 收盘位置
            if kld['high'] != kld['low']:
                close_positions.append((kld['close'] - kld['low']) / (kld['high'] - kld['low']))
            
            # B1日涨跌幅
            if kld_idx > 0 and kld_list[kld_idx-1]['close'] > 0:
                b1_returns.append((kld['close'] - kld_list[kld_idx-1]['close']) / kld_list[kld_idx-1]['close'] * 100)
                pre_1d_returns.append((kld_list[kld_idx-1]['close'] - kld_list[kld_idx-2]['close']) / max(kld_list[kld_idx-2]['close'], 0.01) * 100 if kld_idx > 1 else 0)
            
            # 前5日涨跌幅
            if kld_idx >= 5:
                pre_5d_returns.append((kld['close'] - kld_list[kld_idx-5]['close']) / max(kld_list[kld_idx-5]['close'], 0.01) * 100)
            
            # 后3日涨跌幅
            if kld_idx + 3 < len(kld_list):
                post_3d_returns.append((kld_list[kld_idx+3]['close'] - kld['close']) / max(kld['close'], 0.01) * 100)
            
            # 跳空高开
            if kld_idx > 0 and kld_list[kld_idx-1]['high'] > 0:
                gaps.append(1 if kld['open'] > kld_list[kld_idx-1]['high'] else 0)
        
        # ── RS ──
        rs_data = rs_dict.get(code, {}).get(b1_date)
        if rs_data:
            stock_rs20.append(rs_data[0] or 0)
            stock_rs250.append(rs_data[1] or 0)
        
        # ── 行业RS ──
        mw = mw_dict.get((code, b1_date))
        if mw:
            ind_code = mw['ind_code']
            if ind_code:
                irs = irs_dict.get((ind_code, b1_date))
                if irs:
                    ind_rs250_list.append(irs[0] or 0)
                    ind_rs20_list.append(irs[1] or 0)
        
        # ── MW结构 ──
        if mw:
            if mw['decline_pct'] is not None: decline_pcts.append(mw['decline_pct'])
            if mw['h_rs250'] is not None: h_rs250s.append(mw['h_rs250'])
            if mw['h_rs20'] is not None: h_rs20s.append(mw['h_rs20'])
            if mw['c_amplitude_pct'] is not None: c_amplitudes.append(mw['c_amplitude_pct'])
            if mw['c_amount_avg'] is not None: c_amount_avgs.append(mw['c_amount_avg'])
            if mw['b1_vol_ratio'] is not None: b1_vol_ratios_mw.append(mw['b1_vol_ratio'])
            if mw['p_max_dd_pct'] is not None: p_max_dds.append(mw['p_max_dd_pct'])
            if mw['h_pre_rise_pct'] is not None: h_pre_rises.append(mw['h_pre_rise_pct'])
            
            if mw['score'] is not None: scores.append(mw['score'])
            if mw['score_v2'] is not None: score_v2s.append(mw['score_v2'])
            if mw['score_h'] is not None: score_hs.append(mw['score_h'])
            if mw['score_d'] is not None: score_ds.append(mw['score_d'])
            if mw['score_c'] is not None: score_cs.append(mw['score_c'])
            if mw['score_p'] is not None: score_ps.append(mw['score_p'])
            if mw['score_i1'] is not None: score_i1s.append(mw['score_i1'])
            if mw['score_i2'] is not None: score_i2s.append(mw['score_i2'])
            if mw['score_gap'] is not None: score_gaps.append(mw['score_gap'])
            if mw['score_sig'] is not None: score_sigs.append(mw['score_sig'])
            
            if mw['is_plus'] == 1: is_plus_count += 1
            if mw['b2_date']: b2_confirmed += 1
            if mw['b2_is_gap']: b2_is_gap_count += 1
            if mw['b2_return_pct'] is not None: b2_returns.append(mw['b2_return_pct'])
            
            if mw['ind_name']: industries[mw['ind_name']] += 1
        
        # ── 市场环境 ──
        regimes[r['market_regime']] += 1
        months[dt.strftime('%Y-%m')] += 1
        signal_counts[r['signal_count']] += 1
    
    def safe_stats(arr):
        if not arr: return {'mean': None, 'median': None, 'std': None, 'n': 0}
        a = np.array(arr, dtype=np.float64)
        a = a[~np.isnan(a)]
        if len(a) == 0: return {'mean': None, 'median': None, 'std': None, 'n': 0}
        return {'mean': float(np.mean(a)), 'median': float(np.median(a)),
                'std': float(np.std(a)), 'n': len(a)}
    
    def safe_pct(arr):
        if not arr: return 0.0
        return sum(arr) / len(arr) * 100
    
    return {
        'n': n,
        'performance': {
            'net_ret_mean': np.mean([r['net_ret_pct'] for r in rows_list]),
            'net_ret_median': np.median([r['net_ret_pct'] for r in rows_list]),
            'gross_ret_mean': np.mean([r['gross_ret'] for r in rows_list]),
            'win_rate': np.mean([r['is_win'] for r in rows_list]) * 100,
            'excess_ret_mean': np.mean([r['excess_ret_pct'] for r in rows_list]),
        },
        'signal_co_occurrence': {
            'PP_V1': {
                'before': safe_pct(signal_co_occur['PP_V1_before']),
                'same_day': safe_pct(signal_co_occur['PP_V1_same']),
                'after': safe_pct(signal_co_occur['PP_V1_after']),
                'any_in_window': safe_pct(signal_co_occur['PP_V1_any']),
            },
            'PP_V2': {
                'before': safe_pct(signal_co_occur['PP_V2_before']),
                'same_day': safe_pct(signal_co_occur['PP_V2_same']),
                'after': safe_pct(signal_co_occur['PP_V2_after']),
                'any_in_window': safe_pct(signal_co_occur['PP_V2_any']),
            },
            'BO_V2': {
                'before': safe_pct(signal_co_occur['BO_V2_before']),
                'same_day': safe_pct(signal_co_occur['BO_V2_same']),
                'after': safe_pct(signal_co_occur['BO_V2_after']),
                'any_in_window': safe_pct(signal_co_occur['BO_V2_any']),
            },
            'MW_B2': {
                'before': safe_pct(signal_co_occur['MW_B2_before']),
                'same_day': safe_pct(signal_co_occur['MW_B2_same']),
                'after': safe_pct(signal_co_occur['MW_B2_after']),
                'any_in_window': safe_pct(signal_co_occur['MW_B2_any']),
            },
        },
        'volume_price': {
            'b1_vol_ratio': safe_stats(vol_ratios),
            'b1_amount': safe_stats(amounts),
            'amount_vs_ma20_ratio': safe_stats(amount_ma20_ratios),
            'b1_close_position': safe_stats(close_positions),
            'b1_return_pct': safe_stats(b1_returns),
            'pre_1d_return': safe_stats(pre_1d_returns),
            'pre_5d_return': safe_stats(pre_5d_returns),
            'post_3d_return': safe_stats(post_3d_returns),
            'gap_up_rate': safe_pct(gaps),
        },
        'rs_strength': {
            'stock_rs20': safe_stats(stock_rs20),
            'stock_rs250': safe_stats(stock_rs250),
            'ind_rs250': safe_stats(ind_rs250_list),
            'ind_rs20': safe_stats(ind_rs20_list),
        },
        'mw_structure': {
            'decline_pct': safe_stats(decline_pcts),
            'h_rs250': safe_stats(h_rs250s),
            'h_rs20': safe_stats(h_rs20s),
            'c_amplitude': safe_stats(c_amplitudes),
            'c_amount_avg': safe_stats(c_amount_avgs),
            'b1_vol_ratio': safe_stats(b1_vol_ratios_mw),
            'p_max_dd': safe_stats(p_max_dds),
            'h_pre_rise': safe_stats(h_pre_rises),
        },
        'mw_scoring': {
            'score': safe_stats(scores),
            'score_v2': safe_stats(score_v2s),
            'score_h': safe_stats(score_hs),
            'score_d': safe_stats(score_ds),
            'score_c': safe_stats(score_cs),
            'score_p': safe_stats(score_ps),
            'score_i1': safe_stats(score_i1s),
            'score_i2': safe_stats(score_i2s),
            'score_gap': safe_stats(score_gaps),
            'score_sig': safe_stats(score_sigs),
            'is_plus_pct': is_plus_count / n * 100,
            'b2_confirmed_pct': b2_confirmed / n * 100,
            'b2_is_gap_pct': b2_is_gap_count / n * 100 if b2_confirmed > 0 else 0,
            'b2_return': safe_stats(b2_returns),
        },
        'market_regime': dict(regimes.most_common()),
        'top_months': dict(months.most_common(10)),
        'top_industries': dict(industries.most_common(15)),
        'signal_count_dist': dict(signal_counts.most_common()),
    }


# ═══════════════════ 执行分析 ═══════════════════
print(f'\n{"=" * 70}')
print(f'分析 Top {N} (表现最好)')
print(f'{"=" * 70}')
top_analysis = analyze_group(top, 'Top', mw_dict, rs_dict, kline_idx,
                              ppv1_dict, ppv2_dict, bo_dict, mw_b2_dict, irs_dict)

print(f'\n{"=" * 70}')
print(f'分析 Bottom {N} (表现最差) - 作为对照组')
print(f'{"=" * 70}')
bottom_analysis = analyze_group(bottom, 'Bottom', mw_dict, rs_dict, kline_idx,
                                 ppv1_dict, ppv2_dict, bo_dict, mw_b2_dict, irs_dict)

# ═══════════════════ 对比输出 ═══════════════════
def print_comparison(key_path, top_val, bottom_val, label, fmt='.2f', unit=''):
    """打印Top vs Bottom对比"""
    tv = top_val
    bv = bottom_val
    
    if isinstance(tv, dict) and 'mean' in tv:
        tv = tv['mean']
    if isinstance(bv, dict) and 'mean' in bv:
        bv = bv['mean']
    
    if tv is None or bv is None:
        return
    
    diff = tv - bv
    direction = '↑' if diff > 0 else ('↓' if diff < 0 else '→')
    
    if fmt == '.0f':
        ts = f'{tv:.0f}{unit}'
        bs = f'{bv:.0f}{unit}'
    elif fmt == '.2f':
        ts = f'{tv:.2f}{unit}'
        bs = f'{bv:.2f}{unit}'
    elif fmt == '.1f':
        ts = f'{tv:.1f}{unit}'
        bs = f'{bv:.1f}{unit}'
    else:
        ts = f'{tv}{unit}'
        bs = f'{bv}{unit}'
    
    print(f'  {label:<35s} Top={ts:>10s}  Bottom={bs:>10s}  {direction}')

print(f'\n{"=" * 70}')
print(f'Top {N} vs Bottom {N} 对比分析')
print(f'{"=" * 70}')

t = top_analysis
b = bottom_analysis

print(f'\n── 表现 ──')
print(f'  {"平均净收益":<35s} Top={t["performance"]["net_ret_mean"]:.2f}%  Bottom={b["performance"]["net_ret_mean"]:.2f}%')
print(f'  {"胜率":<35s} Top={t["performance"]["win_rate"]:.1f}%  Bottom={b["performance"]["win_rate"]:.1f}%')
print(f'  {"超额收益(相对基准)":<35s} Top={t["performance"]["excess_ret_mean"]:.2f}%  Bottom={b["performance"]["excess_ret_mean"]:.2f}%')

print(f'\n── 共现信号（B1前后5+3天窗口内）──')
for sig in ['PP_V1', 'PP_V2', 'BO_V2', 'MW_B2']:
    print(f'  [{sig}]')
    for key, label in [('before', '前5天内'), ('same_day', '同日'), ('after', '后3天内'), ('any_in_window', '窗口内任一')]:
        print_comparison(f'  {label}', 
                        t['signal_co_occurrence'][sig][key],
                        b['signal_co_occurrence'][sig][key],
                        f'    {label}', '.1f', '%')

print(f'\n── 量价特征 ──')
for key, label in [
    ('b1_vol_ratio', 'B1日成交量(股)'),
    ('amount_vs_ma20_ratio', 'B1日成交额/20日均'),
    ('b1_close_position', 'B1日收盘在K线位置(0~1)'),
    ('b1_return_pct', 'B1日涨跌幅'),
    ('pre_1d_return', 'B1前1日涨跌幅'),
    ('pre_5d_return', 'B1前5日涨跌幅'),
    ('post_3d_return', 'B1后3日涨跌幅'),
    ('gap_up_rate', '跳空高开占比'),
]:
    print_comparison(label, t['volume_price'][key], b['volume_price'][key], label, 
                    '.1f' if 'rate' in key or 'pct' in key else '.2f',
                    '%' if ('rate' in key or 'pct' in key or 'return' in key) else '')

print(f'\n── RS强度 ──')
for key, label in [
    ('stock_rs20', 'B1日个股RPS20'),
    ('stock_rs250', 'B1日个股RPS250'),
    ('ind_rs250', 'B1日行业RS250'),
    ('ind_rs20', 'B1日行业RS20'),
]:
    print_comparison(label, t['rs_strength'][key], b['rs_strength'][key], label, '.1f')

print(f'\n── MW结构 ──')
for key, label in [
    ('decline_pct', '调整深度(H→L跌幅)'),
    ('h_rs250', '前高时个股RS250'),
    ('c_amplitude', '横盘区振幅'),
    ('b1_vol_ratio', 'B1量比(引擎计算)'),
    ('h_pre_rise', '前高前涨幅'),
    ('p_max_dd', '整理期最大回撤'),
]:
    print_comparison(label, t['mw_structure'][key], b['mw_structure'][key], label, '.1f',
                    '%' if 'pct' in key or 'amplitude' in key or 'rise' in key or 'dd' in key else '')

print(f'\n── MW评分 ──')
for key, label in [
    ('score', '体系1总分'),
    ('score_v2', '体系2总分'),
    ('score_h', 'H前高趋势分'),
    ('score_d', 'D调整深度分'),
    ('score_c', 'C横盘质量分'),
    ('score_i1', 'I1行业RS分'),
    ('score_i2', 'I2个股RS分'),
    ('score_sig', 'Sig信号共振分'),
    ('is_plus_pct', 'PLUS占比'),
    ('b2_confirmed_pct', 'B2确认率(B1后有B2)'),
    ('b2_return', 'B2日收益(如有)'),
]:
    print_comparison(label, t['mw_scoring'][key], b['mw_scoring'][key], label,
                    '.1f', '%' if 'pct' in key or 'rate' in key else '')

print(f'\n── 市场环境 ──')
t_regime_total = sum(t['market_regime'].values()) or 1
b_regime_total = sum(b['market_regime'].values()) or 1
for regime in ['bull', 'bear', 'ranging']:
    tp = t['market_regime'].get(regime, 0) / t_regime_total * 100
    bp = b['market_regime'].get(regime, 0) / b_regime_total * 100
    print(f'  {regime:<10s} Top={tp:.1f}%  Bottom={bp:.1f}%')

print(f'\n── Top信号集中行业 ──')
for ind, cnt in list(t['top_industries'].items())[:15]:
    pct = cnt / t['n'] * 100
    print(f'  {ind:<20s} {cnt:>4d} ({pct:.1f}%)')

print(f'\n── Top信号集中月份 ──')
for month, cnt in list(t['top_months'].items())[:10]:
    pct = cnt / t['n'] * 100
    print(f'  {month}  {cnt:>4d} ({pct:.1f}%)')

print(f'\n── 信号共振计数分布 ──')
print(f'  Top: {t["signal_count_dist"]}')

# ── 最后：输出Top信号的基础信息（供进一步人工检查）──
print(f'\n{"=" * 70}')
print(f'Top 20 B1信号详情')
print(f'{"=" * 70}')
print(f'{"代码":<8s} {"名称":<10s} {"B1日期":<12s} {"净收益":>8s} {"行业":<15s} {"decline":>7s} {"hRS250":>7s} {"共现信号":<20s}')
for i, r in enumerate(top[:20]):
    mw = mw_dict.get((r['stock_code'], r['b1_date']))
    name = mw['ind_name'][:10] if mw and mw.get('ind_name') else ''
    decline = f'{mw["decline_pct"]:.1f}%' if mw and mw.get('decline_pct') else '-'
    hrs = f'{mw["h_rs250"]}' if mw and mw.get('h_rs250') else '-'
    
    # 共现信号
    coex = []
    mask = r['signal_mask']
    for bit, name_sig in [(0,'B1'),(1,'B2'),(2,'+'),(3,'V1'),(4,'V2'),(5,'BO')]:
        if mask & (1 << bit): coex.append(name_sig)
    
    print(f'{r["stock_code"]:<8s} {name:<10s} {r["b1_date"]:<12s} {r["net_ret_pct"]:>7.1f}% {r.get("ind_name","")[:15]:<15s} {decline:>7s} {hrs:>7s} {"+".join(coex):<20s}')

print(f'\n分析完成。')
print(f'数据文件建议保存为 D:/hanako/investment-system/data/mw_b1_analysis.json')
