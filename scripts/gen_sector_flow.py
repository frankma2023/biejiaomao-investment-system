# -*- coding: utf-8 -*-
"""各板块 10 日资金与两融（固定页面）。

输出 web/sector-flow/index.html：行业板块（一级 + 二级）主表 + 主题/策略榜，
每行含 10 日涨跌、成交额、量比、融资余额、10 日融资净买入、余额变化、
相对全市场变化、资金流评分（10/65/250 日）。

数据全部来自本地库 data/lixinger.db，可重复运行。
"""
from __future__ import annotations

import io
import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'data', 'lixinger.db')
CFG = os.path.join(ROOT, 'config', 'index_style.yaml')
OUT_DIR = os.path.join(ROOT, 'web', 'sector-flow')
OUT = os.path.join(OUT_DIR, 'index.html')

WIN = 10                      # 观察窗口（交易日）
CAT_LABEL = {
    'sector_l1': '行业·一级',
    'sector_l2': '行业·二级',
    'thematic': '主题',
    'strategy': '策略',
    'market': '宽基',
}


# ---------------------------------------------------------------- 工具
def q(con, sql, args=()):
    return con.execute(sql, args).fetchall()


def fnum(x, nd=1, sign=False):
    if x is None:
        return '—'
    s = ('%+.' + str(nd) + 'f') % x if sign else ('%.' + str(nd) + 'f') % x
    return s


def cell(x, nd=1, sign=True, unit=''):
    """带涨跌色的数字单元格（正红负绿，A 股口径）。"""
    if x is None:
        return '<td class="num" data-v="-1e9">—</td>'
    cls = 'up' if x > 0 else ('down' if x < 0 else 'flat')
    return ('<td class="num %s" data-v="%s">%s%s</td>'
            % (cls, '%.4f' % x, fnum(x, nd, sign), unit))


def plain(x, nd=1, sign=False, unit=''):
    if x is None:
        return '<td class="num" data-v="-1e9">—</td>'
    return ('<td class="num" data-v="%s">%s%s</td>'
            % ('%.4f' % x, fnum(x, nd, sign), unit))


def load_categories():
    import yaml
    with io.open(CFG, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    out = {}
    for key in ('sector_l1', 'sector_l2', 'thematic', 'strategy', 'market'):
        items = (cfg.get('categories') or {}).get(key) or []
        out[key] = [(str(it['code']), it['name']) for it in items]
    return out


def trade_dates(con, n):
    rows = q(con, "SELECT DISTINCT date FROM daily_kline_adj "
                  "ORDER BY date DESC LIMIT ?", (n,))
    return sorted(r[0] for r in rows)


def margin_maps(con, dates):
    """逐日 {code: (融资余额, 当日融资净买入, 融券余额)}（亿元）。"""
    maps, total = {}, {}
    for d in dates:
        m = {}
        tf = tn = ts = 0.0
        for r in q(con, "SELECT stock_code, fb_balance, net_buy_d1, sb_balance "
                        "FROM daily_review_margin WHERE date=?", (d,)):
            fb = (r[1] or 0) / 1e8
            nb = (r[2] or 0) / 1e8
            sb = (r[3] or 0) / 1e8
            m[r[0]] = (fb, nb, sb)
            tf += fb
            tn += nb
            ts += sb
        maps[d] = m
        total[d] = (tf, tn, ts)
    return maps, total


def cons_map(con):
    """每个指数最新一期的成分股列表。"""
    out = {}
    rows = q(con, "SELECT index_code, MAX(date) FROM index_constituents "
                  "GROUP BY index_code")
    latest = {r[0]: r[1] for r in rows}
    for code, d in latest.items():
        out[code] = [r[0] for r in q(
            con, "SELECT stock_code FROM index_constituents "
                 "WHERE index_code=? AND date=?", (code, d))]
    return out


def index_series(con, code, dates):
    rows = q(con, "SELECT date, close, amount FROM index_daily_kline "
                  "WHERE stock_code=? AND kline_type='normal' "
                  "AND date BETWEEN ? AND ? ORDER BY date",
             (code, dates[0], dates[-1]))
    return {r[0]: (r[1], r[2]) for r in rows}


def flow_scores(con, date):
    out = {}
    for r in q(con, "SELECT stock_code, score_10d, score_65d, score_250d, "
                    "mfv_10d, flow_label FROM index_capital_flow_daily "
                    "WHERE date=?", (date,)):
        out[r[0]] = (r[1], r[2], r[3], r[4], r[5])
    return out


# ---------------------------------------------------------------- 计算
def build(con, dates):
    cats = load_categories()
    mmaps, mtot = margin_maps(con, dates)
    cmap = cons_map(con)
    flows = flow_scores(con, dates[-1])
    mkt_fb = mtot[dates[0]][0], mtot[dates[-1]][0]
    mkt_chg = (mkt_fb[1] / mkt_fb[0] - 1) * 100 if mkt_fb[0] else None
    mkt_net = sum(mtot[d][1] for d in dates)

    rows, stale = [], []
    for key in ('sector_l1', 'sector_l2', 'thematic', 'strategy', 'market'):
        for code, name in cats[key]:
            kl = index_series(con, code, dates)
            if not kl:
                stale.append(name)
                continue
            ds = [d for d in dates if d in kl]
            if len(ds) < 3:
                stale.append(name)
                continue
            if ds[-1] != dates[-1]:
                # 末个交易日缺数据 → 10 日窗口不成立，不入表（否则涨跌和量比都是错的口径）
                stale.append(name)
                continue
            closes = [kl[d][0] for d in ds]
            amts = [kl[d][1] or 0 for d in ds]
            chg = (closes[-1] / closes[0] - 1) * 100 if closes[0] else None
            amt_sum = sum(amts) / 1e8
            k = max(2, len(amts) // 2)
            early = sum(amts[:k]) / k
            late = sum(amts[-k:]) / k
            vr = (late / early) if early else None

            members = cmap.get(code) or []
            fbs, nbs, sbs = [], [], []
            for d in ds:
                mm = mmaps[d]
                fb = nb = sb = 0.0
                for c in members:
                    t = mm.get(c)
                    if t:
                        fb += t[0]
                        nb += t[1]
                        sb += t[2]
                fbs.append(fb)
                nbs.append(nb)
                sbs.append(sb)
            fb0, fb1 = fbs[0], fbs[-1]
            fb_chg = (fb1 / fb0 - 1) * 100 if fb0 else None
            net10 = sum(nbs)
            if fb1 <= 0:                      # 无 A 股成分（港股/海外指数），两融无意义
                fb1, fb_chg, net10 = None, None, None
                rel = None
            else:
                rel = (fb_chg - mkt_chg) if (fb_chg is not None and mkt_chg is not None) else None
            sc = flows.get(code)
            rows.append({
                'code': code, 'name': name, 'cat': CAT_LABEL.get(key, key),
                'key': key, 'chg': chg, 'amt': amt_sum, 'vr': vr,
                'fb': fb1, 'net10': net10, 'fb_chg': fb_chg,
                'rel': rel,
                's10': sc[0] if sc else None, 's65': sc[1] if sc else None,
                's250': sc[2] if sc else None,
                'n_mem': len(members),
            })
    rows.sort(key=lambda r: (r['chg'] if r['chg'] is not None else -99), reverse=True)
    return rows, mkt_fb, mkt_chg, mkt_net, dates, stale


# ---------------------------------------------------------------- 解读
def narrate(rows, mkt_fb, mkt_chg, mkt_net):
    ind = [r for r in rows if r['key'] in ('sector_l1', 'sector_l2')
           and r['net10'] is not None]
    pos = [r for r in ind if r['net10'] > 0]
    up = [r for r in ind if (r['chg'] or 0) > 0]
    best = max(ind, key=lambda r: r['net10'])
    worst = min(ind, key=lambda r: r['net10'])
    out = []

    same = (len(pos) / max(len(ind), 1) > 0.5 and len(up) / max(len(ind), 1) > 0.5) or \
           (len(pos) / max(len(ind), 1) < 0.5 and len(up) / max(len(ind), 1) < 0.5)
    out.append(
        '全市场融资余额 %.0f 亿元，10 个交易日变化 %s%%，融资净买入合计 %s 亿元。'
        '行业板块 %d 个，其中融资余额正增长 %d 个、指数上涨 %d 个，'
        '杠杆资金与价格%s。'
        % (mkt_fb[1], fnum(mkt_chg, 2, True), fnum(mkt_net, 0, True),
           len(ind), len(pos), len(up), '同向' if same else '不同步'))

    if best['net10'] > 0 > worst['net10']:
        out.append(
            '融资净买入为正的只有 %s（%s 亿元，余额 %s%%），减仓最多的是 %s'
            '（%s 亿元，余额 %s%%），两端相差 %.1f 亿元。'
            '这是板块之间的存量切换，不是整体加杠杆。'
            % (best['name'], fnum(best['net10'], 1, True), fnum(best['fb_chg'], 1, True),
               worst['name'], fnum(worst['net10'], 1, True), fnum(worst['fb_chg'], 1, True),
               best['net10'] - worst['net10']))
    else:
        out.append(
            '全部行业板块都是融资净流出。流出最少的是 %s（%s 亿元，余额 %s%%），'
            '最多的是 %s（%s 亿元，余额 %s%%），两端相差 %.1f 亿元。'
            % (best['name'], fnum(best['net10'], 1, True), fnum(best['fb_chg'], 1, True),
               worst['name'], fnum(worst['net10'], 1, True), fnum(worst['fb_chg'], 1, True),
               abs(best['net10'] - worst['net10'])))

    vr_rows = [r for r in ind if r['vr'] is not None]
    if vr_rows:
        shr = min(vr_rows, key=lambda r: r['vr'])
        exp_ = max(vr_rows, key=lambda r: r['vr'])
        if exp_['vr'] < 1.0:
            vol_txt = ('所有行业板块都在缩量：缩得最狠的是 %s（后 5 日均额为前 5 日的 %.2f 倍），'
                       '相对最活跃的是 %s（%.2f 倍），也没有放大。'
                       % (shr['name'], shr['vr'], exp_['name'], exp_['vr']))
        else:
            vol_txt = ('缩量最深的是 %s（%.2f 倍），放量最多的是 %s（%.2f 倍）。'
                       % (shr['name'], shr['vr'], exp_['name'], exp_['vr']))
        if shr['fb_chg'] is not None and shr['fb_chg'] < -2:
            judge = '缩量叠加融资余额下降，这是主动去杠杆。'
        else:
            judge = '缩量而融资余额基本不动，这是被动套住，不是主动离场。'
        out.append(vol_txt + judge)

    strong = sorted([r for r in ind if r['rel'] is not None], key=lambda r: -r['rel'])[:3]
    weak = sorted([r for r in ind if r['rel'] is not None], key=lambda r: r['rel'])[:3]
    if strong and weak:
        out.append(
            '相对全市场（%s%%），融资余额最抗跌的是 %s；去杠杆最快的是 %s。'
            % (fnum(mkt_chg, 2, True),
               '、'.join('%s %s 个百分点' % (r['name'], fnum(r['rel'], 1, True)) for r in strong),
               '、'.join('%s %s 个百分点' % (r['name'], fnum(r['rel'], 1, True)) for r in weak)))

    no_flow = len([r for r in rows if r['s10'] is None])
    out.append('资金流评分本地只覆盖 %d 个板块，其余 %d 个板块该列显示「—」，'
               '融资与成交额列不受影响。' % (len(rows) - no_flow, no_flow))
    return out


# ---------------------------------------------------------------- HTML
CSS = """
:root{--up:#ef4444;--down:#10b981}
body{background:#0e0e12;color:#d4d4d8;font-family:var(--font-body,system-ui)}
.app-container{max-width:1440px;margin:0 auto;padding:18px 20px 40px}
h1{font-family:var(--font-display,serif);font-size:26px;font-weight:400;color:#f4f4f5;margin:8px 0 6px}
.sub{font-size:12px;color:#8b8b90;margin-bottom:14px;line-height:1.7}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:12px 0 18px}
.kpi{background:rgba(26,26,31,.62);border:1px solid rgba(255,255,255,.07);border-radius:14px;padding:12px 14px}
.kpi .lb{font-size:11px;color:#8b8b90;margin-bottom:6px}
.kpi .vv{font-size:22px;font-weight:500;color:#f4f4f5;font-family:var(--font-num,monospace)}
.kpi .ft{font-size:11px;color:#71717a;margin-top:6px}
h2{font-family:var(--font-display,serif);font-size:18px;font-weight:400;color:#e4e4e7;margin:22px 0 4px;
   padding-left:10px;border-left:3px solid #f59e0b}
.note{font-size:12px;color:#8b8b90;line-height:1.75;margin:6px 0 10px}
.read{background:rgba(127,127,127,.075);border-left:3px solid #f59e0b;border-radius:0 10px 10px 0;
      padding:12px 16px;margin:12px 0 6px}
.read p{font-size:13px;line-height:1.85;color:#c9c9ce;margin:0 0 8px}
.read p:last-child{margin-bottom:0}
.read .lb{font-size:11px;color:#f59e0b;letter-spacing:.08em;margin-bottom:6px}
.tw{overflow-x:auto;border:1px solid rgba(255,255,255,.07);border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:12px}
th{position:sticky;top:0;background:#17171c;color:#a1a1aa;font-weight:500;padding:9px 8px;
   text-align:center;white-space:nowrap;border-bottom:1px solid rgba(255,255,255,.09);cursor:pointer}
th:hover{color:#f59e0b}
td{padding:7px 8px;text-align:center;border-bottom:1px solid rgba(255,255,255,.04);white-space:nowrap}
td.l{text-align:left}
tbody tr:hover{background:rgba(245,158,11,.05)}
.num{font-family:var(--font-num,monospace)}
.up{color:#ef4444}.down{color:#10b981}.flat{color:#8b8b90}
.tag{display:inline-block;font-size:10px;padding:1px 6px;border-radius:6px;
     background:rgba(255,255,255,.06);color:#a1a1aa}
.filt{width:220px;padding:6px 10px;margin:4px 0 8px;border-radius:8px;font-size:12px;
      background:rgba(26,26,31,.6);border:1px solid rgba(255,255,255,.09);color:#e4e4e7;outline:none}
.filt:focus{border-color:#f59e0b}
.foot{font-size:11px;color:#52525b;margin-top:20px;line-height:1.8}
"""

JS = """
(function(){
  function num(td){ var v = td.getAttribute('data-v'); return v === null ? null : parseFloat(v); }
  document.querySelectorAll('table.sortable').forEach(function(tb){
    tb.querySelectorAll('th').forEach(function(th, i){
      th.addEventListener('click', function(){
        var body = tb.tBodies[0];
        var rows = Array.prototype.slice.call(body.rows);
        var dir = th.getAttribute('data-dir') === 'asc' ? -1 : 1;
        tb.querySelectorAll('th').forEach(function(x){ x.removeAttribute('data-dir');
          x.textContent = x.textContent.replace(/\\s*[▲▼]$/, ''); });
        th.setAttribute('data-dir', dir === 1 ? 'desc' : 'asc');
        th.textContent = th.textContent.replace(/\\s*[▲▼]$/, '') + (dir === 1 ? ' ▼' : ' ▲');
        rows.sort(function(a, b){
          var x = num(a.cells[i]), y = num(b.cells[i]);
          if (x === null || y === null) { return 0; }
          return (x - y) * dir;
        });
        rows.forEach(function(r){ body.appendChild(r); });
      });
    });
  });
  document.querySelectorAll('.filt').forEach(function(inp){
    inp.addEventListener('input', function(){
      var v = inp.value.trim();
      var tb = document.getElementById(inp.getAttribute('data-for'));
      if (!tb) { return; }
      Array.prototype.forEach.call(tb.tBodies[0].rows, function(r){
        r.style.display = (!v || r.cells[0].textContent.indexOf(v) >= 0) ? '' : 'none';
      });
    });
  });
})();
"""

HEAD = """<!DOCTYPE html>
<html lang="zh-CN" class="dark">
<head>
<meta charset="UTF-8">
<meta name="color-scheme" content="dark">
<link rel="icon" href="../images/favicon.svg" type="image/svg+xml">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>板块资金与两融</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;500&family=Inter:wght@300;400;500&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="../shared/css/hanako-glass.css">
<script src="../shared/js/nav.js"></script>
<style>%s</style>
</head>
<body>
<canvas id="bg-canvas"></canvas>
<div class="app-container">
<nav id="top-nav"></nav>
"""

TAIL = """<div class="foot">数据来自本地库 data/lixinger.db，由 scripts/gen_sector_flow.py 生成于 %s。
本页只做数据整理，不构成投资建议。</div>
</div>
<script>%s</script>
<script>Nav.init({brandIcon:"\\ud83d\\udcb0",brandText:"板块资金",currentPage:"sector-flow"})</script>
</body>
</html>
"""


def table(rows, cols, tid, height_note=''):
    """cols: [(表头, 取值函数, 是否可排序)]"""
    head = ''.join('<th>%s</th>' % c[0] for c in cols)
    body = []
    for r in rows:
        tds = []
        for c in cols:
            tds.append(c[1](r))
        body.append('<tr>%s</tr>' % ''.join(tds))
    return ('<div class="tw"><table class="sortable" id="%s"><thead><tr>%s</tr></thead>'
            '<tbody>%s</tbody></table></div>%s' % (tid, head, ''.join(body), height_note))


def name_cell(r):
    return ('<td class="l">%s <span class="tag">%s</span></td>'
            % (r['name'], r['cat']))


def code_cell(r):
    return '<td class="num" data-v="%s">%s</td>' % (r['code'], r['code'])


def render(rows, mkt_fb, mkt_chg, mkt_net, dates, stale):
    ind = [r for r in rows if r['key'] in ('sector_l1', 'sector_l2')
           and r['net10'] is not None]
    theme = [r for r in rows if r['key'] in ('thematic', 'strategy')
             and r['net10'] is not None]
    pos = [r for r in ind if r['net10'] > 0]

    cols = [
        ('板块', name_cell, False),
        ('代码', code_cell, False),
        ('10日涨跌', lambda r: cell(r['chg'], 2, True, '%'), True),
        ('成交额(亿)', lambda r: plain(r['amt'], 0), True),
        ('量比(后5/前5)', lambda r: plain(r['vr'], 2), True),
        ('融资余额(亿)', lambda r: plain(r['fb'], 0), True),
        ('10日净买入(亿)', lambda r: cell(r['net10'], 1, True), True),
        ('余额变化', lambda r: cell(r['fb_chg'], 2, True, '%'), True),
        ('相对全市场', lambda r: cell(r['rel'], 1, True, 'pp'), True),
        ('评分10', lambda r: plain(r['s10'], 0), True),
        ('评分65', lambda r: plain(r['s65'], 0), True),
        ('评分250', lambda r: plain(r['s250'], 0), True),
    ]

    kpi = (
        '<div class="kpi-grid">'
        '<div class="kpi"><div class="lb">全市场融资余额</div><div class="vv">%s 亿</div>'
        '<div class="ft">10 日 %s%%｜净买入 %s 亿</div></div>'
        '<div class="kpi"><div class="lb">行业板块融资余额正增长</div><div class="vv">%d / %d</div>'
        '<div class="ft">%d 个板块上涨</div></div>'
        '<div class="kpi"><div class="lb">融资加仓最多</div><div class="vv">%s</div>'
        '<div class="ft">%s 亿元｜余额 %s%%</div></div>'
        '<div class="kpi"><div class="lb">融资减仓最多</div><div class="vv">%s</div>'
        '<div class="ft">%s 亿元｜余额 %s%%</div></div>'
        '</div>'
        % (fnum(mkt_fb[1], 0), fnum(mkt_chg, 2, True), fnum(mkt_net, 0),
           len(pos), len(ind), len([r for r in ind if (r['chg'] or 0) > 0]),
           max(ind, key=lambda r: r['net10'])['name'],
           fnum(max(ind, key=lambda r: r['net10'])['net10'], 1, True),
           fnum(max(ind, key=lambda r: r['net10'])['fb_chg'], 1, True),
           min(ind, key=lambda r: r['net10'])['name'],
           fnum(min(ind, key=lambda r: r['net10'])['net10'], 1, True),
           fnum(min(ind, key=lambda r: r['net10'])['fb_chg'], 1, True))
    )

    read = ''.join('<p>%s</p>' % p for p in narrate(rows, mkt_fb, mkt_chg, mkt_net))

    top = sorted(theme, key=lambda r: r['net10'], reverse=True)[:15]
    bot = sorted(theme, key=lambda r: r['net10'])[:15]
    theme_rows = top + bot
    stale_txt = '、'.join(stale[:12]) + ('等 %d 个' % len(stale) if len(stale) > 12 else '') if stale else '无'

    return ''.join([
        '<h1>板块资金与两融</h1>',
        '<div class="sub">数据截至 %s 收盘。窗口为最近 %d 个交易日（%s ~ %s）。'
        '融资与融券按各指数最新成分股（快照 2026-05-01）汇总。'
        '红涨绿跌：涨跌、资金流入为红，下跌、流出为绿。</div>'
        % (dates[-1], WIN, dates[0], dates[-1]),
        kpi,
        '<h2>一、行业板块：10 日资金与两融</h2>',
        '<div class="note">一级行业 11 个、二级行业 45 个，按 10 日涨跌排序。'
        '点表头可切换排序。量比 = 后 5 个交易日日均成交额 ÷ 前 5 个交易日日均成交额，'
        '小于 1 为缩量。「相对全市场」= 本板块融资余额变化 − 全市场变化。</div>',
        '<input class="filt" data-for="t-ind" placeholder="筛选板块名称，例如 医药 / 半导体">',
        table(ind, cols, 't-ind'),
        '<div class="read"><div class="lb">解读</div>%s</div>' % read,
        '<h2>二、主题与策略：10 日融资净买入前 15</h2>',
        '<div class="note">主题（%d 个有效）与策略（%d 个有效）板块中融资净买入最高的 15 个。'
        '这些板块多为窄口径指数，融资余额基数小，单只个股就能左右方向，看趋势不看幅度。</div>'
        % (len([r for r in rows if r['key'] == 'thematic' and r['net10'] is not None]),
           len([r for r in rows if r['key'] == 'strategy' and r['net10'] is not None])),
        '<input class="filt" data-for="t-theme" placeholder="筛选板块名称">',
        table(top, cols, 't-theme'),
        '<h2>三、主题与策略：10 日融资净买入后 15</h2>',
        '<div class="note">同一批板块里资金撤得最多的 15 个，与上一张表对照看，'
        '能看出存量资金从哪一类主题流向哪一类。</div>',
        '<input class="filt" data-for="t-theme2" placeholder="筛选板块名称">',
        table(bot, cols, 't-theme2'),
        '<h2>四、指标口径与数据缺口</h2>',
        '<div class="note">'
        '<b>融资余额</b>：`daily_review_margin.fb_balance` 按成分股加总，单位为亿元；'
        '与交易所公布的两融口径可能存在成分股调整带来的差异。<br>'
        '<b>10 日融资净买入</b>：窗口内每个交易日 `net_buy_d1` 之和。<br>'
        '<b>余额变化</b>：窗口末融资余额 ÷ 窗口首融资余额 − 1；'
        '「相对全市场」为本值减全市场同口径值。<br>'
        '<b>量比</b>：窗口后半段日均成交额 ÷ 前半段日均成交额，只用指数自身的成交额。<br>'
        '<b>资金流评分</b>：本地 `index_capital_flow_daily` 的 10/65/250 日综合分，'
        '由 MFV（日内收盘位置 × 成交量）、量比、上涨量占比、金额增速、价量相关性合成，'
        '是强度代理，<b>不是主力资金净流入金额</b>；系统内没有逐笔委托或 L2 数据。<br>'
        '<b>覆盖缺口</b>：一级行业、策略、宽基板块没有资金流评分（本地只覆盖二级行业与主题），'
        '这三类的评分列显示「—」。以下板块因最后一个交易日缺 K 线或没有成分股数据，未入表：'
        '%s。'
        '</div>' % stale_txt,
        '<h2>五、历史</h2>',
        '<div class="note">本页每日随 `scripts/daily_update.py` 重算覆盖，只保留最新一期。'
        '历史日期可查 `web/market-logic/&lt;日期&gt;/index.html` 的行业与两融章节。</div>',
    ])


def main():
    con = sqlite3.connect('file:%s?mode=ro' % DB.replace('\\', '/'), uri=True)
    con.row_factory = sqlite3.Row
    dates = trade_dates(con, WIN + 1)
    rows, mkt_fb, mkt_chg, mkt_net, ds, stale = build(con, dates)
    body = render(rows, mkt_fb, mkt_chg, mkt_net, ds, stale)
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    html = HEAD % CSS + body + TAIL % (stamp, JS)
    os.makedirs(OUT_DIR, exist_ok=True)
    with io.open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print('已生成 %s（%d 个板块｜%d 字节）' % (OUT, len(rows), len(html)))


if __name__ == '__main__':
    sys.exit(main())
