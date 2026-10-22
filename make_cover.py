"""三篇合集封面（小红书 3:4）。"""
import os
import sqlite3

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.font_manager import FontProperties

con = sqlite3.connect('file:data/lixinger.db?mode=ro', uri=True)
rows = con.execute("SELECT date, close FROM index_full_return_daily "
                   "WHERE stock_code='000922' AND date <= '2026-09-29' "
                   "ORDER BY date").fetchall()
con.close()
V = [float(r[1]) for r in rows]
N = len(V)
hi, AD = -1.0, []
for v in V:
    if v > hi:
        hi = v
    AD.append((hi - v) / hi * 100)

BG, GRID, TXT, MUT = '#0e0e12', '#2a2a33', '#e9e9ec', '#8f8f97'
AMBER, RED, GREEN, BLUE = '#f59e0b', '#ef4444', '#10b981', '#539BF5'
PURPLE = '#a855f7'
FP, FPB = r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\msyhbd.ttc'


def F(sz, bold=False):
    return FontProperties(fname=FPB if bold else FP, size=sz)


matplotlib.rcParams.update({
    'figure.facecolor': BG, 'axes.facecolor': BG, 'savefig.facecolor': BG,
    'font.family': 'sans-serif', 'font.sans-serif': ['Microsoft YaHei', 'SimHei'],
})
W, H = 9.0, 12.0
fig = plt.figure(figsize=(W, H), dpi=240)

# 底部回撤曲线（背景元素）
ax = fig.add_axes([0.0, 0.0, 1.0, 0.185])
x = np.arange(N)
ax.fill_between(x, AD, 0, color=AMBER, alpha=.13, linewidth=0)
ax.plot(x, AD, color=AMBER, linewidth=.75, alpha=.55)
ax.invert_yaxis()
ax.set_xlim(0, N)
ax.set_ylim(max(AD) * 1.06, -2)
ax.axis('off')

# 顶部装饰条
fig.patches.append(plt.Rectangle((0.075, 0.900), 0.115, 0.0075,
                                 transform=fig.transFigure,
                                 facecolor=AMBER, edgecolor='none'))

fig.text(0.075, 0.945, '红利投资 · 回撤系列', fontproperties=F(19, True),
         color=AMBER, va='top')
fig.text(0.075, 0.862, '回撤\n投资法', fontproperties=F(62, True),
         color=TXT, va='top', linespacing=1.12)
fig.text(0.075, 0.660, '三篇合集 · 用十年半的数据，回答三个问题',
         fontproperties=F(17), color=MUT, va='top')

# 三篇
ITEMS = [
    ('01', '回撤多少才该买', '10% / 14% / 20%，哪个才是买点', BLUE),
    ('02', '拿多久才算数', '20% 档为什么在三个月里垫底', GREEN),
    ('03', '该买、该等还是该割', '等回撤，到底值不值', AMBER),
]
y = 0.560
for num, title, sub, col in ITEMS:
    fig.text(0.075, y, num, fontproperties=F(30, True), color=col, va='top')
    fig.text(0.148, y + 0.004, title, fontproperties=F(24, True),
             color=TXT, va='top')
    fig.text(0.148, y - 0.036, sub, fontproperties=F(14.5), color=MUT, va='top')
    y -= 0.088

# 关键数字条
fig.patches.append(plt.Rectangle((0.075, 0.310), 0.85, 0.0012,
                                 transform=fig.transFigure,
                                 facecolor=GRID, edgecolor='none'))
KD = [('64.8%', '随便哪天买\n60 日胜率'), ('80.0%', '14% 档\n60 日胜率'),
      ('64%', '红利收益里\n分红的占比')]
for i, (v, l) in enumerate(KD):
    x0 = 0.075 + i * 0.285
    fig.text(x0, 0.272, v, fontproperties=F(30, True), color=AMBER, va='top')
    fig.text(x0, 0.208, l, fontproperties=F(12.5), color=MUT, va='top',
             linespacing=1.5)

fig.text(0.075, 0.040, '仅供研究与学习参考，不构成投资建议',
         fontproperties=F(12), color=MUT, va='top')

os.makedirs('docs/article/_cover', exist_ok=True)
out = 'docs/article/_cover/红利回撤三篇合集_封面.png'
fig.savefig(out, dpi=240, facecolor=BG)
fig.savefig(out.replace('.png', '.svg'), facecolor=BG)
plt.close(fig)
print('  写出', out, f'({os.path.getsize(out)/1024:.0f} KB)')
