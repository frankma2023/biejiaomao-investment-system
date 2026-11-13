#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「当日大盘交易总结」页面（market-logic）。

无人值守版：全部事实从本地库现算，叙事按规则拼装，不依赖人工注解。
与 gen_market_logic_report.py 的区别：
  · 后者需要手写 web/market-logic/data/<DATE>.json 注解，只能人工跑；
  · 本脚本零人工输入，可挂在 daily_update.py 末尾每日执行。

用法:
  python scripts/gen_daily_market_summary.py                  # 最新交易日
  python scripts/gen_daily_market_summary.py --date 2026-11-12

输出:
  web/market-logic/index.html          当日总结（用户入口）
  web/market-logic/<DATE>/index.html   同日归档
  web/market-logic/index.json          机器可读索引
"""
import argparse
import html as H
import json
import os
import re
import sqlite3
from datetime import datetime

import yaml

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')
GZH_DB = os.path.join(PROJECT, 'data', 'gzh.db')
ROOT = os.path.join(PROJECT, 'web', 'market-logic')
STYLE_YAML = os.path.join(PROJECT, 'config', 'index_style.yaml')

CSS = open(os.path.join(PROJECT, '.agents', 'skills', '_shared',
                        'report-style.css'), encoding='utf-8').read()

JS = """
(function(){var K='ml-theme';
try{var s=localStorage.getItem(K);if(s)document.documentElement.dataset.theme=s;}catch(e){}
window.toggleTheme=function(){var c=document.documentElement.dataset.theme==='dark'?'light':'dark';
document.documentElement.dataset.theme=c;try{localStorage.setItem(K,c);}catch(e){}};})();
"""

BROAD = [('000001', '上证指数'), ('399001', '深证成指'), ('399006', '创业板指'),
         ('000688', '科创50'), ('000300', '沪深300'), ('000905', '中证500'),
         ('000852', '中证1000'), ('000985', '中证全指'), ('000510', '中证A500'),
         ('000922', '中证红利'), ('931848', '800红利低波'), ('932000', '中证2000')]

# 辅助指标（参与健康评分）的读数口径说明
SUB_METRICS = [
    ('ma50_above', '50 日均线上方占比', '%', '越高越健康'),
    ('hl_ratio', '新高/新低比', '倍', '>1 多头占优'),
    ('ad_ratio', '涨跌家数比（AD）', '倍', '>1 上涨家数多'),
    ('vol_breakout', '放量突破数', '只', '越多做多动能越强'),
    ('margin_5d', '融资余额 5 日变化', '%', '转为下降是去杠杆信号'),
    ('sector_rot', '板块轮动强度', '分', '高=轮动活跃'),
    ('fear_greed', '恐贪读数', '分', '0 极恐 / 100 极贪'),
]

SELL_SIGNALS = [
    ('dist_days_25d', 'dist_score', '抛盘日（25 日内）'),
    ('ftd_failed', 'ftd_score', '追盘日失效'),
    ('leader_top_fallen', 'leader_score', '龙头见顶'),
    ('junk_rally', 'junk_score', '垃圾股补涨'),
    ('divergence_count', 'divergence_score', '指数背离'),
    ('ma50_broken', 'ma50_score', '跌破 50 日线'),
    ('ma200_broken', 'ma200_score', '跌破 200 日线'),
    ('death_cross', 'death_cross_score', '均线死亡交叉'),
    ('ad_ratio_low5', 'ad_low5_score', 'AD 低位（5 日）'),
    ('ad_ratio_crash', 'ad_crash_score', 'AD 骤降'),
    ('hlnl_ratio_low', 'hlnl_score', '新高新低比走低'),
    ('vol_dry_up', 'vol_dry_score', '上涨无量'),
]

KEYWORDS = ['科技', '半导体', '芯片', '算力', 'AI', '军工', '新能源', '光伏',
            '白酒', '医药', '证券', '银行', '煤炭', '化工', '黄金', '红利',
            '港股', '美股', '债', '汇率', '地产', '汽车']

BULL_WORDS = ['涨', '反弹', '新高', '走强', '上行', '上攻', '拉升', '回暖']
BEAR_WORDS = ['跌', '回落', '走弱', '下调', '下跌', '跳水', '走低', '杀跌', '调整']


# ── 小工具 ────────────────────────────────────────────
def esc(s):
    return H.escape(str(s)) if s is not None else ''


def pct(v, nd=2):
    return '—' if v is None else f'{v:+.{nd}f}%'


def num(v, nd=0):
    return '—' if v is None else f'{v:,.{nd}f}'


def cls(v):
    if v is None:
        return ''
    return 'rise' if v >= 0 else 'fall'


def rating_label(score, rating):
    if rating in ('A', 'B'):
        return '偏强'
    if rating == 'C':
        return '中性'
    if rating == 'D':
        return '偏弱'
    return '很弱' if rating == 'E' else '—'


def sparkline(vals, w=430, h=96, color='var(--blue)'):
    """极简折线：只用 .chart 的样式类，不引外部库。"""
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return '<p style="font-size:12px;color:var(--mut)">序列不足，无法作图</p>'
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    pad = 6
    pts = []
    for i, v in enumerate(vals):
        x = pad + i * (w - 2 * pad) / (len(vals) - 1)
        y = h - pad - (v - lo) / rng * (h - 2 * pad)
        pts.append(f'{x:.1f},{y:.1f}')
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<polyline class="ln" style="stroke:{color}" points="{" ".join(pts)}"/>'
            f'<text class="ax" x="{pad}" y="{pad + 8}">{hi:,.0f}</text>'
            f'<text class="ax" x="{pad}" y="{h - 1}">{lo:,.0f}</text>'
            f'</svg>')


def strip_tags(s):
    """只把块级标签换成换行，行内标签直接删除，避免段落被切成单句。"""
    s = re.sub(r'<script.*?</script>', ' ', s or '', flags=re.S | re.I)
    s = re.sub(r'<style.*?</style>', ' ', s, flags=re.S | re.I)
    s = re.sub(r'</(p|div|h[1-6]|li|blockquote|tr)\s*>', '\n', s, flags=re.I)
    s = re.sub(r'<br\s*/?>', '\n', s, flags=re.I)
    s = re.sub(r'<[^>]+>', '', s)
    s = H.unescape(s)
    s = re.sub(r'[ \t\u3000]+', ' ', s)
    s = re.sub(r' *\n *', '\n', s)
    s = re.sub(r'\n{2,}', '\n', s)
    return s.strip()


# ── 数据层 ────────────────────────────────────────────
def load_style():
    cfg = yaml.safe_load(open(STYLE_YAML, encoding='utf-8'))
    cats = cfg['categories']
    names, etf_codes = {}, set()
    for key in ('market', 'sector_l1', 'sector_l2', 'thematic', 'strategy', 'etf'):
        for it in cats.get(key, []) or []:
            names.setdefault(it['code'], it['name'])
            if key == 'etf':
                etf_codes.add(it['code'])
    return names, cats.get('sector_l1', []) or [], etf_codes


def q1(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return r


def gather(con, date):
    names, sector_l1, etf_codes = load_style()
    d = {'date': date}

    # 1) 宽度：涨跌家数 / 成交额 / 平均涨幅
    d['breadth'] = q1(con, """
        SELECT SUM(CASE WHEN change_pct>0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN change_pct<0 THEN 1 ELSE 0 END),
               COUNT(*), AVG(change_pct)*100, SUM(amount)/1e8
          FROM daily_kline_adj WHERE date=?""", (date,)) or (None,) * 5

    # 2) 指数涨跌：库里的 change 字段只保留两位小数（0.02 / -0.01），
    #    低于 0.5% 的变动会被抹成 0.00%，故一律用收盘价对比前一交易日重算。
    prev_date = (q1(con, "SELECT MAX(date) FROM index_daily_kline "
                       "WHERE kline_type='normal' AND date<?", (date,)) or [None])[0]
    close_now, close_prev = {}, {}
    if prev_date:
        for dt, mp in ((date, close_now), (prev_date, close_prev)):
            for code, cl in con.execute(
                    "SELECT stock_code, close FROM index_daily_kline "
                    "WHERE date=? AND kline_type='normal'", (dt,)):
                if cl:
                    mp[code] = cl

    def chg_of(code):
        a, b = close_now.get(code), close_prev.get(code)
        if not a or not b:
            return None
        return (a / b - 1) * 100

    # 2.1) 主要指数
    broad = []
    for code, nm in BROAD:
        v = chg_of(code)
        if v is not None:
            broad.append((nm, code, v))
    d['broad'] = broad

    # 3) 指数池涨跌（用于关键词对照）：只取风格配置里的指数，剔除 ETF 与脏数据
    pool = []
    for code in names:
        if code in etf_codes:
            continue
        v = chg_of(code)
        if v is None or abs(v) > 12:   # 单日涨跌幅超过 12% 的指数行视为脏数据
            continue
        pool.append((names[code], v))
    seen, ranked = set(), []
    for nm, chg in sorted(pool, key=lambda x: -x[1]):
        if nm in seen:
            continue
        seen.add(nm)
        ranked.append((nm, chg))
    d['ranked'] = ranked

    # 4) 大盘健康分
    d['health'] = q1(con, "SELECT * FROM market_health_daily WHERE date=?", (date,))

    # 5) 卖出评分
    d['sell'] = q1(con, "SELECT * FROM market_sell_score_daily WHERE date=?", (date,))

    # 6) 行业分组健康分
    d['sectors'] = con.execute(
        "SELECT group_name, group_label, total_score, rating, position, stocks_count "
        "FROM market_health_sector_daily WHERE date=? ORDER BY total_score DESC",
        (date,)).fetchall()

    # 7) 资金活跃度 TOP10
    flows = con.execute("SELECT * FROM index_capital_flow_daily WHERE date=?", (date,)).fetchall()
    d['flow'] = {}
    for key, sk in (('10d', 'score_10d'), ('65d', 'score_65d'), ('250d', 'score_250d')):
        top = sorted(flows, key=lambda r: r[sk] or 0, reverse=True)[:10]
        d['flow'][key] = [{'code': r['stock_code'],
                           'name': names.get(r['stock_code'], r['stock_code']),
                           'score': r[sk], 'label': r['flow_label'] or '000'} for r in top]

    # 8) 两融聚合趋势（daily_review_margin 与龙虎榜同源，日期一致）
    tr = con.execute("""
        SELECT date, SUM(fb_balance)/1e8, SUM(sb_balance)/1e8, SUM(net_buy_d1)/1e8
          FROM daily_review_margin WHERE date<=? GROUP BY date ORDER BY date DESC
         LIMIT 61""", (date,)).fetchall()
    tr = list(reversed(tr))
    d['margin_trend'] = tr
    d['margin'] = ({'date': tr[-1][0], 'fb': tr[-1][1], 'sb': tr[-1][2], 'net': tr[-1][3]}
                   if tr else None)
    if len(tr) >= 6:
        d['margin']['fb_5d'] = (tr[-1][1] / tr[-6][1] - 1) * 100 if tr[-6][1] else None
        d['margin']['sb_5d'] = (tr[-1][2] / tr[-6][2] - 1) * 100 if tr[-6][2] else None
    if len(tr) >= 21:
        d['margin']['fb_20d'] = (tr[-1][1] / tr[-21][1] - 1) * 100 if tr[-21][1] else None

    # 9) 个股融资净买入 TOP / BOTTOM
    def flow_top(asc):
        return con.execute(f"""
            SELECT m.stock_code, b.name, m.net_buy_d1/1e8
              FROM daily_review_margin m LEFT JOIN stock_basic b ON m.stock_code=b.stock_code
             WHERE m.date=? AND m.net_buy_d1 IS NOT NULL
             ORDER BY m.net_buy_d1 {'ASC' if asc else 'DESC'} LIMIT 10""", (date,)).fetchall()
    d['net_in'] = flow_top(False)
    d['net_out'] = flow_top(True)

    # 10) L1 行业融资余额分解
    s1 = []
    for sec in sector_l1:
        code = sec['code']
        nm = sec['name'].replace('全指', '')
        snap = q1(con, "SELECT MAX(date) FROM index_constituents WHERE index_code=?", (code,))
        members = []
        if snap and snap[0]:
            members = [r[0] for r in con.execute(
                "SELECT stock_code FROM index_constituents WHERE index_code=? AND date=?",
                (code, snap[0]))]
        if not members:
            continue
        ph = ','.join('?' * len(members))
        r = q1(con, f"""SELECT SUM(fb_balance)/1e8, SUM(net_buy_d1)/1e8
                          FROM daily_review_margin WHERE date=? AND stock_code IN ({ph})""",
               (date, *members))
        prev = q1(con, "SELECT DISTINCT date FROM daily_review_margin WHERE date<? "
                       "ORDER BY date DESC LIMIT 1 OFFSET 4", (date,))
        r5 = None
        if prev:
            r5 = q1(con, f"""SELECT SUM(fb_balance)/1e8 FROM daily_review_margin
                              WHERE date=? AND stock_code IN ({ph})""", (prev[0], *members))
        bal = r[0] if r and r[0] else None
        chg5 = ((bal / r5[0] - 1) * 100) if (r5 and r5[0] and bal) else None
        s1.append({'name': nm, 'code': code, 'balance': bal,
                   'net': (r[1] if r and r[1] else None), 'chg5': chg5,
                   'n': len(members)})
    d['sector_margin'] = [x for x in sorted(
        s1, key=lambda x: -(x['balance'] or 0)) if x['balance']][:8]

    # 11) 龙虎榜
    lhb = con.execute("""
        SELECT stock_code, stock_name, reason, net_amount, inst_net
          FROM daily_review_lhb WHERE date=? ORDER BY net_amount DESC""", (date,)).fetchall()
    d['lhb'] = {
        'count': len(lhb),
        'net': sum(r[3] or 0 for r in lhb) / 1e8 if lhb else None,
        'inst': sum(r[4] or 0 for r in lhb) / 1e8 if lhb else None,
        'top': [(r[1], r[2], (r[3] or 0) / 1e8, (r[4] or 0) / 1e8) for r in lhb[:8]],
    }

    # 12) 指汇盈当日文章
    d['cons_date'] = (q1(con, "SELECT MAX(date) FROM index_constituents") or [None])[0]
    d['articles'] = gzh_articles(date)
    d['kw'] = keyword_check(d['articles'], ranked)
    return d


def gzh_articles(date):
    if not os.path.exists(GZH_DB):
        return []
    try:
        c = sqlite3.connect(f'file:{GZH_DB}?mode=ro', uri=True)
        cols = [r[1] for r in c.execute('PRAGMA table_info(gzh_articles)')]
        body_col = 'body_html' if 'body_html' in cols else ('body' if 'body' in cols else None)
        sel = f"title, url, publish_at, brief, {body_col or 'NULL'}"
        rows = c.execute(
            f"SELECT {sel} FROM gzh_articles WHERE substr(publish_at,1,10)=? "
            f"ORDER BY publish_at DESC", (date,)).fetchall()
        c.close()
    except Exception:
        return []
    out = []
    for title, url, pub, brief, body in rows:
        text = strip_tags(body or '')
        paras = [p.strip() for p in text.split('\n') if len(p.strip()) >= 30]
        out.append({'title': title, 'url': url, 'pub': pub, 'brief': brief or '',
                    'paras': paras[:4], 'text': text})
    return out


def keyword_check(articles, ranked):
    """对文中提到的板块，用当日指数涨跌做一次方向核对。"""
    if not articles or not ranked:
        return []
    text = '\n'.join(a['text'] for a in articles)
    out = []
    for kw in KEYWORDS:
        idx = text.find(kw)
        if idx < 0:
            continue
        seg = text[max(0, idx - 60): idx + 60]
        bull = sum(seg.count(w) for w in BULL_WORDS)
        bear = sum(seg.count(w) for w in BEAR_WORDS)
        tone = '偏多' if bull > bear else ('偏空' if bear > bull else '中性')
        hit = next(((nm, ch) for nm, ch in ranked if kw in nm), None)
        if not hit:
            out.append((kw, tone, '（未找到对应指数）', None, '无法判定'))
            continue
        nm, ch = hit
        if tone == '中性':
            verdict = '无法判定'
        else:
            same = (tone == '偏多' and ch >= 0) or (tone == '偏空' and ch < 0)
            verdict = '一致' if same else '相反'
        out.append((kw, tone, nm, ch, verdict))
    return out[:10]


# ── 呈现层 ────────────────────────────────────────────
def render(d):
    date = d['date']
    up, dn, n, avg, amt = d['breadth']
    hz, sx = d['health'], d['sell']

    # 一句话结论
    lead = []
    lead.append(f'沪深两市 {num(up)} 家上涨、{num(dn)} 家下跌，成交 {num(amt)} 亿元，'
                f'平均涨幅 {pct(avg)}')
    if hz:
        lead.append(f'大盘健康分 {hz["total_score"]:.0f}（评级 {hz["rating"] or "—"}，'
                    f'{rating_label(hz["total_score"], hz["rating"])}）')
    if sx:
        lead.append(f'卖出评分 {sx["total_score"]} 分，对应动作「{sx["position_advice"] or "—"}」')
    if d['sectors']:
        b = d['sectors'][0]
        w = d['sectors'][-1]
        lead.append(f'行业组最强 {b["group_label"]}（{b["total_score"]} 分）、'
                    f'最弱 {w["group_label"]}（{w["total_score"]} 分）')
    if d['margin']:
        m = d['margin']
        lead.append(f'两融余额 {num(m["fb"])} 亿元（5 日 {pct(m.get("fb_5d"), 1)}），'
                    f'当日融资净买入 {num(m["net"])} 亿元')
    if d['flow']['10d']:
        tops = '、'.join(x['name'] for x in d['flow']['10d'][:3])
        lead.append(f'短线资金活跃度前三为 {tops}')
    lead_txt = '；'.join(lead) + '。'

    # 主要指数
    broad_rows = ''.join(
        f'<tr><td>{esc(nm)}</td><td class="num" style="color:var(--mut)">{code}</td>'
        f'<td class="num {cls(v)}">{pct(v)}</td></tr>' for nm, code, v in d['broad'])

    # 参与健康评分
    sub_rows = ''
    if hz:
        for key, label, unit, note in SUB_METRICS:
            val = hz[key + '_value'] if key + '_value' in hz.keys() else None
            sc = hz[key + '_score'] if key + '_score' in hz.keys() else None
            if val is None:
                continue
            uv = f'{val:,.2f}{unit}' if unit in ('倍', '分') else f'{val:,.1f}{unit}'
            sub_rows += (f'<tr><td>{label}</td><td class="num">{uv}</td>'
                         f'<td class="num">{num(sc) if sc is not None else "—"}</td>'
                         f'<td style="color:var(--mut)">{note}</td></tr>')

    # 卖出评分明细
    sell_rows = ''
    trig = 0
    if sx:
        for col, scol, label in SELL_SIGNALS:
            try:
                flag = sx[col]
            except (IndexError, KeyError):
                flag = None
            score = sx[scol] if scol in sx.keys() else None
            if flag in (None, 0, False):
                state, c = '未触发', 'var(--mut)'
            else:
                state, c = '触发', 'var(--up)'
                trig += 1
            detail = ''
            if col == 'dist_days_25d' and flag:
                detail = f'{flag} 个'
            elif col == 'divergence_count' and flag:
                detail = f'{flag} 只指数'
            sell_rows += (f'<tr><td>{label}</td><td style="color:{c}">{state}'
                          f'{" " + detail if detail else ""}</td>'
                          f'<td class="num">{num(score) if score is not None else "—"}</td></tr>')

    # 行业组
    sec_rows = ''.join(
        f'<tr><td>{esc(r[1])}</td><td class="num">{r[2]}</td>'
        f'<td class="num">{esc(r[3] or "—")}</td><td class="num">{num(r[4])}</td>'
        f'<td class="num" style="color:var(--mut)">{num(r[5])}</td></tr>'
        for r in d['sectors'])

    # 资金活跃度三表
    def flow_tbl(key):
        rows = ''
        for i, it in enumerate(d['flow'][key], 1):
            lbl = ''.join(
                f'<span style="color:{"var(--up)" if ch == "+" else ("var(--down)" if ch == "-" else "var(--mut)")}">{ch}</span>'
                for ch in it['label'])
            sc = it['score']
            sc_w = '600' if sc and sc >= 80 else '400'   # 评分为中性指标，只用字重不用颜色
            rows += (f'<tr><td class="num">{i}</td><td>{esc(it["name"])}</td>'
                     f'<td class="num" style="font-weight:{sc_w}">{num(sc)}</td>'
                     f'<td class="num">{lbl}</td></tr>')
        return rows

    # 两融
    fb_series = [r[1] for r in d['margin_trend']]
    sb_series = [r[2] for r in d['margin_trend']]
    margin_kpi = ''
    if d['margin']:
        m = d['margin']
        margin_kpi = f"""
<div class="kpi">
<div><div class="k">融资余额</div><div class="v">{num(m['fb'])} 亿</div>
<div class="n">5 日 {pct(m.get('fb_5d'), 1)} · 20 日 {pct(m.get('fb_20d'), 1)}</div></div>
<div><div class="k">融券余额</div><div class="v">{num(m['sb'], 1)} 亿</div>
<div class="n">5 日 {pct(m.get('sb_5d'), 1)}</div></div>
<div><div class="k">当日融资净买入</div><div class="v" style="color:{'var(--up)' if (m['net'] or 0) >= 0 else 'var(--down)'}">{num(m['net'])} 亿</div>
<div class="n">数据日 {m['date']}</div></div>
</div>"""

    net_in_rows = ''.join(
        f'<tr><td>{esc(r[1] or r[0])}</td><td class="num" style="color:var(--mut)">{r[0]}</td>'
        f'<td class="num">{r[2]:+.2f}</td></tr>' for r in d['net_in'])
    net_out_rows = ''.join(
        f'<tr><td>{esc(r[1] or r[0])}</td><td class="num" style="color:var(--mut)">{r[0]}</td>'
        f'<td class="num">{r[2]:+.2f}</td></tr>' for r in d['net_out'])

    sec_m_rows = ''.join(
        f'<tr><td>{esc(x["name"])}</td><td class="num">{num(x["balance"])}</td>'
        f'<td class="num">{pct(x["chg5"], 1)}</td>'
        f'<td class="num" style="color:{cls(x["net"])}">{num(x["net"], 2)}</td>'
        f'<td class="num" style="color:var(--mut)">{x["n"]}</td></tr>'
        for x in d['sector_margin'])

    lhb = d['lhb']
    lhb_rows = ''.join(
        f'<tr><td>{esc(nm)}</td><td>{esc((rs or "")[:18])}</td>'
        f'<td class="num" style="color:{cls(net)}">{net:+.2f}</td>'
        f'<td class="num" style="color:{cls(inst)}">{inst:+.2f}</td></tr>'
        for nm, rs, net, inst in lhb['top'])

    # 指汇盈
    art_html = ''
    if d['articles']:
        for a in d['articles']:
            paras = ''.join(f'<p>{esc(p)}</p>' for p in a['paras'])
            art_html += (f'<h3>{esc(a["title"])}</h3>'
                         f'<p style="font-size:12px;color:var(--mut)">{esc(a["pub"])} ｜ '
                         f'<a href="{esc(a["url"])}" target="_blank" rel="noopener">原文</a></p>'
                         f'{paras}')
    else:
        art_html = ('<p style="color:var(--mut)">当日未抓到指汇盈文章（或该日为视频稿，'
                    '正文只有标题）。抓取器在 daily_update.py 倒数第二步执行。</p>')

    kw_rows = ''.join(
        f'<tr><td>{esc(kw)}</td><td>{esc(tone)}</td><td>{esc(nm)}</td>'
        f'<td class="num {cls(ch)}">{pct(ch) if ch is not None else "—"}</td>'
        f'<td><span class="done {"ok" if v == "一致" else ("no" if v == "相反" else "lag")}">{esc(v)}</span></td></tr>'
        for kw, tone, nm, ch, v in d['kw'])

    hist = history_items()
    hist_rows = ''.join(
        f'<tr><td style="white-space:nowrap"><a href="{esc(it["date"])}/index.html">'
        f'{esc(it["date"])}</a></td>'
        f'<td>{esc(it["headline"])}</td></tr>' for it in hist[:30])

    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="light"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{date} 大盘交易总结</title><style>{CSS}</style></head><body><div class="wrap">
<div class="top"><span class="thm" onclick="toggleTheme()">◐</span>
<b>大盘交易总结 · {date}</b> ｜ 口径：EOD 收盘 ｜ 两融数据日 {esc((d['margin'] or {}).get('date', '—'))} ｜
<a href="../market-scan/index.html">大盘扫描</a></div>
<h1>{date} 大盘交易总结</h1>
<div class="lead">{esc(lead_txt)}</div>

<h2>一、大盘健康分与卖出评分</h2>
<div class="kpi">
<div><div class="k">大盘健康分</div><div class="v">{f"{hz['total_score']:.0f}" if hz else '—'}</div>
<div class="n">评级 {esc((hz['rating'] if hz else None) or '—')} · {rating_label(hz['total_score'], hz['rating']) if hz else '—'}</div></div>
<div><div class="k">卖出评分</div><div class="v">{sx['total_score'] if sx else '—'}</div>
<div class="n">{esc((sx['position_advice'] if sx else None) or '—')}</div></div>
<div><div class="k">触发信号</div><div class="v">{trig} / {len(SELL_SIGNALS)}</div>
<div class="n">信号越多环境越差</div></div>
<div><div class="k">涨跌家数</div><div class="v"><span class="rise">{num(up)}</span> / <span class="fall">{num(dn)}</span></div>
<div class="n">样本 {num(n)} 只</div></div>
</div>
<h3>卖出评分信号明细</h3>
<div class="tw"><table class="wide"><tr><th>信号</th><th>状态</th><th>得分</th></tr>{sell_rows}</table></div>

<h2>二、主要指数表现</h2>
<div class="tw"><table><tr><th>指数</th><th>代码</th><th>涨跌</th></tr>{broad_rows}</table></div>

<h2>三、辅助指标 · 参与健康评分</h2>
<div class="tw"><table class="wide"><tr><th>指标</th><th>读数</th><th>得分</th><th>含义</th></tr>{sub_rows}</table></div>

<h2>四、行业分组健康分</h2>
<div class="tw"><table><tr><th>分组</th><th>健康分</th><th>评级</th><th>仓位建议</th><th>成分数</th></tr>{sec_rows}</table></div>

<h2>五、资金活跃度 TOP 10</h2>
<p style="font-size:13px;color:var(--mut)">评分 0–100；三字符依次是短线(10日)/中线(65日)/长线(250日)方向，
<span class="rise">+</span> 净流入、<span class="fall">−</span> 净流出、0 中性。</p>
<h3>短线（10 日）</h3>
<div class="tw"><table><tr><th>#</th><th>指数</th><th>评分</th><th>方向</th></tr>{flow_tbl('10d')}</table></div>
<h3>中线（65 日）</h3>
<div class="tw"><table><tr><th>#</th><th>指数</th><th>评分</th><th>方向</th></tr>{flow_tbl('65d')}</table></div>
<h3>长线（250 日）</h3>
<div class="tw"><table><tr><th>#</th><th>指数</th><th>评分</th><th>方向</th></tr>{flow_tbl('250d')}</table></div>

<h2>六、资金情绪</h2>
<h3>6.1 两融余额趋势（近 60 个交易日）</h3>
{margin_kpi}
<div class="tw"><table><tr><th>融资余额走势</th></tr><tr><td>{sparkline(fb_series, color='var(--up)')}</td></tr></table></div>
<div class="tw"><table><tr><th>融券余额走势</th></tr><tr><td>{sparkline(sb_series, color='var(--accent)')}</td></tr></table></div>

<h3>6.2 个股融资净买入（当日流向，亿元）</h3>
<div class="tw"><table><tr><th>净买入前十</th><th>代码</th><th>净买入</th></tr>{net_in_rows}</table></div>
<div class="tw"><table><tr><th>净卖出前十</th><th>代码</th><th>净买入</th></tr>{net_out_rows}</table></div>

<h3>6.3 L1 行业融资余额分解（亿元）</h3>
<div class="tw"><table><tr><th>行业</th><th>融资余额</th><th>5 日变化</th><th>当日净买入</th><th>成分股</th></tr>{sec_m_rows}</table></div>

<h3>6.4 龙虎榜</h3>
<p style="font-size:13px">上榜 <b>{lhb['count']}</b> 只，合计净额 <b>{num(lhb['net'], 2)}</b> 亿元，
机构席位净额 <b>{num(lhb['inst'], 2)}</b> 亿元。</p>
<div class="tw"><table><tr><th>个股</th><th>上榜原因</th><th>净额</th><th>机构净额</th></tr>{lhb_rows}</table></div>

<h2>七、指汇盈当日观点</h2>
{art_html}
<h3>7.1 文中板块与当日指数方向核对</h3>
<div class="tw"><table><tr><th>关键词</th><th>文中语气</th><th>对应指数</th><th>当日涨跌</th><th>判定</th></tr>{kw_rows}</table></div>
<p style="font-size:12px;color:var(--mut)">判定规则：取关键词前后 60 字，数「涨/反弹/新高」与「跌/回落/走弱」出现次数定语气；
语气与对应指数当日涨跌同向记「一致」，反向记「相反」。这是粗筛，不替代人工阅读。</p>

<h2>八、口径与数据缺口</h2>
<ul style="font-size:13.5px;color:var(--tx)">
<li>交易日 {date}；行情与指数为 EOD 收盘数据，两融与龙虎榜为
{esc((d['margin'] or {}).get('date', '—'))} 数据（两个源发布时间不同，页面分别标明）。</li>
<li>健康分、卖出评分、行业分组分、资金活跃度均来自本地日更库；资金活跃度评分口径见
<code>src/scanners/index_capital_flow.py</code>。</li>
<li>指汇盈文章经搜狐号镜像抓取（公众号原页需订阅号后台，见 progress 教训 202）；视频稿无正文段落。</li>
<li>行业组成分快照日 {esc(d.get('cons_date') or '—')}，季度更新，非当日实时成分。</li>
</ul>

<h2>九、历史</h2>
<div class="tw"><table class="wide"><tr><th>日期</th><th>一句话结论</th></tr>{hist_rows}</table></div>

<div class="ft">生成器 <code>scripts/gen_daily_market_summary.py</code>（daily_update.py 末两步自动执行）
｜ 数据源：本地 <code>lixinger.db</code> + <code>gzh.db</code> ｜
本页为盘面事实的规则化汇总，不构成买卖建议、目标价或仓位建议。</div>
</div><script>{JS}</script></body></html>"""


def history_items():
    items = []
    if not os.path.isdir(ROOT):
        return items
    for name in sorted(os.listdir(ROOT), reverse=True):
        p = os.path.join(ROOT, name, 'index.html')
        if not (os.path.isdir(os.path.join(ROOT, name)) and os.path.exists(p)):
            continue
        head = ''
        meta_p = os.path.join(ROOT, 'data', f'{name}.json')
        if os.path.exists(meta_p):
            try:
                head = json.load(open(meta_p, encoding='utf-8')).get('headline', '')
            except Exception:
                head = ''
        items.append({'date': name, 'headline': head})
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    a = ap.parse_args()

    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    date = a.date or con.execute(
        "SELECT MAX(date) FROM daily_kline_adj").fetchone()[0]

    hz = q1(con, "SELECT total_score FROM market_health_daily WHERE date=?", (date,))
    if not hz:
        print(f'[SKIP] {date} 无大盘健康分记录，不生成总结')
        con.close()
        return 0

    d = gather(con, date)
    con.close()

    html = render(d)
    os.makedirs(os.path.join(ROOT, date), exist_ok=True)
    with open(os.path.join(ROOT, date, 'index.html'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(html)
    with open(os.path.join(ROOT, 'index.html'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(html)

    items = history_items()
    with open(os.path.join(ROOT, 'index.json'), 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(items, fh, ensure_ascii=False, indent=1)

    # 让历史条目带上当日结论（供索引页显示）
    head = re.sub(r'\s+', ' ', (d['articles'][0]['title'] if d['articles'] else ''))
    if head:
        os.makedirs(os.path.join(ROOT, 'data'), exist_ok=True)
        with open(os.path.join(ROOT, 'data', f'{date}.json'), 'w',
                  encoding='utf-8', newline='\n') as fh:
            json.dump({'headline': head, 'generated_at':
                       datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
                      fh, ensure_ascii=False, indent=1)

    print(f'已生成 {date} 大盘交易总结（{len(html)/1024:.1f} KB）'
          f'｜历史 {len(items)} 期｜文章 {len(d["articles"])} 篇')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
