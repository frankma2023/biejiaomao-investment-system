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

# ── ① 概念示意 ────────────────────────────────────────
SCHEMA = r'''
   前高 P0
    ────┐                                                    ┌── 突破日
        │＼                                          ┌───────┘   (S1) 收盘 > 买点
        │  ＼                                        │           (S2) 量 ≥ 1.5 × MA20
        │    ＼                                   ┌──┘
        │      ＼        杯身深度 15~40%          │  杯口 P2
        │        ＼      (V5) = (P2−P1)/P2       │
        │          ＼                            │    买点 = 杯口 × 1.01
        │            ＼                          │    ↑ 收盘站上它才买
        │              ＼                        │
        │                ＼                     ╱
        │                  ＼                 ╱  柄部 (V8) 回撤 5~15%
        │                    ＼             ╱         (V9) ≤ 15 个交易日
        │                      ＼         ╱           缩量（巫毒日）
        │                        ＼     ╱
        │                          ＼ ╱
        └───────────────────────────┴────────────────────────────
                                   杯底 P1
                       (V13) 杯底 > 上涨起点 L0（不得跌破）
                       (V4)  杯底距今 35 ~ 325 个交易日

    ① 前置上涨        ② 杯左侧         ③ 杯右侧        ④ 柄部        ⑤ 突破
    (V2) ≥ 25%       (V3) ≥ 10 根     (B2) ≥ 5 根     (V8)(V9)     (S1)(S2)
    (V3) 下跌笔      圆弧，非直线      杯口/前高       窄幅横盘      放量长阳
                     (V6) ≤ 1.10      不高于前高

    ※ 杯口 ≠ 前高：杯口是「杯底之后右侧回升的最高收盘」，是柄部的起点，
      可以与前高不同高（603903 实测杯口 14.76 < 前高 16.60）。
    ※ 杯口只能被「走到」，不能被「跳上去」：单日跳涨出前 10 日区间 3% 以上的
      收盘不得充当杯口，否则突破日会把自己变成杯口，突破永远成立。
'''


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
    PRE, L0, P0, P1, P2, P3, BRK = 380, 10.00, 14.00, 10.50, 13.80, 12.55, 14.10
    T0, T1, T2 = PRE + 30, PRE + 45, PRE + 75
    T_SIG = T2 + 12
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
        elif i < T_SIG:
            closes.append(drift[i - (T2 + 5)])
        else:
            closes.append(BRK)

    params = ch.load_params()
    xs = list(range(PRE - 26, N))
    series = [closes[i] for i in xs]
    W = 104
    col = {i: int(round((i - xs[0]) * (W - 1) / float(len(xs) - 1)))
           for i in (PRE, T0, T1, T2, T2 + 4, T_SIG)}
    marks = {col[PRE]: ('L', L0), col[T0]: ('H', P0), col[T1]: ('B', P1),
             col[T2]: ('M', P2), col[T2 + 4]: ('P', P3), col[T_SIG]: ('X', BRK)}
    fig = render(series, W=W, H=23, lo=9.3, hi=14.6, marks=marks,
                 title='② 引擎实测样本（按比例，收盘价连线）  '
                       '判定 = SIGNAL   %d 根K线' % N)
    axis = None  # 横轴由 render() 输出
    tick = [' '] * (W + 10)
    for i, lab in ((PRE, 'L0'), (T0, 'P0'), (T1, 'P1'), (T2, 'P2'), (T_SIG, '突破')):
        c = col[i] + 8                      # 行首是 '%6.2f │' 共 8 字符
        for k, ch in enumerate(lab):
            if c + k < len(tick):
                tick[c + k] = ch
    legend = ('\n   L=上涨起点 10.00   H=前高 14.00   B=杯底 10.50   '
              'M=杯口 13.80   P=柄低 12.55   X=突破日 收 14.10\n'
              '   横轴为交易日，共 %d 根（含前置历史 26 根）；纵轴为收盘价（元）\n'
              % len(xs))

    text = ('════════════════════════════════════════════════════════════════'
            '══════════════════════════════\n'
            '  ① 标准杯柄形态 · 概念示意\n'
            '════════════════════════════════════════════════════════════════'
            '══════════════════════════════\n'
            + SCHEMA + '\n'
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
