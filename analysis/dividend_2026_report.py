"""2026年红利指数事件链 HTML 报告"""
import json

DATA = 'D:/hanako/investment-system/analysis/dividend_2026_events.json'
OUT = 'D:/hanako/investment-system/web/analysis/dividend_2026_report.html'

CAT_NAMES = {'pure': '纯红利', 'lowvol': '红利低波', 'quality': '红利质量/成长', 'other': '行业/其他'}

CSS = """
body{background:var(--bg);color:var(--text-primary);font-family:var(--font-body);font-size:13px;margin:0;padding:24px}
.wrap{max-width:1300px;margin:0 auto}
h1{font-family:var(--font-display);font-size:1.4rem;font-weight:400;color:var(--accent);margin:0 0 6px}
.sub{font-size:0.7rem;color:var(--muted);margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px;margin-bottom:16px}
.card h2{font-size:0.85rem;font-weight:600;color:var(--text-primary);margin:0 0 12px}
table{width:100%;border-collapse:collapse;font-size:0.7rem}
th{padding:7px 8px;border-bottom:2px solid var(--border);font-weight:600;color:var(--muted);font-size:0.58rem;text-transform:uppercase;letter-spacing:0.04em;text-align:right;background:var(--card)}
th:first-child,td:first-child{text-align:left}
td{padding:6px 8px;border-bottom:1px solid var(--border);text-align:right;font-family:var(--font-mono)}
td:nth-child(2),td:nth-child(4){text-align:left;font-family:var(--font-body)}
tr:hover td{background:var(--color-accent-subtle)}
.up{color:#ef4444;font-weight:600}
.dn{color:#10b981;font-weight:600}
.mid{color:#f59e0b;font-weight:600}
.muted{color:var(--muted)}
.badge{font-size:0.55rem;padding:2px 8px;border-radius:8px;font-weight:600}
.badge-live{background:rgba(239,68,68,.1);color:#ef4444}
.tag{font-size:0.55rem;background:var(--color-accent-subtle);color:var(--color-accent);padding:2px 8px;border-radius:8px}
"""

def main():
    data = json.load(open(DATA, 'r', encoding='utf-8'))
    from collections import defaultdict
    by_cat = defaultdict(list)
    for r in data:
        by_cat[r['cat']].append(r)

    h = []
    h.append('<!DOCTYPE html>\n<html lang="zh-CN" class="dark">\n<head>\n<meta charset="UTF-8">\n<title>2026年红利指数事件链</title>\n<link rel="stylesheet" href="/shared/css/hanako-glass.css">\n<style>' + CSS + '</style>\n</head>\n<body>\n<div class="wrap">')
    h.append('<h1>📈 2026年红利指数 · 高点→回撤→反弹事件链</h1>')
    h.append('<div class="sub">高点 = 250日滚动最高确认 · 回撤 = 高点至低点跌幅 · 反弹 = 低点至反弹峰值 · 数据截至 2026-07-31</div>')

    for cat in ['pure', 'lowvol', 'quality', 'other']:
        items = by_cat.get(cat, [])
        if not items:
            continue
        h.append('<div class="card"><h2>🏷️ ' + CAT_NAMES.get(cat, cat) + ' <span class="tag">' + str(len(items)) + ' 只</span></h2>')
        h.append('<table><thead><tr><th>指数</th><th>2026高点</th><th>高点日期</th><th>回撤10%</th><th>回撤15%</th><th>回撤20%</th><th>最大回撤</th><th>低点日期</th><th>反弹幅度</th><th>反弹峰值日</th><th>状态</th></tr></thead><tbody>')
        for r in items:
            dd = r['dd_dates']
            dd10 = dd.get('10', '—')
            dd15 = dd.get('15', '—')
            dd20 = dd.get('20', '—')
            dd_cls = 'dn' if r['max_dd'] >= 15 else 'mid'
            bounce_cls = 'up' if r['bounce_pct'] >= 10 else ('mid' if r['bounce_pct'] >= 5 else 'dn')
            state = '<span class="badge badge-live">反弹中</span>' if r['still_bouncing'] else '<span class="muted">已结束</span>'
            h.append('<tr>'
                     + '<td>' + r['name'] + '<span class="muted" style="font-size:0.55rem"> ' + r['code'] + '</span></td>'
                     + '<td>' + str(r['high_price']) + '</td>'
                     + '<td>' + r['high_date'] + '</td>'
                     + '<td>' + dd10 + '</td>'
                     + '<td>' + dd15 + '</td>'
                     + '<td>' + dd20 + '</td>'
                     + '<td class="' + dd_cls + '">-' + str(r['max_dd']) + '%</td>'
                     + '<td>' + r['low_date'] + '</td>'
                     + '<td class="' + bounce_cls + '">+' + str(r['bounce_pct']) + '%</td>'
                     + '<td>' + r['bounce_peak_date'] + '</td>'
                     + '<td>' + state + '</td>'
                     + '</tr>')
        h.append('</tbody></table></div>')

    h.append('</div></body></html>')
    html = '\n'.join(h)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"报告已生成: {OUT} ({len(html)} bytes)")

if __name__ == '__main__':
    main()
