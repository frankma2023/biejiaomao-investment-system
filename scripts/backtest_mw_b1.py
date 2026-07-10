#!/usr/bin/env python
"""
MW B1 全周期专项回测分析 v1.0
================================
基于 backtest_results（2016-2026）对 MW B1 信号做全维度分析。

核心产出：
  - B1 技术置信度五级分层表现
  - B1+B2 确认 vs B1-only 对比
  - PP_V1 共现窗口分析
  - 市场环境分层
  - 子周期一致性检验（20个半年）
  - 信号改善规则实证验证
  - config/strategy/mw_b1.yaml 输出

用法：
  python scripts/backtest_mw_b1.py
"""

import sys, os, sqlite3, json, yaml, math
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT, 'data', 'lixinger.db')
CONFIG_DIR = os.path.join(PROJECT, 'config', 'strategy')
os.makedirs(CONFIG_DIR, exist_ok=True)

# ── 技术置信度五级分层 ──
TS_TIERS = [
    (85, 100, '极高'),
    (75, 84, '很高'),
    (65, 74, '高'),
    (50, 64, '中'),
    (0, 49, '低'),
]

# ── 子周期 ──
def get_half(date_str):
    """返回 YYYYH1 或 YYYYH2"""
    m = int(date_str[5:7])
    return f"{date_str[:4]}H{'1' if m <= 6 else '2'}"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def compute_stats(rets, label=''):
    """基础统计"""
    if not rets:
        return {'count': 0, 'win_rate': 0, 'median_ret': 0, 'mean_ret': 0, 'std': 0}
    arr = np.array(rets)
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    avg_win = float(np.mean(wins)) if wins.size > 0 else 0
    avg_loss = float(np.mean(losses)) if losses.size > 0 else 0
    win_rate = len(wins) / len(arr)
    # 凯利
    b_val = avg_win / abs(avg_loss) if avg_loss != 0 and abs(avg_loss) > 0.1 else 0
    if b_val > 0:
        k = max(0, min(0.5, win_rate - (1 - win_rate) / b_val))
    else:
        k = 0
    return {
        'count': len(arr),
        'win_rate': round(win_rate * 100, 1),
        'median_ret': round(float(np.median(arr)), 2),
        'mean_ret': round(float(np.mean(arr)), 2),
        'std': round(float(np.std(arr)), 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'kelly': round(k, 4),
        'worst_1pct': round(float(np.percentile(arr, 1)), 2),
        'var_95': round(float(np.percentile(arr, 5)), 2),
    }


def main():
    db = get_db()
    print("=" * 60)
    print("MW B1 全周期专项回测 (2016-2026)")
    print("=" * 60)
    
    # ═══════════════════════════════════════════
    # 1. 加载 MW B1 信号 + 关联回测结果
    # ═══════════════════════════════════════════
    print("\n[1] 加载 MW B1 信号 + 回测数据...")
    
    # mw_signal_daily 中 B1 信号（含 B2 确认信息和 tech_score）
    b1_rows = db.execute("""
        SELECT stock_code, stock_name, b1_date, b2_date, 
               tech_score, score as mw_score, confidence,
               decline_pct, h_rs250, b1_return_pct, b1_vol_ratio,
               score_h, score_d, score_c,
               score_i1, score_i2, score_sig,
               is_plus
        FROM mw_signal_daily 
        WHERE b1_date IS NOT NULL AND b1_date >= '2016-01-01' AND b1_date <= '2026-07-03'
          AND stock_code != '_sentinel_'
    """).fetchall()
    print(f"  MW B1 信号: {len(b1_rows)} 条")
    
    # 预加载 backtest_results（MW_B1 的 H20/T+1_O）
    bt_rows = db.execute("""
        SELECT stock_code, signal_date, net_ret_pct, is_win, 
               peak_ret_pct, trough_ret_pct, index_ret_pct, excess_ret_pct,
               market_regime
        FROM backtest_results 
        WHERE signal_mask & 1 = 1 AND entry_method='T+1_O' AND hold_days=20
    """).fetchall()
    
    bt_map = {}
    for r in bt_rows:
        bt_map[(r['stock_code'], r['signal_date'])] = r
    print(f"  回测结果(H20/T+1_O): {len(bt_map)} 条匹配")
    
    # 预加载 PP_V1 信号日期（用于共现分析）
    pp_dates = defaultdict(set)
    for r in db.execute("""
        SELECT stock_code, date FROM pocket_pivot_daily 
        WHERE engine_version='V1' AND date >= '2015-12-01' AND date <= '2026-07-30'
    """):
        pp_dates[r['stock_code']].add(r['date'])
    
    # ═══════════════════════════════════════════
    # 2. 关联 B1 信号与回测结果
    # ═══════════════════════════════════════════
    records = []
    for s in b1_rows:
        key = (s['stock_code'], s['b1_date'])
        bt = bt_map.get(key)
        if not bt:
            continue
        # B2 确认状态（B2必须在B1之后）
        has_b2 = (s['b2_date'] is not None and s['b2_date'] > '' 
                  and s['b2_date'] > s['b1_date'])
        # TS 层级
        ts = s['tech_score'] or 0
        ts_tier = '低'
        for lo, hi, label in TS_TIERS:
            if lo <= ts <= hi:
                ts_tier = label
                break
        # PP_V1 共现窗口
        pp_win = 'none'
        b1_dt = datetime.strptime(s['b1_date'], '%Y-%m-%d')
        pp_set = pp_dates.get(s['stock_code'], set())
        for d_offset in range(1, 6):
            day = (b1_dt + timedelta(days=d_offset)).strftime('%Y-%m-%d')
            if day in pp_set:
                pp_win = f'after_{d_offset}d'
                break
        if pp_win == 'none':
            for d_offset in range(1, 6):
                day = (b1_dt - timedelta(days=d_offset)).strftime('%Y-%m-%d')
                if day in pp_set:
                    pp_win = f'before_{d_offset}d'
                    break
        
        records.append({
            'code': s['stock_code'], 'name': s['stock_name'],
            'b1_date': s['b1_date'], 'b2_date': s['b2_date'],
            'has_b2': has_b2, 'is_plus': s['is_plus'] == 1,
            'tech_score': ts, 'ts_tier': ts_tier,
            'mw_score': s['mw_score'], 'confidence': s['confidence'],
            'decline_pct': s['decline_pct'],
            'h_rs250': s['h_rs250'],
            'b1_return_pct': s['b1_return_pct'],
            'b1_vol_ratio': s['b1_vol_ratio'],
            'score_h': s['score_h'], 'score_d': s['score_d'], 'score_c': s['score_c'],
            'score_i1': s['score_i1'], 'score_i2': s['score_i2'], 'score_sig': s['score_sig'],
            'net_ret': bt['net_ret_pct'],
            'is_win': bt['is_win'],
            'regime': bt['market_regime'],
            'half': get_half(s['b1_date']),
            'pp_win': pp_win,
        })
    
    print(f"  有效记录（有回测数据）: {len(records)}")
    
    # ═══════════════════════════════════════════
    # 3. 全量统计
    # ═══════════════════════════════════════════
    print("\n[2] 全量统计...")
    all_rets = [r['net_ret'] for r in records]
    all_stats = compute_stats(all_rets)
    
    b2_rets = [r['net_ret'] for r in records if r['has_b2']]
    b2_stats = compute_stats(b2_rets, 'B1+B2')
    
    b1_only_rets = [r['net_ret'] for r in records if not r['has_b2']]
    b1only_stats = compute_stats(b1_only_rets, 'B1-only')
    
    plus_rets = [r['net_ret'] for r in records if r['is_plus']]
    plus_stats = compute_stats(plus_rets, 'PLUS')
    
    print(f"\n  {'':20s} {'样本':>6s} {'胜率':>6s} {'均收益':>7s} {'中位':>7s} {'凯利':>7s}")
    print(f"  {'全量':20s} {all_stats['count']:>6d} {all_stats['win_rate']:>5.1f}% {all_stats['mean_ret']:>6.1f}% {all_stats['median_ret']:>6.1f}% {all_stats['kelly']:>7.4f}")
    print(f"  {'B1+B2确认':20s} {b2_stats['count']:>6d} {b2_stats['win_rate']:>5.1f}% {b2_stats['mean_ret']:>6.1f}% {b2_stats['median_ret']:>6.1f}% {b2_stats['kelly']:>7.4f}")
    print(f"  {'B1-only(无B2)':20s} {b1only_stats['count']:>6d} {b1only_stats['win_rate']:>5.1f}% {b1only_stats['mean_ret']:>6.1f}% {b1only_stats['median_ret']:>6.1f}% {b1only_stats['kelly']:>7.4f}")
    print(f"  {'PLUS':20s} {plus_stats['count']:>6d} {plus_stats['win_rate']:>5.1f}% {plus_stats['mean_ret']:>6.1f}% {plus_stats['median_ret']:>6.1f}% {plus_stats['kelly']:>7.4f}")
    
    # ═══════════════════════════════════════════
    # 4. 技术置信度分层
    # ═══════════════════════════════════════════
    print("\n[3] 技术置信度五级分层 (H20/T+1_O)...")
    ts_data = {}
    for lo, hi, label in TS_TIERS:
        rets = [r['net_ret'] for r in records if lo <= r['tech_score'] <= hi]
        b2_rets_ts = [r['net_ret'] for r in records if lo <= r['tech_score'] <= hi and r['has_b2']]
        b2_rate = len([r for r in records if lo <= r['tech_score'] <= hi and r['has_b2']]) / max(len([r for r in records if lo <= r['tech_score'] <= hi]), 1)
        ts_data[label] = {
            'range': f'{lo}-{hi}',
            'count': len(rets),
            'b2_rate': round(b2_rate * 100, 1),
            'all': compute_stats(rets),
            'b2_confirmed': compute_stats(b2_rets_ts) if b2_rets_ts else None,
        }
    
    print(f"  {'层级':6s} {'信号数':>7s} {'B2率':>6s} {'胜率':>6s} {'均收益':>7s} {'凯利':>7s} {'B2后胜率':>9s} {'B2后收益':>9s}")
    for label in ['极高', '很高', '高', '中', '低']:
        d = ts_data[label]
        b2 = d['b2_confirmed']
        b2_wr = f"{b2['win_rate']:.1f}%" if b2 and b2['count'] > 0 else '—'
        b2_ar = f"{b2['mean_ret']:.1f}%" if b2 and b2['count'] > 0 else '—'
        print(f"  {label:6s} {d['count']:>7d} {d['b2_rate']:>5.1f}% {d['all']['win_rate']:>5.1f}% {d['all']['mean_ret']:>6.1f}% {d['all']['kelly']:>7.4f} {b2_wr:>9s} {b2_ar:>9s}")
    
    # ═══════════════════════════════════════════
    # 5. 市场环境分层
    # ═══════════════════════════════════════════
    print("\n[4] 市场环境 × 技术置信度...")
    env_data = {}
    for regime in ['all', 'bull', 'bear', 'ranging']:
        env_data[regime] = {}
        for lo, hi, label in TS_TIERS:
            rets = [r['net_ret'] for r in records 
                    if (regime == 'all' or r['regime'] == regime) and lo <= r['tech_score'] <= hi]
            env_data[regime][label] = compute_stats(rets) if rets else {'count': 0}
    
    # 只显示关键切片
    for regime in ['bull', 'bear', 'ranging']:
        tier_labels = ['极高', '很高', '高', '中', '低']
        print(f"  [{regime}] {'极高':>8s} {'很高':>8s} {'高':>8s} {'中':>8s} {'低':>8s}")
        for metric in ['count', 'win_rate', 'mean_ret']:
            vals = []
            for t in tier_labels:
                d = env_data[regime][t]
                if d['count'] <= 0:
                    vals.append('       —')
                elif metric == 'count':
                    vals.append(f"{d['count']:>8d}")
                elif metric == 'win_rate':
                    vals.append(f"{d['win_rate']:>7.1f}%")
                else:
                    vals.append(f"{d['mean_ret']:>7.1f}%")
            label_m = {'count': '信号数', 'win_rate': '胜率  ', 'mean_ret': '均收益'}[metric]
            print(f"    {label_m} " + ' '.join(vals))
    
    # ═══════════════════════════════════════════
    # 6. 子周期一致性
    # ═══════════════════════════════════════════
    print("\n[5] 子周期一致性检验...")
    halves = sorted(set(r['half'] for r in records))
    half_data = {}
    for h in halves:
        rets = [r['net_ret'] for r in records if r['half'] == h]
        rets_hi = [r['net_ret'] for r in records if r['half'] == h and r['tech_score'] >= 75]
        b2_rets_h = [r['net_ret'] for r in records if r['half'] == h and r['has_b2']]
        half_data[h] = {
            'all': compute_stats(rets),
            'ts_high': compute_stats(rets_hi) if rets_hi else {'count': 0},
            'b2': compute_stats(b2_rets_h) if b2_rets_h else {'count': 0},
        }
    
    # 一致性检验：高置信(TS≥75) 在多少子周期上正收益
    pos_ts_high = sum(1 for h in halves if half_data[h]['ts_high'].get('mean_ret', -999) > 0)
    total_h = len(halves)
    consistency = pos_ts_high / total_h * 100
    print(f"  高置信(TS≥75) 正收益子周期: {pos_ts_high}/{total_h} ({consistency:.0f}%)")
    
    pos_b2 = sum(1 for h in halves if half_data[h]['b2'].get('mean_ret', -999) > 0)
    b2_pos = sum(1 for h in halves if half_data[h]['b2'].get('count', 0) >= 5)
    consistency_b2 = pos_b2 / max(b2_pos, 1) * 100
    print(f"  B1+B2 正收益子周期: {pos_b2}/{b2_pos} ({consistency_b2:.0f}%) (>=5样本)")
    
    print(f"\n  {'半期':6s} {'全信号':>7s} {'胜率':>6s} {'均收益':>7s} {'TS≥75':>7s} {'TS≥75胜率':>9s} {'TS≥75收益':>9s}")
    for h in halves:
        d = half_data[h]
        ts = d['ts_high']
        ts_cnt = f"{ts['count']:>7d}" if ts['count'] > 0 else '      —'
        ts_wr = f"{ts['win_rate']:>8.1f}%" if ts['count'] > 0 else '       —'
        ts_ar = f"{ts['mean_ret']:>8.1f}%" if ts['count'] > 0 else '       —'
        print(f"  {h:6s} {d['all']['count']:>7d} {d['all']['win_rate']:>5.1f}% {d['all']['mean_ret']:>6.1f}% {ts_cnt} {ts_wr} {ts_ar}")
    
    # ═══════════════════════════════════════════
    # 7. PP_V1 共现分析
    # ═══════════════════════════════════════════
    print("\n[6] PP_V1 共现窗口分析...")
    pp_groups = defaultdict(list)
    for r in records:
        pp_groups[r['pp_win']].append(r['net_ret'])
    
    print(f"  {'窗口':15s} {'信号数':>7s} {'胜率':>6s} {'均收益':>7s}")
    for win_label in ['none', 'before_1d', 'before_2-5d', 'after_1-3d', 'after_4-5d']:
        if win_label == 'before_2-5d':
            rets = [r['net_ret'] for r in records if r['pp_win'] in ['before_2d','before_3d','before_4d','before_5d']]
        elif win_label == 'after_1-3d':
            rets = [r['net_ret'] for r in records if r['pp_win'] in ['after_1d','after_2d','after_3d']]
        elif win_label == 'after_4-5d':
            rets = [r['net_ret'] for r in records if r['pp_win'] in ['after_4d','after_5d']]
        else:
            rets = pp_groups.get(win_label, [])
        s = compute_stats(rets)
        print(f"  {win_label:15s} {s['count']:>7d} {s['win_rate']:>5.1f}% {s['mean_ret']:>6.1f}%")
    
    # TS≥75 子集
    print(f"\n  [TS≥75 子集]")
    for win_label in ['none', 'before_1d', 'before_2-5d', 'after_1-3d', 'after_4-5d']:
        if win_label == 'before_2-5d':
            rets = [r['net_ret'] for r in records if r['tech_score'] >= 75 and r['pp_win'] in ['before_2d','before_3d','before_4d','before_5d']]
        elif win_label == 'after_1-3d':
            rets = [r['net_ret'] for r in records if r['tech_score'] >= 75 and r['pp_win'] in ['after_1d','after_2d','after_3d']]
        elif win_label == 'after_4-5d':
            rets = [r['net_ret'] for r in records if r['tech_score'] >= 75 and r['pp_win'] in ['after_4d','after_5d']]
        else:
            rets = [r['net_ret'] for r in records if r['tech_score'] >= 75 and r['pp_win'] == win_label]
        s = compute_stats(rets)
        print(f"  {win_label:15s} {s['count']:>7d} {s['win_rate']:>5.1f}% {s['mean_ret']:>6.1f}%")
    
    # ═══════════════════════════════════════════
    # 8. 信号改善规则验证
    # ═══════════════════════════════════════════
    print("\n[7] 信号改善规则实证验证...")
    
    # 8.1 前高 RS250 门禁
    print("\n  8.1 前高 RS250 门禁 (h_rs250 ≥ 60)...")
    for gate, label in [(True, 'h_rs250≥60'), (False, '无门禁')]:
        rets = [r['net_ret'] for r in records if (r['h_rs250'] and r['h_rs250'] >= 60) == gate or (not gate and not (r['h_rs250'] and r['h_rs250'] >= 60))]
        b2r = len([r for r in records if r['has_b2'] and ((r['h_rs250'] and r['h_rs250'] >= 60) == gate)]) / max(len(rets), 1)
        s = compute_stats(rets)
        print(f"    {label}: {s['count']}条 B2率={b2r*100:.1f}% 胜率={s['win_rate']:.1f}% 均收益={s['mean_ret']:.1f}%")
    
    # 8.2 B1 涨幅上限
    print("\n  8.2 B1 涨幅区间分析...")
    for lo, hi, label in [(0, 3, '<3%'), (3, 5, '3-5%'), (5, 8, '5-8%'), (8, 100, '>8%')]:
        rets = [r['net_ret'] for r in records if r['b1_return_pct'] and lo <= r['b1_return_pct'] < hi]
        s = compute_stats(rets)
        print(f"    B1涨幅{label}: {s['count']}条 胜率={s['win_rate']:.1f}% 均收益={s['mean_ret']:.1f}%")
    
    # 8.3 调整深度区间
    print("\n  8.3 调整深度区间...")
    for lo, hi, label in [(0, 15, '<15%'), (15, 20, '15-20%'), (20, 35, '20-35%'), (35, 100, '>35%')]:
        rets = [r['net_ret'] for r in records if r['decline_pct'] and lo <= r['decline_pct'] < hi]
        s = compute_stats(rets)
        print(f"    调整{label}: {s['count']}条 胜率={s['win_rate']:.1f}% 均收益={s['mean_ret']:.1f}%")
    
    # 8.4 C 横盘质量（H/D/C 因子相关性）
    print("\n  8.4 因子与收益相关系数...")
    for factor, field in [('H(前高趋势)', 'score_h'), ('D(调整深度)', 'score_d'), ('C(横盘质量)', 'score_c'),
                            ('I1(行业RS)', 'score_i1'), ('I2(个股RS)', 'score_i2'), ('Sig(共振)', 'score_sig')]:
        vals = [(r[field] or 0, r['net_ret']) for r in records if r[field] is not None]
        if len(vals) > 10:
            xs = [v[0] for v in vals]
            ys = [v[1] for v in vals]
            corr = np.corrcoef(xs, ys)[0, 1] if np.std(xs) > 0 else 0
            print(f"    {factor:15s}: r={corr:+.4f}")
    
    # ═══════════════════════════════════════════
    # 9. 输出 YAML
    # ═══════════════════════════════════════════
    print("\n[8] 写入 config/strategy/mw_b1.yaml ...")
    
    output = {
        'meta': {
            'version': '2.0',
            'date': '2026-07-07',
            'data_range': '2016-01-01 ~ 2026-07-03',
            'signal_source': 'mw_signal_daily',
            'backtest_source': 'backtest_results (H20/T+1_O)',
            'total_signals': len(records),
        },
        'overview': {
            'all': all_stats,
            'b1_b2_confirmed': b2_stats,
            'b1_only': b1only_stats,
            'plus': plus_stats,
        },
        'tech_score_tiers': ts_data,
        'market_regime': {},
        'sub_periods': {},
        'pp_v1_cooccurrence': {},
        'improvement_tests': {
            'rs250_gate': {},
            'b1_return_buckets': {},
            'decline_buckets': {},
            'factor_correlations': {},
        },
    }
    
    # 市场环境
    for regime in ['bull', 'bear', 'ranging']:
        output['market_regime'][regime] = {
            label: env_data[regime][label]
            for label in ['极高', '很高', '高', '中', '低']
            if env_data[regime][label]['count'] > 0
        }
    
    # 子周期
    for h in halves:
        output['sub_periods'][h] = {
            'all': half_data[h]['all'],
            'ts_high': half_data[h]['ts_high'],
            'b2': half_data[h]['b2'],
        }
    
    # PP_V1
    for win_label in ['none', 'after_1d', 'after_2d', 'after_3d', 'after_4d', 'after_5d',
                        'before_1d', 'before_2d', 'before_3d', 'before_4d', 'before_5d']:
        rets = pp_groups.get(win_label, [])
        if rets:
            output['pp_v1_cooccurrence'][win_label] = compute_stats(rets)
    
    with open(os.path.join(CONFIG_DIR, 'mw_b1.yaml'), 'w', encoding='utf-8') as f:
        yaml.dump(output, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    
    print(f"  已写入 config/strategy/mw_b1.yaml")
    print(f"\n{'='*60}")
    print("MW B1 全周期专项回测完成")
    print(f"{'='*60}")
    
    db.close()


if __name__ == '__main__':
    main()
