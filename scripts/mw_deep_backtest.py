"""
MW B1/B2 信号深度回测分析
目标：
1. 验证 B1/B2 信号对买入的参考价值
2. 找出高置信度因子组合
3. 建立置信度评分规则

数据源: D:\hanako\investment-system\data\lixinger.db
回测区间: 2016-01-01 ~ 2026-07-03
入场方式: T+1_O (次日开盘)
持有期: 20个交易日
"""

import sqlite3
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

DB_PATH = r"D:\hanako\investment-system\data\lixinger.db"
OUTPUT_DIR = r"D:\hanako\investment-system\data"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def query(db, sql, params=()):
    cur = db.execute(sql, params)
    rows = cur.fetchall()
    return [dict(r) for r in rows]

def stats(rows, ret_field='net_ret_pct'):
    """计算统计指标"""
    rets = [r[ret_field] for r in rows if r.get(ret_field) is not None]
    n = len(rets)
    if n == 0:
        return {'n': 0, 'win_rate': 0, 'avg_ret': 0, 'median_ret': 0, 'excess_ret': 0}
    rets_sorted = sorted(rets)
    wins = [r for r in rets if r > 0]
    avg_ret = sum(rets) / n
    median_ret = rets_sorted[n // 2]
    avg_excess = sum(r.get('excess_ret_pct', 0) for r in rows if r.get('excess_ret_pct') is not None) / n
    # Kelly criterion: f = (p*b - (1-p)) / b, where b = avg_win/avg_loss
    if wins and len(wins) < n:
        avg_win = sum(wins) / len(wins)
        losses = [r for r in rets if r <= 0]
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0
        p = len(wins) / n
        b = avg_win / avg_loss if avg_loss > 0 else 0
        kelly = max(0, (p * b - (1 - p)) / b) if b > 0 else 0
    else:
        kelly = 0.5 if len(wins) == n else 0
    
    # VaR 95%
    var95_idx = int(n * 0.05)
    var95 = rets_sorted[var95_idx] if var95_idx < n else rets_sorted[0]
    
    return {
        'n': n,
        'win_rate': round(len(wins) / n * 100, 1),
        'avg_ret': round(avg_ret, 2),
        'median_ret': round(median_ret, 2),
        'excess_ret': round(avg_excess, 2),
        'kelly': round(kelly, 4),
        'var95': round(var95, 2),
        'std': round((sum((r - avg_ret) ** 2 for r in rets) / n) ** 0.5, 2)
    }

def run():
    db = get_db()
    results = {}
    
    print("=" * 60)
    print("MW B1/B2 信号深度回测分析")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # ═══════════════════════════════════════════════════════════
    # 1. 基准: 全部 B1 信号 vs B2 确认 vs B1-only
    # ═══════════════════════════════════════════════════════════
    print("\n[1/8] 基准统计: B1 全量 vs B2确认 vs B1-only...")
    
    # B1 全量 (T+1_O, H20)
    b1_all = query(db, """
        SELECT m.*, b.net_ret_pct, b.ret_pct, b.is_win, b.excess_ret_pct, 
               b.peak_ret_pct, b.trough_ret_pct, b.market_regime
        FROM mw_signal_daily m
        JOIN backtest_results b 
          ON b.stock_code = m.stock_code AND b.signal_date = m.b1_date
        WHERE b.entry_method = 'T+1_O' 
          AND b.hold_days = 20 
          AND b.signal_mask & 1 = 1
          AND m.b1_date >= '2016-01-01' 
          AND m.b1_date <= '2026-07-03'
          AND m.stock_code != '_sentinel_'
    """)
    
    # 分 B2 确认 / B1-only
    b2_confirmed = [r for r in b1_all if r.get('b2_date') and r['b2_date'] > r['b1_date']]
    b1_only = [r for r in b1_all if not (r.get('b2_date') and r['b2_date'] > r['b1_date'])]
    
    results['overview'] = {
        'all_b1': stats(b1_all),
        'b2_confirmed': stats(b2_confirmed),
        'b1_only': stats(b1_only),
    }
    
    print(f"  B1全量: {len(b1_all)} 笔, 胜率 {results['overview']['all_b1']['win_rate']}%, 均收 {results['overview']['all_b1']['avg_ret']}%")
    print(f"  B2确认: {len(b2_confirmed)} 笔, 胜率 {results['overview']['b2_confirmed']['win_rate']}%, 均收 {results['overview']['b2_confirmed']['avg_ret']}%")
    print(f"  B1-only: {len(b1_only)} 笔, 胜率 {results['overview']['b1_only']['win_rate']}%, 均收 {results['overview']['b1_only']['avg_ret']}%")
    
    # ═══════════════════════════════════════════════════════════
    # 2. 单因子分层分析 (仅B2确认信号)
    # ═══════════════════════════════════════════════════════════
    print("\n[2/8] 单因子分层分析 (B2确认信号)...")
    
    base_data = b2_confirmed  # 只分析B2确认的
    
    def bucket_analysis(rows, field, buckets):
        """按字段分桶统计"""
        result = {}
        for label, lo, hi in buckets:
            subset = [r for r in rows if r.get(field) is not None and lo <= r[field] < hi]
            result[label] = stats(subset)
        return result
    
    # 2a. h_rs250 分层
    rs250_buckets = [
        ('≥90', 90, 200), ('80-89', 80, 90), ('70-79', 70, 80),
        ('60-69', 60, 70), ('<60', 0, 60)
    ]
    results['factor_rs250'] = bucket_analysis(base_data, 'h_rs250', rs250_buckets)
    
    # 2b. decline_pct 分层 (回调深度)
    decline_buckets = [
        ('>40%', 40, 200), ('30-40%', 30, 40), ('25-30%', 25, 30),
        ('20-25%', 20, 25), ('15-20%', 15, 20), ('10-15%', 10, 15)
    ]
    results['factor_decline'] = bucket_analysis(base_data, 'decline_pct', decline_buckets)
    
    # 2c. b1_return_pct 分层 (B1日涨幅)
    b1_ret_buckets = [
        ('>8%', 8, 200), ('5-8%', 5, 8), ('3-5%', 3, 5),
        ('2-3%', 2, 3)
    ]
    results['factor_b1_ret'] = bucket_analysis(base_data, 'b1_return_pct', b1_ret_buckets)
    
    # 2d. b1_vol_ratio 分层 (量比)
    vol_buckets = [
        ('>3.0', 3.0, 100), ('2.0-3.0', 2.0, 3.0), ('1.5-2.0', 1.5, 2.0),
        ('1.0-1.5', 1.0, 1.5), ('<1.0', 0, 1.0)
    ]
    results['factor_vol_ratio'] = bucket_analysis(base_data, 'b1_vol_ratio', vol_buckets)
    
    # 2e. c_amplitude_pct 分层 (横盘振幅)
    amp_buckets = [
        ('>15%', 15, 200), ('10-15%', 10, 15), ('7-10%', 7, 10),
        ('5-7%', 5, 7), ('<5%', 0, 5)
    ]
    results['factor_c_amplitude'] = bucket_analysis(base_data, 'c_amplitude_pct', amp_buckets)
    
    # 2f. score 分层 (HDC总分)
    score_buckets = [
        ('≥80', 80, 200), ('70-79', 70, 80), ('60-69', 60, 70),
        ('50-59', 50, 60), ('40-49', 40, 50), ('<40', 0, 40)
    ]
    results['factor_score'] = bucket_analysis(base_data, 'score', score_buckets)
    
    # 2g. tech_score 分层
    tech_buckets = [
        ('≥85', 85, 200), ('75-84', 75, 85), ('65-74', 65, 75),
        ('50-64', 50, 65), ('1-49', 1, 50), ('0', 0, 1)
    ]
    results['factor_tech_score'] = bucket_analysis(base_data, 'tech_score', tech_buckets)
    
    # 2h. ind_rs250 分层
    ind_rs_buckets = [
        ('≥90', 90, 200), ('80-89', 80, 90), ('70-79', 70, 80),
        ('<70', 0, 70), ('NULL', -1, 0)
    ]
    # 对于NULL单独处理
    ind_rs_data = {}
    for label, lo, hi in ind_rs_buckets:
        if label == 'NULL':
            subset = [r for r in base_data if r.get('ind_rs250') is None]
        else:
            subset = [r for r in base_data if r.get('ind_rs250') is not None and lo <= r['ind_rs250'] < hi]
        ind_rs_data[label] = stats(subset)
    results['factor_ind_rs250'] = ind_rs_data
    
    # 2i. market_regime 分层
    regime_data = {}
    for regime in ['bull', 'bear', 'ranging']:
        subset = [r for r in base_data if r.get('market_regime') == regime]
        regime_data[regime] = stats(subset)
    results['factor_market_regime'] = regime_data
    
    # 打印关键因子
    for fname, fdata in [('RS250', 'factor_rs250'), ('回调深度', 'factor_decline'), 
                          ('HDC总分', 'factor_score'), ('技术分', 'factor_tech_score'),
                          ('市场环境', 'factor_market_regime')]:
        print(f"\n  --- {fname} ---")
        for k, v in results[fdata].items():
            print(f"    {k}: n={v['n']}, 胜率={v['win_rate']}%, 均收={v['avg_ret']}%, 超额={v['excess_ret']}%")
    
    # ═══════════════════════════════════════════════════════════
    # 3. 因子组合分析
    # ═══════════════════════════════════════════════════════════
    print("\n[3/8] 双因子组合分析...")
    
    def combo_analysis(rows, f1, f1_filter, f2, f2_filter, label):
        subset = [r for r in rows if f1_filter(r.get(f1)) and f2_filter(r.get(f2))]
        return {label: stats(subset)}
    
    # 关键组合测试
    combos = {}
    
    # RS250 × decline
    combos['RS≥80 × 回调25-40%'] = stats([r for r in base_data 
        if (r.get('h_rs250') or 0) >= 80 and 25 <= (r.get('decline_pct') or 0) <= 40])
    
    # RS250 × tech_score
    combos['RS≥80 × 技术分≥75'] = stats([r for r in base_data 
        if (r.get('h_rs250') or 0) >= 80 and (r.get('tech_score') or 0) >= 75])
    
    # RS250 × 市场环境
    combos['RS≥80 × 震荡市'] = stats([r for r in base_data 
        if (r.get('h_rs250') or 0) >= 80 and r.get('market_regime') == 'ranging'])
    
    combos['RS≥80 × 牛市'] = stats([r for r in base_data 
        if (r.get('h_rs250') or 0) >= 80 and r.get('market_regime') == 'bull'])
    
    # decline × vol_ratio
    combos['回调25-40% × 量比≥2.0'] = stats([r for r in base_data 
        if 25 <= (r.get('decline_pct') or 0) <= 40 and (r.get('b1_vol_ratio') or 0) >= 2.0])
    
    # decline × tech_score
    combos['回调25-40% × 技术分≥75'] = stats([r for r in base_data 
        if 25 <= (r.get('decline_pct') or 0) <= 40 and (r.get('tech_score') or 0) >= 75])
    
    # score × tech_score
    combos['HDC≥70 × 技术分≥75'] = stats([r for r in base_data 
        if (r.get('score') or 0) >= 70 and (r.get('tech_score') or 0) >= 75])
    
    combos['HDC≥70 × 技术分≥65'] = stats([r for r in base_data 
        if (r.get('score') or 0) >= 70 and (r.get('tech_score') or 0) >= 65])
    
    # ind_rs × stock_rs
    combos['行业RS≥80 × 个股RS≥80'] = stats([r for r in base_data 
        if (r.get('ind_rs250') or 0) >= 80 and (r.get('h_rs250') or 0) >= 80])
    
    # 低振幅 × 高RS
    combos['横盘振幅<7% × RS≥80'] = stats([r for r in base_data 
        if (r.get('c_amplitude_pct') or 0) < 7 and (r.get('h_rs250') or 0) >= 80])
    
    # 三因子: RS + decline + market
    combos['RS≥80 × 回调25-40% × 震荡市'] = stats([r for r in base_data 
        if (r.get('h_rs250') or 0) >= 80 and 25 <= (r.get('decline_pct') or 0) <= 40 
        and r.get('market_regime') == 'ranging'])
    
    combos['RS≥80 × 回调25-40% × 量比≥1.5'] = stats([r for r in base_data 
        if (r.get('h_rs250') or 0) >= 80 and 25 <= (r.get('decline_pct') or 0) <= 40 
        and (r.get('b1_vol_ratio') or 0) >= 1.5])
    
    combos['RS≥80 × 回调25-40% × 技术分≥65'] = stats([r for r in base_data 
        if (r.get('h_rs250') or 0) >= 80 and 25 <= (r.get('decline_pct') or 0) <= 40 
        and (r.get('tech_score') or 0) >= 65])
    
    # HDC PLUS
    combos['PLUS (HDC≥80+D=25+I1=20)'] = stats([r for r in base_data 
        if (r.get('is_plus') or 0) == 1])
    
    results['combos'] = combos
    
    print("\n  --- 双因子/三因子组合 ---")
    for label, s in combos.items():
        print(f"    {label}: n={s['n']}, 胜率={s['win_rate']}%, 均收={s['avg_ret']}%, 超额={s['excess_ret']}%, Kelly={s['kelly']}")
    
    # ═══════════════════════════════════════════════════════════
    # 4. 年度稳定性分析
    # ═══════════════════════════════════════════════════════════
    print("\n[4/8] 年度稳定性分析...")
    
    yearly = {}
    for row in base_data:
        year = row['b1_date'][:4]
        if year not in yearly:
            yearly[year] = []
        yearly[year].append(row)
    
    results['yearly'] = {y: stats(rows) for y, rows in sorted(yearly.items())}
    
    print("\n  --- 年度统计 (B2确认) ---")
    for y, s in results['yearly'].items():
        print(f"    {y}: n={s['n']}, 胜率={s['win_rate']}%, 均收={s['avg_ret']}%, 超额={s['excess_ret']}%")
    
    # ═══════════════════════════════════════════════════════════
    # 5. 不同持有期对比
    # ═══════════════════════════════════════════════════════════
    print("\n[5/8] 不同持有期对比...")
    
    hold_periods = {}
    for hd in [5, 10, 20, 60]:
        data = query(db, f"""
            SELECT m.stock_code, m.b1_date, m.h_rs250, m.decline_pct, m.score, m.tech_score,
                   m.b1_vol_ratio, m.c_amplitude_pct, m.confidence, m.is_plus,
                   m.b2_date, m.ind_rs250,
                   b.net_ret_pct, b.is_win, b.excess_ret_pct, b.market_regime
            FROM mw_signal_daily m
            JOIN backtest_results b 
              ON b.stock_code = m.stock_code AND b.signal_date = m.b1_date
            WHERE b.entry_method = 'T+1_O' 
              AND b.hold_days = {hd}
              AND b.signal_mask & 1 = 1
              AND m.b1_date >= '2016-01-01' 
              AND m.b1_date <= '2026-07-03'
              AND m.stock_code != '_sentinel_'
              AND m.b2_date IS NOT NULL AND m.b2_date > m.b1_date
        """)
        hold_periods[f'H{hd}'] = stats(data)
        print(f"  H{hd}: n={len(data)}, 胜率={hold_periods[f'H{hd}']['win_rate']}%, 均收={hold_periods[f'H{hd}']['avg_ret']}%")
    
    results['hold_periods'] = hold_periods
    
    # ═══════════════════════════════════════════════════════════
    # 6. 置信度评分规则构建
    # ═══════════════════════════════════════════════════════════
    print("\n[6/8] 置信度评分规则构建...")
    
    # 测试不同评分规则的筛选效果
    scoring_rules = {}
    
    # 规则A: RS250 ≥ 80 (简单RS门禁)
    rule_a = [r for r in base_data if (r.get('h_rs250') or 0) >= 80]
    scoring_rules['A_RS≥80'] = stats(rule_a)
    
    # 规则B: RS250 ≥ 80 + 回调25-40%
    rule_b = [r for r in base_data if (r.get('h_rs250') or 0) >= 80 and 25 <= (r.get('decline_pct') or 0) <= 40]
    scoring_rules['B_RS≥80+回调25-40%'] = stats(rule_b)
    
    # 规则C: RS≥80 + 回调25-40% + 量比≥1.5
    rule_c = [r for r in base_data if (r.get('h_rs250') or 0) >= 80 
              and 25 <= (r.get('decline_pct') or 0) <= 40
              and (r.get('b1_vol_ratio') or 0) >= 1.5]
    scoring_rules['C_RS+回调+量比'] = stats(rule_c)
    
    # 规则D: RS≥80 + 回调25-40% + 振幅<10%
    rule_d = [r for r in base_data if (r.get('h_rs250') or 0) >= 80 
              and 25 <= (r.get('decline_pct') or 0) <= 40
              and (r.get('c_amplitude_pct') or 0) < 10]
    scoring_rules['D_RS+回调+窄幅'] = stats(rule_d)
    
    # 规则E: RS≥75 + 回调20-40% + 量比≥1.5 + 振幅<10%
    rule_e = [r for r in base_data if (r.get('h_rs250') or 0) >= 75 
              and 20 <= (r.get('decline_pct') or 0) <= 40
              and (r.get('b1_vol_ratio') or 0) >= 1.5
              and (r.get('c_amplitude_pct') or 0) < 10]
    scoring_rules['E_综合门禁'] = stats(rule_e)
    
    # 规则F: 加权评分制 (满分100)
    def weighted_score(r):
        score = 0
        rs = r.get('h_rs250') or 0
        decline = r.get('decline_pct') or 0
        vol = r.get('b1_vol_ratio') or 0
        amp = r.get('c_amplitude_pct') if r.get('c_amplitude_pct') is not None else 100
        tech = r.get('tech_score') or 0
        
        # RS (30分)
        if rs >= 90: score += 30
        elif rs >= 80: score += 25
        elif rs >= 70: score += 15
        elif rs >= 60: score += 8
        
        # 回调深度 (25分)
        if 25 <= decline <= 40: score += 25
        elif 20 <= decline < 25: score += 18
        elif 15 <= decline < 20: score += 10
        elif decline > 40: score += 5
        
        # 量比 (15分)
        if vol >= 2.0: score += 15
        elif vol >= 1.5: score += 10
        elif vol >= 1.0: score += 5
        
        # 横盘质量 (15分)
        if amp < 5: score += 15
        elif amp < 7: score += 12
        elif amp < 10: score += 8
        elif amp < 15: score += 4
        
        # 技术分 (15分)
        if tech >= 85: score += 15
        elif tech >= 75: score += 12
        elif tech >= 65: score += 8
        elif tech >= 50: score += 4
        
        return score
    
    scored = [(weighted_score(r), r) for r in base_data]
    
    for threshold in [80, 70, 60, 50]:
        subset = [r for s, r in scored if s >= threshold]
        scoring_rules[f'F_加权≥{threshold}'] = stats(subset)
    
    results['scoring_rules'] = scoring_rules
    
    print("\n  --- 置信度评分规则筛选效果 ---")
    for label, s in scoring_rules.items():
        print(f"    {label}: n={s['n']}, 胜率={s['win_rate']}%, 均收={s['avg_ret']}%, 超额={s['excess_ret']}%, Kelly={s['kelly']}")
    
    # ═══════════════════════════════════════════════════════════
    # 7. 加权评分的年度稳定性
    # ═══════════════════════════════════════════════════════════
    print("\n[7/8] 加权评分年度稳定性 (≥70分)...")
    
    high_score = [(s, r) for s, r in scored if s >= 70]
    yearly_high = {}
    for s, r in high_score:
        year = r['b1_date'][:4]
        if year not in yearly_high:
            yearly_high[year] = []
        yearly_high[year].append(r)
    
    results['high_score_yearly'] = {y: stats(rows) for y, rows in sorted(yearly_high.items())}
    
    # 同时算全量B2的年度做对比
    yearly_all = {}
    for r in base_data:
        year = r['b1_date'][:4]
        if year not in yearly_all:
            yearly_all[year] = []
        yearly_all[year].append(r)
    
    print("\n  年度对比: 高分(≥70) vs 全量B2")
    print(f"    {'年份':<6} {'高分n':>6} {'高分胜率':>8} {'高分均收':>8} | {'全量n':>6} {'全量胜率':>8} {'全量均收':>8}")
    all_years = sorted(set(list(yearly_high.keys()) + list(yearly_all.keys())))
    for y in all_years:
        hs = stats(yearly_high.get(y, []))
        al = stats(yearly_all.get(y, []))
        print(f"    {y:<6} {hs['n']:>6} {hs['win_rate']:>7}% {hs['avg_ret']:>7}% | {al['n']:>6} {al['win_rate']:>7}% {al['avg_ret']:>7}%")
    
    # ═══════════════════════════════════════════════════════════
    # 8. 最终推荐评分规则
    # ═══════════════════════════════════════════════════════════
    print("\n[8/8] 最终推荐评分规则...")
    
    # 对比不同阈值的分级效果
    final_rules = {}
    for label, lo, hi in [('S级(≥80)', 80, 200), ('A级(70-79)', 70, 80), 
                           ('B级(60-69)', 60, 70), ('C级(50-59)', 50, 60),
                           ('D级(<50)', 0, 50)]:
        subset = [r for s, r in scored if lo <= s < hi]
        final_rules[label] = stats(subset)
    
    results['final_grading'] = final_rules
    
    print("\n  --- 最终分级 ---")
    for label, s in final_rules.items():
        print(f"    {label}: n={s['n']}, 胜率={s['win_rate']}%, 均收={s['avg_ret']}%, 超额={s['excess_ret']}%, Kelly={s['kelly']}")
    
    # 保存结果
    output_path = os.path.join(OUTPUT_DIR, 'mw_backtest_deep_analysis.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存到: {output_path}")
    
    db.close()
    return results

if __name__ == '__main__':
    run()
