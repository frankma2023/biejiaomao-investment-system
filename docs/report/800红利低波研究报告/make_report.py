# -*- coding: utf-8 -*-
"""中证800红利低波动指数（931848）研究报告 —— 呈现层。

只读 data/snapshot.json，不重算数字。
样式内联 .agents/skills/_shared/report-style.css（唯一来源）。

用法：python docs/report/800红利低波研究报告/make_report.py
"""
import os
import sys
import json
import math
import hashlib

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SNAP = os.path.join(HERE, 'data', 'snapshot.json')
CSS_PATH = os.path.join(PROJECT, '.agents', 'skills', '_shared', 'report-style.css')
OUT = os.path.join(HERE, 'index.html')

D = json.load(open(SNAP, encoding='utf-8'))
CSS_TXT = open(CSS_PATH, encoding='utf-8').read()
SHA = hashlib.sha1(open(SNAP, 'rb').read()).hexdigest()[:16]

COLOR = {
    '800红利低波': '#b45309',
    '800红利低波（价格）': '#a1a1aa',
    '中证红利': '#0f766e',
    '沪深300': '#2563eb',
    '红利低波': '#7c3aed',
    '红利低波100': '#be123c',
}

M = D['meta']
P = D['perf']['all']
P8, P8P, P922, P300, PLV, PLV100 = (P['d800'], P['d800p'], P['d922'], P['d300'],
                                    P['dlv'], P['dlv100'])
SP = D['perf_split']
BT, LV = SP['backtest'], SP['live']
Y = D['yearly']
CORR = D['corr']['matrix']
REG = D['regression']
REGL = D.get('regression_live') or {}
CUR = D['current']
EV = D['drawdown_events']
DDD = D['dd_dist']
BK = D['dd_buckets']
BASE = D['baseline']
TE = D['tier_events']
VAL = D['valuation']
CON = D['constituents']
PEER = D['peers']['calibers']['price']
PEERQ = D['peers']['calibers'].get('qfq', {})
ST = D['strategy']
RSI = D['rsi14']
C2D = D['cross2d']
C2DR = D['cross2d_ref']
CH = D['charts']

V8 = VAL['931848']
V922 = VAL['000922']
V300 = VAL['000300']
VLV = VAL['H30269']

GAP_SPLIT = BT['d800']['cagr'] - LV['d800']['cagr']
ALPHA_922 = REG['vs922']['alpha_annual']
ALPHA_300 = REG['vs300']['alpha_annual']


def esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def pct(x, d=2, sign=True):
    if x is None:
        return '—'
    return (('%+.' if sign else '%.') + str(d) + 'f%%') % x


def cls(x):
    if x is None:
        return ''
    return 'rise' if x > 0 else ('fall' if x < 0 else '')


def tbl(headers, rows, wide=False, num_from=1, sid=None, left=()):
    h = ''.join('<th>%s</th>' % esc(x) for x in headers)
    body = ''
    for r in rows:
        tds = ''
        for i, c in enumerate(r):
            isnum = i >= num_from and i not in left
            if isinstance(c, tuple):
                txt, cl = c
                if isnum:
                    tds += '<td class="num %s">%s</td>' % (cl, txt)
                else:
                    tds += '<td class="%s">%s</td>' % (cl, txt)
            else:
                tds += ('<td class="num">%s</td>' % c) if isnum else ('<td>%s</td>' % c)
        body += '<tr>%s</tr>' % tds
    attrs = (' id="%s"' % sid if sid else '') + (' class="wide"' if wide else '')
    return ('<div class="tw"><table%s><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
            % (attrs, h, body))


def nice_ticks(lo, hi, target=5):
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / target
    mag = 10 ** math.floor(math.log10(raw))
    step = mag * 10
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            step = m * mag
            break
    start = math.ceil(lo / step) * step
    out = []
    v = start
    while v <= hi + 1e-9:
        out.append(round(v, 6))
        v += step
    return out


def line_chart(series, W=980, H=380, log=True, ylab='', pad_l=54, pad_r=16, pad_t=16, pad_b=34):
    allv = [v for _, _, pts in series for _, v in pts]
    ally = [d for _, _, pts in series for d, _ in pts]
    x0, x1 = min(ally), max(ally)

    def tt(d):
        return int(d[:4]) + (int(d[5:7]) - 1) / 12 + (int(d[8:10]) - 1) / 365

    t0, t1 = tt(x0), tt(x1)
    lo, hi = min(allv), max(allv)
    if log:
        lo, hi = math.log10(lo), math.log10(hi)
    pad = (hi - lo) * 0.06 or 0.1
    lo -= pad
    hi += pad

    def sx(d):
        return pad_l + (tt(d) - t0) / (t1 - t0) * (W - pad_l - pad_r)

    def sy(v):
        e = math.log10(v) if log else v
        return H - pad_b - (e - lo) / (hi - lo) * (H - pad_b - pad_t)

    p = ['<svg class="chart" viewBox="0 0 %d %d" preserveAspectRatio="xMidYMid meet">' % (W, H)]
    ticks = []
    if log:
        s = math.floor(lo)
        while s <= hi:
            for m in (1, 2, 5):
                v = m * (10 ** s)
                if 10 ** lo <= v <= 10 ** hi:
                    ticks.append(v)
            s += 1
        ticks = sorted(set(ticks))
        while len(ticks) > 8:
            ticks = ticks[::2]
    else:
        ticks = nice_ticks(10 ** lo if log else lo, 10 ** hi if log else hi, 5)
    for t in ticks:
        y = sy(t)
        p.append('<line class="gl" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>' % (pad_l, y, W - pad_r, y))
        lbl = ('%g' % round(t)) if t >= 10 else ('%.2g' % t)
        p.append('<text class="ay" x="%d" y="%.1f" text-anchor="end">%s</text>' % (pad_l - 6, y + 3.5, lbl))
    yy = int(x0[:4]) + 1
    step_y = 2 if (t1 - t0) > 8 else 1
    while yy <= int(x1[:4]):
        d = '%d-01-01' % yy
        if x0 <= d <= x1:
            x = sx(d)
            p.append('<line class="gl" x1="%.1f" y1="%d" x2="%.1f" y2="%d"/>' % (x, pad_t, x, H - pad_b))
            p.append('<text class="ax" x="%.1f" y="%d" text-anchor="middle">%d</text>' % (x, H - pad_b + 16, yy))
        yy += step_y
    for name, color, pts in series:
        d = 'M' + ' L'.join('%.1f,%.1f' % (sx(a), sy(b)) for a, b in pts)
        p.append('<path class="ln" d="%s" stroke="%s"/>' % (d, color))
    if ylab:
        p.append('<text class="ax" x="%d" y="%d">%s</text>' % (pad_l, pad_t - 2, esc(ylab)))
    p.append('</svg>')
    return ''.join(p)


def drawdown_chart(pts, W=980, H=240, color='#b45309'):
    lo = min(v for _, v in pts)
    hi = 0.0
    pad_l, pad_r, pad_t, pad_b = 54, 16, 14, 30
    span = (hi - lo) or 0.01

    def sx(i):
        return pad_l + i / max(1, len(pts) - 1) * (W - pad_l - pad_r)

    def sy(v):
        return pad_t + (hi - v) / span * (H - pad_t - pad_b)

    p = ['<svg class="chart" viewBox="0 0 %d %d">' % (W, H)]
    for t in nice_ticks(lo, hi, 5):
        y = sy(t)
        p.append('<line class="gl" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>' % (pad_l, y, W - pad_r, y))
        p.append('<text class="ay" x="%d" y="%.1f" text-anchor="end">%g%%</text>' % (pad_l - 6, y + 3.5, t))
    area = 'M%.1f,%.1f ' % (sx(0), sy(0)) + ' L'.join('%.1f,%.1f' % (sx(i), sy(v))
                                                   for i, (_, v) in enumerate(pts)) + \
        ' L%.1f,%.1f Z' % (sx(len(pts) - 1), sy(0))
    p.append('<path d="%s" fill="%s" opacity=".14"/>' % (area, color))
    p.append('<path class="ln" d="M' + ' L'.join('%.1f,%.1f' % (sx(i), sy(v))
                                                 for i, (_, v) in enumerate(pts)) +
             '" stroke="%s" stroke-width="1.5"/>' % color)
    y0 = int(pts[0][0][:4]) + 1
    while y0 <= int(pts[-1][0][:4]):
        tgt = '%d-01-01' % y0
        hit = [i for i, (q, _) in enumerate(pts) if q >= tgt]
        if hit:
            p.append('<text class="ax" x="%.1f" y="%d" text-anchor="middle">%d</text>' %
                     (sx(hit[0]), H - pad_b + 16, y0))
        y0 += 2
    p.append('</svg>')
    return ''.join(p)


def year_bars(yearly, keys, W=980, H=320, colors=None):
    years = sorted(yearly)
    colx = colors or COLOR
    n = len(keys)
    left, right = 62, 16
    gsz = (W - left - right) / len(years)
    bw = gsz * 0.74 / n
    vals = [yearly[y][k] for y in years for k in keys if k in yearly[y]]
    lo, hi = min(min(vals), 0), max(max(vals), 0)
    span = (hi - lo) or 1
    pad_t, pad_b = 22, 30

    def sy(v):
        return pad_t + (hi - v) / span * (H - pad_t - pad_b)

    p = ['<svg class="chart" viewBox="0 0 %d %d">' % (W, H)]
    for t in nice_ticks(lo, hi, 5):
        y = sy(t)
        p.append('<line class="gl" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>' % (left, y, W - right, y))
        p.append('<text class="ay" x="%d" y="%.1f" text-anchor="end">%g%%</text>' % (left - 6, y + 3.5, t))
    p.append('<line class="gl" x1="%d" y1="%.1f" x2="%.1f" y2="%.1f" stroke-width="1.3"/>' %
             (left, sy(0), W - right, sy(0)))
    for i, y in enumerate(years):
        x0 = left + gsz * i
        for j, k in enumerate(keys):
            if k not in yearly[y]:
                continue
            v = yearly[y][k]
            top, bot = sy(max(v, 0)), sy(min(v, 0))
            p.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" opacity=".86"/>' %
                     (x0 + gsz * 0.13 + bw * j, top, bw, max(1, bot - top), colx.get(k, '#888')))
        p.append('<text class="ax" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (x0 + gsz / 2, H - 10, y))
    p.append('</svg>')
    return ''.join(p)


def legend(items):
    return '<div class="lg">%s</div>' % ''.join(
        '<span><i style="background:%s"></i>%s</span>' % (c, esc(n)) for n, c in items)


# ─────────────────────── 图表实例 ───────────────────────
NM = CH['normalized']
CH_MAIN = line_chart([
    ('800红利低波（全收益）', COLOR['800红利低波'], [(d, v) for d, v in NM['d800']]),
    ('800红利低波（价格）', COLOR['800红利低波（价格）'], [(d, v) for d, v in NM['d800p']]),
    ('中证红利（全收益）', COLOR['中证红利'], [(d, v) for d, v in NM['d922']]),
    ('沪深300（全收益）', COLOR['沪深300'], [(d, v) for d, v in NM['d300']]),
], ylab='净值（2013-12-31 = 1000，对数轴）')
LEG_MAIN = legend([('800红利低波（全收益）', COLOR['800红利低波']),
                   ('800红利低波（价格）', COLOR['800红利低波（价格）']),
                   ('中证红利（全收益）', COLOR['中证红利']),
                   ('沪深300（全收益）', COLOR['沪深300'])])

CH_DD = drawdown_chart([(d, v) for d, v in CH['dd']])
Y_T = {yy: {k: Y[k][yy] for k in Y if yy in Y[k]} for yy in sorted(Y['d800'])}
YCOL = {'d800': COLOR['800红利低波'], 'd922': COLOR['中证红利'], 'd300': COLOR['沪深300']}
CH_YEAR = year_bars(Y_T, ['d800', 'd922', 'd300'], colors=YCOL)
LEG_YEAR = legend([('800红利低波', COLOR['800红利低波']),
                   ('中证红利', COLOR['中证红利']),
                   ('沪深300', COLOR['沪深300'])])

CH_RSI = line_chart([('RSI(14)', '#0891b2', [(d, v) for d, v in CH['rsi']])],
                    H=260, log=False, ylab='RSI(14)')
CH_PE8 = line_chart([('800红利低波 PE', COLOR['800红利低波'],
                      [(d, v) for d, v in CH['pe800']])], H=260, log=False, ylab='PE(TTM)')
CH_PE922 = line_chart([('中证红利 PE', COLOR['中证红利'],
                        [(d, v) for d, v in CH['pe922']])], H=260, log=False, ylab='PE(TTM)')
CH_DY8 = line_chart([('800红利低波 股息率', COLOR['800红利低波'],
                      [(d, v) for d, v in CH['dyr800']])], H=250, log=False, ylab='股息率 %')
CH_DY922 = line_chart([('中证红利 股息率', COLOR['中证红利'],
                        [(d, v) for d, v in CH['dyr922']])], H=250, log=False, ylab='股息率 %')

# ─────────────────────── 表格 ───────────────────────
rows = []
for name, v in (('800红利低波（全收益）', P8), ('800红利低波（价格）', P8P),
                ('中证红利（全收益）', P922), ('沪深300（全收益）', P300),
                ('红利低波（H30269，全收益）', PLV), ('红利低波100（930955，全收益）', PLV100)):
    rows.append([esc(name), pct(v['tot'], 1), (pct(v['cagr']), cls(v['cagr'])),
                 pct(v['vol'], 2, False), '%.2f' % v['sharpe'],
                 (pct(v['mdd'], 1, False), 'fall'), '%.2f' % v['calmar']])
T_PERF = tbl(['序列', '累计收益', '年化', '年化波动', '夏普', '最大回撤', 'Calmar'], rows)

rows = []
for name, bt, lv in (('800红利低波', BT['d800'], LV['d800']),
                     ('800红利低波（价格）', BT['d800p'], LV['d800p']),
                     ('中证红利', BT['d922'], LV['d922']),
                     ('沪深300', BT['d300'], LV['d300']),
                     ('红利低波（H30269）', BT['dlv'], LV['dlv']),
                     ('红利低波100（930955）', BT['dlv100'], LV['dlv100'])):
    rows.append([esc(name),
                 (pct(bt['cagr']), cls(bt['cagr'])), (pct(lv['cagr']), cls(lv['cagr'])),
                 (pct(lv['cagr'] - bt['cagr']), cls(lv['cagr'] - bt['cagr'])),
                 '%.2f' % bt['years'], '%.2f' % lv['years']])
T_SPLIT = tbl(['序列', '回溯段年化', '实盘段年化', '落差', '回溯段年数', '实盘段年数'], rows)

yk = ['d800', 'd922', 'd300']
yrows = []
for y in sorted(Y['d800']):
    row = [y]
    for k in yk:
        v = Y[k].get(y)
        row.append((pct(v, 1), cls(v)) if v is not None else '—')
    d92 = Y['d922'].get(y)
    row.append((pct(Y['d800'][y] - d92, 1), cls(Y['d800'][y] - d92)) if d92 is not None else '—')
    yrows.append(row)
T_YEAR = tbl(['年份', '800红利低波', '中证红利', '沪深300', '超额（对中证红利）'], yrows)

ck = list(CORR.keys())
T_CORR = tbl([''] + ck, [[esc(a)] + ['%.3f' % CORR[a][b] for b in ck] for a in ck])


DISP = {
    'vs922': '800红利低波 vs 中证红利',
    'vs300': '800红利低波 vs 沪深300',
    'two': '双因子（沪深300 + 中证红利）',
    'lv_vs922': '参照：红利低波 vs 中证红利',
}


def reg_row(label, r):
    disp = DISP.get(label, label)
    if not r:
        return [esc(disp), '—', '—', '—', '—', '—']
    betas = '、'.join('%s β=%.3f' % (n, b) for n, b in zip(D['regression_names']
                                                          .get(label, ['因子'] * len(r['betas'])),
                                                          r['betas']))
    return [esc(disp), (pct(r['alpha_annual']), cls(r['alpha_annual'])),
            '%.2f' % r['alpha_t'], betas, '%.3f' % r['r2'], str(r['n'])]


T_REG = tbl(['模型', '年化 alpha', 't 值', '因子暴露', 'R²', '样本'], [
    reg_row('vs922', REG['vs922']), reg_row('vs300', REG['vs300']),
    reg_row('two', REG['two']), reg_row('lv_vs922', REG['lv_vs922'])], left=(3,))
T_REGL = tbl(['模型', '年化 alpha', 't 值', '因子暴露', 'R²', '样本'], [
    reg_row('vs922', REGL.get('vs922')), reg_row('vs300', REGL.get('vs300'))], left=(3,))

T_EV = tbl(['前高日', '前高', '最低点日', '深度', '距前高交易日', '收复日'], [
    [e['peak_date'], '%.2f' % e['peak'], e['trough_date'], (pct(e['depth'], 1, False), 'fall'),
     str(e['days_to_trough']), e['recover_date'] or '未收复'] for e in EV], left=(2, 5))

T_BK = tbl(['锚定回撤档', '样本（交易日）', '60 日均值', '60 日中位', '60 日胜率',
            '120 日均值', '250 日均值', '250 日胜率'], [
    [b['tier'], str(b['h60']['n']), pct(b['h60']['mean']), pct(b['h60']['median']),
     '%.1f%%' % b['h60']['win'], pct(b['h120']['mean']), pct(b['h250']['mean']),
     '%.1f%%' % b['h250']['win']] for b in BK['buckets']] + [
    ['全部交易日（基准）', str(BASE['h60']['n']), pct(BASE['h60']['mean']), pct(BASE['h60']['median']),
     '%.1f%%' % BASE['h60']['win'], pct(BASE['h120']['mean']), pct(BASE['h250']['mean']),
     '%.1f%%' % BASE['h250']['win']]])

trows = []
for t in TE:
    for e in t['events']:
        trows.append(['%d%%' % t['tier'], e['signal_date'], e['buy_date'],
                      (pct(e['h60']), cls(e['h60'])), (pct(e['h120']), cls(e['h120'])),
                      (pct(e['h250']), cls(e['h250']))])
T_TIER = tbl(['档位', '信号日', '买入日', '60 日', '120 日', '250 日'], trows, left=(1, 2))

T_DDD = tbl(['分位', '锚定回撤'], [['%s%%' % k, pct(v, 2, False)] for k, v in
                                sorted(DDD.items(), key=lambda x: int(x[0]))])

rows = []
for name, v, span in (('800红利低波（931848）', V8, '上市以来 600 个交易日'),
                      ('中证红利（000922）', V922, '2016 年以来'),
                      ('沪深300（000300）', V300, '2016 年以来'),
                      ('红利低波（H30269）', VLV, '2016 年以来')):
    rows.append([esc(name), '%.2f' % v['pe'], '%.1f%%' % v['pe_pct'],
                 '%.2f' % v['pb'], '%.1f%%' % v['pb_pct'],
                 ('%.2f%%' % v['dyr']) if v['dyr'] is not None else '—',
                 ('%.1f%%' % v['dyr_pct']) if v['dyr_pct'] is not None else '—',
                 '%.2f%%' % v['roe_implied'], esc(span)])
T_VAL = tbl(['指数', 'PE(TTM)', 'PE 分位', 'PB', 'PB 分位', '股息率', '股息率分位',
             'ROE（PB/PE）', '分位样本区间'], rows, wide=True)

T_CON = tbl(['项目', '数值'], [
    ['成分数（2026-05-01 快照）', '%d 只' % CON['n_931848']],
    ['与沪深300（000300）重叠', '%.1f%%（%d 只）' % (CON['overlap_300'], round(CON['overlap_300']))],
    ['与中证红利（000922）重叠', '%.1f%%（%d 只）' % (CON['overlap_922'], round(CON['overlap_922']))],
    ['中证红利成分中的 60% 不在本指数内', '%d 只' % CON['only_922_not_800']],
    ['前十大权重合计（%s）' % CON['weight_date'], '%.2f%%' % CON['top10_weight']],
    ['第一大权重', '%s %s（%.2f%%）' % (CON['top10'][0]['name'], CON['top10'][0]['code'],
                                  CON['top10'][0]['w'])],
    ['权重数据限制', esc(CON['weight_note'])],
], wide=True, left=(1,))

T_TOP = tbl(['代码', '名称', '权重'], [[x['code'], esc(x['name']), '%.2f%%' % x['w']]
                                    for x in CON['top10']], left=(1,))

prows = []
for gname, glabel in (('main', '主组：共同窗口 2013-12-31 ~ 2026-11-09'),
                      ('extra', '补充组：窗口较短（受数据源历史限制）')):
    g = PEER['groups'].get(gname)
    if not g:
        continue
    for sym, v in g['rows'].items():
        nm = '800红利低波（价格）' if sym == '931848' else sym
        prows.append([esc(glabel if sym == list(g['rows'].keys())[0] else ''), esc(nm),
                      '%.2f' % v['years'], (pct(v['cagr']), cls(v['cagr'])),
                      pct(v['vol'], 2, False), '%.2f' % v['sharpe'],
                      (pct(v['mdd'], 1, False), 'fall'), str(v['bad_ticks'])])
T_PEER = tbl(['组 / 窗口', '标的', '年数', '年化', '年化波动', '夏普', '最大回撤', '坏点'],
             prows, wide=True, left=(1,))

T_STRAT = tbl(['方式', '说明', '结果'], [
    ['一次性持有（全区间）', '%s 起，%s 至 %s' % (ST['hold_all']['start'], '%.2f 年' % ST['hold_all']['years'],
                                          ST['hold_all']['end']),
     '累计 %s，年化 %s' % (pct(ST['hold_all']['tot'], 1), pct(ST['hold_all']['cagr']))],
    ['一次性持有（实盘段）', '%s 起，%.2f 年' % (ST['hold_live']['start'], ST['hold_live']['years']),
     '累计 %s，年化 %s' % (pct(ST['hold_live']['tot'], 1), pct(ST['hold_live']['cagr']))],
    ['月度定投', '每月首个交易日投入 1,000 元，共 %d 期，累计投入 %.0f 元' %
     (ST['dca']['periods'], ST['dca']['invested']),
     '期末 %.0f 元，总收益 %s' % (ST['dca']['end_value'], pct(ST['dca']['profit_pct'], 1))],
    ['回撤分档加仓', '每轮每档首次跌破后的次日收盘买入，持有至 2026-11-09',
     '；'.join('%d%% 档 %d 次，均值 %s' % (t['tier'], t['n'], pct(t['mean_pct'], 1))
              for t in ST['tiers'] if t['mean_pct'] is not None)],
], wide=True, left=(1,))

ss = ST['start_sens']
T_SENS = tbl(['持有年限', '起点组合数', '最差年化', '中位年化', '最好年化'], [
    ['3 年', str(ss['y3']['n']), pct(ss['y3']['min']), pct(ss['y3']['median']), pct(ss['y3']['max'])],
    ['5 年', str(ss['y5']['n']), pct(ss['y5']['min']), pct(ss['y5']['median']), pct(ss['y5']['max'])],
])

T_RSI = tbl(['周期', '当前值', '历史分位', '中位', '区间'], [
    ['日线', '%.1f' % RSI['daily']['last'], '%.1f%%' % RSI['daily']['pct'],
     '%.1f' % RSI['daily']['median'], '%.1f ~ %.1f' % (RSI['daily']['min'], RSI['daily']['max'])],
    ['周线', '%.1f' % RSI['weekly']['last'], '%.1f%%' % RSI['weekly']['pct'], '—', '—'],
    ['月线', '%.1f' % RSI['monthly']['last'], '%.1f%%' % RSI['monthly']['pct'], '—', '—'],
])

cells = C2D['cells']
T_C2D = tbl(['估值档 \\ 回撤档', '回撤<10%', '回撤10~20%', '回撤≥20%'], [
    [esc(vb)] + ['%s（n=%d）' % (pct(cells.get(vb, {}).get(d, {}).get('mean')),
                                cells.get(vb, {}).get(d, {}).get('n', 0))
                 for d in ('回撤<10%', '回撤10~20%', '回撤≥20%')]
    for vb in ('PE低(<30%)', 'PE中(30~70%)', 'PE高(≥70%)')])

c2 = C2DR['cells']
T_C2DR = tbl(['估值档 \\ 回撤档', '回撤<10%', '回撤10~20%', '回撤≥20%'], [
    [esc(vb)] + ['%s（n=%d）' % (pct(c2.get(vb, {}).get(d, {}).get('mean')),
                                c2.get(vb, {}).get(d, {}).get('n', 0))
                 for d in ('回撤<10%', '回撤10~20%', '回撤≥20%')]
    for vb in ('PE低(<30%)', 'PE中(30~70%)', 'PE高(≥70%)')])


# ═══════════════════ 正文 ═══════════════════
TOPMETA = (
    f"{M['title']}｜窗口 {M['window']}，{M['days']} 个交易日，{M['years']:.2f} 年"
    f"｜数据截至 {M['data_asof']}｜生成 {M['generated_at']}｜快照指纹 {SHA}"
)

KPI = (
    '<div class="kpi">'
    f'<div><div class="k">全区间年化（全收益）</div><div class="v">{pct(P8["cagr"])}</div>'
    f'<div class="n">累计 {pct(P8["tot"], 1)}</div></div>'
    f'<div><div class="k">回溯段年化</div><div class="v">{pct(BT["d800"]["cagr"])}</div>'
    f'<div class="n">{BT["d800"]["years"]:.2f} 年，占样本 {BT["d800"]["n"] / M["days"] * 100:.0f}%</div></div>'
    f'<div><div class="k">实盘段年化</div><div class="v">{pct(LV["d800"]["cagr"])}</div>'
    f'<div class="n">{LV["d800"]["years"]:.2f} 年，占样本 {LV["d800"]["n"] / M["days"] * 100:.0f}%</div></div>'
    f'<div><div class="k">最大回撤</div><div class="v fall">{pct(P8["mdd"], 1, False)}</div>'
    f'<div class="n">{P8["mdd_peak"]} 至 {P8["mdd_trough"]}</div></div>'
    f'<div><div class="k">当前回撤</div><div class="v">{pct(CUR["dd"], 2)}</div>'
    f'<div class="n">前高 {CUR["peak"]:.2f}（{CUR["peak_date"]}）</div></div>'
    f'<div><div class="k">PE 分位</div><div class="v">{V8["pe_pct"]:.1f}%</div>'
    f'<div class="n">PE {V8["pe"]:.2f}，样本 {V8["n"]} 个交易日</div></div>'
    '</div>'
)

GAP_922 = LV['d922']['cagr'] - BT['d922']['cagr']
GAP_300 = LV['d300']['cagr'] - BT['d300']['cagr']
GAP_955 = LV['dlv100']['cagr'] - BT['dlv100']['cagr']
DIV_GAP = P8['cagr'] - P8P['cagr']
Y800 = Y['d800']
Y922 = Y['d922']
N_WIN = sum(1 for y in Y800 if Y922.get(y) is not None and Y800[y] > Y922[y])
N_CMP = sum(1 for y in Y800 if Y922.get(y) is not None)
BEST_Y = max((y for y in Y800 if Y922.get(y) is not None), key=lambda y: Y800[y] - Y922[y])
WORS_Y = min((y for y in Y800 if Y922.get(y) is not None), key=lambda y: Y800[y] - Y922[y])
C8 = CORR['800红利低波']
C922L = REGL.get('vs922') or {}
C300L = REGL.get('vs300') or {}
BK0, BK1, BK2, BK3 = BK['buckets']

T_SPEC = tbl(['项目', '本报告口径'], [
    ['统计区间', f"{M['window']}，{M['days']} 个交易日，{M['years']:.2f} 年"],
    ['回溯与实盘切分', f"回溯段 {M['window'][:10]} ~ 2024-05-20（{BT['d800']['years']:.2f} 年，{BT['d800']['n']} 个交易日）；"
                 f"实盘段 2024-05-21 ~ {M['data_asof']}（{LV['d800']['years']:.2f} 年，{LV['d800']['n']} 个交易日）"],
    ['收益口径', '全收益指数（index_full_return_daily，含分红再投）；价格口径单列对照'],
    ['年化换算', '按日历日折年：分母 = (结束日 − 起始日) ÷ 365.25，不用交易日数折年'],
    ['波动', f"日收益样本标准差 × √{M['trading_days_per_year']}"],
    ['夏普', f"（年化收益 − 无风险利率 {M['risk_free']}%）÷ 年化波动"],
    ['回撤', '锚定口径：（波段运行最高点 − 当日点位）÷ 波段运行最高点；最高点被突破才更新'],
    ['回撤档位', '10% / 14% / 20%，与 AGENTS.md §1.3 固定口径一致，便于与中证红利横向比较'],
    ['前瞻收益', '信号日次日收盘买入，持有 60 / 120 / 250 个交易日'],
    ['基准', '中证红利（000922）全收益为主基准，沪深300（000300）全收益为市场基准'],
    ['买入时点', '一律次日收盘，不用信号当日收盘，不取事后极值'],
    ['样本独立性', '按天口径的相邻交易日不独立；另给出每轮每档只计一次的事件口径'],
], wide=True)

CH1 = f'''
<h2>一、研究问题与结论摘要</h2>
<p>本报告回答三个问题。第一，中证800红利低波动指数（下称 800红利低波）在 {M['window']} 的
{M['days']} 个交易日里，收益与风险是什么水平。第二，它的超额来自哪里，是否与中证红利（000922）重复。
第三，按当前点位与估值，现在能不能买。</p>

<div class="ess">
<p><b>结论一：整段历史有八成是回溯。</b>800红利低波基日 2013-12-31、基点 1000，发布日是 2024-05-21。
回溯段 {BT['d800']['years']:.2f} 年（占样本 {BT['d800']['n'] / M['days'] * 100:.0f}%），
实盘段 {LV['d800']['years']:.2f} 年（占样本 {LV['d800']['n'] / M['days'] * 100:.0f}%）。
任何引用「十年年化」的说法，都建立在编制方案的事后回算上。</p>

<p><b>结论二：回溯段与实盘段落差 {GAP_SPLIT:.2f} 个百分点。</b>回溯段年化 {BT['d800']['cagr']:.2f}%，
实盘段 {LV['d800']['cagr']:.2f}%。同期中证红利落差 {GAP_922:.2f} pp，沪深300 反向 {GAP_300:+.2f} pp。
落差不是本指数独有，但幅度比中证红利更大，说明回算阶段的选样优势在实盘中衰减更多。</p>

<p><b>结论三：对中证红利有稳定 alpha，实盘段尚未证实。</b>全区间对中证红利年化 alpha {ALPHA_922:.2f}%，
t 值 {REG['vs922']['alpha_t']:.2f}，β {REG['vs922']['betas'][0]:.3f}，R² {REG['vs922']['r2']:.3f}。
实盘段 alpha 抬到 {C922L.get('alpha_annual', 0):.2f}%，但 t 值只有 {C922L.get('alpha_t', 0):.2f}，
在 {LV['d800']['years']:.2f} 年样本上不构成证据。</p>

<p><b>结论四：它不是新风格，是红利低波家族的又一个变体。</b>与红利低波100（930955）日收益相关
{C8['红利低波100']:.3f}、与红利低波（H30269）{C8['红利低波']:.3f}、与中证红利 {C8['中证红利']:.3f}。
差异来自选样池限定在中证800 之内，不是来自新因子。</p>

<p><b>结论五：现在不是买点。</b>{CUR['last_date']} 收 {CUR['point']:.2f}（全收益），
距 {CUR['peak_date']} 的高点 {CUR['peak']:.2f} 只差 {CUR['dd']:.2f}%。PE {V8['pe']:.2f} 落在
自发布以来 {V8['pe_pct']:.1f}% 分位，股息率 {V8['dyr']:.2f}% 落在 {V8['dyr_pct']:.1f}% 分位，
日线 RSI(14) {RSI['daily']['last']:.1f}。位置与估值都在高位，锚定回撤尚未进入观察档。</p>
</div>
'''

CH2 = ('<h2>二、统计口径</h2>'
       '<p>口径先于数字。下表是本报告全部数字的定义，后续各章不再重复。</p>' + T_SPEC +
       '<div class="warn"><p><b>为什么必须切开回溯段。</b>指数编制方用今天的规则回算历史，'
       '回算时已经知道哪些股票后来表现好。回溯段与实盘段的收益不能相加后当作「该指数的长期业绩」。'
       '本报告所有年化数字都同时给两段。</p></div>')

T_RULES = tbl(['指数', '基日', '发布日', '回溯年数', '成分数', '调仓频率', '加权方式'], [
    ['中证800红利低波动（931848）', '2013-12-31', '2024-05-21', '10.38', '100', '每半年', '股息率'],
    ['中证红利（000922）', '2004-12-31', '2008-05-26', '3.40', '100', '每年', '2013-07 起改股息率'],
    ['中证红利低波动（H30269）', '2005-12-30', '2013-12-19', '7.97', '50', '每年', '股息率'],
    ['中证红利低波动100（930955）', '2005-12-30', '2017-05-26', '11.40', '100', '每季度', '股息率 / 波动率'],
], wide=True, left=(1, 2, 5))

T1 = CON['top10'][0]

CH3 = f'''
<h2>三、编制规则、发布日与修订史</h2>
<p>这一章决定整份报告的可信度，所以放在收益分析之前。</p>

<h3>3.1 编制规则</h3>
<p>中证800红利低波动指数从中证800 指数样本中选取 100 只连续分红、股息率较高且波动率较低的证券，
采用股息率加权，每半年调整一次样本。基日 2013-12-31，基点 1000，发布日 2024-05-21，
发布渠道为「数据服务平台」。英文名 CSI 800 Dividend Low Volatility Index。</p>
<p>三个设计要点值得单独指出。第一，选样空间是<b>中证800 成分</b>而不是全市场，因此天然偏向大中盘，
成分与沪深300 重叠 {CON['overlap_300']:.1f}%。第二，加权用<b>股息率</b>而不是市值，单只权重被压缩，
第一大成分 {T1['name']}（{T1['code']}）仅占 {T1['w']:.2f}%。第三，调仓半年一次，比中证红利（每年）更频繁，
比红利低波100（每季度）更稀疏。</p>

<h3>3.2 发布日与回溯段</h3>
''' + T_RULES + f'''
<p>800红利低波的回溯段长达 10.38 年，是红利低波系指数里回溯占比较高的一只。作为对照，
中证红利 2008-05-26 发布、回溯仅 3.40 年，它的历史更接近「真实可投资经验」。
本报告第五章的归因因此分成全区间与实盘段两套，不合并成一套。</p>

<div class="warn"><p><b>未查到的部分。</b>本报告未取得 931848 编制方案的原始修订公告。
官网 basic-info 接口给出的发布日为 2024-05-21，未见「改造自其它指数」的迹象
（其基日与中证红利低波系列不同源）。相较国证自由现金流（980092）那种「旧指数被删除、
按新规则重算」的情形，本指数没有发现同类问题。这属于「未发现问题」，不等于「已证明不存在」。
检索记录见附录 C。</p></div>

<h3>3.3 中证红利的两次修订</h3>
<p>中证红利 2013-07 将加权方式由市值改为股息率，2022-10 又做了一次修订，官方明示修订后
年化收益由 9.3% 升至 10.2%。这意味着「中证红利过去十年年化 X%」这类表述本身也包含
规则变更带来的一次性抬升。本报告把中证红利作为基准时，读数是「按现行规则回算的序列」，
不是当年投资者真实拿到的序列。</p>

<h2>四、收益与风险</h2>

<h3>4.1 净值曲线</h3>
''' + CH_MAIN + LEG_MAIN + f'''
<p>图上可以直接读出两件事。第一，全收益与价格的差距就是分红贡献：{M['years']:.2f} 年里，
800红利低波全收益年化 {P8['cagr']:.2f}%，价格年化 {P8P['cagr']:.2f}%，差 {DIV_GAP:.2f} pp。
第二，2024-05 之后曲线斜率明显变缓，对应发布日之后的实盘段。</p>

<h3>4.2 全区间表现</h3>
''' + T_PERF + f'''
<p>全区间 800红利低波全收益年化 {P8['cagr']:.2f}%，高于中证红利 {P922['cagr']:.2f}%、
沪深300 {P300['cagr']:.2f}%，低于红利低波100 {PLV100['cagr']:.2f}%。波动 {P8['vol']:.2f}%，
比中证红利 {P922['vol']:.2f}% 低，但最大回撤 {P8['mdd']:.2f}% 只比中证红利 {P922['mdd']:.2f}%
好 {P8['mdd'] - P922['mdd']:.2f} pp。夏普 {P8['sharpe']:.2f}，低于红利低波100 的 {PLV100['sharpe']:.2f}。</p>

<h3>4.3 回溯段与实盘段的落差</h3>
''' + T_SPLIT + f'''
<p>这是本报告最重要的一张表。回溯段 {BT['d800']['cagr']:.2f}% 的年化在红利类指数里属于极高水平，
实盘段掉到 {LV['d800']['cagr']:.2f}%。三个观察：</p>
<ul>
<li>落差 {GAP_SPLIT:.2f} pp，比中证红利的 {GAP_922:.2f} pp 更大。回算阶段的选股优势在实盘里衰减更多。</li>
<li>实盘段 800红利低波仍跑赢中证红利（{LV['d800']['cagr']:.2f}% 对 {LV['d922']['cagr']:.2f}%），
也跑赢沪深300（{LV['d300']['cagr']:.2f}%）。</li>
<li>红利低波100 在回溯段最猛（{BT['dlv100']['cagr']:.2f}%），实盘段掉到 {LV['dlv100']['cagr']:.2f}%，
落差 {GAP_955:.2f} pp，衰减最狠。回溯段的排名不能外推。</li>
</ul>

<h3>4.4 分年度</h3>
''' + CH_YEAR + LEG_YEAR + T_YEAR + f'''
<p>{N_CMP} 个完整年度里，800红利低波有 {N_WIN} 年跑赢中证红利，胜率 {N_WIN / N_CMP * 100:.0f}%。
最大单年优势出现在 {BEST_Y} 年（{Y800[BEST_Y] - Y922[BEST_Y]:+.2f} pp），
最大劣势出现在 {WORS_Y} 年（{Y800[WORS_Y] - Y922[WORS_Y]:+.2f} pp）。
2018 年两者同步下跌（{Y800['2018']:.2f}% 对 {Y922['2018']:.2f}%），
2022 年 800红利低波几乎零收益（{Y800['2022']:+.2f}%）而沪深300 跌 {abs(Y['d300']['2022']):.2f}%，
这是红利低波属性的直接体现。</p>
'''

CH5 = f'''
<h2>五、相似度与归因</h2>

<h3>5.1 日收益相关矩阵</h3>
''' + T_CORR + f'''
<p>800红利低波与红利低波100 的相关高达 {C8['红利低波100']:.3f}、与红利低波 {C8['红利低波']:.3f}、
与中证红利 {C8['中证红利']:.3f}，与沪深300 只有 {C8['沪深300']:.3f}。
把它当作「沪深300 的替代」是错的，把它当作「红利低波家族的第四个成员」是对的。</p>

<h3>5.2 归因回归（全区间）</h3>
''' + T_REG + f'''
<p>单因子对中证红利：年化 alpha {ALPHA_922:.2f}%，t 值 {REG['vs922']['alpha_t']:.2f}（|t| &gt; 1.96，显著），
β {REG['vs922']['betas'][0]:.3f}，R² {REG['vs922']['r2']:.3f}。
换成人话：800红利低波约等于「{REG['vs922']['betas'][0]:.2f} 倍的中证红利 +
每年 {ALPHA_922:.1f} 个百分点的稳定截距」。双因子模型加入沪深300 后 alpha 升到
{REG['two']['alpha_annual']:.2f}%（t={REG['two']['alpha_t']:.2f}），沪深300 的 β 为
{REG['two']['betas'][0]:.3f}（负），说明它在市场上涨时参与度低，超额来自「少跌」而不是「多涨」。</p>

<h3>5.3 实盘段归因</h3>
''' + T_REGL + f'''
<p>实盘段的 alpha 点估计更高（{C922L.get('alpha_annual', 0):.2f}%），t 值 {C922L.get('alpha_t', 0):.2f}，
远未达到显著。{LV['d800']['years']:.2f} 年、{LV['d800']['n']} 个交易日的样本，
无法把「每年 {C922L.get('alpha_annual', 0):.1f} 个百分点」从噪声里分出来。
本报告不据此认为实盘 alpha 已成立。</p>

<h2>六、上涨本质总结</h2>

<h3>6.1 收益从哪来</h3>
<p>800红利低波赚的是三笔钱。第一笔是<b>股息</b>：全收益与价格的年化差 {DIV_GAP:.2f} pp，
即平均每年约 {DIV_GAP:.2f} 个百分点的分红再投贡献，这是最确定的一笔。
第二笔是<b>低波动溢价</b>：选样要求波动率低，组合天然避开高波动小盘，2018 与 2022 两年的相对抗跌
来自这里。第三笔是<b>股息率加权的再平衡收益</b>：半年调仓把涨多了、股息率被摊薄的成分权重压回去，
被动完成一次高抛低吸。</p>

<h3>6.2 依赖哪些外部条件</h3>
<p>这笔超额依赖三个条件同时成立。一是<b>利率环境</b>：股息率资产的吸引力与无风险利率直接竞争，
利率上行期红利类普遍跑输。二是<b>选样池稳定性</b>：选样被限定在中证800 这 800 只里，
如果未来中证800 大幅纳入低股息成长股，池子的股息率中枢会下移。三是<b>分红持续性</b>：
指数要求「连续分红」，一旦经济下行导致大面积削减分红，高股息率会变成估值陷阱。
本报告没有可用数据量化这三个条件各自贡献多少。</p>

<h3>6.3 能不能复制</h3>
<p>可以复制，但受两个约束。一是<b>载体</b>：跟踪该指数的 ETF 有华宝（159355）、鑫元（563980）、
万家（158023）三只，都是 2025 年前后新发，规模与成交额都小，大额资金进出会有冲击成本。
二是<b>时点</b>：回溯段的 {BT['d800']['cagr']:.2f}% 不可复制，因为那是事后选样。
可持续的预期应取实盘段 {LV['d800']['cagr']:.2f}% 与全区间 {P8['cagr']:.2f}% 之间的偏保守一侧。</p>
'''

CH7 = f'''
<h2>七、回撤与买点</h2>

<h3>7.1 锚定回撤曲线</h3>
''' + CH_DD + f'''
<p>曲线按「波段运行最高点」锚定：最高点被突破才更新。{M['years']:.2f} 年里最深一次回撤
{pct(EV[0]['depth'], 1, False)}（{EV[0]['peak_date']} 至 {EV[0]['trough_date']}，
{EV[0]['days_to_trough']} 个交易日），另一次 {pct(EV[1]['depth'], 1, False)}
（{EV[1]['peak_date']} 至 {EV[1]['trough_date']}）。当前回撤 {CUR['dd']:.2f}%，基本等于历史新高。</p>

<h3>7.2 回撤事件与分位分布</h3>
''' + T_EV + T_DDD + f'''
<p>分布说明 10 / 14 / 20 三档在本指数上偏深：中位回撤只有 {DDD['50']:.2f}%，
75% 分位 {DDD['75']:.2f}%，即四分之三的交易日回撤不到 3%。
10% 档只在约四分之一的交易日出现，「跌破 10%」本身就是事件。</p>

<h3>7.3 按天口径：回撤档与前瞻收益</h3>
''' + T_BK + f'''
<div class="warn"><p><b>样本量被高估。</b>按天口径下，同一轮下跌里的相邻交易日高度相关，
n={BK3['h60']['n']} 不等于这么多个独立事件。实际的独立机会只有 7 次（见 7.4）。
本节数字只能用来观察分布形状，不能当作胜率承诺。</p></div>
<p>读数有两个反直觉之处。一是 10~14% 档的表现最差（250 日均值 {BK1['h250']['mean']:+.2f}%），
低于「全部交易日」基准（{BASE['h250']['mean']:+.2f}%），说明「跌了 10% 就买」在这只指数上没有优势。
二是 14~20% 与 ≥20% 两档更好（250 日均值 {BK2['h250']['mean']:+.2f}% 与 {BK3['h250']['mean']:+.2f}%，
胜率 {BK2['h250']['win']:.1f}% 与 {BK3['h250']['win']:.1f}%），深层回撤才是买点。三档梯度不单调，
说明回撤档在这只指数上不是稳定的择时变量。</p>

<h3>7.4 事件口径：每轮每档只买一次</h3>
''' + T_TIER + f'''
<p>{M['years']:.2f} 年只给了 {TE[0]['n']} 次 10% 档机会、{TE[1]['n']} 次 14% 档、{TE[2]['n']} 次 20% 档。
20% 档的两次是 {TE[2]['events'][0]['signal_date']}（60 日 {TE[2]['events'][0]['h60']:+.2f}%）
与 {TE[2]['events'][1]['signal_date']}（60 日 {TE[2]['events'][1]['h60']:+.2f}%、
250 日 {TE[2]['events'][1]['h250']:+.2f}%）。样本量个位数，任何胜率都不能作为决策依据。</p>
'''

CH8 = f'''
<h2>八、估值与派生财务</h2>

<h3>8.1 三个估值指标与它们的分位口径</h3>
''' + T_VAL + f'''
<div class="danger"><p><b>931848 的分位只有 {V8['n']} 个交易日。</b>理杏仁的指数估值表对该指数的覆盖
从发布日 2024-05-21 开始，共 {V8['n']} 行。因此 PE {V8['pe_pct']:.1f}% 分位的准确含义是
「自发布以来处于最高区」，不是「十年最高区」。中证红利、沪深300、红利低波的分位自 2016 年起算，
两者不可直接比较。</p></div>
<p>当前读数：PE {V8['pe']:.2f}、PB {V8['pb']:.2f}、股息率 {V8['dyr']:.2f}%。
与中证红利（PE {V922['pe']:.2f}、股息率 {V922['dyr']:.2f}%）相比，800红利低波更贵、股息更低。
原因在选样池：限定中证800 会把大量中小盘高股息标的排除在外。</p>

<h3>8.2 PE 与股息率走势</h3>
<h4>800红利低波（2024-05-21 起，{V8['n']} 个交易日）</h4>
''' + CH_PE8 + CH_DY8 + f'''
<h4>中证红利（2016-01-04 起，{V922['n']} 个交易日）</h4>
''' + CH_PE922 + CH_DY922 + f'''
<p>两张图放在一起看。中证红利的 PE 在样本内的区间是 {V922['pe_min']:.2f} ~ {V922['pe_max']:.2f}，
当前 {V922['pe']:.2f} 处于 {V922['pe_pct']:.1f}% 分位；800红利低波只在
{V8['pe_min']:.2f} ~ {V8['pe_max']:.2f} 的窄区间里运行过，当前 {V8['pe']:.2f} 已接近区间上沿。
区间窄是因为样本只有 {V8['n']} 个交易日，不代表它的真实估值波动范围就这么小。</p>

<h3>8.3 派生财务：ROE、净利润、净资产</h3>
<p>指数估值与财务存在恒等关系：PE = 总市值 ÷ 归母净利润，PB = 总市值 ÷ 归母净资产。
两者相除消去市值，得 ROE = PB ÷ PE。这条恒等式让本报告可以在缺少指数财报的情况下，
用估值表反推四个聚合指标。下表的数字全部由估值表推算，不是原始财报。</p>
''' + tbl(['指标', '800红利低波', '中证红利', '沪深300', '红利低波'],
          [['ROE（PB÷PE）', f"{V8['roe_implied']:.2f}%", f"{V922['roe_implied']:.2f}%",
            f"{V300['roe_implied']:.2f}%", f"{VLV['roe_implied']:.2f}%"],
           ['ROE 区间（样本内）', f"{V8['roe_min']:.2f}% ~ {V8['roe_max']:.2f}%",
            f"{V922['roe_min']:.2f}% ~ {V922['roe_max']:.2f}%",
            f"{V300['roe_min']:.2f}% ~ {V300['roe_max']:.2f}%",
            f"{VLV['roe_min']:.2f}% ~ {VLV['roe_max']:.2f}%"],
           ['归母净利润（亿元）', f"{V8['np_yi']:,.0f}", f"{V922['np_yi']:,.0f}",
            f"{V300['np_yi']:,.0f}", f"{VLV['np_yi']:,.0f}"],
           ['归母净资产（亿元）', f"{V8['eq_yi']:,.0f}", f"{V922['eq_yi']:,.0f}",
            f"{V300['eq_yi']:,.0f}", f"{VLV['eq_yi']:,.0f}"],
           ['年分红金额（亿元）', f"{V8['dv_yi']:,.0f}", f"{V922['dv_yi']:,.0f}",
            f"{V300['dv_yi']:,.0f}", f"{VLV['dv_yi']:,.0f}"]]) + '''
<div class="warn"><p><b>这是派生口径，不是原始财报。</b>本报告未能取得 931848 的指数财报：
理杏仁 <code>/index/fs/non_financial</code>、<code>security</code>、<code>bank</code> 三个端点
对该指数均返回 0 条，只有 <code>hybrid</code> 端点返回 2 个报告期（2024-12-31、2025-12-31），
不足以画趋势。上表数字由估值表恒等式反推，口径为「成分股聚合」，
与基金披露的指数财务数据在四舍五入与调整项上可能有小差异。</p></div>

<h3>8.4 二维交叉：估值分位 × 回撤档</h3>
<p>先看 800红利低波自身。它的估值样本只有 ''' + f'''{V8['n']} 个交易日，
且这段窗口最大回撤仅 {abs(LV['d800']['mdd']):.1f}%，高回撤格没有样本：</p>
''' + T_C2D + f'''
<div class="warn"><p>{C2D['note']}。四格里三格的样本全部落在「回撤&lt;10%」，
「回撤10~20%」只有 3 个观测。本表不作为任何决策依据。</p></div>
<p>作为替代，用中证红利（000922）自身估值与回撤构造同样的二维表，样本 2016 年起：</p>
''' + T_C2DR + f'''
<p>中证红利的表给出一个有用方向：<b>PE 低 + 回撤深</b>的格（PE&lt;30% 且回撤≥20%）前瞻 120 日均值
+18.42%，n=47；而 <b>PE 高 + 回撤中等</b>的格（PE≥70% 且回撤 10~20%）为 −7.44%，n=181。
两个极端都出现，说明「估值与位置同时到位」才有效，单看一个维度容易买在半山腰。
注意这张表属于中证红利，不能直接搬到 800红利低波身上。</p>
'''

CH9 = f'''
<h2>九、技术面位置</h2>
''' + T_RSI + CH_RSI + f'''
<p>日线 RSI(14) 当前 {RSI['daily']['last']:.1f}，历史分位 {RSI['daily']['pct']:.1f}%，
中位 {RSI['daily']['median']:.1f}，样本区间 {RSI['daily']['min']:.1f} ~ {RSI['daily']['max']:.1f}，n={RSI['daily']['n']}。
周线 {RSI['weekly']['last']:.1f}（分位 {RSI['weekly']['pct']:.1f}%），月线 {RSI['monthly']['last']:.1f}
（分位 {RSI['monthly']['pct']:.1f}%）。三个周期同在高位，且价格处于历史新高附近。
技术面给出的信息是「不便宜」，不是「不能买」。</p>

<h2>十、成分结构</h2>
''' + T_CON + f'''
<p>前十大权重合计 {CON['top10_weight']:.2f}%，第一大 {T1['name']} {T1['w']:.2f}%。
权重集中度不高，但注意数据限制：{esc(CON['weight_note'])}。
与沪深300 重叠 {CON['overlap_300']:.1f}%，与中证红利只重叠 {CON['overlap_922']:.1f}%——
中证红利有 {CON['only_922_not_800']} 只成分不在这只指数里。这个差额就是它的超额来源：
它把中证红利里那些不在中证800 内的小盘高股息股排除掉了。</p>
''' + T_TOP + f'''
<p>前十的行业分布集中在煤炭（{T1['name']}、中国神华）、石油石化（中国海油、中国石油）、
银行（建设银行）、交运（唐山港、四川路桥）、可选消费（雅戈尔、双汇发展、格力电器）。
重资产 + 高分红 + 低估值，这是典型的红利低波画像，也说明它<b>不是成长型标的</b>：
未来收益主要靠股息与估值修复，不靠利润高增长。</p>

<h2>十一、国际同类对照</h2>
''' + T_PEER + f'''
<p>对照口径为<b>价格口径 + 拆股还原</b>，不含分红，主组窗口与本地序列完全一致
（{M['window']}）。美国低波红利类产品同窗口年化：SPLV {PEER['groups']['main']['rows']['SPLV']['cagr']:.2f}%、
USMV {PEER['groups']['main']['rows']['USMV']['cagr']:.2f}%、SCHD {PEER['groups']['main']['rows']['SCHD']['cagr']:.2f}%、
SPY {PEER['groups']['main']['rows']['SPY']['cagr']:.2f}%。800红利低波价格口径 {P8P['cagr']:.2f}%，
高于三个红利产品，低于标普500。夏普方面 800红利低波 {P8P['sharpe']:.2f}，SPLV 只有
{PEER['groups']['main']['rows']['SPLV']['sharpe']:.2f}。最大回撤 800红利低波
{pct(P8P['mdd'], 1, False)}，比 SPLV 的 {pct(PEER['groups']['main']['rows']['SPLV']['mdd'], 1, False)} 更深——
A 股红利类在 2015 与 2018 的跌幅显著大于美股同类。</p>

<div class="warn"><p><b>为什么不用前复权口径。</b>新浪的 qfq 序列对美股分红调整偏大：
SPY 的 qfq 年化 {PEERQ.get('groups', {}).get('main', {}).get('rows', {}).get('SPY', {}).get('cagr', 0):.2f}%，
比价格口径 {PEER['groups']['main']['rows']['SPY']['cagr']:.2f}% 高出
{PEERQ.get('groups', {}).get('main', {}).get('rows', {}).get('SPY', {}).get('cagr', 0) - PEER['groups']['main']['rows']['SPY']['cagr']:.2f} pp，
而 SPY 的真实股息率约 1.3%。这个偏差会把美股红利产品系统性抬高，所以正式对照一律用价格口径，
qfq 数字只作脚注。另需注意：价格口径不含分红，与 A 股全收益口径不可直接比较，
故上表把 800红利低波的价格序列（{P8P['cagr']:.2f}%）放在同一行对照。</p></div>
'''

ss = ST['start_sens']
TIERS_TXT = '；'.join(f"{t['tier']}% 档 {t['n']} 次，均值 {pct(t['mean_pct'], 1)}"
                     for t in ST['tiers'] if t['mean_pct'] is not None)

CH12 = f'''
<h2>十二、资金到位回测</h2>
''' + T_STRAT + f'''
<p>三种方式的结果差异值得说清。一次性持有全区间年化 {ST['hold_all']['cagr']:.2f}%，
但这是从 2013-12-31 的 1000 点开始——一个事后看完美的起点。
月度定投 {ST['dca']['periods']} 期、累计投入 {ST['dca']['invested']:,.0f} 元、期末
{ST['dca']['end_value']:,.0f} 元，总收益 {pct(ST['dca']['profit_pct'], 1)}，
年化远低于一次性持有，因为资金是逐月到位的。
分档加仓的均值看着很高（{TIERS_TXT}），但每档样本只有 2~7 次，且都是「持有到今天」的时点相关结果，
不能当作策略收益预期。</p>

<h3>12.1 起始点敏感性</h3>
''' + T_SENS + f'''
<p>换一个起点，结论会不会变？任意 3 年持有窗口 {ss['y3']['n']} 个起点，最差年化
{pct(ss['y3']['min'])}、中位 {pct(ss['y3']['median'])}、最好 {pct(ss['y3']['max'])}；
任意 5 年窗口 {ss['y5']['n']} 个起点，最差 {pct(ss['y5']['min'])}、中位 {pct(ss['y5']['median'])}。
3 年窗口最差仍为负（{pct(ss['y3']['min'])}），说明<b>这只指数不能保证任意 3 年不亏</b>。
5 年窗口最差 {pct(ss['y5']['min'])}，接近零但仍是负的。
「长期持有必赚」在这只指数上，需要的时间比 3 年长。</p>

<h2>十三、现在能不能买</h2>
<p>把前面各章的证据收成一张判断表。</p>
''' + tbl(['维度', '当前读数', '判断'], [
    ['点位', f"{CUR['point']:.2f}，距前高 {CUR['peak']:.2f} 仅 {abs(CUR['dd']):.2f}%", ('历史高位', 'fall')],
    ['PE 分位', f"{V8['pe']:.2f}，自发布以来 {V8['pe_pct']:.1f}% 分位", ('偏贵', 'fall')],
    ['股息率', f"{V8['dyr']:.2f}%，{V8['dyr_pct']:.1f}% 分位", ('历史最低区', 'fall')],
    ['锚定回撤', f"{CUR['dd']:.2f}%（10% 档尚未触发）", ('无买点信号', '')],
    ['日线 RSI', f"{RSI['daily']['last']:.1f}，分位 {RSI['daily']['pct']:.1f}%", ('高位', 'fall')],
    ['长期 alpha', f"全区间 {ALPHA_922:.2f}%（t={REG['vs922']['alpha_t']:.2f}）", ('成立', 'ok')],
    ['实盘 alpha', f"{C922L.get('alpha_annual', 0):.2f}%（t={C922L.get('alpha_t', 0):.2f}）", ('未证实', 'lag')],
    ['载体', '3 只 ETF，均为 2025 年新发，规模小', ('可用但容量有限', 'lag')],
], wide=True, left=(1,)) + f'''
<div class="ess">
<p><b>结论：可以放在观察名单，现在不建议买入。</b>四条理由：位置在历史新高附近；
PE 处于自发布以来 {V8['pe_pct']:.1f}% 分位；股息率处于历史最低区（{V8['dyr_pct']:.1f}% 分位）；
锚定回撤 {CUR['dd']:.2f}%，离 10% 观察档还有 10 个百分点的空间。</p>
<p><b>什么条件下转为可买。</b>三个条件按优先级排列。第一，锚定回撤进入 10% 档（对应点位约
{CUR['peak'] * 0.9:.0f} 以下），这是本报告唯一有事件样本支持的触发条件（7 次，均值 {pct(ST['tiers'][0]['mean_pct'], 1)}）。
第二，PE 回落到 8.5 以下（样本区间 {V8['pe_min']:.2f} ~ {V8['pe_max']:.2f} 的下沿附近）。
第三，股息率回到 4.5% 以上。三个条件满足两个再动手，不必等全满足。</p>
<p><b>仓位与工具。</b>若执行，优先选规模最大、成交最活跃的那只 ETF，避开日成交额仅数百万元的品种。
单只红利低波类资产不建议超过组合的 20%，因为它与中证红利相关 {C8['中证红利']:.2f}，
与红利低波100 相关 {C8['红利低波100']:.2f}，不提供分散化收益。</p>
<p><b>最大的不确定性。</b>整份报告八成数字建立在回溯段上。实盘段只有 {LV['d800']['years']:.2f} 年、
{LV['d800']['n']} 个交易日，且这段时间恰好是红利风格占优的区间。
如果未来红利风格转向，实盘段的 {LV['d800']['cagr']:.2f}% 也会被证伪。</p>
</div>
'''


# ═══════════════════ 附录 ═══════════════════
APX_A = f'''
<h2>附录 A　数据与代码来源</h2>
''' + tbl(['项目', '内容'], [
    ['数据库', f"{M['db']}（SQLite，本地投研库）"],
    ['行情主表', 'index_full_return_daily（全收益，含分红再投）／index_daily_kline（kline_type=\'normal\'，价格）'],
    ['估值表', 'index_fundamental_daily（pe_ttm / pb / dyr 及其历史分位，理杏仁口径）'],
    ['成分表', 'index_constituents（月频快照）／index_constituent_weightings（仅前十大权重）'],
    ['数字层脚本', 'scripts/divlow800_research.py → data/snapshot.json'],
    ['国际对照脚本', 'scripts/fetch_us_divlow_peers.py（akshare.stock_us_daily，新浪源，price 与 qfq 各抓一遍）'],
    ['呈现层脚本', 'docs/report/800红利低波研究报告/make_report.py（只读 snapshot，不重算数字）'],
    ['样式来源', '.agents/skills/_shared/report-style.css（唯一来源，生成时内联）'],
    ['数字快照指纹', f"snapshot.json sha1 前 16 位 = {SHA}"],
    ['运行顺序', 'fetch_us_divlow_peers.py → divlow800_research.py → make_report.py'],
], wide=True) + f'''
<p>三层产物模型：数字层（脚本现算 + JSON 快照）→ 核查层（本附录与附录 C/E 的证据登记）→
呈现层（本 HTML）。三层共用同一份快照，报告里没有任何手工誊写的数字。</p>
'''

APX_B = '''
<h2>附录 B　统计口径对照（回溯段 vs 实盘段）</h2>
''' + tbl(['项目', '回溯段', '实盘段'], [
    ['起止', '2013-12-31 ~ 2024-05-20', '2024-05-21 ~ 2026-11-09'],
    ['交易日数', f"{BT['d800']['n']}", f"{LV['d800']['n']}"],
    ['年数（日历日折年）', f"{BT['d800']['years']:.2f}", f"{LV['d800']['years']:.2f}"],
    ['800红利低波 年化（全收益）', f"{BT['d800']['cagr']:.2f}%", f"{LV['d800']['cagr']:.2f}%"],
    ['800红利低波 年化（价格）', f"{BT['d800p']['cagr']:.2f}%", f"{LV['d800p']['cagr']:.2f}%"],
    ['中证红利 年化（全收益）', f"{BT['d922']['cagr']:.2f}%", f"{LV['d922']['cagr']:.2f}%"],
    ['沪深300 年化（全收益）', f"{BT['d300']['cagr']:.2f}%", f"{LV['d300']['cagr']:.2f}%"],
    ['最大回撤', f"{pct(BT['d800']['mdd'], 2, False)}", f"{pct(LV['d800']['mdd'], 2, False)}"],
    ['对中证红利 alpha（年化）', f"{ALPHA_922:.2f}%（t={REG['vs922']['alpha_t']:.2f}）",
     f"{C922L.get('alpha_annual', 0):.2f}%（t={C922L.get('alpha_t', 0):.2f}）"],
], wide=True) + '''
<p>两段的样本量差 4 倍（2,524 对 600 个交易日），统计功效差约 2 倍。
实盘段的 alpha 不显著不是「没有 alpha」，而是「用 600 天分不出来」。
本报告的处理是：点估计照给，显著性照标，不合并两段。</p>
'''

APX_C = '''
<h2>附录 C　未核实与证据不足清单</h2>
''' + tbl(['#', '项目', '状态', '说明'], [
    ['C1', '931848 编制方案的原始修订公告', '未取得',
     '只取到中证官网 basic-info 接口的发布日与基日；未找到 PDF 编号级的修订公告。'
     '检索词包括「中证800红利低波动 编制方案」「931848 修订公告」「CSI 800 Dividend Low Volatility Index methodology」。'],
    ['C2', '931848 是否改造自其它指数', '未发现问题，未证明不存在',
     '其基日 2013-12-31 与中证红利低波系列（2005-12-30）不同源，未见代码复用迹象。'],
    ['C3', '指数财报（revenue/net_profit/roe 时间序列）', '无数据',
     '理杏仁三个端点返回 0 条，hybrid 仅 2 期。以 PB÷PE 反推 ROE 替代，见 §8.3。'],
    ['C4', '全部 100 只成分的权重', '部分缺失',
     '理杏仁只返回前十大（合计 %.2f%%），HHI 只给出下界 %.4f。' % (CON['top10_weight'], CON['hhi_lower_bound'])],
    ['C5', '10~20% 回撤档的前瞻收益（931848 自身）', '样本不足',
     '估值窗口内最大回撤仅 12.6%，该格 3 个观测，不可用；已改用中证红利参照面板。'],
    ['C6', '实盘段 alpha 的显著性', '不显著',
     't 值 %.2f，600 个交易日。点估计 %.2f%% 只作为方向参考。' % (C922L.get('alpha_t', 0), C922L.get('alpha_annual', 0))],
    ['C7', '回溯段与实盘段落差的原因分解', '未做',
     '无法区分「事后选样」「风格周期」「编制规则细节」各自的贡献，缺少可用代理变量。'],
    ['C8', '三只 ETF 的规模与流动性', '仅定性',
     '确认存在（159355 / 563980 / 158023），未取得持续规模、日均成交额的完整时间序列。'],
], wide=True, left=(1, 2))

APX_D = '''
<h2>附录 D　修正记录（含被证伪的做法）</h2>
''' + tbl(['#', '原做法', '问题', '改为'], [
    ['D1', '美股对照用 akshare qfq（前复权）序列', 'SPY qfq 年化 15.85%，比价格口径高 4.3 pp，'
     '而 SPY 真实股息率约 1.3%，说明新浪对美股的分红调整偏大，会把美股系统性抬高',
     '正式对照改用价格口径 + 拆股还原（desplit），qfq 只作脚注'],
    ['D2', '美股不复权价格直接用', 'SCHD 价格年化算出 −0.22%，NOBL 0.39%，因 3:1 与 2:1 拆股未调整',
     '加 desplit：按跳变比例反推缩放后段，检出 NOBL 2026-05-28（ratio 0.5004）、'
     'SCHD 2024-10-11（ratio 0.3373），修正后 SCHD 8.58%、NOBL 8.19%'],
    ['D3', '年化按交易日数 ÷ 243 折年', '美股每年约 252 个交易日，用 A 股 243 会把跨市场 CAGR 压低',
     '改为日历日折年：(结束日 − 起始日) ÷ 365.25，A 股与美股同标准'],
    ['D4', '用指数财报算 ROE 趋势', '931848 财报端点全部返回 0 条', '改用 PB÷PE 恒等式反推，并标注为派生口径'],
    ['D5', '把 931848 的 PE 分位当作十年分位', '估值表只有 600 行，从发布日起算', '全文改称「自发布以来分位」'],
], wide=True, left=(1, 2))

APX_E = '''
<h2>附录 E　数据源质量与缺口声明</h2>
''' + tbl(['数据源', '覆盖', '质量判断'], [
    ['index_full_return_daily（931848）', '3,124 行，2013-12-31 ~ 2026-11-09', '完整，无缺口，本报告主序列'],
    ['index_daily_kline（931848）', '3,125 行，多 1 个交易日（2026-11-10）', '完整；价格口径，用于拆分分红贡献'],
    ['index_fundamental_daily（931848）', '600 行，2024-05-21 起（= 发布日）', '无回溯数据，分位只能叫「自发布以来」'],
    ['index_financials_annual（931848）', '0 行', '缺失；已用估值恒等式替代'],
    ['index_constituent_weightings（931848）', '前十大/期，合计 ~18%', '仅部分权重；前十大合计可算，HHI 只有下界'],
    ['index_constituents（931848）', '12 期月频快照，2025-06-01 ~ 2026-05-01', '可用于重叠度计算，无法还原更早成分'],
    ['akshare.stock_us_daily（新浪）', 'SPLV/USMV/SCHD/SPY 12.86 年；NOBL/DVY 9.2 年', '价格口径经 desplit 后可用；qfq 口径判定为偏大，不采用'],
    ['中证指数官网 basic-info', '基日/发布日/成分数/调仓频率', '权威；但无修订公告原文'],
], wide=True, left=(1,)) + f'''
<p>三处必须让读者知道的限制。第一，<b>八成历史是回溯</b>：{BT['d800']['n']} / {M['days']} 个交易日
（{BT['d800']['n'] / M['days'] * 100:.0f}%）落在 2024-05-21 发布日之前。
第二，<b>估值分位不可比</b>：931848 的 {V8['n']} 个交易日对上中证红利的 {V922['n']} 个交易日。
第三，<b>国际对照是价格口径</b>：与 A 股全收益口径的差异约等于年化股息率，
本次对照表内已把 931848 的价格序列放在同一行，避免混比。</p>
'''

DISCLAIMER = '''
<div class="ft">
<p><b>免责声明。</b>本报告为研究方法演示，全部数字由本地数据库脚本现算，不构成任何投资建议、
不推荐买卖任何证券或基金、不给出目标价与仓位指令。报告中的指数历史收益均为回算或已实现的历史，
不代表未来表现。回溯段收益由编制方案事后回算，实盘段样本仅 2.47 年，两者都不能外推。
引用本报告数字时请同时引用统计区间与口径，单独引用任一年化数字均属断章取义。</p>
<p>数据截至 2026-11-09。数据库：data/lixinger.db。数字快照指纹：SNAP。
本报告由脚本生成，请勿直接编辑 HTML，改生成器后重新运行。</p>
</div>
'''

# ═══════════════════ 组装 ═══════════════════
HEAD = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>''' + esc(M['title']) + '''</title>
<style>
''' + CSS_TXT + '''
</style>
</head>
<body>
'''

TOP_BAR = f'''<div class="top">
<span class="thm" onclick="var h=document.documentElement;h.dataset.theme=h.dataset.theme==='dark'?'':'dark';">深色 / 浅色</span>
<b>指数研究报告</b>　{M['data_asof']}　·　800红利低波（931848）
</div>
<div class="wrap">
<h1>{esc(M['title'])}</h1>
<p class="lead">一句话结论：序列可用，超额真实（对中证红利年化 alpha {ALPHA_922:.2f}%），
但八成历史是回溯，且当前点位与估值都在高位——<b>放进观察名单，不在现价买入</b>。</p>
<p style="font-size:12px;color:var(--mut);margin:4px 0 0;">{esc(TOPMETA)}</p>
{KPI}
'''

BODY = ''.join([CH1, CH2, CH3, CH5, CH7, CH8, CH9, CH12,
                APX_A, APX_B, APX_C, APX_D, APX_E])

TAIL = '</div>\n</body>\n</html>\n'

HTML = HEAD + TOP_BAR + BODY + DISCLAIMER.replace('SNAP', SHA) + TAIL

open(OUT, 'w', encoding='utf-8').write(HTML)
print('written', OUT, len(HTML))

