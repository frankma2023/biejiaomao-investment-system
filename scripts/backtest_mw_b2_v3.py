"""
MW B2 回测报告 v3 · 有效分层维度
"""
import json, os, numpy as np
from datetime import datetime
from collections import defaultdict

WIDE = 'D:/hanako/investment-system/config/strategy/mw_backtest_wide.json'
OUT = 'D:/hanako/investment-system/config/strategy/mw_b2_report_v3.html'

with open(WIDE, 'r') as f: wide = json.load(f)
b2_all = [r for r in wide if r['has_b2']]
print(f"B2: {len(b2_all)} 条")

def s(arr):
    arr = np.array([x for x in arr if x is not None and not (isinstance(x,float) and np.isnan(x))])
    if len(arr) < 5: return None
    return {'n':len(arr),'wr':round((arr>0).mean()*100,1),'med':round(np.median(arr)*100,2),
            'mn':round(arr.mean()*100,2),'mx':round(arr.max()*100,1),'dd':round(arr.min()*100,2)}

def ret20(subset): return [r['ret_b1_20d'] for r in subset if r.get('ret_b1_20d') is not None]
def ret10(subset): return [r['ret_b1_10d'] for r in subset if r.get('ret_b1_10d') is not None]

# 基准
b2_20 = s(ret20(b2_all))
b2_10 = s(ret10(b2_all))

# ── 分层维度 ──
dims = {
    '行业 RS_20': [
        ('≥90（行业领涨）', lambda r: (r.get('ind_rs20') or 0) >= 90),
        ('80~89', lambda r: 80 <= (r.get('ind_rs20') or 0) <= 89),
        ('60~79', lambda r: 60 <= (r.get('ind_rs20') or 0) <= 79),
        ('<60 或缺失', lambda r: (r.get('ind_rs20') or 0) < 60),
    ],
    '回调深度': [
        ('浅调 <20%（V型反弹）', lambda r: (r.get('decline_pct') or 0) < 20),
        ('中调 20~35%', lambda r: 20 <= (r.get('decline_pct') or 0) <= 35),
        ('深调 >35%', lambda r: (r.get('decline_pct') or 0) > 35),
    ],
    'B2 涨幅': [
        ('强 B2 ≥8%', lambda r: (r.get('b2_return_pct') or 0) >= 8),
        ('中 B2 5~8%', lambda r: 5 <= (r.get('b2_return_pct') or 0) < 8),
        ('弱 B2 <5%', lambda r: (r.get('b2_return_pct') or 0) < 5),
    ],
    '市场环境': [
        ('牛市（60日涨≥15%）', lambda r: r.get('market_regime') == '牛市'),
        ('震荡市', lambda r: r.get('market_regime') == '震荡市'),
        ('熊市（60日跌≥15%）', lambda r: r.get('market_regime') == '熊市'),
    ],
    '乖离率（MA20）': [
        ('正乖离 0~10%', lambda r: r.get('deviation_ma20') is not None and 0 <= r['deviation_ma20'] < 10),
        ('负乖离（超跌反弹）', lambda r: r.get('deviation_ma20') is not None and r['deviation_ma20'] < 0),
        ('高乖离 ≥10%', lambda r: r.get('deviation_ma20') is not None and r['deviation_ma20'] >= 10),
    ],
    'B1 涨幅': [
        ('强 B1 ≥5%', lambda r: (r.get('b1_return_pct') or 0) >= 5),
        ('温和 B1 2~5%', lambda r: 2 <= (r.get('b1_return_pct') or 0) < 5),
        ('弱 B1 <2%', lambda r: (r.get('b1_return_pct') or 0) < 2),
    ],
}

# 交叉维度
crosses = [
    ('🏆 行业领涨 + 浅回调', lambda r: (r.get('ind_rs20') or 0) >= 90 and (r.get('decline_pct') or 0) < 20),
    ('🏆 行业领涨 + 深回调', lambda r: (r.get('ind_rs20') or 0) >= 90 and (r.get('decline_pct') or 0) > 35),
    ('🏆 熊市 + 深回调', lambda r: r.get('market_regime') == '熊市' and (r.get('decline_pct') or 0) > 35),
    ('🏆 浅回调 + 强B2', lambda r: (r.get('decline_pct') or 0) < 20 and (r.get('b2_return_pct') or 0) >= 8),
    ('PLUS 信号', lambda r: r.get('is_plus') == 1),
]

# 计算
dim_results = {}
for dim_name, tiers in dims.items():
    dim_results[dim_name] = {}
    for label, fn in tiers:
        sub = [r for r in b2_all if fn(r)]
        st20 = s(ret20(sub))
        st10 = s(ret10(sub))
        if st20:
            dim_results[dim_name][label] = {'n':len(sub),'r20':st20,'r10':st10}

cross_results = {}
for label, fn in crosses:
    sub = [r for r in b2_all if fn(r)]
    st20 = s(ret20(sub))
    st10 = s(ret10(sub))
    if st20:
        cross_results[label] = {'n':len(sub),'r20':st20,'r10':st10}

# PLUS
plus_sub = [r for r in b2_all if r['is_plus']]
plus_s = s(ret20(plus_sub))

# 年度
yearly = defaultdict(lambda: {'n':0,'r':[]})
for r in b2_all:
    yr = r['b1_date'][:4]
    if r.get('ret_b1_20d') is not None:
        yearly[yr]['r'].append(r['ret_b1_20d'])
        yearly[yr]['n'] += 1

# ── HTML ──
def td(v, fmt='.1f', sign=False, cls=''):
    if v is None: return '<td>-</td>'
    c = f' class="{cls}"' if cls else ''
    p = '+' if (sign and isinstance(v,(int,float)) and v>0) else ''
    return f'<td{c}>{p}{v:{fmt}}</td>'

def ctd(v, fmt='.1f', suffix=''):
    if v is None: return '<td>-</td>'
    c = 'positive' if v > 0 else ('negative' if v < 0 else '')
    return f'<td class="{c}">{v:{fmt}}{suffix}</td>'

def dim_table(dim_results, dim_name):
    h = f'<h3>{dim_name}</h3><table><tr><th>子层</th><th>N</th><th>20日胜率</th><th>20日中位</th><th>20日均值</th><th>10日胜率</th><th>10日中位</th></tr>'
    for label, st in dim_results[dim_name].items():
        r20 = st['r20']; r10 = st.get('r10')
        h += f'<tr><td>{label}</td><td>{st["n"]:,}</td>{ctd(r20["wr"],".1f","%")}{ctd(r20["med"],".2f","%")}{ctd(r20["mn"],".2f","%")}'
        h += f'{ctd(r10["wr"],".1f","%") if r10 else "<td>-</td>"}{ctd(r10["med"],".2f","%") if r10 else "<td>-</td>"}</tr>'
    h += '</table>'
    return h

print("生成HTML...")

html = f'''<!DOCTYPE html><html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>MW B2 信号 · 有效分层回测 v3</title>
<style>
:root{{--bg:#0d0d12;--card:rgba(26,26,31,0.85);--border:rgba(255,255,255,0.06);
--text:#e0e0e0;--text-secondary:#8b8b90;--accent:#f59e0b;
--accent-subtle:rgba(245,158,11,0.1);--red:#ef4444;--green:#10b981;
--font-display:'Instrument Serif','Noto Serif SC',Georgia,serif;
--font-body:'Inter','PingFang SC',system-ui,sans-serif;
--font-mono:'JetBrains Mono','SF Mono',monospace}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:var(--font-body);font-size:13px;line-height:1.7;max-width:960px;margin:0 auto;padding:40px 24px 80px}}
.cover{{text-align:center;padding:60px 0 40px;border-bottom:1px solid var(--border);margin-bottom:40px}}
.cover h1{{font-family:var(--font-display);font-size:1.8rem;font-weight:400;margin-bottom:8px}}
.cover .sub{{font-size:.85rem;color:var(--text-secondary)}}
.cover .meta{{font-size:.7rem;color:var(--text-secondary);margin-top:12px}}
h2{{font-family:var(--font-display);font-size:1.2rem;font-weight:400;color:var(--accent);margin:36px 0 16px;padding-bottom:8px;border-bottom:1px solid var(--border)}}
h3{{font-size:.78rem;font-weight:600;margin:18px 0 6px;color:var(--text)}}
p{{margin:10px 0;color:var(--text-secondary)}}
table{{width:100%;border-collapse:collapse;margin:8px 0 16px;font-size:.75rem}}
th{{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);font-weight:500;color:var(--text);font-family:var(--font-mono);font-size:.65rem;letter-spacing:.05em}}
td{{padding:5px 10px;border-bottom:1px solid rgba(255,255,255,.03);font-family:var(--font-mono)}}
tr:hover td{{background:rgba(245,158,11,.03)}}
tr.hl td{{background:rgba(245,158,11,0.06)}}
.positive{{color:var(--green)}}.negative{{color:var(--red)}}.accent{{color:var(--accent)}}
.kpi-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:16px 0}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center}}
.kpi .val{{font-family:var(--font-display);font-size:1.5rem;color:var(--accent)}}
.kpi .lbl{{font-size:.6rem;color:var(--text-secondary);margin-top:4px;text-transform:uppercase;letter-spacing:.05em}}
.note{{background:var(--accent-subtle);border-left:3px solid var(--accent);padding:10px 14px;border-radius:0 8px 8px 0;margin:12px 0;font-size:.72rem;color:var(--text-secondary)}}
.note strong{{color:var(--accent)}}
.flow{{font-family:var(--font-mono);font-size:.72rem;text-align:center;padding:12px;background:rgba(255,255,255,.02);border-radius:8px}}
.best{{background:rgba(16,185,129,0.06)}}
</style></head><body>
<div class="cover">
<h1>MW B2 确认信号 · 有效分层回测</h1>
<div class="sub">新引擎 v5.2 下的真实区分维度</div>
<div class="meta">{len(b2_all):,} 条 B2 信号 · 2016-01 ~ 2026-07 · B1 T+1 开盘入场 · 20 日持有 · {datetime.now().strftime("%Y-%m-%d")}</div>
</div>

<h2>摘要</h2>
<div class="kpi-row">
<div class="kpi"><div class="val">{len(b2_all):,}</div><div class="lbl">B2 确认信号</div></div>
<div class="kpi"><div class="val positive">{b2_20['wr']}%</div><div class="lbl">20日胜率</div></div>
<div class="kpi"><div class="val positive">{b2_20['med']:+.1f}%</div><div class="lbl">20日中位收益</div></div>
<div class="kpi"><div class="val positive">{b2_20['mn']:+.1f}%</div><div class="lbl">20日平均收益</div></div>
<div class="kpi"><div class="val">{plus_s['n']:,}</div><div class="lbl">PLUS 信号</div></div>
</div>

<div class="note"><strong>关键发现：</strong>新引擎 v5.2 的 H/L 检测（pre_rise≥30% + max涨幅 + RS250门禁）已预过滤低质信号。B2 全量胜率即达 {b2_20['wr']}%，MW 形态评分（HDC）失去分层效力。真正有增量区分力的维度是 <strong>行业 RS_20、回调深度、B2 涨幅、市场环境</strong>。</div>

<h2>1. 行业 RS_20（行业动量）</h2>
<p>B1 日所属行业的 RS_20 强度。行业领涨时突破信号质量显著更高。</p>
{dim_table(dim_results, '行业 RS_20')}

<h2>2. 回调深度</h2>
<p>H→L 的最大跌幅。浅回调（V 型反弹）信号质量最高。</p>
{dim_table(dim_results, '回调深度')}

<h2>3. B2 涨幅</h2>
<p>B2 确认日的涨幅强度。强劲的 B2 意味着更强的突破动能。</p>
{dim_table(dim_results, 'B2 涨幅')}

<h2>4. 市场环境</h2>
<p>中证全指 60 日涨跌分类：≥15%牛 ≤-15%熊。</p>
{dim_table(dim_results, '市场环境')}

<h2>5. 乖离率（入场时偏离 MA20）</h2>
{dim_table(dim_results, '乖离率（MA20）')}

<h2>6. B1 涨幅</h2>
{dim_table(dim_results, 'B1 涨幅')}

<h2>7. 最优交叉组合</h2>
<table><tr><th>组合</th><th>N</th><th>20日胜率</th><th>20日中位</th><th>20日均值</th><th>10日胜率</th><th>10日中位</th></tr>'''
for label, st in cross_results.items():
    r20 = st['r20']; r10 = st.get('r10')
    tag = ' class="hl"' if r20['wr'] >= 75 else ''
    html += f'<tr{tag}><td>{label}</td><td>{st["n"]:,}</td>{ctd(r20["wr"],".1f","%")}{ctd(r20["med"],".2f","%")}{ctd(r20["mn"],".2f","%")}'
    html += f'{ctd(r10["wr"],".1f","%") if r10 else "<td>-</td>"}{ctd(r10["med"],".2f","%") if r10 else "<td>-</td>"}</tr>'
html += '</table>'

# 年度
html += '<h2>8. 年度稳定性</h2><table><tr><th>年份</th><th>N</th><th>20日胜率</th><th>20日中位</th><th>20日均值</th></tr>'
for yr in sorted(yearly.keys()):
    y = yearly[yr]
    st = s(y['r'])
    if st:
        html += f'<tr><td>{yr}</td><td>{st["n"]:,}</td>{ctd(st["wr"],".1f","%")}{ctd(st["med"],".2f","%")}{ctd(st["mn"],".2f","%")}</tr>'
html += '</table>'

html += f'''<h2>9. 结论</h2>
<div class="note"><strong>操作建议：</strong>B2 信号全量即高质量（胜率 {b2_20['wr']}%，中位 +{b2_20['med']}%），无需复杂分层。如需进一步筛选，优先看行业 RS_20（≥90 时胜率提升明显）和回调深度（浅调<20% 最佳）。B2 涨幅≥8% 时 20 日胜率可达 70%+。熊市深回调中的 B2 信号是逆向布局窗口。</div>

<hr><div style="text-align:center;margin-top:40px;color:var(--text-secondary);font-size:.65rem">MW B2 回测 v3 · 引擎 v5.2 · Project Hanako</div>
</body></html>'''

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"→ {OUT} ({len(html):,} 字符)")
