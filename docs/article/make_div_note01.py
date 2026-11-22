# -*- coding: utf-8 -*-
"""生成《红利还能拿吗：六只指数同时亮红灯》小红书图文。

交付物：
  docs/article/红利减仓笔记01_六只亮红灯/index.html
  docs/article/红利减仓笔记01_六只亮红灯/images/p1..p6.{png,svg}

数字全部现算自 data/lixinger.db，数据日 2026-11-20。

口径（与 /market-scan/red-dividend/ 页面一致）：
  PE / PB / 股息率分位 = 滚动 750 交易日（约 3 年）分位，仅用当日及之前数据；
  指数历史不足 750 个交易日的（价值100、智选高股息、800红利低波），窗口实为上市以来；
  息差 = 股息率 − 10 年国债收益率（不是 E/P 口径）。
"""
import io
import os
import sqlite3

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch, Rectangle

NAME = '红利减仓笔记01_六只亮红灯'
OUT = os.path.join('docs', 'article', NAME)
IMG = os.path.join(OUT, 'images')
DB = os.path.join('data', 'lixinger.db')
DATE = '2026-11-20'

FP = r'C:\Windows\Fonts\msyh.ttc'
FPB = r'C:\Windows\Fonts\msyhbd.ttc'
BG = '#0e0e12'
CARD = '#1b1b21'
TXT = '#e9e9ec'
MUT = '#8f8f97'
LINE = '#2a2a33'
AMBER = '#f59e0b'
GREEN = '#10b981'
RED = '#ef4444'
BLUE = '#539BF5'
PURPLE = '#a78bfa'
TEAL = '#2dd4bf'

DPI = 240
WIN = 750

CODES = [
    ('000922', '中证红利', 'A股'),
    ('H30269', '红利低波', 'A股'),
    ('930955', '红利低波100', 'A股'),
    ('932305', '智选高股息', 'A股'),
    ('000015', '红利指数', 'A股'),
    ('931848', '800红利低波', 'A股'),
    ('931468', '红利质量', 'A股'),
    ('980081', '价值100', 'A股'),
    ('930914', '港股通高股息', '港股'),
]


def F(size, bold=False):
    return FontProperties(fname=FPB if bold else FP, size=size)


def save(fig, name):
    os.makedirs(IMG, exist_ok=True)
    for ext in ('png', 'svg'):
        fig.savefig(os.path.join(IMG, f'{name}.{ext}'), dpi=DPI if ext == 'png' else None,
                    facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f'    images/{name}.png / .svg')


def canvas(w=10.8, h=14.4):
    fig = plt.figure(figsize=(w, h), facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')
    ax.set_facecolor(BG)
    return fig, ax


def head(ax, kicker, title, sub=None, y=0.945):
    ax.text(0.075, y, kicker, fontproperties=F(20), color=AMBER)
    ax.plot([0.075, 0.135], [y - 0.022, y - 0.022], color=AMBER, lw=3)
    ax.text(0.075, y - 0.058, title, fontproperties=F(38, True), color=TXT,
            va='top', linespacing=1.42)
    if sub:
        ax.text(0.075, y - 0.058 - 0.055 * (title.count('\n') + 1), sub,
                fontproperties=F(19), color=MUT, va='top', linespacing=1.75)


def foot(ax, text, y=0.040):
    ax.text(0.075, y, text, fontproperties=F(14), color=MUT, va='bottom', linespacing=1.7)


def card(ax, x, y, w, h, fc=CARD, ec=LINE, lw=1.5):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle='round,pad=0.012,rounding_size=0.02',
                                facecolor=fc, edgecolor=ec, lw=lw))


# ───────────────────────── 取数 ─────────────────────────
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row


def pct(vals, cur):
    w = [v for v in vals if v is not None]
    if not w:
        return None
    return round(sum(1 for x in w if x <= cur) / len(w) * 100, 1)


y10_row = con.execute("SELECT date, y10 FROM bond_yield_daily WHERE date<=?"
                      " ORDER BY date DESC LIMIT 1", (DATE,)).fetchone()
Y10 = y10_row['y10']
Y10_DATE = y10_row['date']

ROWS = []
for code, name, mkt in CODES:
    f = con.execute("""SELECT date, pe_ttm, pb, dyr FROM index_fundamental_daily
                       WHERE stock_code=? AND date<=? AND pe_ttm IS NOT NULL
                       ORDER BY date""", (code, DATE)).fetchall()
    pe = [x['pe_ttm'] for x in f]
    pb = [x['pb'] for x in f]
    dy = [x['dyr'] for x in f]
    pe_w, pb_w, dy_w = pe[-WIN:], pb[-WIN:], dy[-WIN:]
    spread_hist = []
    for x in f[-WIN:]:
        if x['dyr'] is not None and x['date'] in dict(
                (r['date'], r['y10']) for r in con.execute(
                    "SELECT date, y10 FROM bond_yield_daily WHERE date<=? AND date>=?",
                    (DATE, f[0]['date']))):
            pass
    # 用一次批量查询拼息差序列
    y10map = dict((r['date'], r['y10']) for r in con.execute(
        "SELECT date, y10 FROM bond_yield_daily WHERE date<=?", (DATE,)))
    spread_hist = [(x['date'], round(x['dyr'] * 100 - y10map[x['date']], 2))
                   for x in f[-WIN:] if x['dyr'] is not None and y10map.get(x['date']) is not None]

    fr = con.execute("""SELECT date, close FROM index_full_return_daily
                        WHERE stock_code=? AND date<=? AND close IS NOT NULL
                        ORDER BY date""", (code, DATE)).fetchall()
    frs = [(x['date'], x['close']) for x in fr][-WIN:]
    if frs:
        hi = max(frs, key=lambda t: t[1])
        dd = round((frs[-1][1] / hi[1] - 1) * 100, 1)
        hi_date = hi[0]
    else:
        k = con.execute("""SELECT date, close FROM index_daily_kline
                           WHERE stock_code=? AND kline_type='normal' AND date<=?
                           ORDER BY date""", (code, DATE)).fetchall()
        ks = [(x['date'], x['close']) for x in k][-WIN:]
        hi = max(ks, key=lambda t: t[1]) if ks else ('', 0)
        dd = round((ks[-1][1] / hi[1] - 1) * 100, 1) if ks else None
        hi_date = hi[0]

    ROWS.append({
        'code': code, 'name': name, 'mkt': mkt,
        'start': f[0]['date'], 'n': len(f),
        'pe': round(pe[-1], 2), 'pe_pct': pct(pe_w, pe[-1]), 'pe_all': pct(pe, pe[-1]),
        'pb': round(pb[-1], 2), 'pb_pct': pct(pb_w, pb[-1]),
        'dyr': round(dy[-1] * 100, 2), 'dyr_pct': pct(dy_w, dy[-1]), 'dyr_all': pct(dy, dy[-1]),
        'spread': round(dy[-1] * 100 - Y10, 2),
        'dd': dd, 'hi_date': hi_date,
        'spread_hist': spread_hist,
        'pe_hist': [(x['date'], round(x['pe_ttm'], 2)) for x in f[-WIN:]],
        'dy_hist': [(x['date'], round(x['dyr'] * 100, 2)) for x in f[-WIN:] if x['dyr'] is not None],
    })

BY = dict((r['code'], r) for r in ROWS)


def verdict(r):
    """按页面 v2.0 规则复算结论：风险分 = max(股息率分位, PE 分位, 息差)，广度≥2 才减仓。"""
    dp, pp = r['dyr_pct'], r['pe_pct']
    r_div = 70 if dp < 20 else (30 if dp < 40 else 0)
    r_pe = 30 if pp >= 80 else 0
    r_sp = 60 if r['spread'] < 0 else (10 if r['spread'] < 1 else 0)
    risk = max(r_div, r_pe, r_sp)
    breadth = sum(1 for v in (r_div, r_pe, r_sp) if v >= 30)
    buy = 85 if (r['dd'] or 0) <= -20 else (25 if (r['dd'] or 0) <= -10 else 0)
    if buy >= 35 and risk < 70:
        return '买入区', 'buy'
    if risk >= 70 and buy < 20 and breadth >= 2:
        return '减仓', 'reduce'
    if risk >= 60 and buy < 35:
        return '谨慎', 'caution'
    return '观望', 'hold'


for r in ROWS:
    r['verdict'], r['level'] = verdict(r)

CUT = [r for r in ROWS if r['level'] == 'reduce']
CAUT = [r for r in ROWS if r['level'] == 'caution']
HOLD = [r for r in ROWS if r['level'] == 'hold']
print(f'数据日 {DATE}  10Y {Y10}%（{Y10_DATE}）')
for r in ROWS:
    print(f"  {r['name']:<10} PE {r['pe']:>6} 3y{r['pe_pct']:>6}%  PB {r['pb']:>5} 3y{r['pb_pct']:>6}%"
          f"  DYR {r['dyr']:>5} 3y{r['dyr_pct']:>6}%  息差 {r['spread']:>5}  距高 {r['dd']:>6}%  {r['verdict']}")
print(f'  减仓 {len(CUT)}  谨慎 {len(CAUT)}  观望 {len(HOLD)}')

MIN_DY_PCT = min(r['dyr_pct'] for r in CUT)
MAX_DY_PCT = max(r['dyr_pct'] for r in CUT)
NEAR_HIGH = [r for r in ROWS if r['dd'] is not None and r['dd'] > -1.0]


# ───────────────────────── p1 封面 ─────────────────────────
def p1():
    fig, ax = canvas()
    ax.text(0.075, 0.925, '红利指数 · 模型读数', fontproperties=F(21), color=AMBER)
    ax.plot([0.075, 0.135], [0.903, 0.903], color=AMBER, lw=3)
    ax.text(0.075, 0.840, '红利还能拿吗', fontproperties=F(64, True), color=TXT, va='top')
    ax.text(0.075, 0.752, '六只同时亮红灯', fontproperties=F(64, True), color=AMBER, va='top')
    ax.text(0.075, 0.640, '9 只红利指数 · 数据日 2026-11-20', fontproperties=F(23), color=MUT)

    kp = [(f'{len(CUT)}/9', '判断减仓', RED),
          (f'{MIN_DY_PCT}~{MAX_DY_PCT}%', '股息率近 3 年分位', AMBER),
          ('0.0%', '800红利低波距高点', GREEN)]
    for i, (v, lab, col) in enumerate(kp):
        x = 0.075 + i * 0.285
        card(ax, x, 0.455, 0.255, 0.115)
        ax.text(x + 0.022, 0.525, v, fontproperties=F(30, True), color=col, va='center')
        ax.text(x + 0.022, 0.485, lab, fontproperties=F(14), color=MUT, va='center')

    card(ax, 0.075, 0.230, 0.85, 0.185)
    ax.text(0.100, 0.380, '一句话', fontproperties=F(15), color=AMBER)
    ax.text(0.100, 0.342,
            '不是因为跌，是因为贵。\n指数贴在三年高点，股息率掉到三年最低，\n'
            '股债息差只剩 2 个百分点上下。',
            fontproperties=F(20), color=TXT, va='top', linespacing=1.85)

    ax.text(0.075, 0.180, '本篇看点', fontproperties=F(15), color=AMBER)
    ax.text(0.075, 0.145,
            '01 名单与读数　02 PE/PB 分位轨道　03 股债息差为什么变薄\n'
            '04 指数位置　05 两个例外　06 判断怎么算　07 后续四个信号',
            fontproperties=F(15), color=MUT, va='top', linespacing=1.95)
    foot(ax, '口径：分位为滚动 3 年；息差 = 股息率 − 十年国债。非投资建议。')
    save(fig, 'p1')


# ───────────────────────── p2 PE/PB 分位轨道 ─────────────────────────
def p2():
    fig, ax = canvas()
    head(ax, '贵在哪', 'PE 与 PB 都贴在轨道右端',
         '轨道 0~100 为该指数近 3 年分位；越靠右越贵。\n'
         '六个标红的指数，PE 分位全在 99.8% 以上。')

    order = [r for r in ROWS if r['level'] == 'reduce'] + \
            [r for r in ROWS if r['level'] != 'reduce']
    y0, gap = 0.700, 0.038
    for i, r in enumerate(order):
        y = y0 - i * gap
        ax.text(0.085, y, r['name'], fontproperties=F(15, True), color=TXT, va='center')
        ax.add_patch(Rectangle((0.315, y - 0.009), 0.545, 0.018, facecolor='#26262e',
                               edgecolor='none'))
        for gx in (20, 50, 80):
            ax.plot([0.315 + 0.545 * gx / 100] * 2, [y - 0.012, y + 0.012],
                    color='#4a4a55', lw=1.0)
        px = 0.315 + 0.545 * (r['pe_pct'] if r['pe_pct'] is not None else 0) / 100
        ax.plot([px], [y + 0.005], 'o', ms=10, color=AMBER, zorder=5)
        bx = 0.315 + 0.545 * (r['pb_pct'] if r['pb_pct'] is not None else 0) / 100
        ax.plot([bx], [y - 0.007], 'o', ms=10, color=BLUE, zorder=5)
        ax.text(0.878, y + 0.004, f"PE {r['pe_pct']:.0f}%", fontproperties=F(12.5),
                color=AMBER, va='center')
        ax.text(0.878, y - 0.015, f"PB {r['pb_pct']:.0f}%", fontproperties=F(12.5),
                color=BLUE, va='center')

    ax.text(0.085, 0.738, '●PE', fontproperties=F(14), color=AMBER)
    ax.text(0.148, 0.738, '●PB', fontproperties=F(14), color=BLUE)
    ax.text(0.211, 0.738, '竖线 20 / 50 / 80', fontproperties=F(12), color=MUT)
    ax.text(0.587, 0.738, '50', fontproperties=F(12), color=MUT, ha='center')
    ax.text(0.860, 0.738, '100 分位', fontproperties=F(12), color=MUT, ha='center')

    card(ax, 0.075, 0.225, 0.85, 0.135)
    ax.text(0.100, 0.338, '中证红利这条线最典型', fontproperties=F(15), color=AMBER)
    r = BY['000922']
    ax.text(0.100, 0.302,
            f"PE {r['pe']}，是近 3 年最贵的一天（区间 5.76~9.49）；\n"
            f"股息率 {r['dyr']}%，是近 3 年最低的一天（区间 3.84~6.89）。\n"
            '同一只指数，两头同时顶到边。',
            fontproperties=F(15.5), color=TXT, va='top', linespacing=1.7)

    ax.text(0.075, 0.120, f"但「100% 分位」只在近 3 年成立：中证红利全历史 PE 分位 "
                          f"{BY['000922']['pe_all']}%、股息率分位 {BY['000922']['dyr_all']}%。",
            fontproperties=F(14.5), color=AMBER, va='top', linespacing=1.7)
    foot(ax, '注：PB 分位同样按滚动 3 年算，与页面上的供应商字段（≈全史）不是同一窗口。')
    save(fig, 'p2')


# ───────────────────────── p3 息差收窄 ─────────────────────────
def p3():
    fig, ax = canvas()
    head(ax, '性价比', '股债息差被压到 2 个百分点',
         '息差 = 股息率 − 十年国债收益率。它越薄，红利相对债券的性价比越低。\n'
         '下面是三条代表序列的三年走势。')

    area = fig.add_axes([0.115, 0.400, 0.755, 0.230], facecolor=CARD)
    picks = [('000922', '中证红利', AMBER), ('H30269', '红利低波', BLUE),
             ('930914', '港股通高股息', TEAL)]
    for code, name, col in picks:
        h = BY[code]['spread_hist']
        xs = list(range(len(h)))
        ys = [v for _, v in h]
        area.plot(xs, ys, lw=2.0, color=col, label=name)
        area.plot([xs[-1]], [ys[-1]], 'o', ms=6, color=col, zorder=6)
    area.axhline(0, color='#5a5a66', lw=1.2, ls='--')
    area.text(6, 0.20, '0 = 股息率与国债持平', fontproperties=F(11.5), color=MUT, va='bottom')
    area.set_xlim(0, len(BY['000922']['spread_hist']) + 16)
    area.set_ylim(-0.3, 8.0)
    area.tick_params(colors=MUT, labelsize=10)
    for s in area.spines.values():
        s.set_color('#33333c')
    area.grid(alpha=0.10, color='#8f8f97')
    area.legend(loc='lower left', bbox_to_anchor=(0, 1.015), frameon=False,
                prop=F(12.5), labelcolor=TXT, ncol=3, handlelength=1.8,
                columnspacing=1.6, borderpad=0)
    dates = [d for d, _ in BY['000922']['spread_hist']]
    ticks = [0, len(dates) // 2, len(dates) - 1]
    area.set_xticks(ticks)
    area.set_xticklabels([dates[i][2:7] for i in ticks], fontproperties=F(11))
    area.set_ylabel('pp', fontproperties=F(12), color=MUT)

    # 三条读数卡
    for i, (code, name, col) in enumerate(picks):
        r = BY[code]
        x = 0.075 + i * 0.287
        card(ax, x, 0.255, 0.258, 0.105)
        ax.text(x + 0.020, 0.338, name, fontproperties=F(13), color=MUT)
        ax.text(x + 0.020, 0.305, f"{r['spread']:.2f}pp", fontproperties=F(24, True), color=col)
        ax.text(x + 0.020, 0.270, f"股息率 {r['dyr']:.2f}% − 国债 {Y10:.2f}%",
                fontproperties=F(11.5), color=MUT)

    ax.text(0.075, 0.212, f'十年国债 {Y10:.2f}%（{Y10_DATE}）· 三条序列都已从 2024 年的 5~7pp 压到 2~3.6pp',
            fontproperties=F(14.5), color=TXT, va='top')
    ax.text(0.075, 0.168, '港股通 3.61pp 最厚，是这组里唯一还有缓冲的；\n'
                          'A 股红利两只只剩 2.3pp，等于多拿一年分红只多赚 2.3% 的利差。',
            fontproperties=F(14.5), color=MUT, va='top', linespacing=1.75)
    foot(ax, '注意：这个「息差」是股息率口径，不是 E/P 口径。两者相差约 4 倍，不可混用。')
    save(fig, 'p3')


# ───────────────────────── p4 位置对照 ─────────────────────────
def p4():
    fig, ax = canvas()
    head(ax, '位置', '指数还贴在 3 年高点附近',
         '柱长 = 距近 3 年最高点的回撤。柱子越短，越说明没有跌过。')

    order = sorted(ROWS, key=lambda r: (r['dd'] if r['dd'] is not None else 0))
    y = 0.730
    for r in order:
        dd = r['dd'] or 0
        w = min(abs(dd) / 20.0, 1.0) * 0.42
        col = RED if dd > -3 else (AMBER if dd > -10 else BLUE)
        ax.add_patch(Rectangle((0.315, y - 0.011), max(w, 0.004), 0.022, facecolor=col,
                               edgecolor='none'))
        ax.text(0.085, y, r['name'], fontproperties=F(15, True), color=TXT, va='center')
        ax.text(0.775, y, f'{dd:.1f}%', fontproperties=F(14, True), color=col, va='center')
        ax.text(0.900, y, r['hi_date'], fontproperties=F(10.5), color=MUT, va='center')
        y -= 0.041

    ax.text(0.315, 0.770, '距 3 年高点回撤', fontproperties=F(12), color=MUT)
    ax.text(0.775, 0.770, '回撤', fontproperties=F(12), color=MUT)
    ax.text(0.900, 0.770, '高点日', fontproperties=F(12), color=MUT)

    card(ax, 0.075, 0.200, 0.85, 0.140)
    ax.text(0.100, 0.312, f'{len(NEAR_HIGH)} 只距高点不到 1%', fontproperties=F(15), color=AMBER)
    ax.text(0.100, 0.276,
            '红利低波 0.1%、800红利低波 0.0%、红利低波100 0.2%、智选高股息 0.0%。\n'
            '唯一跌出空间的是红利质量 −14.9%（高点 2026-05-11）。',
            fontproperties=F(16), color=TXT, va='top', linespacing=1.75)
    foot(ax, '高点取 index_full_return_daily 全收益序列的 750 日最高收盘，含分红再投。')
    save(fig, 'p4')


# ───────────────────────── p5 九只一览 ─────────────────────────
def p5():
    fig, ax = canvas()
    head(ax, '一览', '九只红利指数，结论分三档', f'数据日 {DATE} · 息差对 10 年国债 {Y10:.2f}%')

    cx = [0.075, 0.290, 0.400, 0.495, 0.590, 0.685, 0.775, 0.870]
    heads = ['指数', 'PE', 'PE分位', '股息率', '息差', '距高点', '结论']
    hx = [cx[0], cx[1], cx[2], cx[3], cx[4], cx[5], cx[7]]
    y = 0.757
    for x, t in zip(hx, heads):
        ax.text(x, y, t, fontproperties=F(13), color=MUT,
                ha='right' if x not in (cx[0],) else 'left')
    ax.plot([0.075, 0.925], [y - 0.018] * 2, color=LINE, lw=1.2)

    y = 0.712
    for r in ROWS:
        vcol = {'reduce': RED, 'caution': AMBER, 'hold': BLUE}[r['level']]
        ax.text(0.075, y, r['name'], fontproperties=F(15, True), color=TXT, va='center')
        ax.text(cx[1], y, f"{r['pe']:.1f}", fontproperties=F(14), color=TXT, va='center', ha='right')
        ax.text(cx[2], y, f"{r['pe_pct']:.0f}%", fontproperties=F(14),
                color=AMBER if r['pe_pct'] >= 80 else TXT, va='center', ha='right')
        ax.text(cx[3], y, f"{r['dyr']:.2f}%", fontproperties=F(14), color=TXT, va='center', ha='right')
        ax.text(cx[4], y, f"{r['spread']:.2f}", fontproperties=F(14), color=TXT, va='center', ha='right')
        ax.text(cx[5], y, f"{r['dd']:.1f}%", fontproperties=F(14), color=TXT, va='center', ha='right')
        ax.text(cx[7], y, r['verdict'], fontproperties=F(15, True), color=vcol, va='center', ha='right')
        y -= 0.045

    ax.plot([0.075, 0.925], [y + 0.018] * 2, color=LINE, lw=1.2)
    ax.text(0.075, y - 0.010,
            f'减仓 {len(CUT)} 只 · 谨慎 {len(CAUT)} 只 · 观望 {len(HOLD)} 只。\n'
            '触发减仓的是股息率分位跌破 20%，不是 PE。',
            fontproperties=F(14.5), color=MUT, va='top', linespacing=1.7)

    card(ax, 0.075, 0.150, 0.85, 0.115)
    ax.text(0.100, 0.238, '三个反差', fontproperties=F(14), color=AMBER)
    ax.text(0.100, 0.205,
            'PE 分位打满的六只全在减仓档；股息率 3.84% 是三年最低；\n'
            '但放到全历史，红利还不算最贵 —— 只是这三年最贵。',
            fontproperties=F(14.5), color=TXT, va='top', linespacing=1.75)
    foot(ax, 'PE / 股息率分位为滚动 3 年；价值100（2024-12 起）、智选高股息（2024-09 起）、\n'
             '800红利低波（2024-05 起）历史不足 3 年，实为上市以来分位。')
    save(fig, 'p5')


# ───────────────────────── p6 口径与用法 ─────────────────────────
def p6():
    fig, ax = canvas()
    head(ax, '怎么用', '三条口径，四个动作')

    card(ax, 0.075, 0.655, 0.85, 0.190)
    ax.text(0.100, 0.815, '口径先说清', fontproperties=F(16), color=AMBER)
    ax.text(0.100, 0.778,
            '① 分位 = 滚动 750 交易日（约 3 年），只用当日及之前的数据。\n'
            '② 息差 = 股息率 − 十年国债，不是 E/P 减国债。\n'
            '③ 三只指数历史不足 3 年，它们的分位是「上市以来」，不能跨指数直接比。',
            fontproperties=F(16), color=TXT, va='top', linespacing=1.95)

    card(ax, 0.075, 0.385, 0.85, 0.235)
    ax.text(0.100, 0.585, '接下来看什么', fontproperties=F(16), color=AMBER)
    ax.text(0.100, 0.548,
            '① 股息率分位回到 20% 以上 —— 这是减仓判据的解除条件。\n'
            '② 息差重新走阔到 3pp 以上 —— 才谈得上「相对债券便宜」。\n'
            '③ 指数跌出 10% 以上回撤 —— 才可能出现买点。\n'
            '④ 港股通高股息只有一项不利，是这组里性价比相对最好的。',
            fontproperties=F(16), color=TXT, va='top', linespacing=1.95)

    ax.text(0.075, 0.333, '减仓说的是性价比，不是看空。红利拿分红的长逻辑没有变。',
            fontproperties=F(18, True), color=TXT, va='top')
    ax.text(0.075, 0.268, '贵不贵看分位，值不值看息差，能不能买看回撤。\n'
                          '这三个问题在这九只指数上，答案分别是「三年最贵」「只剩 2pp」「还没有」。',
            fontproperties=F(15), color=MUT, va='top', linespacing=1.8)
    foot(ax, '数据日 2026-11-20。本文只做模型读数说明，不构成投资建议。')
    save(fig, 'p6')


for fn in (p1, p2, p3, p4, p5, p6):
    fn()
    print(f'  {fn.__name__} ok')


# ───────────────────────── HTML ─────────────────────────
CSS = """
*,*::before,*::after{box-sizing:border-box}
body{margin:0;background:#0e0e12;color:#e9e9ec;
 font-family:"PingFang SC","Microsoft YaHei",-apple-system,sans-serif;
 line-height:1.85;-webkit-font-smoothing:antialiased}
.wrap{max-width:680px;margin:0 auto;padding:34px 20px 60px}
.tag{display:inline-block;font-size:12px;letter-spacing:.08em;color:#f59e0b;
 border:1px solid rgba(245,158,11,.4);border-radius:999px;padding:3px 11px;margin-bottom:18px}
h1{font-size:30px;line-height:1.42;margin:0 0 10px;font-weight:800;letter-spacing:-.01em}
.sub{color:#8f8f97;font-size:14px;margin:0 0 26px}
.lede{border-left:3px solid #f59e0b;background:rgba(245,158,11,.07);
 padding:15px 18px;border-radius:0 10px 10px 0;margin:0 0 30px;font-size:15.5px;color:#dedee2}
h2{font-size:21px;margin:44px 0 14px;font-weight:800;display:flex;align-items:baseline;gap:9px}
h2 .n{color:#f59e0b;font-size:14px;font-weight:700;letter-spacing:.06em}
p{margin:0 0 18px;font-size:15.5px;color:#d6d6da}
b,strong{color:#fff;font-weight:700}
.kpi{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:0 0 28px}
.kpi div{background:#1b1b21;border:1px solid #2a2a33;border-radius:12px;padding:13px 12px}
.kpi .v{font-size:20px;font-weight:800;display:block;line-height:1.3}
.kpi .l{font-size:11.5px;color:#8f8f97;display:block;margin-top:3px}
figure{margin:26px 0 30px}
figure img{width:100%;display:block;border-radius:14px;border:1px solid #2a2a33}
figcaption{color:#8f8f97;font-size:12.5px;margin-top:9px;line-height:1.7}
.tw{overflow-x:auto;margin:0 0 26px;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:13.5px;min-width:520px}
th,td{padding:9px 10px;border-bottom:1px solid #26262e;text-align:right;white-space:nowrap}
th{color:#8f8f97;font-weight:600;font-size:12px;border-bottom:1px solid #3a3a44}
td:first-child,th:first-child{text-align:left}
tr.hi td{background:rgba(239,68,68,.07)}
.red{color:#ef4444}.amber{color:#f59e0b}.blue{color:#539BF5}.teal{color:#2dd4bf}.mut{color:#8f8f97}
blockquote{margin:0 0 26px;padding:14px 17px;border-radius:10px;font-size:15px}
blockquote.warn{background:rgba(239,68,68,.09);border-left:3px solid #ef4444;color:#f2d7d7}
blockquote.end{background:rgba(16,185,129,.09);border-left:3px solid #10b981;color:#d8efe6}
ul{margin:0 0 22px;padding-left:20px}
li{margin-bottom:9px;font-size:15px;color:#d6d6da}
.foot{color:#6f6f78;font-size:12px;margin-top:40px;padding-top:18px;border-top:1px solid #26262e;line-height:1.8}
"""


def fig(name, cap):
    return (f'<figure><img src="images/{name}.svg" alt="{cap}" loading="lazy">'
            f'<figcaption>{cap}</figcaption></figure>')


def tbl(headers, rows, left_cols=(0,), hi_rows=()):
    out = ['<div class="tw"><table>']
    out.append('<tr>' + ''.join(f'<th>{h}</th>' for h in headers) + '</tr>')
    for i, r in enumerate(rows):
        cls = ' class="hi"' if i in hi_rows else ''
        cells = []
        for j, c in enumerate(r):
            cls2 = ''
            if isinstance(c, tuple):
                c, cls2 = c
            al = '' if j in left_cols else ''
            cells.append(f'<td class="{cls2}{al}">{c}</td>' if (cls2 or al) else f'<td>{c}</td>')
        out.append(f'<tr{cls}>' + ''.join(cells) + '</tr>')
    out.append('</table></div>')
    return '\n'.join(out)


def vcls(level):
    return {'reduce': 'red', 'caution': 'amber', 'hold': 'blue'}[level]


rows_perf = []
for r in ROWS:
    rows_perf.append([
        r['name'],
        f"{r['pe']:.1f}",
        (f"{r['pe_pct']:.0f}%", 'amber' if r['pe_pct'] >= 80 else ''),
        f"{r['dyr']:.2f}%",
        (f"{r['dyr_pct']:.1f}%", 'red' if r['dyr_pct'] < 20 else ''),
        f"{r['spread']:.2f}pp",
        f"{r['dd']:.1f}%",
        (r['verdict'], vcls(r['level'])),
    ])

T_MAIN = tbl(['指数', 'PE', 'PE分位', '股息率', '股息率分位', '息差', '距高点', '结论'],
             rows_perf,
             hi_rows=tuple(i for i, r in enumerate(ROWS) if r['level'] == 'reduce'))

T_CUT = tbl(['指数', 'PE', '股息率', '股息率近3年分位', '息差', '距3年高点'],
            [[r['name'], f"{r['pe']:.1f}", f"{r['dyr']:.2f}%",
              (f"{r['dyr_pct']:.0f}%", 'red'), f"{r['spread']:.2f}pp", f"{r['dd']:.1f}%"]
             for r in CUT], hi_rows=())

BODY = f"""
<div class="wrap">
<span class="tag">指数读数 · 第 01 期</span>
<h1>红利还能拿吗：六只指数同时亮红灯</h1>
<p class="sub">9 只红利指数 · 数据日 2026-11-20 · 息差对 10 年国债 {Y10:.2f}%</p>

<div class="lede">
不是因为跌，是因为贵。指数贴在三年高点，股息率掉到三年最低，股债息差只剩 2 个百分点上下。
九个标的里，六个的判断是减仓。
</div>

<div class="kpi">
<div><span class="v red">{len(CUT)} / 9</span><span class="l">判断减仓</span></div>
<div><span class="v amber">{MIN_DY_PCT}~{MAX_DY_PCT}%</span><span class="l">减仓组股息率近三年分位</span></div>
<div><span class="v blue">{len(CAUT)} + {len(HOLD)}</span><span class="l">谨慎 + 观望</span></div>
</div>

<h2><span class="n">01</span>先看名单</h2>
<p>中证红利（000922）、红利低波（H30269）、红利低波100（930955）、智选高股息（932305）、
红利指数（000015）、800红利低波（931848）—— 六只的判断都是<b>减仓</b>：
估值与息差多重不利、且无买点。</p>
<p>另外三只不同：红利质量（931468）与价值100（980081）是<b>观望</b>；港股通高股息（930914）是<b>谨慎</b>——建议降低加仓意愿，但不主动卖出。</p>

{T_MAIN}

<h2><span class="n">02</span>贵在哪：PE 和 PB 同时贴在轨道右端</h2>
<p>把每只指数的 PE、PB 放到近三年分位轨道上，六个减仓标的的 PE 分位全在 99.8% 以上。
PB 没那么极端，但多数也在 90% 以上。</p>

{fig('p2', '轨道 0~100 为近三年分位，越靠右越贵。琥珀点为 PE，蓝点为 PB。')}

<p>PB 没有 PE 那么整齐：红利低波100 的 PB 分位是 {BY['930955']['pb_pct']:.0f}%、
800红利低波 {BY['931848']['pb_pct']:.0f}%，只有中证红利、红利低波、红利指数三只的 PB 也顶到
{BY['000922']['pb_pct']:.0f}%。<b>这轮贵主要贵在 PE</b>，也就是价格涨得比盈利快。</p>

<blockquote class="warn">
PE 分位 100% 只在「近三年」成立。放到 2016 年以来的全历史，中证红利的 PE 分位是
{BY['000922']['pe_all']}%、股息率分位是 {BY['000922']['dyr_all']}%。
说「历史最贵」是错的，说「三年最贵」才是数据支持的。
</blockquote>

<h2><span class="n">03</span>性价比：股债息差被压到 2 个百分点</h2>
<p>红利的核心卖点是「拿着分红等债券收益」。这个比较就是股债息差：
股息率减去十年国债收益率。国债现在 {Y10:.2f}%，中证红利股息率 {BY['000922']['dyr']:.2f}%，
息差 {BY['000922']['spread']:.2f}pp。</p>

{fig('p3', '三条代表序列的股息率息差走势。2024 年港股通在 6~7pp、A 股红利在 3~5pp，现在都压到 2~3.6pp。')}

<p>三个读数：中证红利 {BY['000922']['spread']:.2f}pp、红利低波 {BY['H30269']['spread']:.2f}pp、
港股通高股息 {BY['930914']['spread']:.2f}pp。港股通是这组里息差最厚的，
所以它只落在「谨慎」。</p>

<blockquote>
这里说的息差是<b>股息率口径</b>。另一套常用口径是 E/P 减国债（也就是股债性价比 / ERP），
中证红利现在是 {100 / BY['000922']['pe'] - Y10:.2f}pp，是股息率口径的四倍。
两个数都叫「股债差」，混用会得出完全不同的结论。
</blockquote>

<h2><span class="n">04</span>位置：还贴在三年高点附近</h2>
<p>贵不是靠下跌修复的，是靠时间或下跌。现在这两件事都还没发生：
{len(NEAR_HIGH)} 只指数距三年高点不到 1%。</p>

{fig('p4', '柱长为距近三年最高点的回撤。柱子越短越说明没有跌过。')}

<p>红利低波 0.1%、800红利低波 0.0%、红利低波100 0.2%、智选高股息 0.0%。
唯一跌出空间的是红利质量，距 2026-05-11 的高点 −14.9%，这也是它落在观望的原因之一。</p>

<h2><span class="n">05</span>两个例外与一个折中</h2>
<p><b>红利质量</b>：近三年 PE 分位 {BY['931468']['pe_pct']:.0f}%，股息率分位
{BY['931468']['dyr_pct']:.0f}%，回撤 {abs(BY['931468']['dd']):.1f}%。
属于「跌过了但还没到买点」，观察。<br>
<b>价值100</b>：PB {BY['980081']['pb']}、股息率 {BY['980081']['dyr']:.2f}%、
息差 {BY['980081']['spread']:.2f}pp、回撤 {abs(BY['980081']['dd']):.1f}%，也是观望。
注意它只有 2024-12 以来的数据，分位是「上市以来」。<br>
<b>港股通高股息</b>：股息率 {BY['930914']['dyr']:.2f}%、息差 {BY['930914']['spread']:.2f}pp，
只有股息率一项不利，广度不够，所以是谨慎而不是减仓。</p>

{fig('p5', '九只红利指数的读数与结论。PE 分位用琥珀标出 80% 以上的位置。')}

<h2><span class="n">06</span>判断是怎么算出来的</h2>
<p>风险分取三项里最高的一项：股息率分位低于 20% 记 70 分；PE 分位在 80% 以上记 30 分；
息差低于 0 记 60 分。买点另算，看回撤与 RSI。风险分 ≥70、没有买点、
且至少两项同时不利，才判减仓。</p>
<p>所以这六个减仓标的，真正拿分的是<b>股息率分位</b>（{MIN_DY_PCT}~{MAX_DY_PCT}%），
不是 PE。PE 分位再高也只贡献 30 分。港股通股息率分位 {BY['930914']['dyr_pct']:.1f}% 也拿了 70 分，
但只有这一项，广度不足，落在谨慎。</p>

{fig('p6', '口径与后续观察点。')}

<blockquote class="end">
减仓说的是性价比，不是看空。红利拿分红的长逻辑没有变——变的是现在这个价格上，
这份分红相对国债还值不值。
</blockquote>

<h2><span class="n">07</span>后续看四个信号</h2>
<ul>
<li>股息率分位回到 20% 以上 —— 这是减仓判据的解除条件。</li>
<li>息差重新走阔到 3pp 以上 —— 才谈得上相对债券便宜。</li>
<li>指数跌出 10% 以上回撤 —— 才可能出现买点。</li>
<li>港股通高股息的息差是否继续走阔 —— 它是这组里唯一还有缓冲的。</li>
</ul>

<p class="foot">
数据来源：本机 SQLite（data/lixinger.db）的 index_fundamental_daily（PE / PB / 股息率）、
index_full_return_daily（全收益点位）、bond_yield_daily（十年国债）。数据日 2026-11-20。<br>
口径：分位 = 滚动 750 交易日分位；价值100（2024-12-16 起）、智选高股息（2024-09-30 起）、
800红利低波（2024-05-21 起）历史不足 3 年，其分位为上市以来分位；息差 = 股息率 − 十年国债。<br>
本文为模型读数说明，不构成投资建议。
</p>
</div>
"""

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>红利还能拿吗：六只指数同时亮红灯</title>
<style>{CSS}</style>
</head>
<body>
{BODY}
</body>
</html>
"""

os.makedirs(OUT, exist_ok=True)
with io.open(os.path.join(OUT, 'index.html'), 'w', encoding='utf-8') as f:
    f.write(HTML)
print(f'  index.html {len(HTML)} 字节')
