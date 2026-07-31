"""
MW 综合回测报告 HTML v2 · 包含卖出策略
"""
import json, os, numpy as np
from datetime import datetime
from collections import defaultdict

WIDE = 'D:/hanako/investment-system/config/strategy/mw_backtest_wide.json'
EXIT = 'D:/hanako/investment-system/config/strategy/mw_exit_strategies.json'
OUT = 'D:/hanako/investment-system/config/strategy/mw_backtest_report_v2.html'

t0 = datetime.now()
print("加载数据...", end=' ', flush=True)
with open(WIDE, 'r') as f: wide = json.load(f)
try:
    with open(EXIT, 'r') as f: exit_stats = json.load(f)
except: exit_stats = {}
print(f"{len(wide)} 信号, {len(exit_stats)} 退出策略")

# ── 统计函数 ──
def s(arr):
    arr = np.array([x for x in arr if x is not None and not (isinstance(x,float) and np.isnan(x))])
    if len(arr) < 5: return None
    return {'n':len(arr),'wr':round((arr>0).mean()*100,1),'med':round(np.median(arr)*100,2),
            'mn':round(arr.mean()*100,2),'mx':round(arr.max()*100,1),'dd':round(arr.min()*100,2)}

total = len(wide)
b2c = sum(1 for r in wide if r['has_b2'])
plus = sum(1 for r in wide if r['is_plus'])

# 年份
yr = defaultdict(lambda: {'t':0,'b2':0})
for r in wide: 
    y = r['b1_date'][:4]
    yr[y]['t']+=1
    if r['has_b2']: yr[y]['b2']+=1

# 市场
mkt = defaultdict(lambda: {'t':0,'r':[]})
for r in wide:
    reg = r.get('market_regime','未知')
    mkt[reg]['t']+=1
    if r.get('ret_b1_10d') is not None: mkt[reg]['r'].append(r['ret_b1_10d'])

# 关注分×持有期
attn = {}
for r in wide:
    t = r.get('attention_tier','?')
    for k,hd in [('ret_b1_5d',5),('ret_b1_10d',10),('ret_b1_20d',20),('ret_b1_60d',60)]:
        key = (t,hd)
        if key not in attn: attn[key] = []
        if r.get(k) is not None: attn[key].append(r[k])

# 多因子
combos = {
    '全量': lambda r: True,
    '有B2': lambda r: r['has_b2'],
    '有B2+极高': lambda r: r['has_b2'] and r['tech_score']>=80,
    '有B2+浅调<20%': lambda r: r['has_b2'] and r['decline_pct']<20,
    '有B2+行业RS≥90': lambda r: r['has_b2'] and (r.get('ind_rs20') or 0)>=90,
    '有B2+正乖离0~10%': lambda r: r['has_b2'] and r.get('deviation_ma20') is not None and 0<=r['deviation_ma20']<10,
    'B1强≥5%+极高': lambda r: (r['b1_return_pct'] or 0)>=5 and r['tech_score']>=80,
    'B1弱<3%+极高': lambda r: (r['b1_return_pct'] or 0)<3 and r['tech_score']>=80,
    '行业RS≥90(全量)': lambda r: (r.get('ind_rs20') or 0)>=90,
}
combo_r = {}
for name,fn in combos.items():
    sub = [r for r in wide if fn(r)]
    c10 = s([r['ret_b1_10d'] for r in sub if r.get('ret_b1_10d') is not None])
    c20 = s([r['ret_b1_20d'] for r in sub if r.get('ret_b1_20d') is not None])
    combo_r[name] = {'n':len(sub),'r10':c10,'r20':c20}

print("生成HTML...", end=' ', flush=True)

# ═══════════════════════════════════════
# HTML
# ═══════════════════════════════════════
def td(v, fmt='.2f', color=False, sign=False):
    if v is None: return '<td>-</td>'
    c = ''
    if color and isinstance(v,(int,float)):
        c = ' style="color:#4caf50"' if v>0 else (' style="color:#ef4444"' if v<0 else '')
    prefix = '+' if (sign and isinstance(v,(int,float)) and v>0) else ''
    return f'<td{c}>{prefix}{v:{fmt}}</td>'

def tr(cells, cls=''):
    return '<tr'+(' class="'+cls+'"' if cls else '')+'>'+''.join(f'<td>{c}</td>' if not isinstance(c,tuple) else td(*c) for c in cells)+'</tr>'

def table(headers, rows, cls=''):
    h = ''.join(f'<th>{h}</th>' for h in headers)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{"".join(rows)}</tbody></table>'

def card(title, content):
    return f'<div class="section"><h2>{title}</h2>{content}</div>'

def kpi(v, l, c=''):
    return f'<div class="kpi"><div class="v" style="color:{c or "var(--accent)"}">{v}</div><div class="l">{l}</div></div>'

# 卖出策略标签
ex_labels = {'hold_5':'持有5日','hold_10':'持有10日','hold_20':'持有20日','hold_60':'持有60日',
             'ma10_exit':'跌破MA10卖出','ma20_exit':'跌破MA20卖出','stop7':'亏损7%止损'}

html = f'''<!DOCTYPE html><html lang="zh-CN" class="dark">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>MW 信号全面回测报告 v2</title>
<link rel="stylesheet" href="../web/shared/css/hanako-glass.css">
<style>
:root{{--accent:#f59e0b;--bg:#0f0f13;--card:#1a1a1f;--border:#2a2a30;--text:#d4d4d8;--muted:#71717a;--font-body:'Inter',system-ui,sans-serif;--font-display:'Instrument Serif',Georgia,serif}}
body{{background:var(--bg);color:var(--text);font-family:var(--font-body);line-height:1.6;font-size:14px}}
.app-container{{max-width:1200px;margin:0 auto;padding:24px 28px 60px}}
h1{{font-family:var(--font-display);font-size:1.5rem;margin:0 0 4px;color:var(--accent);font-weight:400}}
h2{{font-family:var(--font-display);font-size:1rem;margin:32px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--border);color:var(--text);font-weight:400}}
h3{{font-size:0.78rem;margin:16px 0 6px;color:var(--accent);font-weight:600}}
.subtitle{{color:var(--muted);font-size:0.7rem;margin-bottom:28px}}
.section{{margin-bottom:4px}}
.grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.kpi{{background:var(--card);border-radius:12px;padding:16px;text-align:center}}
.kpi .v{{font-family:var(--font-display);font-size:1.7rem;font-weight:900}}
.kpi .l{{font-size:0.62rem;color:var(--muted);margin-top:4px;text-transform:uppercase;letter-spacing:0.05em}}
table{{width:100%;border-collapse:collapse;font-size:0.7rem;margin:8px 0 16px}}
th{{background:var(--card);color:var(--muted);font-weight:600;padding:8px 10px;text-align:right;border-bottom:2px solid var(--border);font-size:0.62rem;letter-spacing:0.04em;text-transform:uppercase}}
th:first-child{{text-align:left}}
td{{padding:6px 10px;text-align:right;border-bottom:1px solid var(--border)}}
td:first-child{{text-align:left;font-weight:500}}
tr:hover td{{background:rgba(245,158,11,0.04)}}
tr.sub td{{background:rgba(245,158,11,0.02);font-style:italic}}
.finding{{background:var(--card);border-radius:12px;padding:14px 18px;margin:6px 0;border-left:3px solid var(--accent)}}
.finding strong{{color:var(--accent);font-size:0.75rem}}
.finding span{{font-size:0.68rem;color:var(--muted);display:block;margin-top:3px}}
.footer{{margin-top:48px;text-align:center;font-size:0.6rem;color:var(--muted)}}
.tag{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:0.6rem;margin-left:4px}}
.tag-g{{background:rgba(76,175,80,0.15);color:#4caf50}}
.tag-r{{background:rgba(239,68,68,0.15);color:#ef4444}}
.tag-y{{background:rgba(245,158,11,0.15);color:#f59e0b}}
</style></head><body><div class="app-container">
<h1>MW 信号全面回测报告</h1>
<p class="subtitle">引擎 v5.2 · 2016-01 ~ 2026-07 · {total:,} 条信号 · 生成于 {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
'''

# ── KPI ──
html += '<div class="grid3">'
html += kpi(f'{total:,}', 'MW 信号总数')
html += kpi(f'{b2c:,}', f'含 B2（{b2c/total*100:.0f}%）')
html += kpi(f'{plus}', 'PLUS 评级')
html += kpi(f'{len(set(r["stock_code"] for r in wide)):,}', '覆盖股票')
html += kpi(f'{(total-b2c)/total*100:.0f}%', 'B1-only 占比')
html += kpi('10.6年', '回测周期')
html += '</div>'

# ── 1. 年份分布 ──
rows = [tr([y, f'{d["t"]:,}', f'{d["b2"]:,}', f'{d["b2"]/d["t"]*100:.0f}%']) for y,d in sorted(yr.items())]
html += card('一、年份分布', table(['年份','信号数','B2数','B2率'], rows))

# ── 2. 市场环境 ──
rows = []
for reg in ['牛市','震荡市','熊市']:
    m = mkt.get(reg,{'t':0,'r':[]})
    st = s(m['r'])
    if st:
        rows.append(tr([reg, f'{m["t"]:,}', f'{m["t"]/total*100:.0f}%', (st['wr'],'.1f',True), (st['med'],'.2f',True,True), (st['mn'],'.2f',True,True)]))
html += card('二、市场环境分布 <span style="font-size:0.65rem;color:var(--muted)">中证全指 60日涨跌: ≥15%牛 ≤-15%熊</span>',
    table(['市场','信号','占比','10日胜率','中位','均值'], rows))

# ── 3. 关注分分层 × 持有期 ──
html += card('三、B1 关注分分层 × 持有期 <span style="font-size:0.65rem;color:var(--muted)">T+1开盘入场</span>', '')
for hd_name, hd_days in [('10日持有',10),('20日持有',20)]:
    html += f'<h3>{hd_name}</h3>'
    rows = []
    for tier in ['极高≥80','高65~79','关注50~64','一般35~49','低<35']:
        st = s(attn.get((tier,hd_days),[]))
        if st: rows.append(tr([tier, f'{st["n"]:,}', (st['wr'],'.1f',True), (st['med'],'.2f',True,True), (st['mn'],'.2f',True,True), (st['mx'],'.1f'), (st['dd'],'.2f')]))
    html += table(['关注分','N','胜率','中位','均值','最佳','最差'], rows)

# ── 4. 多因子交叉 ──
rows = []
for name in ['全量','有B2','有B2+极高','有B2+浅调<20%','有B2+行业RS≥90','B1强≥5%+极高','B1弱<3%+极高','行业RS≥90(全量)']:
    c = combo_r.get(name,{})
    r10 = c.get('r10')
    if r10:
        tag_html = ''
        if r10['wr']>=55: tag_html='<span class="tag tag-g">优质</span>'
        elif r10['wr']>=50: tag_html='<span class="tag tag-y">关注</span>'
        rows.append(tr([name+tag_html, f'{c["n"]:,}', (r10['wr'],'.1f',True), (r10['med'],'.2f',True,True), (r10['mn'],'.2f',True,True), (r10['mx'],'.1f'), (r10['dd'],'.2f')]))
html += card('四、多因子交叉矩阵 <span style="font-size:0.65rem;color:var(--muted)">T+1开盘·10日持有</span>',
    table(['条件','N','胜率','中位','均值','最佳','最差'], rows))

# ── 5. 入场时机 ──
entries = {
    'T+1开盘 5日': s([r.get('ret_b1_5d') for r in wide if r.get('ret_b1_5d') is not None]),
    'T+1开盘 10日': s([r.get('ret_b1_10d') for r in wide if r.get('ret_b1_10d') is not None]),
    'T+1开盘 20日': s([r.get('ret_b1_20d') for r in wide if r.get('ret_b1_20d') is not None]),
    'T+0收盘 5日': s([r.get('ret_b1c_5d') for r in wide if r.get('ret_b1c_5d') is not None]),
    'T+0收盘 10日': s([r.get('ret_b1c_10d') for r in wide if r.get('ret_b1c_10d') is not None]),
    'T+0收盘 20日': s([r.get('ret_b1c_20d') for r in wide if r.get('ret_b1c_20d') is not None]),
}
rows = [tr([name, f'{st["n"]:,}', (st['wr'],'.1f',True), (st['med'],'.2f',True,True), (st['mn'],'.2f',True,True)]) for name,st in entries.items() if st]
html += card('五、入场时机对比', table(['入场方式','N','胜率','中位','均值'], rows))

# ── 6. 卖出策略 ──
if exit_stats:
    rows = []
    for name in ['hold_5','hold_10','hold_20','hold_60','ma10_exit','ma20_exit','stop7']:
        st = exit_stats.get(name)
        if st:
            tag = '<span class="tag tag-g">推荐</span>' if name=='hold_10' else ''
            rows.append(tr([ex_labels.get(name,name)+tag, f'{st["n"]:,}', (st['win_rate'],'.1f',True), (st['median'],'.2f',True,True), (st['mean'],'.2f',True,True), (st['max_dd'],'.2f')]))
    html += card('六、卖出策略对比 <span style="font-size:0.65rem;color:var(--muted)">持有 vs 动态止损</span>',
        table(['策略','N','胜率','中位','均值','最大亏损'], rows))

# ── 7. 关键发现 ──
findings = [
    ('B2 是唯一强区分器（但 B1 时不可知）','有B2信号胜率68.9% vs 无B2仅29.1%。然而B1当天所有可观测因子预测B2的能力仅40~49%（基准44%），无法筛选。实战应以B1轻仓、B2加仓为主。','tag-y'),
    ('关注分 v3.5 区分力弱','五档之间胜率几乎无差异（45~50%），h_rs250权重50分过度放大，建议下次标定降低RS权重、提升距H天数和行业RS权重。','tag-r'),
    ('浅回调优于深回调','回调<20%的信号10日胜率71.7%中位+5.83%，显著优于深调>35%的69.2%/+3.87%。V型反弹>深蹲再起。','tag-g'),
    ('行业RS有加成','行业RS_20≥90的信号胜率56.0%中位+1.44%，是B1时点唯一有区分力的可观测因子。强势行业里的信号质量更高。','tag-g'),
    ('温和突破 > 猛烈突破','B1涨幅<3%的胜率49.4%高于B1涨幅≥5%的44.1%。缩量温和推升比放量猛拉可靠。','tag-y'),
    ('h_rs250 无区分力','RS≥90(68.8%) vs 80~89(69.1%) vs <70(69.2%)，胜率完全相同。50的门禁足够，再高不加分。','tag-r'),
    ('动态止损劣于固定持有','MA10/MA20/7%止损的胜率均显著低于持有10日。MW信号是突破型信号，正常回调即触发止损，杀死利润。','tag-y'),
    ('20日持有最优','关注分极高+20日持有胜率49.6%均值+2.10%，长持优于短持但边际递减。','tag-g'),
]
for title,desc,tag in findings:
    html += f'<div class="finding"><strong>{title}</strong> <span class="tag {tag}">{tag.replace("tag-","").replace("g","优").replace("r","警").replace("y","注")}</span><span>{desc}</span></div>'

html += f'<div class="footer">MW 信号回测报告 v2 · 引擎 v5.2 · {datetime.now().strftime("%Y-%m-%d")}</div></div></body></html>'

with open(OUT, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"→ {OUT} ({len(html):,} 字符, {(datetime.now()-t0).total_seconds():.0f}s)")
