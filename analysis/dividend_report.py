"""
红利指数超跌触发验证 - Ticket 4: HTML 报告生成（hanako-glass 样式）
"""
import json
from collections import defaultdict

RESULTS = 'D:/hanako/investment-system/analysis/dividend_results.json'
OUT = 'D:/hanako/investment-system/analysis/dividend_trigger_report.html'

COND_ORDER = ['dd250_10','dd250_15','dd250_20','ddhist_15','ddhist_25',
              'pe_pct_10','pe_pct_20','pe_pct_30','pb_pct_10','pb_pct_20','pb_pct_30',
              'dyr_pct_80','dyr_pct_90']
CAT_NAMES = {'pure': '纯红利', 'lowvol': '红利低波', 'quality': '红利质量/成长', 'other': '行业/其他'}

CSS = """
body{background:var(--bg);color:var(--text-primary);font-family:var(--font-body);font-size:13px;margin:0;padding:24px}
.wrap{max-width:1200px;margin:0 auto}
h1{font-family:var(--font-display);font-size:1.4rem;font-weight:400;color:var(--accent);margin:0 0 6px}
.sub{font-size:0.7rem;color:var(--muted);margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px;margin-bottom:16px}
.card h2{font-size:0.85rem;font-weight:600;color:var(--text-primary);margin:0 0 12px;display:flex;align-items:center;gap:8px}
.card h2 .tag{font-size:0.55rem;background:var(--color-accent-subtle);color:var(--color-accent);padding:2px 8px;border-radius:8px}
table{width:100%;border-collapse:collapse;font-size:0.72rem}
th{padding:8px 10px;border-bottom:2px solid var(--border);font-weight:600;color:var(--muted);font-size:0.6rem;text-transform:uppercase;letter-spacing:0.05em;text-align:right;background:var(--card)}
th:first-child{text-align:left}
td{padding:7px 10px;border-bottom:1px solid var(--border);text-align:right;font-family:var(--font-mono)}
td:first-child{text-align:left;font-family:var(--font-body)}
tr:hover td{background:var(--color-accent-subtle)}
.up{color:#ef4444;font-weight:600}
.dn{color:#10b981;font-weight:600}
.mid{color:#f59e0b;font-weight:600}
.muted{color:var(--muted)}
.highlight{background:var(--color-accent-subtle)!important}
.legend{display:flex;gap:16px;font-size:0.65rem;color:var(--muted);margin-bottom:12px}
.legend span{display:inline-flex;align-items:center;gap:4px}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.meta{font-size:0.62rem;color:var(--muted);margin-top:4px}
"""

def load():
    with open(RESULTS, 'r', encoding='utf-8') as f:
        return json.load(f)

def build_html(data):
    cond_labels = data['cond_labels']
    cat_agg = data['cat_agg']
    index_results = data['index_results']

    # 全池合并
    merged = defaultdict(list)
    for cat, conds in cat_agg.items():
        for cond, windows in conds.items():
            merged[cond].extend(windows.get('20', {}).get('data', []))

    # 类别汇总
    cat_rows = {}
    for cat in CAT_NAMES:
        rows = []
        for cond in COND_ORDER:
            wins = cat_agg.get(cat, {}).get(cond, {}).get('20', {})
            n = wins.get('n', 0)
            if n == 0:
                continue
            data20 = wins.get('data', [])
            wr = sum(1 for r in data20 if r > 0) / n * 100 if n else 0
            avg = sum(data20) / n if n else 0
            sd = sorted(data20)
            med = sd[n//2] if n % 2 else (sd[n//2-1] + sd[n//2]) / 2
            rows.append({'label': cond_labels.get(cond, cond), 'n': n, 'wr': wr, 'avg': avg, 'med': med})
        rows.sort(key=lambda r: -r['wr'])
        cat_rows[cat] = rows

    # 单指数明细
    index_rows = []
    for code, info in index_results.items():
        best = None
        for cond, stats in info['conds'].items():
            s = stats.get('20', {})
            if s.get('n', 0) < 10:
                continue
            if best is None or s['win_rate'] > best['wr']:
                best = {'cond': cond, 'wr': s['win_rate'], 'n': s['n'], 'avg': s['avg']}
        if best:
            index_rows.append({'code': code, 'name': info['name'], 'cat': info['cat'], **best})
    index_rows.sort(key=lambda r: -r['wr'])

    h = []
    h.append('<!DOCTYPE html>\n<html lang="zh-CN" class="dark">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width,initial-scale=1.0">\n<title>红利指数超跌触发验证</title>\n<link rel="stylesheet" href="/shared/css/hanako-glass.css">\n<style>' + CSS + '</style>\n</head>\n<body>\n<div class="wrap">')
    h.append('<h1>📊 红利指数超跌触发验证报告</h1>')
    h.append('<div class="sub">2016-01 ~ 2026-07 · 次日开盘买入 · 持有 20 个交易日 · 事件合并去重（20日窗口）</div>')
    h.append('<div class="legend"><span><span class="dot" style="background:#ef4444"></span> 胜率≥60%</span><span><span class="dot" style="background:#f59e0b"></span> 胜率50-60%</span><span><span class="dot" style="background:#10b981"></span> 胜率&lt;50%</span></div>')

    # 全池矩阵
    h.append('<div class="card"><h2>🎯 全红利池合并 · 触发-胜率-收益矩阵（20日）</h2><table><thead><tr><th>触发条件</th><th>次数</th><th>胜率</th><th>平均收益</th><th>中位收益</th></tr></thead><tbody>')
    msorted = sorted(merged.items(), key=lambda kv: -(sum(1 for r in kv[1] if r > 0) / len(kv[1])) if kv[1] else 0)
    for cond, returns in msorted:
        if not returns:
            continue
        n = len(returns)
        wr = sum(1 for r in returns if r > 0) / n * 100
        avg = sum(returns) / n
        sr = sorted(returns)
        med = sr[n//2] if n % 2 else (sr[n//2-1] + sr[n//2]) / 2
        hl = ' class="highlight"' if wr >= 63 else ''
        wc = 'up' if wr >= 60 else ('mid' if wr >= 50 else 'dn')
        ac = 'up' if avg > 0 else 'dn'
        h.append('<tr' + hl + '><td>' + cond_labels.get(cond, cond) + '</td><td>' + str(n) + '</td><td class="' + wc + '">' + f'{wr:.1f}%' + '</td><td class="' + ac + '">' + f'{avg:+.2f}%' + '</td><td class="' + ac + '">' + f'{med:+.2f}%' + '</td></tr>')
    h.append('</tbody></table><div class="meta">高亮行 = 胜率≥63% 的最强信号</div></div>')

    # 每类汇总
    for cat, cn in CAT_NAMES.items():
        rows = cat_rows.get(cat, [])
        h.append('<div class="card"><h2>🏷️ ' + cn + ' <span class="tag">' + str(len(rows)) + ' 个条件</span></h2>')
        if not rows:
            h.append('<div class="muted">无有效数据</div></div>')
            continue
        h.append('<table><thead><tr><th>触发条件</th><th>次数</th><th>胜率</th><th>平均收益</th><th>中位收益</th></tr></thead><tbody>')
        for r in rows:
            wc = 'up' if r['wr'] >= 60 else ('mid' if r['wr'] >= 50 else 'dn')
            ac = 'up' if r['avg'] > 0 else 'dn'
            hl = ' class="highlight"' if r['wr'] >= 63 else ''
            h.append('<tr' + hl + '><td>' + r['label'] + '</td><td>' + str(r['n']) + '</td><td class="' + wc + '">' + f"{r['wr']:.1f}%" + '</td><td class="' + ac + '">' + f"{r['avg']:+.2f}%" + '</td><td class="' + ac + '">' + f"{r['med']:+.2f}%" + '</td></tr>')
        h.append('</tbody></table></div>')

    # 单指数
    h.append('<div class="card"><h2>🔍 单指数明细（按最强条件胜率排序，前12）</h2><table><thead><tr><th>指数</th><th>类别</th><th>最强条件</th><th>次数</th><th>胜率</th><th>平均收益</th></tr></thead><tbody>')
    for r in index_rows[:12]:
        wc = 'up' if r['wr'] >= 60 else ('mid' if r['wr'] >= 50 else 'dn')
        ac = 'up' if r['avg'] > 0 else 'dn'
        h.append('<tr><td>' + r['code'] + ' ' + r['name'] + '</td><td>' + CAT_NAMES.get(r['cat'], r['cat']) + '</td><td>' + cond_labels.get(r['cond'], r['cond']) + '</td><td>' + str(r['n']) + '</td><td class="' + wc + '">' + f"{r['wr']:.1f}%" + '</td><td class="' + ac + '">' + f"{r['avg']:+.2f}%" + '</td></tr>')
    h.append('</tbody></table></div>')

    h.append('</div></body></html>')
    return '\n'.join(h)

if __name__ == '__main__':
    data = load()
    html = build_html(data)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"报告已生成: {OUT} ({len(html)} bytes)")
