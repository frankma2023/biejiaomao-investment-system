# -*- coding: utf-8 -*-
"""
生成「标准杯柄形态」示意图。

关键：不是手画示意图，而是先构造一段满足全部约束的合成K线，喂给引擎的
detect()，确认它确实判为 SIGNAL，再把这条被引擎认可的曲线画出来。
这样图上标注的每个参数才是引擎真正在用的那一个。

索引编排（PRE 为前置历史，不参与形态，只为满足引擎 need 的最小长度）：
    0 ................ 379   前置历史（长期低位盘整，仅作背景）
  380 ................ 449   ① 前置上涨  L0=10.00 → P0=14.00
  449 ................ 464   ② 杯左侧    P0 → P1=10.50
  464 ................ 494   ③ 杯右侧    P1 → P2=13.80
  494 ................ 505   ④ 柄部      P2 → P3=12.55
  506                        ⑤ 放量突破  收 14.10 > 买点 13.94
"""
import os
import sys
from datetime import date, timedelta

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))

from scanners import cup_handle_v2 as ch  # noqa: E402

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

PRE = 380
TL0 = PRE                 # 前置上涨起点
T0 = TL0 + 30             # 前高 P0
T1 = T0 + 15              # 杯底 P1
T2 = T1 + 30              # 杯口 P2
TH_END = T2 + 11          # 柄部最后一日
T_SIG = T2 + 12           # 检测日（突破日）
N = T_SIG + 1

BASE = 9.60               # 前置历史的盘整中枢
L0 = 10.00                # 前置上涨起点
P0 = 14.00                # 前高
P1 = 10.50                # 杯底
P2 = 13.80                # 杯口
P3 = 12.55                # 柄低
BUY = P2 * 1.01           # 买点（引擎 S1 用 P2 + buy_point_buffer）
BRK = 14.10               # 突破日收盘

HANDLE_DRIFT = [P3 + 0.06, P3 + 0.18, P3 + 0.10, P3 + 0.26,
                P3 + 0.20, P3 + 0.32, P3 + 0.24]      # 499..505

closes = []
for i in range(N):
    if i <= PRE:                                   # 背景：低位横向盘整
        closes.append(BASE + 0.12 * ((i % 11) - 5) / 5.0)
    elif i <= T0:                                  # ① 前置上涨
        u = (i - PRE) / float(T0 - PRE)
        closes.append(L0 + (P0 - L0) * u)
    elif i <= T1:                                  # ② 杯左侧（圆弧，前快后慢）
        u = (i - T0) / float(T1 - T0)
        closes.append(P0 - (P0 - P1) * (1 - (1 - u) ** 2))
    elif i <= T2:                                  # ③ 杯右侧（圆弧）
        u = (i - T1) / float(T2 - T1)
        closes.append(P1 + (P2 - P1) * (1 - (1 - u) ** 2))
    elif i <= T2 + 4:                              # ④ 柄部回落 P2 → P3
        u = (i - T2) / 4.0
        closes.append(P2 - (P2 - P3) * u)
    elif i < T_SIG:                                # ④ 柄部横盘收敛
        closes.append(HANDLE_DRIFT[i - (T2 + 5)])
    else:                                          # ⑤ 放量突破
        closes.append(BRK)

vols = [1.0e6] * N
for i in range(T2 + 1, T_SIG):
    vols[i] = 5.5e5                                # 柄部缩量
vols[T_SIG] = 2.4e6                               # 突破放量

base_d = date(2024, 1, 2)
daily = []
for i in range(N):
    cl = closes[i]
    op = closes[i - 1] if i else cl * 0.998
    daily.append({
        'date': (base_d + timedelta(days=i)).strftime('%Y-%m-%d'),
        'open': round(op, 3), 'high': round(max(op, cl) * 1.006, 3),
        'low': round(min(op, cl) * 0.994, 3), 'close': round(cl, 3),
        'volume': vols[i], 'stock_code': 'SYN001'})
D = [k['date'] for k in daily]

bi = [
    {'sdt': D[TL0] + ' 00:00:00', 'edt': D[T0] + ' 00:00:00', 'direction': '向上',
     'high': P0, 'low': L0, 'length': T0 - TL0},
    {'sdt': D[T0] + ' 00:00:00', 'edt': D[T1] + ' 00:00:00', 'direction': '向下',
     'high': P0, 'low': P1, 'length': T1 - T0},
    {'sdt': D[T1] + ' 00:00:00', 'edt': D[T2] + ' 00:00:00', 'direction': '向上',
     'high': P2, 'low': P1, 'length': T2 - T1},
    {'sdt': D[T2] + ' 00:00:00', 'edt': D[TH_END] + ' 00:00:00', 'direction': '向下',
     'high': P2, 'low': P3, 'length': TH_END - T2},
]

params = ch.load_params()
records, stats = ch.detect(daily, params, bi_list=bi, stock_code='SYN001',
                           as_of=D[T_SIG], record_types=('SIGNAL', 'CANDIDATE'),
                           diagnose=True)
print('=== 引擎判定（K线 %d 根）===' % N)
print('  记录数 =', len(records), ' 漏斗 =', stats)
for r in records:
    print('  %-9s %s  前高=%.3f 杯底=%.3f 杯口=%.3f 柄低=%.3f 买点=%.3f 深=%.2f%% '
          '柄回撤=%.2f%% 柄天数=%d 量比=%s'
          % (r['record_type'], r['date'], r['prior_high_price'], r['bottom_price'],
             r['mouth_price'], r['handle_low_price'], r['buy_point'], r['depth_pct'],
             r['handle_dd_pct'], r['handle_days'], r['breakout_vol_ratio']))
sig = [r for r in records if r['record_type'] == 'SIGNAL']
if not sig:
    sys.exit('引擎未认可该合成形态，先修构造再画图')
r = sig[0]

# ────────────────────────── 画图 ──────────────────────────
fig, (ax, axv) = plt.subplots(2, 1, figsize=(17.5, 10), sharex=True,
                              gridspec_kw={'height_ratios': [3.4, 1], 'hspace': 0.07})
fig.patch.set_facecolor('#0d0d12')
for a in (ax, axv):
    a.set_facecolor('#111118')
    a.tick_params(colors='#9aa0b4', labelsize=9)
    for sp in a.spines.values():
        sp.set_color('#2a2a38')

for i in range(N):
    k = daily[i]
    up = k['close'] >= k['open']
    col = '#ef4444' if up else '#22c55e'
    ax.plot([i, i], [k['low'], k['high']], color=col, lw=0.85, alpha=.85, zorder=3)
    ax.add_patch(Rectangle((i - .32, min(k['open'], k['close'])), .64,
                           max(abs(k['close'] - k['open']), .012), facecolor=col,
                           edgecolor=col, alpha=.95, zorder=3))
    axv.bar(i, k['volume'] / 1e6, color=col, width=.68, alpha=.8)

x = list(range(N))
ma20 = ch._rolling_mean(closes, 20)
ma50 = ch._rolling_mean(closes, 50)
ax.plot(x, [v or float('nan') for v in ma20], color='#8b5cf6', lw=1.0, alpha=.85, label='MA20')
ax.plot(x, [v or float('nan') for v in ma50], color='#f59e0b', lw=1.1, label='MA50')
vma = ch._rolling_mean(vols, params['vol_ma_window'])
axv.plot(x, [v / 1e6 if v else float('nan') for v in vma], color='#f59e0b', lw=1.0,
         label='MA20(量)')
axv.plot(x, [v * params['breakout_vol_ratio'] / 1e6 if v else float('nan') for v in vma],
         color='#FFD700', lw=1.0, ls='--', label='MA20×1.5（S2 门槛）')

bands = [(TL0, T0, '#ef4444', '① 前置上涨', 0),
         (T0, T1, '#10b981', '② 杯左侧', 1),
         (T1, T2, '#00E5FF', '③ 杯右侧', 0),
         (T2, TH_END, '#a78bfa', '④ 柄部', 1),
         (TH_END, N - 1, '#FFD700', '⑤ 突破', 0)]
ROW = (15.08, 14.52)                 # 两级错开，避免窄色带的标签互相压叠
for a_, b_, col, lab, row in bands:
    ax.axvspan(a_ - .5, b_ - .5, color=col, alpha=.055, zorder=0)
    right = (lab == '⑤ 突破')
    ax.annotate(lab, (b_ if right else (a_ + b_) / 2 - .5, ROW[row]),
                ha='right' if right else 'center', va='center', color=col,
                fontsize=11.5, fontweight='bold', zorder=8,
                xytext=(-2, 0) if right else (0, 0), textcoords='offset points')
    if a_ != TL0:
        ax.axvline(a_ - .5, color='#3a3a4c', lw=.9, ls=':', zorder=1)

for y, lab, col, ls, va in (
        (P0, '前高 P0 = %.2f' % P0, '#ef4444', (0, (5, 4)), 'bottom'),
        (P2, '杯口 P2 = %.2f    买点 = %.2f（P2×1.01，S1 触发价）' % (P2, BUY),
         '#00E5FF', (0, (5, 4)), 'top'),
        (P3, '柄低 P3 = %.2f' % P3, '#a78bfa', (0, (5, 4)), 'bottom'),
        (P1, '杯底 P1 = %.2f' % P1, '#10b981', (0, (5, 4)), 'bottom'),
        (L0, '上涨起点 L0 = %.2f（V13 底线，杯底不得跌破）' % L0,
         '#f97316', (0, (1, 3)), 'top')):
    ax.axhline(y, color=col, lw=1.05, ls=ls, alpha=.7, zorder=2)
    ax.annotate(lab, (TL0 - 58, y), color=col, fontsize=9, va=va, ha='left',
                zorder=9, bbox=dict(fc='#0d0d12', ec='none', alpha=.85, pad=1.2))
ax.axhline(BUY, color='#FFD700', lw=1.0, ls=(0, (2, 2)), alpha=.7, zorder=2)

for xi, yi, lab, col, dy in ((T0, P0, '前高', '#ef4444', -20),
                             (T1, P1, '杯底', '#10b981', 20),
                             (T2, P2, '杯口', '#00E5FF', -20),
                             (T2 + 4, P3, '柄低', '#a78bfa', 22),
                             (TL0, L0, '上涨起点', '#f97316', -22)):
    ax.scatter([xi], [yi], s=100, color=col, zorder=7, edgecolor='#0d0d12', linewidth=1.5)
    ax.annotate(lab, (xi, yi), textcoords='offset points', xytext=(0, dy), ha='center',
                color=col, fontsize=11, fontweight='bold', zorder=8)

ax.annotate('', xy=(T0, P0), xytext=(T0, L0),
            arrowprops=dict(arrowstyle='<->', color='#f97316', lw=1.5))
ax.annotate('前置涨幅 %.0f%%' % ((P0 - L0) / L0 * 100), (T0, (P0 + L0) / 2),
            xytext=(10, 0), textcoords='offset points', color='#f97316',
            fontsize=10.5, va='center', fontweight='bold')
ax.annotate('', xy=(T1 + 2, P2), xytext=(T1 + 2, P1),
            arrowprops=dict(arrowstyle='<->', color='#00E5FF', lw=1.5))
ax.annotate('杯身深度 %.1f%%\n= (P2−P1)/P2' % ((P2 - P1) / P2 * 100), (T1 + 2, P2 + 0.28),
            xytext=(14, 0), textcoords='offset points', color='#00E5FF',
            fontsize=10.5, va='center', fontweight='bold')
ax.annotate('', xy=(T2 + 5.4, P2), xytext=(T2 + 5.4, P3),
            arrowprops=dict(arrowstyle='<->', color='#a78bfa', lw=1.5))
ax.annotate('柄部回撤 %.1f%%' % ((P2 - P3) / P2 * 100), (T2 + 5.4, P3 - 0.42),
            ha='center', va='top', color='#a78bfa', fontsize=10.5, fontweight='bold')

ax.set_title('标准杯柄形态 —— 引擎实测判定：%s   %s' % (r['record_type'], r['date']),
             color='#e8e8f0', fontsize=16, fontweight='bold', pad=30)
ax.set_ylabel('价格（元）', color='#9aa0b4', fontsize=10)
ax.set_ylim(BASE - 0.7, 16.1)
ax.set_xlim(TL0 - 62, N + 1)
ax.legend(loc='lower right', facecolor='#181822', edgecolor='#2a2a38',
          labelcolor='#c8ccd8', fontsize=9)

lines = [
    '引擎实参（config/market/cup_handle_v2.yaml）与本次合成样本实测',
    '',
    'V2   前置上涨 %.1f%%    ≥ min_prior_advance 25%%' % ((P0 - L0) / L0 * 100),
    'V3   下跌K线 %d 根      ≥ min_descent_bars 10' % (T1 - T0),
    'V4   杯底距今 %d 日      ∈ [cup_min_age 35, 325]' % (T_SIG - T1),
    'V5   杯身深度 %.1f%%    ∈ [depth_min 15%%, 40%%]' % ((P2 - P1) / P2 * 100),
    'V6   杯口/前高 %.3f    ≤ mouth_vs_high_max 1.10' % (P2 / P0),
    'V8   柄部回撤 %.2f%%    ∈ [handle_dd_min 5%%, 15%%]' % ((P2 - P3) / P2 * 100),
    'V9   柄部时长 %d 日      ≤ handle_days_max 15' % (T_SIG - 1 - T2),
    'V10  柄低 %.2f       ≥ 杯底+50%%杯深 = %.2f' % (P3, P1 + .5 * (P2 - P1)),
    'V13  杯底 %.2f       > 上涨起点 L0 = %.2f' % (P1, L0),
    'S1   收盘 %.2f       > 买点 %.3f' % (BRK, BUY),
    'S2   突破量比 %.2f     ≥ breakout_vol_ratio 1.50' % r['breakout_vol_ratio'],
    'S5   杯口→突破 %d 日     ≤ mouth_to_signal_max 12' % (T_SIG - T2),
    '',
    '以上 13 项全部通过 → 引擎判为 SIGNAL',
]
ax.text(0.004, 0.945, '\n'.join(lines), transform=fig.transFigure, va='top', ha='left',
        fontsize=9.4, color='#b9c0d4', family='Microsoft YaHei', linespacing=1.55,
        bbox=dict(fc='#111118', ec='#2a2a38', alpha=.95, pad=8))

fig.subplots_adjust(left=0.30, right=0.985, top=0.925, bottom=0.075)

axv.annotate('柄部缩量', (T2 + 6, 1.05), color='#a78bfa', fontsize=10.5,
             ha='center', fontweight='bold')
axv.annotate('突破放量 %.2f×' % r['breakout_vol_ratio'], (T_SIG - 2, 2.62),
             color='#FFD700', fontsize=10.5, ha='center', fontweight='bold')
axv.set_ylabel('成交量（百万股）', color='#9aa0b4', fontsize=9)
axv.set_ylim(0, 3.05)
axv.legend(loc='upper left', facecolor='#181822', edgecolor='#2a2a38',
           labelcolor='#c8ccd8', fontsize=8.5)
axv.set_xlabel('交易日', color='#9aa0b4', fontsize=10)

out = os.path.join(PROJECT_DIR, 'docs', 'product', '标准杯柄形态示意.png')
plt.savefig(out, dpi=145, facecolor=fig.get_facecolor(), bbox_inches='tight')
print('saved', out)
