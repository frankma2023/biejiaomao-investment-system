# -*- coding: utf-8 -*-
"""
用字符画输出杯柄形态：① 概念示意  ② 引擎实测的合成样本（按比例）。

概念示意是手写的固定字符串；实测图由 close 序列直接渲染，坐标对齐由程序保证。
运行：python scripts/cup_ascii.py
"""
import io
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))

# ── ① 概念示意（只有几何形状；规则文字由 rule_block() 从 params 生成）────
SCHEMA = r'''
   前高 H
    ────┐                                                    ┌── 突破日
        │＼                                          ┌───────┘   收盘 > 买点 + 放量
        │  ＼                                        │
        │    ＼                                   ┌──┘
        │      ＼                                 │  杯口 M
        │        ＼                               │
        │          ＼                             │    买点 = 杯口 + buy_point_buffer（元）
        │            ＼                           │    ↑ 收盘站上它才买
        │              ＼                         │
        │                ＼                      ╱
        │                  ＼                  ╱  柄部
        │                    ＼              ╱        小幅回调 + 缩量整理
        │                      ＼          ╱
        │                        ＼      ╱
        │                          ＼  ╱
        └───────────────────────────┴────────────────────────────
                                   杯底 B

    ① 前置上涨          ② 杯左侧        ③ 杯右侧        ④ 柄部        ⑤ 突破
    上涨过程中的调整     圆弧形          回升至杯口附近    窄幅整理      放量站上买点

    ※ 杯口 ≠ 前高：杯口是「杯底之后右侧回升的最高收盘」，是柄部的起点；
      与前高不必同高，但不得高于前高（规则 3）。
    ※ 没有柄部就不叫杯柄形态：柄部 0/1/2 个交易日会被规则 11 的下限挡掉。
'''


def rule_block(params):
    """
    当前生效规则的文字说明，**全部取自运行时 params**。

    以前这里是一段写死规则文字的字符画，参数一改就整段过期
    （曾出现「杯身深度 15~40%」「买点 = 杯口 × 1.01」等已废弃表述）。
    改成从 params 生成后不会再过期。

    Args:
        params: `cup_handle_v2.load_params()` 的返回值。

    Returns:
        多行字符串，含规则阈值与输出参数。
    """
    return (
        '   当前生效参数（config/market/cup_handle_v2.yaml）\n'
        '   ──────────────────────────────────────────────\n'
        '   规则  1  前置涨幅 (H−L0)/L0 ≥ %.0f%%\n'
        '   规则  2  杯底不得跌破上涨起点：%s\n'
        '   规则  3  杯口 ≤ 前高（mouth_vs_high_max %.2f，允许相等）\n'
        '   规则  4  杯身深度 (M−B)/M ∈ [%.2f, %.2f]\n'
        '   规则  6  回升段最大回撤 ≤ %.0f%%（绝对值）\n'
        '   规则  7  柄部回撤 ≤ %.0f%%（handle_pm_min %.2f）\n'
        '   规则  8  柄低 ≥ 杯底 + %.2f ×（杯口 − 杯底）\n'
        '   规则  9  杯底区（B 的 ±%.0f%%）内前后各 ≤ %d 个交易日；前侧下限 %d（0=关闭）\n'
        '   规则 10  前高→杯口 ≤ %d 个交易日\n'
        '   规则 11  杯口→突破 ∈ [%d, %d] 个交易日（突破日计入；下限即柄部 ≥ %d 日）\n'
        '   规则 12  买点 = 杯口 + %.2f 元（不是百分比）\n'
        '   规则 13  突破日成交量 ≥ MA20(量) × %.2f（MA20 含当日）\n'
        '   规则 14  次日收盘 > 杯口 → 补 CONFIRM：%s\n'
        '   输出     建议止盈 %.0f%% / 止损 %.0f%% / 最长持有 %d 个交易日\n'
        '   规则集   14 条编号 + 1 条前提约束 `+`，一律收盘价口径\n'
        % (params['min_prior_advance'] * 100,
           '开启' if params['require_bottom_above_origin'] else '已关闭',
           params['mouth_vs_high_max'],
           params['depth_min'], params['depth_max'],
           params['recovery_dd_max'] * 100,
           (1 - params['handle_pm_min']) * 100, params['handle_pm_min'],
           params['handle_position_ratio'],
           params['bottom_zone_pct'] * 100, params['bottom_zone_days_max'],
           params['bottom_zone_before_min'],
           params['mouth_span_max'],
           params['mouth_to_signal_min'], params['mouth_to_signal_max'],
           params['mouth_to_signal_min'] - 1,
           params['buy_point_buffer'],
           params['breakout_vol_ratio'],
           '开启' if params['require_breakout_confirm'] else '关闭',
           params['suggested_tp'] * 100, params['suggested_sl'] * 100,
           params['suggested_max_hold']))



def render(series, W=104, H=21, lo=None, hi=None, marks=None, title=''):
    """
    把收盘序列渲染成字符网格。

    Args:
        series: [(标签, 值)] 或 [值]；按列等距取样。
        W: 网格宽度（字符数）。
        H: 网格高度（行数）。
        lo/hi: 价格下上限；None 则按数据取。
        marks: {列号: (字符, 真实价格)}，按真实价位落点，不用采样值。
        title: 顶部标题。

    Returns:
        多行字符串。
    """
    vals = [v for _, v in series] if series and isinstance(series[0], tuple) else list(series)
    n = len(vals)
    lo = min(vals) if lo is None else lo
    hi = max(vals) if hi is None else hi
    pad = (hi - lo) * 0.04
    lo, hi = lo - pad, hi + pad

    def row_of(v):
        return max(0, min(H - 1, H - 1 - int(round((v - lo) / (hi - lo) * (H - 1)))))

    grid = [[' '] * W for _ in range(H)]
    cols = [vals[int(round(i * (n - 1) / float(W - 1)))] for i in range(W)]
    for c, v in enumerate(cols):
        grid[row_of(v)][c] = '*'
    for c in range(1, W):
        r0, r1 = row_of(cols[c - 1]), row_of(cols[c])
        for r in range(min(r0, r1) + 1, max(r0, r1)):
            grid[r][c] = '|'
    for c, (ch, v) in (marks or {}).items():
        grid[row_of(v)][c] = ch

    out = [title, '']
    for r in range(H):
        price = hi - (hi - lo) * r / (H - 1)
        out.append('%6.2f │%s' % (price, ''.join(grid[r])))
    out.append('       └' + '─' * W)
    return '\n'.join(out)


def main():
    from scanners import cup_handle_v2 as ch

    # 复刻 draw_cup_standard.py 的合成序列（同一条被引擎判为 SIGNAL 的曲线）
    PRE, L0, P0, P1, P2, P3, BRK, CONF = (
        380, 10.00, 14.00, 10.50, 13.80, 12.55, 14.10, 13.95)
    T0, T1, T2 = PRE + 30, PRE + 45, PRE + 75
    T_BRK = T2 + 12
    T_SIG = T_BRK + 1        # 检测日 = 次日确认日
    N = T_SIG + 1
    drift = [P3 + .06, P3 + .18, P3 + .10, P3 + .26, P3 + .20, P3 + .32, P3 + .24]
    closes = []
    for i in range(N):
        if i <= PRE:
            closes.append(9.60 + 0.12 * ((i % 11) - 5) / 5.0)
        elif i <= T0:
            closes.append(L0 + (P0 - L0) * (i - PRE) / float(T0 - PRE))
        elif i <= T1:
            u = (i - T0) / float(T1 - T0)
            closes.append(P0 - (P0 - P1) * (1 - (1 - u) ** 2))
        elif i <= T2:
            u = (i - T1) / float(T2 - T1)
            closes.append(P1 + (P2 - P1) * (1 - (1 - u) ** 2))
        elif i <= T2 + 4:
            closes.append(P2 - (P2 - P3) * (i - T2) / 4.0)
        elif i < T_BRK:
            closes.append(drift[i - (T2 + 5)])
        elif i == T_BRK:
            closes.append(BRK)
        else:
            closes.append(CONF)

    params = ch.load_params()
    xs = list(range(PRE - 26, N))
    series = [closes[i] for i in xs]
    W = 104
    col = {i: int(round((i - xs[0]) * (W - 1) / float(len(xs) - 1)))
           for i in (PRE, T0, T1, T2, T2 + 4, T_BRK, T_SIG)}
    marks = {col[PRE]: ('L', L0), col[T0]: ('H', P0), col[T1]: ('B', P1),
             col[T2]: ('M', P2), col[T2 + 4]: ('P', P3),
             col[T_BRK]: ('X', BRK), col[T_SIG]: ('C', CONF)}
    fig = render(series, W=W, H=23, lo=9.3, hi=14.6, marks=marks,
                 title='② 引擎实测样本（按比例，收盘价连线）  '
                       '判定 = SIGNAL   %d 根K线' % N)
    axis = None  # 横轴由 render() 输出
    tick = [' '] * (W + 10)
    for i, lab in ((PRE, 'L0'), (T0, 'P0'), (T1, 'P1'), (T2, 'P2'),
                   (T_BRK, 'X'), (T_SIG, 'C')):
        c = col[i] + 8                      # 行首是 '%6.2f │' 共 8 字符
        for k, ch in enumerate(lab):
            if c + k < len(tick):
                tick[c + k] = ch
    legend = ('\n   L=上涨起点 %.2f   H=前高 %.2f   B=杯底 %.2f   '
              'M=杯口 %.2f   P=柄低 %.2f\n'
              '   X=突破日 收 %.2f   C=确认日 收 %.2f（> 杯口 %.2f）→ 信号日\n'
              '   横轴为交易日，共 %d 根（含前置历史 26 根）；纵轴为收盘价（元）\n'
              % (L0, P0, P1, P2, P3, BRK, CONF, P2, len(xs)))

    text = ('════════════════════════════════════════════════════════════════'
            '══════════════════════════════\n'
            '  ① 标准杯柄形态 · 概念示意\n'
            '════════════════════════════════════════════════════════════════'
            '══════════════════════════════\n'
            + SCHEMA + '\n' + rule_block(params) + '\n'
            '════════════════════════════════════════════════════════════════'
            '══════════════════════════════\n'
            '  ② 引擎实测样本\n'
            '════════════════════════════════════════════════════════════════'
            '══════════════════════════════\n'
            + fig + '\n' + ''.join(tick) + legend)

    out = os.path.join(PROJECT_DIR, 'docs', 'product', '标准杯柄形态示意.txt')
    io.open(out, 'w', encoding='utf-8').write(text)
    print(text)
    print('saved', out)


if __name__ == '__main__':
    main()
