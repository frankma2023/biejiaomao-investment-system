# -*- coding: utf-8 -*-
"""因子正交贡献分析 → 为关注分配权提供依据
1. 7 因子相关性矩阵（暴露共线）
2. 多元线性概率模型：is_win(H10) ~ 标准化因子，看正交偏贡献
3. 逐步前向选择：ΔR² 看每个因子的独立增量
"""
import json, sys
import numpy as np

WIDE = r"D:\hanako\investment-system\docs\analysis\mw_indep_wide.json"
FACTORS = ['upper_break', 'bias_ma20', 'trend_eff', 'decline_pct', 'dist_h', 'ind_rs20', 'h_rs250']
NAMES = {'upper_break':'上轨突破%','bias_ma20':'乖离率MA20','trend_eff':'趋势效率',
         'decline_pct':'回调深度','dist_h':'距H天数','ind_rs20':'行业RS20','h_rs250':'h_rs250'}


def main():
    data = json.load(open(WIDE, encoding='utf-8'))
    recs = data['records']
    # 收集完整样本
    rows = []
    for r in recs:
        if r.get('ret_10') is None: continue
        vals = [r.get(f) for f in FACTORS]
        if any(v is None for v in vals): continue
        rows.append(vals + [1.0 if r['ret_10'] > 0 else 0.0, r['ret_10']])
    arr = np.array(rows, dtype=float)
    X_raw = arr[:, :len(FACTORS)]
    y_win = arr[:, len(FACTORS)]
    y_ret = arr[:, len(FACTORS)+1]
    n = len(arr)
    print(f"完整样本(7因子全非空 & H10有收益): {n}")

    # winsorize 收益 ±30% 防极端值主导
    y_ret_w = np.clip(y_ret, -30, 30)

    # 标准化 X
    mu = X_raw.mean(0); sd = X_raw.std(0)
    Xz = (X_raw - mu) / sd

    print("\n" + "="*70)
    print("1. 相关性矩阵（Pearson）— 找共线")
    print("="*70)
    C = np.corrcoef(Xz.T)
    hdr = "".join(f"{NAMES[f][:5]:>8}" for f in FACTORS)
    print(f"{'':>10}{hdr}")
    for i, f in enumerate(FACTORS):
        line = "".join(f"{C[i,j]:>8.2f}" for j in range(len(FACTORS)))
        print(f"{NAMES[f]:>10}{line}")

    print("\n  高相关对 (|r|>0.4):")
    for i in range(len(FACTORS)):
        for j in range(i+1, len(FACTORS)):
            if abs(C[i,j]) > 0.4:
                print(f"    {NAMES[FACTORS[i]]} <-> {NAMES[FACTORS[j]]}: r={C[i,j]:.2f}")

    print("\n" + "="*70)
    print("2. 多元线性概率模型: is_win ~ 标准化因子（正交偏贡献）")
    print("   betas 可比，|beta| 反映控制其他因子后的独立贡献")
    print("="*70)
    Xd = np.column_stack([np.ones(n), Xz])
    beta, *_ = np.linalg.lstsq(Xd, y_win, rcond=None)
    yhat = Xd @ beta
    ss_res = ((y_win - yhat)**2).sum(); ss_tot = ((y_win - y_win.mean())**2).sum()
    r2 = 1 - ss_res/ss_tot
    print(f"  多元 R²(is_win) = {r2:.4f}  截距(基准胜率)={beta[0]:.3f}")
    print(f"  {'因子':>10}{'单变量IC':>10}{'多元beta':>10}{'|beta|占比':>10}")
    # 单变量相关
    uni = {f: np.corrcoef(Xz[:,i], y_win)[0,1] for i,f in enumerate(FACTORS)}
    absum = sum(abs(beta[i+1]) for i in range(len(FACTORS)))
    for i, f in enumerate(FACTORS):
        share = abs(beta[i+1])/absum*100
        print(f"  {NAMES[f]:>10}{uni[f]:>+10.3f}{beta[i+1]:>+10.4f}{share:>9.1f}%")

    # 对收益的回归（winsorized）
    beta_r, *_ = np.linalg.lstsq(Xd, y_ret_w, rcond=None)
    print(f"\n  [对照] 对 winsorized H10收益 的 beta:")
    for i, f in enumerate(FACTORS):
        print(f"  {NAMES[f]:>10}: {beta_r[i+1]:>+7.3f}")

    print("\n" + "="*70)
    print("3. 逐步前向选择（每步选使 R² 增量最大的因子）")
    print("   增量小 = 信息被已选因子覆盖（共线）")
    print("="*70)
    selected = []; remaining = list(range(len(FACTORS))); prev_r2 = 0.0
    while remaining:
        best = None; best_r2 = -1
        for idx in remaining:
            cols = selected + [idx]
            Xs = np.column_stack([np.ones(n)] + [Xz[:,c] for c in cols])
            b, *_ = np.linalg.lstsq(Xs, y_win, rcond=None)
            yh = Xs @ b
            rr = 1 - ((y_win-yh)**2).sum()/ss_tot
            if rr > best_r2:
                best_r2 = rr; best = idx
        d = best_r2 - prev_r2
        print(f"  +{NAMES[FACTORS[best]]:<10} 累计R²={best_r2:.4f}  ΔR²={d:.4f}")
        selected.append(best); remaining.remove(best); prev_r2 = best_r2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    main()
