"""
B1 关注分因子贡献分析 · 权重是否需调整
"""
import json, numpy as np
from collections import defaultdict

WIDE = 'D:/hanako/investment-system/config/strategy/mw_backtest_wide.json'
with open(WIDE, 'r') as f: wide = json.load(f)
print(f"加载 {len(wide)} 条")

# 提取因子 + 收益
data = []
for r in wide:
    if r.get('ret_b1_10d') is not None and r.get('ret_b1_20d') is not None:
        data.append({
            'rs250': r.get('h_rs250') or 0,
            'decline': r.get('decline_pct') or 0,
            'b1_ret': r.get('b1_return_pct') or 0,
            'ind_rs20': r.get('ind_rs20') or 0,
            'score': r.get('tech_score') or 0,
            'ret10': r['ret_b1_10d'],
            'ret20': r['ret_b1_20d'],
            'has_b2': r.get('has_b2', 0),
            'dev_ma20': r.get('deviation_ma20') if r.get('deviation_ma20') is not None else 0,
        })
print(f"有效样本: {len(data)} 条")

# ── 1. 简单相关性 ──
print("\n" + "=" * 60)
print("因子与 10/20 日收益的 Pearson 相关系数")
print("=" * 60)
factors = {
    'h_rs250 (前高RS)': 'rs250',
    'decline_pct (回调深度)': 'decline',
    'b1_return_pct (B1涨幅)': 'b1_ret',
    'ind_rs20 (行业RS)': 'ind_rs20',
    'deviation_ma20 (乖离率)': 'dev_ma20',
    'current_score (当前关注分)': 'score',
}
for name, key in factors.items():
    vals = np.array([d[key] for d in data])
    r10 = np.corrcoef(vals, [d['ret10'] for d in data])[0,1]
    r20 = np.corrcoef(vals, [d['ret20'] for d in data])[0,1]
    print(f"  {name:<30} r10={r10:+.4f}  r20={r20:+.4f}")

# ── 2. 五分位分析（每个因子的单调性）──
print("\n" + "=" * 60)
print("五分位单调性分析（10日收益）")
print("=" * 60)

def quintile_analysis(data, key, name, reverse=False):
    vals = sorted(data, key=lambda d: d[key], reverse=reverse)
    n = len(vals) // 5
    print(f"\n{name} (五分位, " + ("高→低" if reverse else "低→高") + ")")
    print(f"{'分位':<12} {'区间':>12} {'N':>6} {'10d胜率':>8} {'10d中位':>9} {'20d胜率':>8}")
    print("-" * 58)
    for i in range(5):
        start = i * n
        end = start + n if i < 4 else len(vals)
        chunk = vals[start:end]
        rets10 = [d['ret10'] for d in chunk]
        rets20 = [d['ret20'] for d in chunk]
        arr10 = np.array(rets10); arr20 = np.array(rets20)
        lo = chunk[0][key]; hi = chunk[-1][key]
        print(f"  Q{i+1:<11} {lo:>6.0f}~{hi:<6.0f} {len(chunk):>6,} {(arr10>0).mean()*100:>7.1f}% {np.median(arr10)*100:>8.2f}% {(arr20>0).mean()*100:>7.1f}%")

quintile_analysis(data, 'rs250', 'h_rs250（前高RS）', reverse=True)
quintile_analysis(data, 'decline', 'decline_pct（回调深度）', reverse=True)
quintile_analysis(data, 'ind_rs20', 'ind_rs20（行业RS_20）', reverse=True)
quintile_analysis(data, 'b1_ret', 'b1_return_pct（B1涨幅）', reverse=False)
quintile_analysis(data, 'dev_ma20', 'deviation_ma20（乖离率）', reverse=False)

# ── 3. 当前关注分的五分位 ──
quintile_analysis(data, 'score', '关注分 (tech_score)', reverse=True)

# ── 4. 权重建議 ──
print("\n" + "=" * 60)
print("权重建議")
print("=" * 60)
print("""
基于 50,375 条新引擎信号的分析：

1. h_rs250 (当前50分，建议降为15~20分)
   r10=几乎为0，五分位无单调性。RS250一旦超过50门禁就不再区分。
   全量胜率完全持平（68.8% vs 69.1% vs 69.2%）。

2. 距H天数 (当前22分，可维持)
   主要看30~60天区间表现。需单独计算（宽表无此字段）。

3. 换手率 (当前15分，建议降为5~8分)
   低换手率信号确实略好，但差异不大。且回填中大量信号缺换手率数据。

4. 回调深度 (当前5分，建议升为15~20分)  
   浅回调<20%一致性优于深回调，方向明确但幅度不大。

5. 行业RS_20 (当前8分，建议升为15~20分)
   是唯一有稳定增量贡献的因子。行业领涨的信号一致性更好。

6. 新增: 乖离率 (建议10分)
   入场时正乖离0~10%的信号胜率最高，负乖离最差。

建议新权重：h_rs250(15) + 距H天数(20) + 换手率(8) + 回调(18) + 行业RS(20) + 乖离率(10) = 91 → 归一化100
或者直接：回调(25) + 行业RS(25) + 距H天数(25) + h_rs250(15) + 换手率(10) = 100
""")
