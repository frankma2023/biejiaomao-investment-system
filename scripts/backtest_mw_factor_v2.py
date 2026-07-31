"""关注分因子贡献分析 v2 · 更新后的信号池"""
import json, numpy as np

WIDE = 'D:/hanako/investment-system/config/strategy/mw_backtest_wide.json'
with open(WIDE, 'r') as f: wide = json.load(f)

merged = [r for r in wide if r.get('ret_b1_10d') is not None and r.get('ret_b1_20d') is not None and r.get('b1_date','')>='2016-01-01']
print(f"有效样本: {len(merged):,} 条")

# ── 1. 相关系数 ──
print("\n" + "=" * 65)
print("因子与 10/20 日收益的 Pearson 相关系数")
print("=" * 65)
factors = {
    'h_rs250 (前高RS)': 'h_rs250',
    'decline_pct (回调深度)': 'decline_pct',
    'b1_return_pct (B1涨幅)': 'b1_return_pct',
    'ind_rs20 (行业RS_20)': 'ind_rs20',
    'ind_rs250 (行业RS_250)': 'ind_rs250',
    'deviation_ma20 (乖离率)': 'deviation_ma20',
    'tech_score (当前关注分)': 'tech_score',
}
for name, key in factors.items():
    vals = np.array([(r.get(key) or 0) for r in merged])
    r10 = np.corrcoef(vals, [r['ret_b1_10d'] for r in merged])[0,1]
    r20 = np.corrcoef(vals, [r['ret_b1_20d'] for r in merged])[0,1]
    print(f"  {name:<30} r10={r10:+.4f}  r20={r20:+.4f}")

# ── 2. 五分位 ──
print("\n" + "=" * 65)
print("五分位单调性（10日胜率 + 中位）")
print("=" * 65)

def quintile(data, key, name, reverse=True):
    vals = sorted(data, key=lambda r: r.get(key) or 0, reverse=reverse)
    n = max(1, len(vals) // 5)
    print(f"\n{name}")
    print(f"  {'分位':<6} {'N':>6} {'10d胜率':>8} {'10d中位':>9} {'20d胜率':>8}")
    print(f"  {'-'*45}")
    for i in range(5):
        s, e = i*n, (i+1)*n if i<4 else len(vals)
        chunk = vals[s:e]
        if not chunk: continue
        r10 = np.array([r['ret_b1_10d'] for r in chunk])
        r20 = np.array([r['ret_b1_20d'] for r in chunk])
        print(f"  Q{i+1:<5} {len(chunk):>6,} {(r10>0).mean()*100:>7.1f}% {np.median(r10)*100:>8.2f}% {(r20>0).mean()*100:>7.1f}%")

quintile(merged, 'h_rs250', 'h_rs250（前高RS）', reverse=True)
quintile(merged, 'decline_pct', 'decline_pct（回调深度）', reverse=True)
quintile(merged, 'ind_rs20', 'ind_rs20（行业RS_20·SW映射后）', reverse=True)
quintile(merged, 'b1_return_pct', 'b1_return_pct（B1涨幅）', reverse=False)
quintile(merged, 'deviation_ma20', 'deviation_ma20（乖离率）', reverse=False)
quintile(merged, 'tech_score', 'tech_score（当前关注分）', reverse=True)

# ── 3. 权重建議 ──
print("\n" + "=" * 65)
print("权重建议")
print("=" * 65)
print(f"  {'因子':<20} {'旧权重':>8} {'建议新':>8}  {'依据'}")
print(f"  {'-'*55}")
recs = [
    ('行业 RS_20', 8, 30, 'r=.08 唯一稳定正因子, Q1=53→Q5=43'),
    ('回调深度', 5, 20, '深调Q1胜率50.6→浅调Q5 46.9'),
    ('距H天数', 22, 15, '保持，适度降'),
    ('乖离率(新增)', 0, 15, '低乖离胜率最高, Q1=52→Q5=42'),
    ('换手率', 15, 10, '低换手略好'),
    ('h_rs250', 50, 10, 'r≈0, 五分位无单调→从50暴降'),
]
for name, old, new, reason in recs:
    print(f"  {name:<20} {old:>8} {new:>8}  {reason}")
print(f"  {'合计':<20} {sum(r[1] for r in recs):>8} {sum(r[2] for r in recs):>8}")
