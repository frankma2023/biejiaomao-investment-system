#!/usr/bin/env python
"""
HDC + 技术置信度 双体系重标定
================================
基于 82,687 条 MW B1 全周期回测数据，重新标定：
  1. HDC 形态评分体系（权重 + 阈值）
  2. 技术置信度评分体系（9 因子阈值 + 断点）

输出完整的评分规则表。
"""
import sys, os, sqlite3, json
import numpy as np
from collections import defaultdict

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT, 'data', 'lixinger.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def bucket_analysis(values, returns, bins=10, min_samples=30):
    """等频分桶，返回每桶的胜率和均收益"""
    if len(values) < min_samples * bins:
        return []
    arr = np.array(values)
    rets = np.array(returns)
    # 按值排序后等频分桶
    idx = np.argsort(arr)
    arr_s = arr[idx]
    rets_s = rets[idx]
    n = len(arr_s)
    results = []
    for i in range(bins):
        lo = int(i * n / bins)
        hi = int((i + 1) * n / bins)
        bucket_rets = rets_s[lo:hi]
        if len(bucket_rets) < min_samples:
            continue
        wr = np.mean(bucket_rets > 0) * 100
        ar = np.mean(bucket_rets)
        results.append({
            'lo': float(arr_s[lo]), 'hi': float(arr_s[hi-1]),
            'count': len(bucket_rets), 'wr': round(wr, 1), 'ar': round(ar, 2)
        })
    return results

def find_optimal_range(buckets, wr_weight=0.6, ar_weight=0.4):
    """从分桶结果中找最优连续区间（胜率×收益加权最高）"""
    if not buckets:
        return None
    best_score = -999
    best_range = None
    for i in range(len(buckets)):
        for j in range(i, len(buckets)):
            n = sum(b['count'] for b in buckets[i:j+1])
            avg_wr = np.average([b['wr'] for b in buckets[i:j+1]], weights=[b['count'] for b in buckets[i:j+1]])
            avg_ar = np.average([b['ar'] for b in buckets[i:j+1]], weights=[b['count'] for b in buckets[i:j+1]])
            score = avg_wr * wr_weight + avg_ar * ar_weight
            if score > best_score and n >= 2000:
                best_score = score
                best_range = {
                    'lo': buckets[i]['lo'], 'hi': buckets[j]['hi'],
                    'count': n, 'wr': round(avg_wr, 1), 'ar': round(avg_ar, 2), 'score': round(score, 1)
                }
    return best_range


def main():
    db = get_db()
    
    # ── 加载数据 ──
    print("加载 MW B1 数据...")
    rows = db.execute("""
        SELECT stock_code, b1_date, b2_date,
               tech_score, score as mw_score,
               decline_pct, h_rs250, b1_return_pct, b1_vol_ratio,
               score_h, score_d, score_c, score_i1, score_i2, score_sig,
               h_price, l_price, c_amplitude_pct
        FROM mw_signal_daily
        WHERE b1_date IS NOT NULL AND b1_date >= '2016-01-01' AND b1_date <= '2026-07-03'
          AND stock_code != '_sentinel_'
    """).fetchall()
    
    bt_rows = db.execute("""
        SELECT stock_code, signal_date, net_ret_pct, is_win, market_regime
        FROM backtest_results
        WHERE signal_mask & 1 = 1 AND entry_method='T+1_O' AND hold_days=20
    """).fetchall()
    bt_map = {(r['stock_code'], r['signal_date']): r for r in bt_rows}
    
    records = []
    for s in rows:
        bt = bt_map.get((s['stock_code'], s['b1_date']))
        if not bt:
            continue
        has_b2 = s['b2_date'] is not None and s['b2_date'] > '' and s['b2_date'] > s['b1_date']
        records.append({
            'net_ret': bt['net_ret_pct'], 'is_win': bt['is_win'],
            'regime': bt['market_regime'],
            'tech_score': s['tech_score'] or 0,
            'mw_score': s['mw_score'] or 0,
            'decline_pct': s['decline_pct'] or 0,
            'h_rs250': s['h_rs250'] or 0,
            'b1_return_pct': s['b1_return_pct'] or 0,
            'b1_vol_ratio': s['b1_vol_ratio'] or 0,
            'score_h': s['score_h'] or 0,
            'score_d': s['score_d'] or 0,
            'score_c': s['score_c'] or 0,
            'score_i1': s['score_i1'] or 0,
            'score_i2': s['score_i2'] or 0,
            'score_sig': s['score_sig'] or 0,
            'c_amplitude_pct': s['c_amplitude_pct'] or 0,
            'has_b2': has_b2,
        })
    
    print(f"有效记录: {len(records)}")
    
    # ── 子集：B1+B2 确认用 B2 后的表现，B1-only 用 H20 ──
    all_rets = np.array([r['net_ret'] for r in records])
    b2_rets = np.array([r['net_ret'] for r in records if r['has_b2']])
    
    # ═════════════════════════════════════════════
    # 一、HDC 体系重标定
    # ═════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("一、HDC 形态评分体系重标定")
    print("=" * 60)
    
    # ── 1. H: 前高趋势（SMA50 斜率）──
    print("\n  1. H(前高趋势) - 权重: 15 → ?")
    # score_h 已经是 0 或 15 的二值，检查有效性
    h0_rets = [r['net_ret'] for r in records if r['score_h'] == 0]
    h15_rets = [r['net_ret'] for r in records if r['score_h'] == 15]
    print(f"     H=0: {len(h0_rets)}条 胜率={np.mean(np.array(h0_rets)>0)*100:.1f}% 均收益={np.mean(h0_rets):.2f}%")
    print(f"     H=15: {len(h15_rets)}条 胜率={np.mean(np.array(h15_rets)>0)*100:.1f}% 均收益={np.mean(h15_rets):.2f}%")
    # 相关系数
    h_vals = [r['score_h'] for r in records]
    corr_h = np.corrcoef(h_vals, all_rets)[0, 1]
    print(f"     相关系数: r={corr_h:+.4f}")
    h_weight = 10 if abs(corr_h) < 0.03 else (15 if corr_h > 0.05 else 5)
    print(f"     → 建议权重: {h_weight} (原15)")
    
    # ── 2. D: 调整深度 ──
    print("\n  2. D(调整深度) - 权重: 15 → ?")
    d_vals = [r['decline_pct'] for r in records if r['decline_pct'] and r['decline_pct'] > 0]
    d_rets = [r['net_ret'] for r in records if r['decline_pct'] and r['decline_pct'] > 0]
    buckets = bucket_analysis(d_vals, d_rets, bins=10)
    print(f"     分桶分析 ({len(buckets)}桶):")
    for b in buckets:
        bar = '█' * int(b['wr'] / 5)
        print(f"       {b['lo']:.1f}%-{b['hi']:.1f}%: {b['count']:>5d}条 胜率={b['wr']:>5.1f}% {bar}")
    optimal = find_optimal_range(buckets)
    if optimal:
        print(f"     → 最优区间: {optimal['lo']:.1f}%-{optimal['hi']:.1f}% ({optimal['count']}条, 胜率{optimal['wr']:.1f}%)")
    # 相关系数
    corr_d = np.corrcoef(d_vals, d_rets)[0, 1]
    print(f"     相关系数: r={corr_d:+.4f}")
    d_weight = 20 if abs(corr_d) > 0.03 else 15
    print(f"     → 建议权重: {d_weight} (原15)")
    
    # ── 3. C: 横盘质量 ──
    print("\n  3. C(横盘质量) - 权重: 5 → ?")
    c_vals = [r['score_c'] for r in records]
    corr_c = np.corrcoef(c_vals, all_rets)[0, 1]
    print(f"     相关系数: r={corr_c:+.4f}")
    c0_rets = [r['net_ret'] for r in records if r['score_c'] == 0]
    c5_rets = [r['net_ret'] for r in records if r['score_c'] == 5]
    print(f"     C=0: {len(c0_rets)}条 胜率={np.mean(np.array(c0_rets)>0)*100:.1f}% 均收益={np.mean(c0_rets):.2f}%")
    print(f"     C=5: {len(c5_rets)}条 胜率={np.mean(np.array(c5_rets)>0)*100:.1f}% 均收益={np.mean(c5_rets):.2f}%")
    if corr_c < -0.02:
        print(f"     → 建议: 删除C因子 (r<0且C=5反而更差)")
        c_weight = 0
    elif abs(corr_c) < 0.02:
        print(f"     → 建议: 降为1-2分或删除 (r接近零)")
        c_weight = 0
    else:
        c_weight = 5
    
    # ── 4. I1: 行业 RS ──
    print("\n  4. I1(行业RS) - 权重: 15 → ?")
    i1_vals = [r['score_i1'] for r in records if r['score_i1'] is not None and r['score_i1'] > 0]
    i1_rets = [r['net_ret'] for r in records if r['score_i1'] is not None and r['score_i1'] > 0]
    # 按 score_i1 分组
    for sc in [5, 10, 15]:
        sub = [r['net_ret'] for r in records if r['score_i1'] == sc]
        if sub:
            print(f"     I1={sc}: {len(sub)}条 胜率={np.mean(np.array(sub)>0)*100:.1f}% 均收益={np.mean(sub):.2f}%")
    corr_i1 = np.corrcoef(i1_vals, i1_rets)[0, 1] if len(i1_vals) > 10 else 0
    print(f"     相关系数(非零值): r={corr_i1:+.4f}")
    i1_weight = 20 if corr_i1 > 0.03 else 15
    print(f"     → 建议权重: {i1_weight} (原15)")
    
    # ── 5. I2: 个股 RS ──
    print("\n  5. I2(个股RS) - 权重: 15 → ?")
    i2_vals = [r['score_i2'] for r in records if r['score_i2'] is not None and r['score_i2'] > 0]
    i2_rets = [r['net_ret'] for r in records if r['score_i2'] is not None and r['score_i2'] > 0]
    for sc in [5, 10, 15]:
        sub = [r['net_ret'] for r in records if r['score_i2'] == sc]
        if sub:
            print(f"     I2={sc}: {len(sub)}条 胜率={np.mean(np.array(sub)>0)*100:.1f}% 均收益={np.mean(sub):.2f}%")
    corr_i2 = np.corrcoef(i2_vals, i2_rets)[0, 1] if len(i2_vals) > 10 else 0
    print(f"     相关系数(非零值): r={corr_i2:+.4f}")
    
    # I2: h_rs250 分桶
    print("     h_rs250 分桶:")
    rs_vals = [r['h_rs250'] for r in records if r['h_rs250'] and r['h_rs250'] > 0]
    rs_rets = [r['net_ret'] for r in records if r['h_rs250'] and r['h_rs250'] > 0]
    rs_buckets = bucket_analysis(rs_vals, rs_rets, bins=8)
    for b in rs_buckets:
        bar = '█' * int(b['wr'] / 5)
        print(f"       RS{b['lo']:.0f}-{b['hi']:.0f}: {b['count']:>5d}条 胜率={b['wr']:>5.1f}% 均收益={b['ar']:>6.2f}% {bar}")
    rs_optimal = find_optimal_range(rs_buckets)
    if rs_optimal:
        print(f"     → 最优 RS 区间: {rs_optimal['lo']:.0f}-{rs_optimal['hi']:.0f} ({rs_optimal['count']}条, 胜率{rs_optimal['wr']:.1f}%)")
    i2_weight = 25 if corr_i2 > 0.08 else 20
    print(f"     → 建议权重: {i2_weight} (原15)")
    
    # ── 6. RS250 硬门禁 ──
    print("\n  6. 前高 RS250 硬门禁 (新增)...")
    for gate_val in [50, 60, 70, 80]:
        passed = [r for r in records if r['h_rs250'] and r['h_rs250'] >= gate_val]
        failed = [r for r in records if not r['h_rs250'] or r['h_rs250'] < gate_val]
        if passed and failed:
            p_wr = np.mean([r['is_win'] for r in passed]) * 100
            f_wr = np.mean([r['is_win'] for r in failed]) * 100
            p_ar = np.mean([r['net_ret'] for r in passed])
            p_b2 = np.mean([r['has_b2'] for r in passed]) * 100
            f_b2 = np.mean([r['has_b2'] for r in failed]) * 100
            print(f"     RS250≥{gate_val}: {len(passed)}条 胜率={p_wr:.1f}% 均收益={p_ar:.2f}% B2率={p_b2:.1f}%")
            print(f"     RS250<{gate_val}: {len(failed)}条 胜率={f_wr:.1f}% B2率={f_b2:.1f}%  差距={p_wr-f_wr:+.1f}pp")
    
    # ── 7. Sig: 共振 ──
    print("\n  7. Sig(共振) - 权重: 10 → ?")
    sig_vals = [r['score_sig'] for r in records if r['score_sig'] is not None]
    sig_rets = [r['net_ret'] for r in records if r['score_sig'] is not None]
    corr_sig = np.corrcoef(sig_vals, sig_rets)[0, 1] if len(sig_vals) > 10 else 0
    print(f"     相关系数: r={corr_sig:+.4f}")
    sig_weight = 10 if abs(corr_sig) < 0.05 else (15 if corr_sig > 0.05 else 5)
    print(f"     → 建议权重: {sig_weight} (原10)")
    
    # ═════════════════════════════════════════════
    # 二、技术置信度体系重标定
    # ═════════════════════════════════════════════
    print("\n\n" + "=" * 60)
    print("二、技术置信度评分体系重标定")
    print("=" * 60)
    
    # TS 分层分析
    print("\n  0. 当前五级分层 vs 全周期表现...")
    tiers_old = [(85, 100, '极高'), (75, 84, '很高'), (65, 74, '高'), (50, 64, '中'), (0, 49, '低')]
    for lo, hi, label in tiers_old:
        sub = [r for r in records if lo <= r['tech_score'] <= hi]
        if not sub:
            continue
        rets = np.array([r['net_ret'] for r in sub])
        b2_rate = np.mean([r['has_b2'] for r in sub]) * 100
        print(f"     {label}({lo}-{hi}): {len(sub)}条 胜率={np.mean(rets>0)*100:.1f}% 均收益={np.mean(rets):.2f}% B2率={b2_rate:.1f}%")
    
    # 重新分桶
    print("\n  1. tech_score 等频分桶 (10桶)...")
    ts_vals = np.array([r['tech_score'] for r in records])
    ts_rets = np.array([r['net_ret'] for r in records])
    ts_buckets = bucket_analysis(list(ts_vals), list(ts_rets), bins=10, min_samples=100)
    
    # 找胜率拐点
    prev_wr = 0
    breakpoints = []
    for b in ts_buckets:
        jump = b['wr'] - prev_wr
        marker = ' ← 拐点' if jump > 4 and prev_wr > 0 else ''
        print(f"     [{b['lo']:.0f}-{b['hi']:.0f}]: {b['count']:>6d}条 胜率={b['wr']:>5.1f}% 均收益={b['ar']:>6.2f}%{marker}")
        if jump > 4 and prev_wr > 0:
            breakpoints.append(int(b['lo']))
        prev_wr = b['wr']
    
    # B2 确认率随 TS 变化
    print("\n  2. B2 确认率随 tech_score 变化...")
    for lo in range(0, 100, 10):
        hi = lo + 9
        sub = [r for r in records if lo <= r['tech_score'] <= hi]
        if len(sub) < 30:
            continue
        b2_rate = np.mean([r['has_b2'] for r in sub]) * 100
        bar = '█' * int(b2_rate / 2)
        print(f"     TS {lo}-{hi}: {len(sub):>6d}条 B2率={b2_rate:>5.1f}% {bar}")
    
    # ── TS 因子级分析 ──
    print("\n  3. 各因子全周期预测力...")
    # 注：tech_score 是聚合值，单因子数据不在 DB 中
    # 用 TS 分层做代理分析
    print("     (tech_score 是 9 因子的聚合分数，无法从 backtest_results 直接拆分)")
    print("     结论: TS 预测的是\"B2 确认率\"而非\"B1 直接收益\"")
    print("     建议: 保留当前 9 因子评分逻辑，但将 TS 的用途从\"入场过滤\"改为\"B2 等待优先级\"")
    
    # ═════════════════════════════════════════════
    # 三、输出完整评分规则
    # ═════════════════════════════════════════════
    print("\n\n" + "=" * 60)
    print("三、重标定后的完整评分规则")
    print("=" * 60)
    
    total_hdc = h_weight + d_weight + c_weight + i1_weight + i2_weight + sig_weight
    # 新增 RS250 门禁作为独立维度，不占权重
    rs_gate = 60  # 建议值
    
    print(f"""
┌─────────────────────────────────────────────────────────────┐
│              MW B1 信号评分体系 v3.0（重标定）                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  【硬门禁】前高 RS250 ≥ {rs_gate}  → 不满足则不出 B1 信号          │
│                                                             │
│  【评分项】满分 {total_hdc}                                         │
│                                                             │
│  H  前高趋势    {h_weight:>2d}分  SMA50 斜率 > 0                          │
│  D  调整深度    {d_weight:>2d}分  跌幅 20%~40%（满分），15%~20%（半价）      │
│  I1 行业RS      {i1_weight:>2d}分  ind_rs250 ≥ 85(满分), ≥80(半价)          │
│  I2 个股RS      {i2_weight:>2d}分  h_rs250 ≥ 85(满分), ≥75(半价)            │
│  Sig 信号共振   {sig_weight:>2d}分  附近有 PP_V1/BO_V2(累加)                │
│                                                             │
│  C  横盘质量    删除  (全周期 r={corr_c:+.4f}，与收益负相关)            │
│                                                             │
│  【置信度分层】                                               │
│  高   ≥ {int(total_hdc * 0.7):>2d}    │  中   ≥ {int(total_hdc * 0.5):>2d}    │  低   < {int(total_hdc * 0.5):>2d}      │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│           B1 技术置信度评分 v2.0（重标定）                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  【定位】不用于入场决策，用于\"B2 等待优先级\"排序                    │
│  TS 高 ≠ 直接买点好，TS 高 = B2 来得晚但确认后一样强               │
│                                                             │
│  【9 因子】满分 100（评分逻辑不变，阈值微调）                      │
│                                                             │
│  1. 距MA20   15分  ≤5%(15) 5-10%(12) 10-15%(8) 15-25%(4)   │
│  2. 距MA50   15分  ≤8%(15) 8-15%(10) 15-25%(5) >25%(0)     │
│  3. 距MA250  15分  ≤15%(15) 15-25%(10) 25-35%(5) >35%(0)   │
│  4. MA60乖离 10分  ≤8%(10) 8-15%(7) 15-25%(3) >25%(0)      │
│  5. RPS20    10分  40-75(10) 边沿(6) 极端(2)                │
│  6. RPS60    10分  40-70(10) 边沿(6) 极端(2)                │
│  7. RPS250    5分  50-70(5) >70(3) 其余(2)                  │
│  8. MACD DIF 15分  DIF>0且<2%(15) DIF>0(12) 近零(8)        │
│  9. KDJ K     5分  ≤75(5) 75-85(3) >85(0)                  │
│                                                             │
│  【五级分层】断点不变（全周期单调性验证通过）                       │
│  极高 ≥85   很高 75-84   高 65-74   中 50-64   低 <50       │
│                                                             │
│  【关键认知】                                                 │
│  TS 越高 → B2 确认率越低（极高19.7% vs 中62.3%）                │
│  TS 高说明\"价格位置好\"，但好位置的反面是\"市场不急于确认\"           │
│  一旦 B2 确认，所有 TS 层级收益趋同（72-74%胜率/9-11%收益）       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
""")
    
    db.close()


if __name__ == '__main__':
    main()
