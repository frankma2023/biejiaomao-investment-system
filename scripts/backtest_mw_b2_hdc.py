"""
MW B2 回测报告 v2 · 对齐旧报告方案
B2信号 + MW形态评分(score字段)分层 + B1 T+1开盘 + 20日持有
"""
import json, os, numpy as np
from datetime import datetime
from collections import defaultdict

WIDE = 'D:/hanako/investment-system/config/strategy/mw_backtest_wide.json'
OUT = 'D:/hanako/investment-system/config/strategy/mw_b2_hdc_report.html'

with open(WIDE, 'r') as f:
    wide = json.load(f)
print(f"加载 {len(wide)} 条")

# 只取B2信号
b2 = [r for r in wide if r['has_b2']]
print(f"B2信号: {len(b2)} 条")

def s(arr, label=''):
    arr = np.array([x for x in arr if x is not None and not (isinstance(x,float) and np.isnan(x))])
    if len(arr) < 5: return None
    wr = (arr > 0).mean()
    return {'n':len(arr),'wr':round(wr*100,1),'med':round(np.median(arr)*100,2),
            'mn':round(arr.mean()*100,2),'mx':round(arr.max()*100,1),'dd':round(arr.min()*100,2)}

# 基准：全量B2，20日持有
b2_all_ret = [r['ret_b1_20d'] for r in b2 if r.get('ret_b1_20d') is not None]
b2_all = s(b2_all_ret, '全量B2')

# MW形态评分分层 (HDC score, 字段名: score)
tiers_hdc = [
    ('≥80', lambda r: (r.get('score') or 0) >= 80),
    ('70~79', lambda r: 70 <= (r.get('score') or 0) <= 79),
    ('60~69', lambda r: 60 <= (r.get('score') or 0) <= 69),
    ('50~59', lambda r: 50 <= (r.get('score') or 0) <= 59),
    ('40~49', lambda r: 40 <= (r.get('score') or 0) <= 49),
    ('<40', lambda r: (r.get('score') or 0) < 40),
]
hdc_tiers = {}
for label, fn in tiers_hdc:
    sub = [r for r in b2 if fn(r)]
    rets = [r['ret_b1_20d'] for r in sub if r.get('ret_b1_20d') is not None]
    st = s(rets, label)
    if st:
        st['label'] = label
        st['excess'] = round(st['mn'] - b2_all['mn'], 1) if b2_all else 0
        st['wr_diff'] = round(st['wr'] - b2_all['wr'], 1) if b2_all else 0
        hdc_tiers[label] = st

# PLUS
plus_sub = [r for r in b2 if r['is_plus']]
plus_rets = [r['ret_b1_20d'] for r in plus_sub if r.get('ret_b1_20d') is not None]
plus_stats = s(plus_rets, 'PLUS')

# 年度
yearly = defaultdict(lambda: {'all_n':0,'all_r':[],'h70_n':0,'h70_r':[]})
for r in b2:
    yr = r['b1_date'][:4]
    if r.get('ret_b1_20d') is not None:
        yearly[yr]['all_r'].append(r['ret_b1_20d'])
        yearly[yr]['all_n'] += 1
    if (r.get('score') or 0) >= 70 and r.get('ret_b1_20d') is not None:
        yearly[yr]['h70_r'].append(r['ret_b1_20d'])
        yearly[yr]['h70_n'] += 1

# 因子增量分析
def factor_drill(base_subset, base_stats, factor_name, filter_fn, label):
    sub = [r for r in base_subset if filter_fn(r)]
    rets = [r['ret_b1_20d'] for r in sub if r.get('ret_b1_20d') is not None]
    st = s(rets, label)
    if st and base_stats:
        st['vs_base'] = round(st['wr'] - base_stats['wr'], 1)
    return st

# ≥70层
h70_sub = [r for r in b2 if (r.get('score') or 0) >= 70]
h70_stats = s([r['ret_b1_20d'] for r in h70_sub if r.get('ret_b1_20d') is not None])
h70_drill = {}
if h70_stats:
    h70_drill['B2涨幅>8%'] = factor_drill(h70_sub, h70_stats, 'B2涨幅>8%',
        lambda r: (r.get('b2_return_pct') or 0) > 8, 'B2涨幅>8%')
    h70_drill['B2涨幅<5%'] = factor_drill(h70_sub, h70_stats, 'B2涨幅<5%',
        lambda r: (r.get('b2_return_pct') or 0) < 5, 'B2涨幅<5%')
    h70_drill['牛市'] = factor_drill(h70_sub, h70_stats, '牛市',
        lambda r: r.get('market_regime') == '牛市', '牛市')
    h70_drill['熊市'] = factor_drill(h70_sub, h70_stats, '熊市',
        lambda r: r.get('market_regime') == '熊市', '熊市')
    h70_drill['回调>40%'] = factor_drill(h70_sub, h70_stats, '回调>40%',
        lambda r: (r.get('decline_pct') or 0) > 40, '回调>40%')
    h70_drill['行业RS≥90'] = factor_drill(h70_sub, h70_stats, '行业RS≥90',
        lambda r: (r.get('ind_rs20') or 0) >= 90, '行业RS≥90')

# 50~69层
h50_sub = [r for r in b2 if 50 <= (r.get('score') or 0) <= 69]
h50_stats = s([r['ret_b1_20d'] for r in h50_sub if r.get('ret_b1_20d') is not None])
h50_drill = {}
if h50_stats:
    h50_drill['回调>40%'] = factor_drill(h50_sub, h50_stats, '回调>40%',
        lambda r: (r.get('decline_pct') or 0) > 40, '回调>40%')
    h50_drill['熊市'] = factor_drill(h50_sub, h50_stats, '熊市',
        lambda r: r.get('market_regime') == '熊市', '熊市')

# <50层
hlo_sub = [r for r in b2 if (r.get('score') or 0) < 50]
hlo_stats = s([r['ret_b1_20d'] for r in hlo_sub if r.get('ret_b1_20d') is not None])
hlo_drill = {}
if hlo_stats:
    hlo_drill['回调>40%'] = factor_drill(hlo_sub, hlo_stats, '回调>40%',
        lambda r: (r.get('decline_pct') or 0) > 40, '回调>40%')
    hlo_drill['熊市'] = factor_drill(hlo_sub, hlo_stats, '熊市',
        lambda r: r.get('market_regime') == '熊市', '熊市')

# ── HTML ──
def td(v, fmt='.1f', sign=False, cls=''):
    if v is None: return '<td>-</td>'
    c = f' class="{cls}"' if cls else ''
    p = '+' if (sign and isinstance(v,(int,float)) and v>0) else ''
    return f'<td{c}>{p}{v:{fmt}}</td>'

def color_td(v, fmt='.1f', suffix=''):
    if v is None: return '<td>-</td>'
    c = 'positive' if v > 0 else ('negative' if v < 0 else '')
    p = '+' if v > 0 else ''
    s = suffix if suffix else ''
    return f'<td class="{c}">{p}{v:{fmt}}{s}</td>'

print("生成HTML...", end=' ', flush=True)

html = f'''<!DOCTYPE html><html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>MW B2 确认信号 · 回测报告 v2</title>
<style>
:root{{--bg:#0d0d12;--card:rgba(26,26,31,0.85);--border:rgba(255,255,255,0.06);
--text:#e0e0e0;--text-secondary:#8b8b90;--accent:#f59e0b;
--accent-subtle:rgba(245,158,11,0.1);--red:#ef4444;--green:#10b981;
--font-display:'Instrument Serif','Noto Serif SC',Georgia,serif;
--font-body:'Inter','PingFang SC',system-ui,sans-serif;
--font-mono:'JetBrains Mono','SF Mono',monospace;--shadow:0 2px 16px rgba(0,0,0,0.3)}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:var(--font-body);font-size:13px;line-height:1.7;max-width:960px;margin:0 auto;padding:40px 24px 80px}}
.cover{{text-align:center;padding:60px 0 40px;border-bottom:1px solid var(--border);margin-bottom:40px}}
.cover h1{{font-family:var(--font-display);font-size:2rem;font-weight:400;margin-bottom:8px}}
.cover .sub{{font-size:.85rem;color:var(--text-secondary)}}
.cover .meta{{font-size:.7rem;color:var(--text-secondary);margin-top:12px}}
h2{{font-family:var(--font-display);font-size:1.25rem;font-weight:400;color:var(--accent);margin:36px 0 16px;padding-bottom:8px;border-bottom:1px solid var(--border)}}
h3{{font-family:var(--font-display);font-size:1rem;font-weight:400;margin:24px 0 10px}}
p{{margin:10px 0;color:var(--text-secondary)}}
table{{width:100%;border-collapse:collapse;margin:12px 0 20px;font-size:.78rem}}
th{{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);font-weight:500;color:var(--text);font-family:var(--font-mono);font-size:.7rem;letter-spacing:.05em}}
td{{padding:6px 10px;border-bottom:1px solid rgba(255,255,255,.03);font-family:var(--font-mono)}}
tr:hover td{{background:rgba(245,158,11,.03)}}
.positive{{color:var(--green)}}.negative{{color:var(--red)}}.accent{{color:var(--accent)}}.muted{{color:var(--text-secondary);font-size:.7rem}}
.kpi-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:16px 0}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;text-align:center}}
.kpi .val{{font-family:var(--font-display);font-size:1.6rem;color:var(--accent)}}
.kpi .lbl{{font-size:.65rem;color:var(--text-secondary);margin-top:4px;text-transform:uppercase;letter-spacing:.05em}}
.note{{background:var(--accent-subtle);border-left:3px solid var(--accent);padding:10px 14px;border-radius:0 8px 8px 0;margin:12px 0;font-size:.75rem;color:var(--text-secondary)}}
.note strong{{color:var(--accent)}}
code{{background:rgba(255,255,255,.05);padding:1px 5px;border-radius:4px;font-family:var(--font-mono);font-size:.72rem}}
hr{{border:none;border-top:1px solid var(--border);margin:32px 0}}
.flow{{font-family:var(--font-mono);font-size:.75rem;text-align:center;padding:12px;background:rgba(255,255,255,.02);border-radius:8px}}
</style></head><body>
<div class="cover">
<h1>MW B2 确认信号 · 回测报告</h1>
<div class="sub">基于 MW 形态评分（HDC score）的 B2 信号质量分层分析</div>
<div class="meta">引擎 v5.2 · 2016-01 ~ 2026-07 · {len(b2):,} 条 B2 信号 · v2.0 · {datetime.now().strftime("%Y-%m-%d")}</div>
</div>

<h2>摘要</h2>
<div class="kpi-row">
<div class="kpi"><div class="val">{len(b2):,}</div><div class="lbl">B2 确认信号</div></div>
<div class="kpi"><div class="val positive">{b2_all['wr']}%</div><div class="lbl">B2 全量胜率</div></div>
<div class="kpi"><div class="val positive">{b2_all['mn']:+.1f}%</div><div class="lbl">B2 全量均收益</div></div>
'''

# HDC≥80
h80 = hdc_tiers.get('≥80',{})
h70 = hdc_tiers.get('70~79',{})
html += f'<div class="kpi"><div class="val positive">{h80.get("wr",0)}%</div><div class="lbl">HDC ≥80 胜率</div></div>'
html += f'<div class="kpi"><div class="val positive">{h80.get("mn",0):+.1f}%</div><div class="lbl">HDC ≥80 均收益</div></div>'
html += '</div>'

html += f'''<div class="note"><strong>核心结论：</strong>新引擎（v5.2）B2 确认信号 {len(b2):,} 条，全量胜率 {b2_all["wr"]}%。MW 形态评分（HDC）在高分段表现{'优于' if h80.get("wr",0) > b2_all["wr"] else '接近'}全量。回调深度和 B2 涨幅是 HDC 评分外的主要增量因子。</div>

<h2>1. 回测参数</h2>
<table>
<tr><th>参数</th><th>值</th></tr>
<tr><td>引擎版本</td><td>v5.2（H检测v2 + L检测v3 + RS250门禁50）</td></tr>
<tr><td>数据区间</td><td>2016-01-01 ~ 2026-07-17</td></tr>
<tr><td>B2 确认信号</td><td>{len(b2):,} 条（占全部 B1 信号的 {len(b2)/len(wide)*100:.0f}%）</td></tr>
<tr><td>入场方式</td><td>B1 日 T+1 开盘价</td></tr>
<tr><td>持有期</td><td>20 个交易日（H20）</td></tr>
<tr><td>评分体系</td><td>MW 形态评分（I2+D+I1+H+Sig，满分 100）</td></tr>
</table>

<h2>2. MW 形态评分分层</h2>
<table>
<tr><th>评分区间</th><th>信号数</th><th>胜率</th><th>均收益</th><th>超额收益</th><th>vs全量</th></tr>'''

for label in ['≥80','70~79','60~69','50~59','40~49','<40']:
    t = hdc_tiers.get(label)
    if not t: continue
    bg = ' style="background:rgba(245,158,11,0.08)"' if label in ('≥80','70~79') else ''
    html += f'<tr{bg}><td><strong>{label}</strong></td><td>{t["n"]:,}</td>{color_td(t["wr"],".1f","%")}{color_td(t["mn"],".1f","%")}{color_td(t["excess"],".1f","pp")}{color_td(t["wr_diff"],".1f","pp")}</tr>'

html += '</table>'

# 单调性判断
h80_wr = hdc_tiers.get('≥80',{}).get('wr',0)
hlo_wr = hdc_tiers.get('<40',{}).get('wr',0)
mono = abs(h80_wr - hlo_wr) if h80_wr and hlo_wr else 0
mono_verdict = '极佳' if mono >= 15 else ('良好' if mono >= 8 else '一般')

html += f'''<div class="note"><strong>单调性：</strong>HDC 评分在 B2 子集上的单调性为 <strong>{mono_verdict}</strong>（最高层与最低层胜率差 {mono:.1f}pp）。高分层（≥70）仅占 B2 的 { (hdc_tiers.get('≥80',{}).get('n',0) + hdc_tiers.get('70~79',{}).get('n',0)) / len(b2) * 100:.0f}% 但质量突出。</div>'''

# PLUS
html += f'''<h2>3. PLUS 信号</h2>
<table><tr><th></th><th>N</th><th>胜率</th><th>均收益</th><th>中位</th></tr>
<tr style="background:rgba(245,158,11,0.08)"><td><strong>PLUS</strong></td><td>{plus_stats["n"]:,}</td>{color_td(plus_stats["wr"],".1f","%")}{color_td(plus_stats["mn"],".1f","%")}{color_td(plus_stats["med"],".2f","%")}</tr>
<tr><td>全量B2</td><td>{b2_all["n"]:,}</td>{color_td(b2_all["wr"],".1f","%")}{color_td(b2_all["mn"],".1f","%")}{color_td(b2_all["med"],".2f","%")}</tr>
</table>'''

# 因子增量
html += '<h2>4. 因子增量检验</h2>'
def drill_table(title, base_n, base_wr, drills):
    h = f'<h3>{title}（{base_n:,} 条，胜率 {base_wr}%）</h3>'
    t = '<table><tr><th>因子</th><th>子层</th><th>N</th><th>胜率</th><th>vs 层均值</th><th>有增量？</th></tr>'
    for name, st in drills.items():
        if not st: continue
        diff = st.get('vs_base', 0)
        star = '★' if abs(diff) >= 3 and st['n'] >= 100 else ('样本小' if st['n'] < 50 else '')
        diff_str = f'+{diff}pp' if diff > 0 else f'{diff}pp'
        t += f'<tr><td>{name.split(chr(62))[0] if chr(62) in name else name}</td><td>{name}</td><td>{st["n"]:,}</td>{color_td(st["wr"],".1f","%")}<td class="{"positive" if diff>0 else "negative"}">{diff_str}</td><td>{star}</td></tr>'
    t += '</table>'
    return h + t

html += drill_table('HDC ≥70 层', h70_stats['n'] if h70_stats else 0, h70_stats['wr'] if h70_stats else 0, h70_drill)
html += drill_table('HDC 50~69 层', h50_stats['n'] if h50_stats else 0, h50_stats['wr'] if h50_stats else 0, h50_drill)
html += drill_table('HDC <50 层', hlo_stats['n'] if hlo_stats else 0, hlo_stats['wr'] if hlo_stats else 0, hlo_drill)

# 年度
html += '<h2>5. 年度稳定性</h2>'
html += '<table><tr><th>年份</th><th>B2全量N</th><th>全量胜率</th><th>HDC≥70 N</th><th>HDC≥70胜率</th><th>超额</th></tr>'
for yr in sorted(yearly.keys()):
    y = yearly[yr]
    all_s = s(y['all_r'])
    h70_s = s(y['h70_r']) if y['h70_r'] else None
    if all_s:
        excess = round(h70_s['wr'] - all_s['wr'], 1) if h70_s else 0
        html += f'<tr><td>{yr}</td><td>{all_s["n"]:,}</td>{color_td(all_s["wr"],".1f","%")}<td>{y["h70_n"]:,}</td>{color_td(h70_s["wr"],".1f","%") if h70_s else "<td>-</td>"}{color_td(excess,".1f","pp")}</tr>'
html += '</table>'

# 仓位建议
html += f'''<h2>6. 仓位建议</h2>
<table><tr><th>MW评分</th><th>回调深度</th><th>建议</th><th>胜率</th><th>均收益</th></tr>
<tr style="background:rgba(76,175,80,0.08)"><td>≥70</td><td>任意</td><td><strong>重仓</strong></td><td>{h70_stats["wr"] if h70_stats else "-"}%</td>{color_td(h70_stats["mn"] if h70_stats else 0,".1f","%")}</tr>
<tr style="background:rgba(76,175,80,0.08)"><td>50~69</td><td>>40%</td><td><strong>重仓</strong></td><td>{h50_drill.get("回调>40%",{}).get("wr","-")}%</td><td>-</td></tr>
<tr><td>50~69</td><td>≤40%</td><td>标准</td><td>{h50_stats["wr"] if h50_stats else "-"}%</td>{color_td(h50_stats["mn"] if h50_stats else 0,".1f","%")}</tr>
<tr><td><50</td><td>>40%</td><td>标准</td><td>{hlo_drill.get("回调>40%",{}).get("wr","-")}%</td><td>-</td></tr>
<tr style="color:var(--text-secondary)"><td><50</td><td>≤40%</td><td>轻仓/放弃</td><td>{hlo_stats["wr"] if hlo_stats else "-"}%</td>{color_td(hlo_stats["mn"] if hlo_stats else 0,".1f","%")}</tr>
</table>'''

# 局限
html += f'''<h2>7. 局限</h2>
<table><tr><th>#</th><th>局限</th></tr>
<tr><td>1</td><td>B2 日买入胜率远低于 B1日买入（已验证 B1次日开盘 > B2次日开盘）。B2 是确认信号非买入信号。</td></tr>
<tr><td>2</td><td>PLUS 仅 {plus_stats["n"]} 条，统计意义有限。</td></tr>
<tr><td>3</td><td>新引擎 H 检测规则（pre_rise≥30%+max涨幅）比旧版严格，B2 信号总数从旧版 39,428 降至新版 {len(b2):,}。</td></tr>
<tr><td>4</td><td>MW 形态评分权重基于旧版信号回测确定，新引擎下可能需要重新标定。</td></tr>
<tr><td>5</td><td>未包含基本面因子（PE/PB/ROE），需要从 fundamental_indicator 补充。</td></tr>
</table>
<hr>
<div style="text-align:center;margin-top:60px;padding-top:20px;border-top:1px solid var(--border);color:var(--text-secondary);font-size:.7rem">
MW B2 确认信号回测报告 v2.0 · 引擎 v5.2 · 欧奈尔 CAN SLIM 量化系统 · Project Hanako
</div></body></html>'''

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"→ {OUT} ({len(html):,} 字符)")
