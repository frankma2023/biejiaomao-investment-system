#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「市场总结」页面（market-logic 技能产出）。

用法:
  python scripts/gen_market_logic_report.py --date 2026-11-05
  python scripts/gen_market_logic_report.py            # 用最新有数据的交易日

输入:
  web/market-logic/data/<DATE>.json   当日注解（结论/主线/核对/明日看什么）
输出:
  web/market-logic/<DATE>/index.html  当日总结
  web/market-logic/index.html         历史的索引页
  web/market-logic/index.json         机器可读索引
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')
ROOT = os.path.join(PROJECT, 'web', 'market-logic')
DATA = os.path.join(ROOT, 'data')

BROAD = [('000985', '中证全指'), ('000300', '沪深300'), ('000905', '中证500'),
         ('000852', '中证1000'), ('000001', '上证指数'), ('399001', '深证成指'),
         ('399006', '创业板指'), ('000688', '科创50'), ('000510', '中证A500'),
         ('000922', '中证红利')]
SECTORS = ['化工', '光伏', '黄金', '半导体', '芯片', '算力', '军工', '煤炭',
           '银行', '白酒', '医药', '证券', '新能源']
STOCKS = {'600309': '万华化学', '002648': '卫星化学', '000703': '恒逸石化',
          '600346': '恒力石化', '601857': '中国石油', '600028': '中国石化',
          '601012': '隆基绿能', '300274': '阳光电源', '600438': '通威股份'}

CSS = """
:root{--bg:#f7f7f9;--card:#fff;--line:#e4e4ea;--tx:#1c1c22;--mut:#6b6b76;
--rise:#10b981;--fall:#ef4444;--accent:#b45309;--blue:#2563eb;--warnbg:#fff8eb;--warnbd:#f0c36d}
html[data-theme="dark"]{--bg:#0e0e12;--card:#16161d;--line:#2a2a33;--tx:#e9e9ec;
--mut:#8f8f97;--accent:#f59e0b;--blue:#539BF5;--warnbg:#241d10;--warnbd:#6b5320}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font-size:15px;line-height:1.72;
font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:22px 20px 70px}
h1,h2,h3{font-family:'Noto Serif SC','Songti SC',Georgia,serif;font-weight:900;
letter-spacing:.005em}
h1{font-size:27px;line-height:1.3;margin:6px 0 8px}
h2{font-size:19px;margin:32px 0 10px;padding-left:11px;border-left:4px solid var(--accent)}
h3{font-size:15px;margin:18px 0 6px}
p{margin:9px 0}
.top{position:sticky;top:0;z-index:9;background:var(--bg);border-bottom:1px solid var(--line);
padding:9px 0;font-size:13px;color:var(--mut);margin-bottom:4px}
.top b{color:var(--tx)}
.thm{float:right;cursor:pointer;border:1px solid var(--line);border-radius:6px;
padding:1px 9px;font-size:12px;color:var(--mut)}
.lead{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--blue);
border-radius:0 10px 10px 0;padding:14px 18px;margin:14px 0;font-size:16px}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.kpi>div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 13px}
.kpi .k{font-size:12px;color:var(--mut)}
.kpi .v{font-family:ui-monospace,Menlo,monospace;font-size:18px;font-weight:700;margin-top:2px}
.tw{overflow-x:auto;margin:10px 0}
table{width:100%;border-collapse:collapse;background:var(--card);font-size:13.5px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}
th{background:rgba(0,0,0,.03);font-size:12.5px;color:var(--mut);font-weight:600}
html[data-theme="dark"] th{background:rgba(255,255,255,.04)}
.num{font-family:ui-monospace,Menlo,monospace;text-align:right;font-variant-numeric:tabular-nums}
.rise{color:#10b981}.fall{color:#ef4444}
.warn{background:var(--warnbg);border:1px solid var(--warnbd);border-left:4px solid var(--accent);
border-radius:8px;padding:11px 14px;margin:12px 0;font-size:14px}
.done{display:inline-block;font-size:11px;padding:0 7px;border-radius:999px;
border:1px solid var(--line);color:var(--mut)}
.ok{color:#10b981;border-color:#10b981}.lag{color:#b45309;border-color:#b45309}
.no{color:#ef4444;border-color:#ef4444}
a{color:var(--blue)}
.ft{margin-top:40px;padding-top:14px;border-top:1px solid var(--line);
font-size:12px;color:var(--mut);line-height:1.8}
"""

JS = """
(function(){var K='ml-theme';
try{var s=localStorage.getItem(K);if(s)document.documentElement.dataset.theme=s;}catch(e){}
window.toggleTheme=function(){var c=document.documentElement.dataset.theme==='dark'?'light':'dark';
document.documentElement.dataset.theme=c;try{localStorage.setItem(K,c);}catch(e){}};})();
"""


def pct(v):
    return '—' if v is None else f'{v:+.2f}%'


def cls(v):
    if v is None:
        return ''
    return 'rise' if v >= 0 else 'fall'


def facts(con, date):
    names = {}
    for c, n in con.execute("SELECT DISTINCT index_code, index_name FROM stock_index "
                            "WHERE index_name IS NOT NULL AND index_name<>''"):
        names.setdefault(c, n)

    breadth = con.execute("""SELECT
        SUM(CASE WHEN change_pct>0 THEN 1 ELSE 0 END),
        SUM(CASE WHEN change_pct<0 THEN 1 ELSE 0 END),
        COUNT(*), AVG(change_pct)*100, SUM(amount)/1e8
      FROM daily_kline_adj WHERE date=?""", (date,)).fetchone()

    broad = []
    for code, nm in BROAD:
        r = con.execute("SELECT change FROM index_daily_kline WHERE stock_code=? "
                        "AND kline_type='normal' AND date=?", (code, date)).fetchone()
        if r and r[0] is not None:
            broad.append((nm, r[0] * 100))

    rows = con.execute("""SELECT stock_code, change FROM index_daily_kline
        WHERE date=? AND kline_type='normal' AND change IS NOT NULL""", (date,)).fetchall()
    ranked = [(names[c], ch * 100) for c, ch in rows if c in names]
    ranked.sort(key=lambda x: -x[1])
    top = ranked[:12]
    bottom = ranked[-6:][::-1]

    sectors = []
    for kw in SECTORS:
        hits = [x for x in ranked if kw in x[0]]
        if hits:
            sectors.append((kw, hits[0][0], hits[0][1]))

    stocks = []
    for code, nm in STOCKS.items():
        r = con.execute("SELECT change_pct FROM daily_kline_adj WHERE stock_code=? "
                        "AND date=?", (code, date)).fetchone()
        if r and r[0] is not None:
            stocks.append((nm, r[0] * 100))
    return {'breadth': breadth, 'broad': broad, 'top': top, 'bottom': bottom,
            'sectors': sectors, 'stocks': stocks}


def render(date, meta, f):
    up, dn, n, avg, amt = f['breadth']
    lines_tbl = ''.join(
        f'<tr><td>{l["no"]}</td><td>{l["event"]}</td><td>{l["chain"]}</td>'
        f'<td>{l["split"]}</td><td>{l["expect"]}</td><td>{l["actual"]}</td>'
        f'<td><span class="done {l.get("cls","ok")}">{l["verdict"]}</span></td></tr>'
        for l in meta['lines'])
    broad_tbl = ''.join(f'<tr><td>{nm}</td><td class="num {cls(v)}">{pct(v)}</td></tr>'
                        for nm, v in f['broad'])
    top_tbl = ''.join(f'<tr><td>{nm}</td><td class="num {cls(v)}">{pct(v)}</td></tr>'
                      for nm, v in f['top'])
    bot_tbl = ''.join(f'<tr><td>{nm}</td><td class="num {cls(v)}">{pct(v)}</td></tr>'
                      for nm, v in f['bottom'])
    sec_tbl = ''.join(f'<tr><td>{kw}</td><td>{nm}</td>'
                      f'<td class="num {cls(v)}">{pct(v)}</td></tr>'
                      for kw, nm, v in f['sectors'])
    stk_tbl = ''.join(f'<tr><td>{nm}</td><td class="num {cls(v)}">{pct(v)}</td></tr>'
                      for nm, v in f['stocks'])
    over_tbl = ''.join(
        f'<tr><td>{o["name"]}</td><td class="num">{o["val"]}</td></tr>'
        for o in meta.get('overnight', []))
    watch = ''.join(f'<li>{w}</li>' for w in meta.get('watch', []))
    gaps = ''.join(f'<li>{g}</li>' for g in meta.get('gaps', []))
    lag_tbl = ''
    if meta.get('lag'):
        lag_tbl = ('<h2>四、传导滞后检查</h2><div class="tw"><table>'
                   '<tr><th>个股</th><th>路线</th><th>T-1</th><th>T</th><th>判定</th></tr>'
                   + ''.join(f'<tr><td>{r["name"]}</td><td>{r["route"]}</td>'
                             f'<td class="num {cls(r["t1"])}">{pct(r["t1"])}</td>'
                             f'<td class="num {cls(r["t"])}">{pct(r["t"])}</td>'
                             f'<td><span class="done {r.get("cls","lag")}">{r["verdict"]}</span></td></tr>'
                             for r in meta['lag'])
                   + '</table></div>')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="light"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>市场总结 {date} · 今日交易什么逻辑</title>
<style>{CSS}</style></head><body><div class="wrap">
<div class="top"><span class="thm" onclick="toggleTheme()">◐</span>
<b>市场总结 · {date}</b> ｜ 口径：EOD 收盘 ｜ <a href="../index.html">← 历史</a> ｜
<a href="../../market-scan/">大盘扫描</a></div>
<h1>{date} 市场总结</h1>
<div class="lead">{meta['headline']}</div>

<h2>一、盘面事实</h2>
<div class="kpi">
<div><div class="k">涨跌家数</div><div class="v"><span class="rise">{up}</span> / <span class="fall">{dn}</span></div></div>
<div><div class="k">平均涨幅</div><div class="v">{avg:+.2f}%</div></div>
<div><div class="k">成交额</div><div class="v">{amt:,.0f} 亿</div></div>
<div><div class="k">样本</div><div class="v">{n:,} 只</div></div>
</div>
<div class="tw"><table><tr><th>宽基</th><th>涨跌</th></tr>{broad_tbl}</table></div>
<p style="font-size:13px;color:var(--mut)">{meta.get('breadth_note', '')}</p>

<h2>二、结构与涨跌幅榜</h2>
<div class="tw"><table><tr><th>指数涨幅前 12</th><th>涨跌</th></tr>{top_tbl}</table></div>
<div class="tw"><table><tr><th>指数跌幅前 6</th><th>涨跌</th></tr>{bot_tbl}</table></div>
<div class="tw"><table><tr><th>关键板块</th><th>代表指数</th><th>涨跌</th></tr>{sec_tbl}</table></div>
<div class="tw"><table><tr><th>关键个股</th><th>涨跌</th></tr>{stk_tbl}</table></div>

<h2>三、隔夜外部</h2>
<div class="tw"><table><tr><th>资产</th><th>数值</th></tr>{over_tbl}</table></div>
<p style="font-size:13px;color:var(--mut)">{meta.get('tz_note', '')}</p>

<h2>五、主线与符号核对</h2>
<div class="tw"><table>
<tr><th>#</th><th>触发事件</th><th>一级传导</th><th>二级分化</th><th>预期符号</th><th>实际</th><th>判定</th></tr>
{lines_tbl}</table></div>
{lag_tbl}

<h2>六、明日看什么</h2><ul>{watch}</ul>
<h2>七、数据缺口</h2><ul>{gaps}</ul>
<div class="ft">由 <code>market-logic</code> 技能生成 ｜ 生成器 <code>scripts/gen_market_logic_report.py</code>
｜ 数据源：本地 <code>lixinger.db</code>（EOD）｜ 本页为规则与盘面的对照记录，
不构成买卖建议、目标价或仓位建议。</div>
</div><script>{JS}</script></body></html>"""
    return html


def build_index():
    items = []
    for d in sorted(os.listdir(ROOT), reverse=True):
        p = os.path.join(ROOT, d, 'index.html')
        if os.path.isdir(os.path.join(ROOT, d)) and os.path.exists(p):
            meta_p = os.path.join(ROOT, 'data', f'{d}.json')
            head = ''
            if os.path.exists(meta_p):
                with open(meta_p, encoding='utf-8') as fh:
                    head = json.load(fh).get('headline', '')
            items.append({'date': d, 'headline': head})
    with open(os.path.join(ROOT, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump(items, fh, ensure_ascii=False, indent=1)

    rows = ''.join(
        f'<tr><td><a href="{it["date"]}/index.html">{it["date"]}</a></td>'
        f'<td>{it["headline"]}</td></tr>' for it in items)
    html = f"""<!DOCTYPE html><html lang="zh-CN" data-theme="light"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>市场总结 · 历史</title><style>{CSS}</style></head><body><div class="wrap">
<div class="top"><span class="thm" onclick="toggleTheme()">◐</span>
<b>市场总结 · 历史</b> ｜ <a href="../market-scan/">← 大盘扫描</a></div>
<h1>市场总结 · 历史</h1>
<p>共 {len(items)} 期。每日总结由 <code>market-logic</code> 技能生成，保存在独立目录。</p>
<div class="tw"><table><tr><th>日期</th><th>一句话结论</th></tr>{rows}</table></div>
</div><script>{JS}</script></body></html>"""
    return html, len(items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    a = ap.parse_args()

    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    date = a.date
    if not date:
        date = con.execute("SELECT MAX(date) FROM daily_kline_adj").fetchone()[0]
    meta_p = os.path.join(DATA, f'{date}.json')
    if not os.path.exists(meta_p):
        print(f'[ERROR] 缺少注解文件: {meta_p}')
        print('  先写当日注解 JSON（见 web/market-logic/data/2026-11-05.json 模板）')
        return 1
    with open(meta_p, encoding='utf-8') as fh:
        meta = json.load(fh)

    f = facts(con, date)
    con.close()

    out_dir = os.path.join(ROOT, date)
    os.makedirs(out_dir, exist_ok=True)
    html = render(date, meta, f)
    with open(os.path.join(out_dir, 'index.html'), 'w', encoding='utf-8') as fh:
        fh.write(html)
    idx_html, cnt = build_index()
    with open(os.path.join(ROOT, 'index.html'), 'w', encoding='utf-8') as fh:
        fh.write(idx_html)
    print(f'已生成 {date}/index.html（{len(html) / 1024:.1f} KB）｜历史页共 {cnt} 期')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
