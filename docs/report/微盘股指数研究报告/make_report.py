# -*- coding: utf-8 -*-
"""微盘股指数研究报告 —— 呈现层。

只读 data/snapshot.json，不重算数字。
样式内联 .agents/skills/_shared/report-style.css（唯一来源）。

用法：python docs/report/微盘股指数研究报告/make_report.py
"""
import os, sys, json, math, hashlib

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
    '微盘400（漂移）': '#b45309',
    '微盘100（漂移）': '#7c3aed',
    '中证2000': '#6b7280',
    '沪深300(全收益)': '#2563eb',
    '中证红利(全收益)': '#0f766e',
}

M = D['meta']
P = D['perf']
Y = D['yearly']
C = D['charts']['normalized']
K400 = '微盘400（漂移）'
K100 = '微盘100（漂移）'
K400D = '微盘400（日再平衡）'
K400C = '微盘400（市值加权）'
K300 = '沪深300(全收益)'
KDIV = '中证红利(全收益)'
cur = D['current']
cap = D['capacity']
turn = D['turnover']
rev = {r['type']: r for r in D['revision']}
reg = {r['label']: r for r in D['regression']}
st = D['strategy']
peers = D['peers']['list']
PEERMAP = {x['name']: x for x in peers}

D_EW_CW = P[K400]['cagr'] - P[K400C]['cagr']
D_REB = P[K400D]['cagr'] - P[K400]['cagr']
D_100 = P[K100]['cagr'] - P[K400]['cagr']
D_2000 = P[K400]['cagr'] - P['中证2000']['cagr']
SLIP_LO, SLIP_HI = turn['微盘400']['annual'] * 0.0015 * 2, turn['微盘400']['annual'] * 0.0030 * 2


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


def tbl(headers, rows, wide=False, num_from=1):
    h = ''.join('<th>%s</th>' % esc(x) for x in headers)
    body = ''
    for r in rows:
        tds = ''
        for i, c in enumerate(r):
            if isinstance(c, tuple):
                txt, cl = c
                if i >= num_from:
                    tds += '<td class="num %s">%s</td>' % (cl, txt)
                else:
                    tds += '<td class="%s">%s</td>' % (cl, txt)
            else:
                tds += ('<td class="num">%s</td>' % c) if i >= num_from else ('<td>%s</td>' % c)
        body += '<tr>%s</tr>' % tds
    return '<div class="tw"><table%s><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (
        ' class="wide"' if wide else '', h, body)


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
        step = (hi - lo) / 5
        ticks = [lo + step * i for i in range(6)]
    for t in ticks:
        y = sy(t)
        p.append('<line class="gl" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>' % (pad_l, y, W - pad_r, y))
        lbl = ('%g' % round(t)) if t >= 10 else ('%.1f' % t)
        p.append('<text class="ay" x="%d" y="%.1f" text-anchor="end">%s</text>' % (pad_l - 6, y + 3.5, lbl))
    yy = int(x0[:4]) + 1
    while yy <= int(x1[:4]):
        d = '%d-01-01' % yy
        if x0 <= d <= x1:
            x = sx(d)
            p.append('<line class="gl" x1="%.1f" y1="%d" x2="%.1f" y2="%d"/>' % (x, pad_t, x, H - pad_b))
            p.append('<text class="ax" x="%.1f" y="%d" text-anchor="middle">%d</text>' % (x, H - pad_b + 16, yy))
        yy += 2
    for name, color, pts in series:
        d = 'M' + ' L'.join('%.1f,%.1f' % (sx(a), sy(b)) for a, b in pts)
        p.append('<path class="ln" d="%s" stroke="%s"/>' % (d, color))
    if ylab:
        p.append('<text class="ax" x="%d" y="%d">%s</text>' % (pad_l, pad_t - 2, esc(ylab)))
    p.append('</svg>')
    return ''.join(p)


def drawdown_chart(pts, W=980, H=240):
    peak = pts[0][1]
    dd = []
    for d, v in pts:
        peak = max(peak, v)
        dd.append((d, v / peak - 1))
    lo = min(x[1] for x in dd)
    hi = 0.0
    pad_l, pad_r, pad_t, pad_b = 54, 16, 14, 30
    span = (hi - lo) or 0.01

    def sx(i):
        return pad_l + i / (len(dd) - 1) * (W - pad_l - pad_r)

    def sy(v):
        return pad_t + (hi - v) / span * (H - pad_t - pad_b)

    p = ['<svg class="chart" viewBox="0 0 %d %d">' % (W, H)]
    for t in nice_ticks(lo, hi, 5):
        y = sy(t)
        p.append('<line class="gl" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>' % (pad_l, y, W - pad_r, y))
        p.append('<text class="ay" x="%d" y="%.1f" text-anchor="end">%g%%</text>' % (pad_l - 6, y + 3.5, t * 100))
    area = 'M%.1f,%.1f ' % (sx(0), sy(0)) + ' L'.join('%.1f,%.1f' % (sx(i), sy(v))
                                                   for i, (_, v) in enumerate(dd)) + \
        ' L%.1f,%.1f Z' % (sx(len(dd) - 1), sy(0))
    p.append('<path d="%s" fill="#b45309" opacity=".16"/>' % area)
    p.append('<path class="ln" d="M' + ' L'.join('%.1f,%.1f' % (sx(i), sy(v))
                                                 for i, (_, v) in enumerate(dd)) +
             '" stroke="#b45309" stroke-width="1.5"/>')
    y0 = int(dd[0][0][:4]) + 1
    while y0 <= int(dd[-1][0][:4]):
        tgt = '%d-01-01' % y0
        hit = [i for i, (q, _) in enumerate(dd) if q >= tgt]
        if hit:
            p.append('<text class="ax" x="%.1f" y="%d" text-anchor="middle">%d</text>' %
                     (sx(hit[0]), H - pad_b + 16, y0))
        y0 += 2
    p.append('</svg>')
    return ''.join(p)


def year_bars(yearly, keys, W=980, H=320):
    years = sorted(yearly)
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
    p.append('<line class="gl" x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke-width="1.3"/>' %
             (left, sy(0), W - right, sy(0)))
    for i, y in enumerate(years):
        x0 = left + gsz * i
        for j, k in enumerate(keys):
            if k not in yearly[y]:
                continue
            v = yearly[y][k]
            top, bot = sy(max(v, 0)), sy(min(v, 0))
            p.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" opacity=".86"/>' %
                     (x0 + gsz * 0.13 + bw * j, top, bw, max(1, bot - top), COLOR.get(k, '#888')))
        p.append('<text class="ax" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (x0 + gsz / 2, H - 10, y))
    p.append('</svg>')
    return ''.join(p)


# ── 表格 ──
perf_rows = []
for k in (K400D, K400, K400C, '微盘100（日再平衡）', K100, '中证2000', '中证1000', K300, KDIV, '中证全指(全收益)'):
    v = P[k]
    perf_rows.append([esc(k), pct(v['tot'], 1), (pct(v['cagr']), cls(v['cagr'])),
                      pct(v['vol'], 2, False), '%.2f' % v['sharpe'],
                      (pct(v['mdd'], 1, False), 'fall'),
                      ('%.2f' % v['calmar']) if v['calmar'] is not None else '—'])
T_PERF = tbl(['序列', '累计收益', '年化', '年化波动', '夏普', '最大回撤', 'Calmar'], perf_rows)

yk = [K400, K100, '中证2000', '中证1000', K300, KDIV]
yrows = []
for y in sorted(Y):
    row = [y]
    for k in yk:
        v = Y[y].get(k)
        row.append((pct(v, 1), cls(v)) if v is not None else '—')
    yrows.append(row)
T_YEAR = tbl(['年份'] + [k.replace('(全收益)', '') for k in yk], yrows)

ck = list(D['corr'].keys())
T_CORR = tbl([''] + [c.replace('(全收益)', '') for c in ck],
             [[esc(a)] + ['%.3f' % D['corr'][a][b] for b in ck] for a in ck])

T_REG = tbl(['模型', '年化 alpha', 't 值', '因子暴露', 'R²'],
            [[esc(r['label']), (pct(r['alpha']), cls(r['alpha'])), '%.2f' % r['t'],
              '、'.join('%s β=%.3f' % (b['name'], b['beta']) for b in r['betas']),
              '%.3f' % r['r2']] for r in D['regression']])

T_EV = tbl(['回撤起点', '前高日', '最低点日', '深度', '收复日'],
           [[e['start'], e['prev_high_date'], e['low_date'], (pct(e['depth'], 1, False), 'fall'),
             e['recover'] or '未收复'] for e in D['drawdown_events'][:8]])

bk = D['dd_buckets']
b_rows = [[b['bucket'], str(b['n']), pct(b['mean'], 2), pct(b['median'], 2), '%.1f%%' % b['win']]
          for b in bk['buckets']]
b_rows.append(['全部交易日（基准）', str(bk['base']['n']), pct(bk['base']['mean'], 2),
               pct(bk['base']['median'], 2), '%.1f%%' % bk['base']['win']])
T_DD = tbl(['锚定回撤档', '样本（交易日）', '未来 60 日均值', '中位', '胜率'], b_rows)

pr_rows = []
for x in peers:
    pr_rows.append([x['name'], '%.2f' % x['years'], (pct(x['cagr']), cls(x['cagr'])),
                    pct(x['vol'], 2, False), '%.2f' % x['sharpe'],
                    (pct(x['mdd'], 1, False), 'fall'), str(x['bad'])])
for k in (K400, K100):
    pr_rows.append([k + '（A股自建）', '%.2f' % M['years'], (pct(P[k]['cagr']), cls(P[k]['cagr'])),
                    pct(P[k]['vol'], 2, False), '%.2f' % P[k]['sharpe'],
                    (pct(P[k]['mdd'], 1, False), 'fall'), '0'])
T_PEER = tbl(['标的', '年数', '年化', '年化波动', '夏普', '最大回撤', '坏点'], pr_rows)

T_TURN = tbl(['指数', '调仓快照数', '单次换手均值', '中位', '最大', '年化换手'],
             [[k, str(v['snapshots']), '%.1f%%' % v['mean'], '%.1f%%' % v['median'],
               '%.1f%%' % v['max'], '%.0f%%' % v['annual']] for k, v in turn.items()])

T_REV = tbl(['序列', '存量末值', '重算末值', '存量累计', '重算累计', '日收益相关', '日均差', '池重合（均值/最低）'],
            [[r['type'], '%.2f' % r['old_last'], '%.2f' % r['new_last'],
              pct(r['old_total'], 1), pct(r['new_total'], 1), '%.4f' % r['corr'],
              '%.4f pp' % r['mean_diff_pp'], '%.1f%% / %.1f%%' % (r['overlap_mean'], r['overlap_min'])]
             for r in D['revision']])

T_TIER = tbl(['锚定回撤档', '触发次数', '未来 60 日均值', '60 日胜率'],
             [[('%d%%' % t['th']), str(t['n']),
               pct(t['f60_mean'], 2) if t['f60_mean'] is not None else '—',
               ('%.1f%%' % t['f60_win']) if t['f60_win'] is not None else '—'] for t in st['tiers']])

T_REVINFO = tbl(['修订', '时间', '改动内容', '影响'], [
    ['建模块 v1.0', '2026-09-09', '选样：月频取总市值最小 400／100 只，等权，月末定池', '起点 2016-01-04 = 1000'],
    ['增量状态修复', '2026-09-10', '只改增量重启的状态恢复与前端 x 轴', '全量路径不变'],
    ['价格口径迁移', '2026-09-14', '数据源由 <code>daily_kline</code> 改为 <code>daily_kline_adj</code> 视图', '量纲变化，选样规则未改'],
    ['本次重算', '2026-11-09', '按当前数据全量重算，收益改用前复权口径', '序列水平整体上移，见 §三'],
], wide=True)

# ── 图 ──
fig1 = line_chart([(k, COLOR[k], C[k]) for k in C if k in COLOR and k != K100],
                  ylab='净值（2016-01-04 = 1000，对数轴）')
fig1b = line_chart([(k, COLOR[k], C[k]) for k in (K400, K100)],
                   ylab='净值（2016-01-04 = 1000，对数轴）')
fig2 = drawdown_chart(C[K400])
fig3 = year_bars(Y, [K400, '中证2000', KDIV])

legend1 = ''.join('<span style="color:%s">&#9679; %s</span>&nbsp;&nbsp;&nbsp;' % (COLOR[k], esc(k))
                  for k in COLOR if k != K100)
legend2 = ''.join('<span style="color:%s">&#9679; %s</span>&nbsp;&nbsp;&nbsp;' % (COLOR[k], esc(k))
                  for k in (K400, K100))
legend3 = ''.join('<span style="color:%s">&#9632; %s</span>&nbsp;&nbsp;&nbsp;' % (COLOR[k], esc(k))
                  for k in (K400, '中证2000', KDIV))

kpi = ('<div class="kpi">'
       '<div><div class="k">微盘400 年化（漂移）</div><div class="v">%s</div><div class="n">%d 个交易日</div></div>'
       '<div><div class="k">中证2000 年化</div><div class="v">%s</div><div class="n">同窗口</div></div>'
       '<div><div class="k">对中证2000 alpha</div><div class="v">%s</div><div class="n">t=%.2f，R²=%.3f</div></div>'
       '<div><div class="k">最大回撤</div><div class="v">%s</div><div class="n">2016-11 至 2018-10</div></div>'
       '<div><div class="k">当前回撤</div><div class="v">%s</div><div class="n">前高 %.0f 点</div></div>'
       '</div>') % (
    pct(P[K400]['cagr']), M['days'], pct(P['中证2000']['cagr']),
    pct(reg['微盘400（等权）对中证2000']['alpha']),
    reg['微盘400（等权）对中证2000']['t'], reg['微盘400（等权）对中证2000']['r2'],
    pct(P[K400]['mdd'], 1, False), pct(cur['dd'], 1, False), cur['peak'])

warn_slip = ''

HTML = f'''<!doctype html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>微盘股指数研究 · 400／100 微盘与可投资性判断</title>
<style>{CSS_TXT}</style>
</head>
<body>
<div class="wrap">
<div class="top"><b>微盘股指数研究</b> · 数据截至 {M['data_asof']} · 快照 {M['generated_at']}
<span class="thm" onclick="var h=document.documentElement;var d=h.getAttribute('data-theme')==='dark';h.setAttribute('data-theme',d?'light':'dark');try{{localStorage.setItem('mc_theme',d?'light':'dark')}}catch(e){{/* noop */}}">&#9686;</span></div>

<h1>微盘股指数研究</h1>
<p class="sub">研究对象：本仓库自建的 400 微盘／100 微盘序列，编制口径对照万得微盘股指数（868008）。
窗口 {M['window'][0]} 至 {M['window'][1]}，{M['days']} 个交易日（{M['years']} 年）。</p>

<div class="lead">
<p><b>结论一：存量曲线有缺陷，已修好。</b>旧曲线无法用当前数据复现，成分池重合度只有 {rev['微盘400']['overlap_mean']}%，且历史段丢失分红与送转。
按前复权口径全量重算后，400 微盘累计收益由 {pct(rev['微盘400']['old_total'], 1)} 修正为 {pct(rev['微盘400']['new_total'], 1)}。</p>
<p><b>结论二：超额来自选样，不来自加权。</b>对中证2000 的年化 alpha {pct(reg['微盘400（等权）对中证2000']['alpha'])}（t={reg['微盘400（等权）对中证2000']['t']}）。
同一只池改成市值加权，年化只降 {D_EW_CW:.2f} pp。差异几乎全部来自「每月买入总市值最小的 400 只」。</p>
<p><b>结论三：A 股没有可投资的微盘指数。</b>唯一真正的微盘指数是万得微盘股指数（868008），数据在 Wind 终端，无 ETF 跟踪。
官方规模档位止于中证2000（932000）与国证2000（399303），属小盘，不属微盘。</p>
<p><b>结论四：不能按「红利指数」的方式长期持有。</b>微盘是策略型暴露，不是可持有的指数资产。四条硬约束见 §十。</p>
</div>

{kpi}

<h2>一、这份序列是什么</h2>
<p>400 微盘／100 微盘是<strong>自建序列</strong>，不是交易所或指数公司发布的指数。构建脚本为
<code>scripts/build_microcap_index.py</code>，编制规则对照万得微盘股指数（868008）：</p>
<ul>
<li>样本空间：沪深 A 股，剔除北交所与 B 股（代码前缀 4／8／92）。</li>
<li>筛选：上市满 60 个自然日；ST 不做历史剔除（本地无历史 ST 状态，硬剔会造成前视）。</li>
<li>选样：每月最后一个交易日，按当日总市值升序取最小的 400 只（100 微盘取 100 只）。</li>
<li>加权：等权。调仓：次月首个交易日换成新池。</li>
<li>点位：以 2016-01-04 = 1000 为基点，按前复权收益连乘。</li>
</ul>
<p>与 868008 的已知差异有二：万得先剔除 ST 与首日连板，再取总市值后 400 名；本序列用「上市满 60 个自然日」近似「非次新」，且不做 ST 历史剔除。
两项都不改变「取市值最小的 400 只」这一核心机制。</p>

<h3>1.1 编制规则的修订史</h3>
{T_REVINFO}
<p>三次提交中只有第三项改了价格口径，选样与加权规则自 2016 起点从未变更。这一点很重要：它说明
<strong>不存在「用新规则回溯重算历史、把曲线美化」的问题</strong>。整段 10.84 年是一次性回算的模拟序列，但模拟用的是同一套规则。</p>

<h2>二、统计口径</h2>
<div class="card">
<table class="wide"><tbody>
<tr><td>统计区间</td><td>{M['window'][0]} 至 {M['window'][1]}，共 {M['days']} 个交易日（{M['years']} 年）。全部序列统一截至 {M['window'][1]}。</td></tr>
<tr><td>指数定义</td><td>月频调仓、等权、总市值最小的 400 只／100 只；基点 2016-01-04 = 1000。</td></tr>
<tr><td>收益口径</td><td>前复权（<code>lxr_fc_close</code>），含分红再投与送转调整。<strong>不含</strong>交易成本、冲击成本、融券费用、管理费。</td></tr>
<tr><td>再平衡口径</td><td>主口径为<strong>月内权重漂移</strong>（换池时才回到等权），即真实持有能拿到的收益。原脚本的「日度等权再平衡」口径另列，两者差 {D_REB:.2f} pp/年。</td></tr>
<tr><td>统计对象</td><td>指数点位序列，不涉及个股选择。</td></tr>
<tr><td>买入时点</td><td>月末收盘定池，次月首个交易日按前复权收益接入。存在轻微前视：定池用月末收盘市值，实际换仓也按该收盘价计算，未计次日开盘滑点。</td></tr>
<tr><td>持有期</td><td>全区间买入持有；前瞻收益使用 60 个交易日。</td></tr>
<tr><td>基准</td><td>中证2000（932000）、中证1000（000852）为价格口径；沪深300（000300）、中证红利（000922）、中证全指（000985）为全收益口径。中证2000 无全收益序列。</td></tr>
<tr><td>样本量</td><td>日频 {M['days']} 个观测，不独立；独立调仓期 {rev['微盘400']['overlap_dates']} 个。回撤分档触发次数为 {st['tiers'][0]['n']}／{st['tiers'][1]['n']}／{st['tiers'][2]['n']} 次，<strong>不做显著性检验</strong>。</td></tr>
</tbody></table>
</div>
<div class="warn">
<p><b>口径差异对结论的影响。</b>「日再平衡」口径比「月内漂移」高 {D_REB:.2f} pp/年（400 类）。日再平衡假设每天免费把 400 只股票调回等权，实盘做不到。
凡是引用旧页面数字的结论，都要按漂移口径下调。</p>
</div>

<h2>三、数据可信度：三个必须交代的问题</h2>

<h3>3.1 存量序列无法复现</h3>
<p>2026-09 建库时写入的曲线，用当前数据无论如何都算不出来。两种价格口径分别重算，结果都与存量不符：</p>
{T_REV}
<p>存量末值 {rev['微盘400']['old_last']:.2f}（400 类），前复权重算 {rev['微盘400']['new_last']:.2f}，不复权重算 5143.78。
存量值落在两者之间，而<strong>成分池重合度只有 {rev['微盘400']['overlap_mean']}%（最低 {rev['微盘400']['overlap_min']}%）</strong>。
池都不同，说明当年建库时的股本数据与现在不一致，不是价格口径能解释的差别。</p>
<p>两条序列的日收益相关系数 {rev['微盘400']['corr']}，日均差 {rev['微盘400']['mean_diff_pp']} pp。差异最大的日子集中在分红送转季
（2017-04-10 −1.58 pp、2018-05-28 −1.51 pp、2017-05-25 −1.44 pp），指向<strong>存量历史段丢失分红与送转调整</strong>。</p>
<div class="danger">
<p><b>处理方式。</b>本轮已全量重算并覆盖线上两张表；存量备份为 <code>microcap_index_daily_v1</code> 与 <code>microcap_index_component_v1</code>。
页面数字随之变化：400 微盘由 3867.30 变为 7157.30，100 微盘由 7081.18 变为 12944.21。</p>
</div>

<h3>3.2 幸存者偏差存在，本地无法量化</h3>
<p>2016 年后停止更新的股票共 {D['data_quality']['stopped_after_2016']} 只，其中 {D['data_quality']['not_in_stock_basic']} 只不在 <code>stock_basic</code> 表内。
<code>stock_basic.delisted_date</code> 全表为空（{D['data_quality']['delisted_date_filled']} 行有值），脚本里的退市过滤分支从未生效。</p>
<p>按 A 股代码前缀过滤后，可补齐的候选为 234 只。这 234 只在 <code>stock_equity_change</code>、<code>fundamental_indicator</code>、
<code>stock_financials_annual</code>、<code>stock_financials_quarterly</code>、<code>stock_financials_annual_ext</code>、
<code>financial_statement</code> 六张表中<strong>全部零行</strong>。没有总股本就算不出市值，它们永远进不了池。</p>
<div class="warn">
<p><b>偏差方向与幅度。</b>方向是<strong>抬高</strong>收益（退市股 −100% 的结局被排除在外），幅度本地数据无法量化。
补齐实验（把 234 只加入股票池）与不补齐的结果完全相同，正是因为它们没有股本数据、仍然进不了池。
本报告所有收益数字都应视为<strong>上限</strong>。</p>
</div>

<h3>3.3 前视与可执行性</h3>
<p>月末收盘定池、同价换仓，省掉了一次开盘滑点。对 400 只等权组合，单边滑点在微盘股上通常为 0.1%~0.3%，
按年化换手 {turn['微盘400']['annual']:.0f}% 折算，年化影响约 {SLIP_LO:.2f}~{SLIP_HI:.2f} pp。方向同样是<strong>高估</strong>。</p>

<h2>四、收益与风险全景</h2>
{T_PERF}
<p class="note">注：中证2000、中证1000 为价格口径，其余为全收益口径。比率类指标（波动、夏普、Calmar）不表示方向，故不着色。</p>
<div class="tw">{fig1}<div class="lg">{legend1}</div></div>
<p class="note">图 1：归一化净值，2016-01-04 = 1000，纵轴对数，每 {D['charts']['step_trading_days']} 个交易日取 1 点。</p>
<div class="tw">{fig1b}<div class="lg">{legend2}</div></div>
<p class="note">图 2：400 微盘与 100 微盘对比。100 微盘更极致，年化高 {D_100:.2f} pp，最大回撤相近。</p>

<h2>五、分年度表现</h2>
<div class="tw">{fig3}<div class="lg">{legend3}</div></div>
<p class="note">图 3：分年度收益。红／绿仅表示涨跌方向。</p>
{T_YEAR}
<p>11 个年度中 400 微盘有 8 个年度为正，跑赢中证2000 的有 9 个。两个负年份是 2017 年（{pct(Y['2017'][K400], 1)}）与 2018 年（{pct(Y['2018'][K400], 1)}）——
正是大盘蓝筹占优、小盘被杀估值的两年。2026 年至今为 {pct(Y['2026'][K400], 1)}，同期中证红利为 {pct(Y['2026'][KDIV], 1)}。</p>

<h2>六、上涨本质总结：收益来自选样，不是加权</h2>
<p>微盘400 与中证2000 的日收益相关系数 {D['corr'][K400]['中证2000']:.3f}，beta 接近 1。只看这两个数字，会得出「微盘就是中证2000 的另一种画法」。
但年化差 {D_2000:.2f} pp，说明在同一个 beta 之上还叠加了一个稳定的截距项。</p>
{T_REG}
<p>三组事实指向同一结论：</p>
<ul>
<li>对中证2000 的 alpha {pct(reg['微盘400（等权）对中证2000']['alpha'])}，t={reg['微盘400（等权）对中证2000']['t']}，在 {M['days']} 个交易日上显著。</li>
<li>加入沪深300 后 alpha 升至 {pct(reg['微盘400（等权）对沪深300＋中证2000']['alpha'])}，沪深300 的 beta 为 {reg['微盘400（等权）对沪深300＋中证2000']['betas'][0]['beta']}（负值）——
微盘与大票是<strong>两个方向</strong>的暴露，不是同一个 beta 的两端。</li>
<li>同一只池改成市值加权，年化由 {pct(P[K400]['cagr'])} 降到 {pct(P[K400C]['cagr'])}，只差 {D_EW_CW:.2f} pp。等权本身贡献极小。</li>
</ul>
<div class="ess">
<p>把「取市值最小的 400 只」换成「取市值最小的 400 只但按市值加权」，超额几乎不变；换成「中证2000 的选样方式」，超额全部消失。
<strong>收益来自每月强制卖出涨上去的、买入跌下来的</strong>，即逆向选样，而非等权再平衡。这与「低估纳入、高估剔除」的直觉一致。</p>
<p><b>上涨本质一句话：</b>每天在市场里市值最小的 400 只股票上做一次强制逆向换仓，赚的是小市值股票的波动与流动性溢价。
这条收益链依赖三个外部条件——壳价值、散户参与者占比、注册制与退市的节奏。三者任一显著改变，超额就会收敛。
本报告无法量化这三条条件各自贡献多少，因为它们都没有可用的历史代理变量。</p>
</div>

<h3>6.1 相关性矩阵</h3>
{T_CORR}
<p>微盘400 与中证红利的相关只有 {D['corr'][K400][KDIV]:.3f}，与沪深300 为 {D['corr'][K400][K300]:.3f}。
微盘不是「小号的红利」，两者的收益驱动不同。</p>

<h2>七、回撤结构</h2>
<div class="tw">{fig2}
<div class="lg"><span style="color:#b45309">&#9644; 400 微盘锚定回撤</span></div></div>
<p class="note">图 4：锚定回撤（运行最高点被突破即更新）。当前 {pct(cur['dd'], 1, False)}。</p>
{T_EV}
<p>两次深跌都接近一半：{D['drawdown_events'][0]['start']} 起 {pct(D['drawdown_events'][0]['depth'], 1, False)}，历时 22 个月才见底；
{D['drawdown_events'][1]['start']} 起 {pct(D['drawdown_events'][1]['depth'], 1, False)}，<strong>只用了 24 个交易日</strong>。
后者是流动性螺旋：小微盘在去杠杆时没有买盘，跌幅与基本面无关。</p>
<p>当前回撤 {pct(cur['dd'], 1, False)}（{cur['last_date']} 收 {cur['point']:.2f} 点），距 2026-05-11 高点 {cur['peak']:.0f} 点。
RSI(14) 为 {D['rsi14'][K400]['now']}，历史分位 {D['rsi14'][K400]['pct']}%，中位 {D['rsi14'][K400]['median']}。</p>

<h3>7.1 回撤分档与前瞻收益</h3>
{T_DD}
<div class="warn">
<p><b>不要照搬红利指数的 10／14／20 三档。</b>微盘的回撤分布完全不同：中位回撤 {D['dd_dist']['50']}%，
四分之一时间回撤超过 {abs(D['dd_dist']['25']):.1f}%，10% 分位达 {D['dd_dist']['10']}%。10% 档在本序列里几乎每天都触发，没有区分度。</p>
<p>本报告改用 −10%／−20%／−30% 三档，由分位数确定，不是最优拟合。<strong>深档的前瞻收益更高，但样本量极小
（最深档 {bk['buckets'][-1]['n']} 个交易日，约 {bk['buckets'][-1]['n'] / 243:.1f} 年等效），不足以支撑决策。</strong></p>
</div>

<h2>八、国际同类对照</h2>
<p>美国市场同样存在微盘股资产类别，且有可交易工具。窗口与 A 股分析对齐，{M['window'][0]} 至 {M['window'][1]}，
价格口径，共同窗口内 |单日涨跌|&gt;35% 的坏点为 0。</p>
{T_PEER}
<p>可读的三件事：</p>
<ul>
<li>美国微盘（IWC，iShares Micro-Cap）同期年化 {pct(PEERMAP['IWC']['cagr'])}，波动 {pct(PEERMAP['IWC']['vol'], 1, False)}，
回撤 {pct(PEERMAP['IWC']['mdd'], 1, False)}。高波动、深回撤是<strong>跨市场共性</strong>，不是 A 股独有。</li>
<li>美国微盘跑输标普500（SPY {pct(PEERMAP['SPY']['cagr'])}，差 {PEERMAP['IWC']['cagr'] - PEERMAP['SPY']['cagr']:.2f} pp）。
A 股微盘却大幅跑赢，这是<strong>A 股特有的制度性溢价</strong>，来源是壳价值、散户结构、注册制节奏与量化拥挤。</li>
<li>IWM（罗素2000）与 IWC（微盘）年化差 {abs(PEERMAP['IWM']['cagr'] - PEERMAP['IWC']['cagr']):.2f} pp。
美国市场里「微盘 vs 小盘」没有系统性溢价，进一步说明 A 股微盘超额不是规模因子的普遍规律。</li>
</ul>
<p class="note">数据源：<code>akshare.stock_us_daily</code>（新浪，qfq），IWC 起 2005-08-16，本表裁到共同窗口。
更长窗口下新浪 qfq 在 2008 段有坏点，故不外推。</p>

<h2>九、A 股有没有真实的微盘指数</h2>
<div class="card">
<table class="wide"><tbody>
<tr><td><b>万得微盘股指数<br>（868008）</b></td><td>唯一的真微盘指数。编制：剔除 ST 与首日连板后，取总市值后 400 名，等权，月度调整。
<strong>不可投资</strong>：属 Wind 终端内部指数，无交易所发布，无 ETF 或场外基金跟踪。
数据需 Wind 授权，本仓库与 akshare 均取不到（<code>index_zh_a_hist('868008')</code> 返回空）。</td></tr>
<tr><td>中证2000<br>（932000）</td><td>官方规模档位的最小一档。2023-08-11 发布，基日 2013-12-31。属<strong>小盘</strong>，成分市值中位约 40 亿元，不是微盘。有 ETF。</td></tr>
<tr><td>国证2000<br>（399303）</td><td>同上，属小盘。本仓库原无该指数 K 线，本次从 akshare 补取（2011-01-04 起 3,848 行）。</td></tr>
<tr><td>中证1000<br>（000852）</td><td>小盘偏中，市值中位约 80 亿元。离微盘更远。</td></tr>
<tr><td>自建 400／100 微盘</td><td>唯一能拿到的微盘序列，本报告标的。缺陷见 §三。</td></tr>
</tbody></table>
</div>
<div class="ess">
<p><b>结论：A 股没有既可投资、又有真实微盘暴露的指数。</b>想要微盘暴露只有三条路：一是自建序列做研究（本报告）；
二是买中证2000 ETF，但那只是小盘 beta，同期年化 {pct(P['中证2000']['cagr'])}，与微盘的 {pct(P[K400]['cagr'])} 不是一回事；
三是买主动量化小微盘基金，需另做尽调，本仓库无相关数据。</p>
</div>

<h2>十、可投资性：四条硬约束</h2>
<h3>10.1 没有载体</h3>
<p>无 ETF、无场外指数基金、无股指期货。任何「买入并持有」都必须由人手工复制 400 只等权组合，或依赖主动基金——
两者都与「指数投资」不是同一件事。</p>

<h3>10.2 容量小</h3>
<div class="kpi">
<div><div class="k">池内总市值</div><div class="v">{cap['cap_sum_yi']:,.0f} 亿元</div><div class="n">400 只合计</div></div>
<div><div class="k">单只市值中位</div><div class="v">{cap['cap_median_yi']} 亿元</div><div class="n">区间 {cap['cap_min_yi']}~{cap['cap_max_yi']} 亿</div></div>
<div><div class="k">单只日均成交额中位</div><div class="v">{cap['per_stock_median_wan']:,.0f} 万元</div><div class="n">最小 {cap['per_stock_min_wan']:,.0f} 万</div></div>
<div><div class="k">池内成交额占比</div><div class="v">{cap['share_pct']}%</div><div class="n">全市场 {cap['market_amount_yi']:,.0f} 亿元</div></div>
</div>
<p>按单只日均成交额中位 {cap['per_stock_median_wan']:,.0f} 万元计算，一个 10 亿元的等权组合每只需建仓 250 万元，
约等于 0.08 个交易日的成交额；月度换手 {turn['微盘400']['mean']}% 时每次调仓名义金额约 1.2 亿元。
这个规模尚可执行，但已产生可见冲击。规模再放大一个数量级，冲击成本会吞掉相当部分超额。</p>

<h3>10.3 换手高</h3>
{T_TURN}
<p>400 类每月替换 {turn['微盘400']['mean']}% 的成分，年化换手 {turn['微盘400']['annual']:.0f}%；100 类年化 {turn['微盘100']['annual']:.0f}%。
加上维持等权所需的再平衡交易，实际双边换手远高于此。按单边 0.15% 估算，年化成本约 {turn['微盘400']['annual'] * 0.0015 * 2:.2f} pp；
微盘冲击更大，实际成本更高。</p>

<h3>10.4 回撤深且快</h3>
<p>−45% 级别的回撤发生过两次，其中一次只用了 24 个交易日。这类回撤不是估值回归，是流动性事件，<strong>无法用基本面判断规避</strong>。
任何杠杆、任何止损纪律在连续跌停面前都会失效。</p>

<h2>十一、入场策略回测</h2>
<p>口径：微盘400 漂移口径，{M['window'][0]} 起，毛收益，不含成本。</p>
<div class="card">
<table class="wide"><tbody>
<tr><td>一次性买入并持有</td><td>{st['hold']['years']} 年累计 {pct(st['hold']['total'], 1)}，年化 {pct(st['hold']['cagr'])}</td></tr>
<tr><td>每月首个交易日定投</td><td>共 {st['dca']['periods']} 期，投入 {st['dca']['invested']} 单位，期末 {st['dca']['end_value']} 单位，累计收益 {pct(st['dca']['profit'], 1)}</td></tr>
</tbody></table>
</div>
<p>定投累计收益率（{pct(st['dca']['profit'], 1)}）低于一次性买入（{pct(st['hold']['total'], 1)}），这是定投的固有性质：
后期投入的资金没有参与早期上涨。但定投的资金曲线更平滑，因为建仓成本被摊平。</p>
{T_TIER}
<div class="warn">
<p>−15% 档触发 {st['tiers'][0]['n']} 次，未来 60 日均值 {pct(st['tiers'][0]['f60_mean'])}，胜率 {st['tiers'][0]['f60_win']}%；
−25% 档触发 {st['tiers'][1]['n']} 次，均值 {pct(st['tiers'][1]['f60_mean'])}，胜率 {st['tiers'][1]['f60_win']}%；
−35% 档只有 {st['tiers'][2]['n']} 次。
<strong>样本量太少，胜率 100% 没有统计意义</strong>，只能作为「深跌后买入的方向性倾向」，不能作为策略参数。</p>
</div>

<h2>十二、结论：能不能像红利指数一样长期投资</h2>
<div class="danger">
<p><b>不能。</b>两者在四个维度上不同，其中三个是结构性的，不会因为持有时间长而消失。</p>
</div>
<div class="tw"><table>
<thead><tr><th>维度</th><th>中证红利（000922）</th><th>400 微盘（自建）</th></tr></thead>
<tbody>
<tr><td>年化（同窗口）</td><td class="num">{pct(P[KDIV]['cagr'])}</td><td class="num">{pct(P[K400]['cagr'])}</td></tr>
<tr><td>年化波动</td><td class="num">{pct(P[KDIV]['vol'], 1, False)}</td><td class="num">{pct(P[K400]['vol'], 1, False)}</td></tr>
<tr><td>最大回撤</td><td class="num">{pct(P[KDIV]['mdd'], 1, False)}</td><td class="num">{pct(P[K400]['mdd'], 1, False)}</td></tr>
<tr><td>夏普</td><td class="num">{P[KDIV]['sharpe']:.2f}</td><td class="num">{P[K400]['sharpe']:.2f}</td></tr>
<tr><td>可投资载体</td><td>多只 ETF、场外指数基金、期货</td><td><b>无</b></td></tr>
<tr><td>现金流</td><td>股息率约 4%~5%，持续派现</td><td>几乎不分红，收益全在价差</td></tr>
<tr><td>容量</td><td>数百亿元量级</td><td>十亿元量级开始有明显冲击</td></tr>
<tr><td>收益来源</td><td>股息 + 估值修复，可由基本面解释</td><td>月频逆向选样 + 流动性溢价，无现金流支撑</td></tr>
</tbody></table></div>
<p>把红利指数当作可长期持有的资产，理由有三条：有载体、有现金流、回撤可由估值解释。微盘一条都不满足。
它的 {pct(P[K400]['cagr'])} 年化是真实的（在本地数据的上限意义下），但它是一种
<strong>需要主动管理、需要承受 −45% 回撤、容量有限的策略暴露</strong>，不是「买了就不看」的指数资产。</p>
<div class="ess">
<p>如果目标是长期持有、少操作，中证红利（000922）仍是更合适的答案：年化 {pct(P[KDIV]['cagr'])}，回撤 {pct(P[KDIV]['mdd'], 1, False)}，有 ETF，有股息。
如果目标是用一部分仓位获取高波动超额，400 微盘值得关注，但必须接受三点：自建序列含不可量化的幸存者偏差；
实盘只能靠主动基金或手工复制；任何一次流动性事件都可能在两个月内抹掉两年收益。</p>
</div>

<h2>附录</h2>
<h3>附录 A　数据与代码来源</h3>
<ul>
<li>数据库：<code>{esc(M['db'])}</code>（{esc(M['db_size_hint'])}）。本次全部数字由该库现算。</li>
<li>数字层：<code>scripts/microcap_research.py</code> → <code>data/snapshot.json</code>（sha1 前缀 {SHA}）。</li>
<li>呈现层：<code>docs/report/微盘股指数研究报告/make_report.py</code>，只读快照。</li>
<li>序列构建：<code>scripts/build_microcap_index.py</code>（线上表）、<code>scripts/build_microcap_variants.py</code>（漂移与市值加权变体，<code>--weight equal|cap --suffix</code>）。</li>
<li>国际数据：<code>scripts/fetch_us_microcap.py</code> → <code>.scratch/us_microcap_peers.json</code>，源 akshare 新浪 qfq。运行顺序：先 fetch，再 research。</li>
<li>A 股基准：中证2000／中证1000／国证2000 由 akshare <code>index_zh_a_hist</code> 补取，落 <code>index_ext_daily</code>；全收益序列取自 <code>index_full_return_daily</code>。</li>
</ul>

<h3>附录 B　统计口径对照</h3>
<div class="card"><table class="wide"><tbody>
<tr><td>漂移 vs 日再平衡</td><td>主表用漂移口径（可执行），差额 {D_REB:.2f} pp 已单列</td></tr>
<tr><td>等权 vs 市值加权</td><td>同一只池，仅权重不同，差额 {D_EW_CW:.2f} pp</td></tr>
<tr><td>价格 vs 全收益</td><td>微盘为前复权（含分红）；中证2000／中证1000 为价格口径，差异在 1% 量级</td></tr>
<tr><td>存量 vs 重算</td><td>见 §3.1，池重合度 {rev['微盘400']['overlap_mean']}%，不可互相替代</td></tr>
<tr><td>中证2000 数据缺口</td><td>本地自 2018-08-15 起 1,995 行；本次补取至 2013-12-31 起 3,124 行</td></tr>
</tbody></table></div>

<h3>附录 C　未核实与证据不足清单</h3>
<ul>
<li><b>幸存者偏差幅度</b>：234 只退市股无股本数据，无法量化。方向为高估，幅度未知。</li>
<li><b>万得 868008 的官方编制文件</b>：仅取到第三方研究报告的转述（剔除 ST 与首日连板、取总市值后 400 名、等权），
未取得万得官网原文或 PDF 编号。</li>
<li><b>微盘股主动基金的业绩</b>：本仓库无公募基金持仓与净值数据，未做对照。</li>
<li><b>交易成本</b>：未做冲击成本实测，仅按换手率估算量级。</li>
<li><b>估值分位</b>：<code>fundamental_indicator</code> 无日期索引，全表聚合耗时过长，本轮未计算微盘池的 PE／PB 分位。</li>
</ul>

<h3>附录 D　修正记录（证伪记录）</h3>
<ul>
<li>2026-11-09：发现存量序列不可复现（池重合度 {rev['微盘400']['overlap_mean']}%），且丢失分红送转调整。全量重算并覆盖线上表，存量备份为 <code>*_v1</code>。</li>
<li>2026-11-09：确认「日度等权再平衡」口径高估 {D_REB:.2f} pp/年，报告改用漂移口径。</li>
<li>2026-11-09：证伪「超额来自等权再平衡」。同一只池改市值加权仅降 {D_EW_CW:.2f} pp，超额主要来自选样。</li>
<li>2026-11-09：证伪「中证2000 可作为微盘的可投资代理」。同期年化差 {D_2000:.2f} pp，两者不是同一资产。</li>
</ul>

<h3>附录 E　数据源质量与缺口声明</h3>
<ul>
<li><code>daily_kline_adj</code> 是视图，底层为 <code>daily_kline</code>。<code>close</code>／<code>adj_close</code>／<code>ex_close</code> 三列均为不复权，仅 <code>lxr_fc_close</code> 为前复权。本次已改用后者。</li>
<li><code>stock_basic.delisted_date</code> 全表为空，退市过滤从未生效。</li>
<li><code>index_full_return_daily</code> 仅覆盖 18 个标的，中证2000／中证1000 无全收益序列。</li>
<li>新浪美股 qfq 序列在 2008 与 2002 段存在单日 ±100% 以上坏点，国际对照只报共同窗口，不外推更早历史。</li>
</ul>

<div class="ft">
<p>生成器：<code>docs/report/微盘股指数研究报告/make_report.py</code>　·　数字层：<code>scripts/microcap_research.py</code>　·　快照 sha1 前缀：{SHA}</p>
<p>数据截止 {M['data_asof']}　·　生成时间 {M['generated_at']}　·　全部数字由脚本现算，未手工誊写。</p>
<p><b>免责声明</b>：本报告为研究方法演示与数据分析，不构成投资建议。文中不含目标价、仓位建议与买卖时点推荐。
自建序列含已知的幸存者偏差与轻微前视，收益数字应视为上限。历史表现不代表未来。</p>
</div>
</div>
<script>
try{{var t=localStorage.getItem('mc_theme');if(t)document.documentElement.setAttribute('data-theme',t);}}catch(e){{/* noop */}}
</script>
</body>
</html>
'''

open(OUT, 'w', encoding='utf-8').write(HTML)
print('report ->', OUT)
print('bytes =', len(HTML.encode('utf-8')))
