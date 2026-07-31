# -*- coding: utf-8 -*-
"""独立回测 · L3 因子分层 + L4 B2 交叉
关注分 7 因子逐个五分位分层，验证单调性与 PRD 声称的 IC 方向。
再验证 tech_score(v4.4) 整体分层 + B2 交叉。
所有收益用 H10 净收益。
"""
import json, sys, statistics as st
from collections import defaultdict

WIDE = r"D:\hanako\investment-system\docs\analysis\mw_indep_wide.json"
HOLD = 10


def pct(x): return f"{x*100:.1f}%"


def stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals: return None
    n = len(vals); wins = sum(1 for v in vals if v > 0)
    return {"n": n, "win": wins/n, "mean": st.mean(vals), "median": st.median(vals)}


def quintile_analysis(recs, factor_key, ret_key, reverse=False):
    """按 factor 五分位分层，输出各层胜率/收益。reverse=True 时 Q1 为因子最大端。"""
    pairs = [(r.get(factor_key), r.get(ret_key)) for r in recs
             if r.get(factor_key) is not None and r.get(ret_key) is not None]
    if len(pairs) < 100:
        return None
    pairs.sort(key=lambda x: x[0], reverse=reverse)
    n = len(pairs)
    out = []
    for q in range(5):
        lo = q * n // 5; hi = (q + 1) * n // 5
        chunk = pairs[lo:hi]
        rets = [c[1] for c in chunk]
        fvals = [c[0] for c in chunk]
        s = stats(rets)
        out.append({"q": q+1, "fmin": min(fvals), "fmax": max(fvals),
                    "n": s["n"], "win": s["win"], "mean": s["mean"], "median": s["median"]})
    return out


def print_quintile(name, res, note=""):
    print(f"\n  【{name}】{note}")
    if not res:
        print("    样本不足"); return
    for q in res:
        print(f"    Q{q['q']} [{q['fmin']:>8.2f},{q['fmax']:>8.2f}]  n={q['n']:>5}  "
              f"胜率={pct(q['win']):>7}  均值={q['mean']:>7.2f}%  中位={q['median']:>7.2f}%")
    spread = (res[0]['win'] - res[-1]['win']) * 100
    print(f"    Q1-Q5 胜率差 = {spread:+.1f}pp")


def main():
    data = json.load(open(WIDE, encoding='utf-8'))
    recs = data["records"]
    rk = f"ret_{HOLD}"

    print("=" * 70)
    print(f"L3 · 关注分 7 因子五分位分层（H{HOLD} 净收益）")
    print("PRD 声称的方向: 上轨突破/乖离/趋势效率=反向, 距H/回调/行业RS/h_rs250=正向")
    print("Q1 统一定义为'PRD 认为最该得高分'的一端")
    print("=" * 70)

    # 反向因子：因子越小越好 → Q1 取最小端 (reverse=False, 升序, Q1=最小)
    print_quintile("上轨突破%（PRD:反向,最强 13.5pp）",
                   quintile_analysis(recs, 'upper_break', rk, reverse=False),
                   "Q1=突破幅度最小/最负")
    print_quintile("乖离率MA20（PRD:反向）",
                   quintile_analysis(recs, 'bias_ma20', rk, reverse=False),
                   "Q1=乖离最小")
    print_quintile("趋势效率（PRD:反向）",
                   quintile_analysis(recs, 'trend_eff', rk, reverse=False),
                   "Q1=效率最低/横盘")
    # 正向因子：因子越大越好 → Q1 取最大端 (reverse=True)
    print_quintile("距H天数（PRD:正向）",
                   quintile_analysis(recs, 'dist_h', rk, reverse=True),
                   "Q1=距H最久")
    print_quintile("回调深度decline_pct（PRD:正向）",
                   quintile_analysis(recs, 'decline_pct', rk, reverse=True),
                   "Q1=回调最深")
    print_quintile("行业RS_20（PRD:正向）",
                   quintile_analysis(recs, 'ind_rs20', rk, reverse=True),
                   "Q1=行业最强")
    print_quintile("h_rs250（PRD:正向,但称0区分力）",
                   quintile_analysis(recs, 'h_rs250', rk, reverse=True),
                   "Q1=个股RS最强")

    print("\n" + "=" * 70)
    print(f"L3-B · tech_score(v4.4) 合成分整体分层（H{HOLD} 净收益）")
    print("=" * 70)
    print_quintile("tech_score 五分位", quintile_analysis(recs, 'tech_score', rk, reverse=True),
                   "Q1=分最高")
    # PRD 声称的阈值分层
    print("\n  PRD 声称阈值分层（60分水岭 / 70-89甜区）:")
    bins = [("<50", lambda t: t < 50), ("50-59", lambda t: 50 <= t < 60),
            ("60-69", lambda t: 60 <= t < 70), ("70-89", lambda t: 70 <= t < 90),
            ("90+", lambda t: t >= 90)]
    for label, cond in bins:
        rets = [r.get(rk) for r in recs if r.get('tech_score') is not None
                and cond(r['tech_score']) and r.get(rk) is not None]
        s = stats(rets)
        if s:
            print(f"    {label:<8} n={s['n']:>6}  胜率={pct(s['win']):>7}  "
                  f"均值={s['mean']:>7.2f}%  中位={s['median']:>7.2f}%")

    print("\n" + "=" * 70)
    print(f"L4 · B2 确认交叉（H{HOLD} 净收益）— B2 是事后确认,非可交易")
    print("=" * 70)
    has_b2 = [r.get(rk) for r in recs if r.get('b2_date') and r['b2_date'] != '_sentinel_' and r.get(rk) is not None]
    no_b2 = [r.get(rk) for r in recs if (not r.get('b2_date') or r['b2_date'] == '_sentinel_') and r.get(rk) is not None]
    s1, s2 = stats(has_b2), stats(no_b2)
    print(f"  有 B2: n={s1['n']:>6}  胜率={pct(s1['win']):>7}  均值={s1['mean']:>6.2f}%  中位={s1['median']:>6.2f}%")
    print(f"  无 B2: n={s2['n']:>6}  胜率={pct(s2['win']):>7}  均值={s2['mean']:>6.2f}%  中位={s2['median']:>6.2f}%")
    print(f"  胜率差 = {(s1['win']-s2['win'])*100:+.1f}pp")

    # tech_score × B2 交叉
    print("\n  tech_score 分层 × 有无B2:")
    for label, cond in [("低(<50)", lambda t: t<50), ("中(50-69)", lambda t: 50<=t<70), ("高(70+)", lambda t: t>=70)]:
        for b2label, b2cond in [("有B2", True), ("无B2", False)]:
            rets = []
            for r in recs:
                t = r.get('tech_score'); rt = r.get(rk)
                if t is None or rt is None or not cond(t): continue
                hb2 = bool(r.get('b2_date') and r['b2_date'] != '_sentinel_')
                if hb2 == b2cond:
                    rets.append(rt)
            s = stats(rets)
            if s:
                print(f"    {label:<10}{b2label}: n={s['n']:>5} 胜率={pct(s['win']):>7} 中位={s['median']:>6.2f}%")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    main()
