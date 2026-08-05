"""
红利指数卖出信号回测 - HTML 报告
核心诚实结论: 单一指标无显著预测力, 与随机基准无差异
"""
import json
from collections import defaultdict

DATA = 'D:/hanako/investment-system/analysis/dividend_sell_results.json'
OUT = 'D:/hanako/investment-system/web/analysis/dividend_sell_report.html'

SIG_LABELS = {
    'ma20': '跌破MA20', 'ma60': '跌破MA60',
    'pe_hi_70': 'PE分位>70%', 'pe_hi_80': 'PE分位>80%', 'pe_hi_90': 'PE分位>90%',
    'pb_hi_70': 'PB分位>70%', 'pb_hi_80': 'PB分位>80%', 'pb_hi_90': 'PB分位>90%',
    'dyr_lo_10': '股息率分位<10%', 'dyr_lo_20': '股息率分位<20%', 'dyr_lo_30': '股息率分位<30%',
}
CAT_NAMES = {'pure': '纯红利', 'lowvol': '红利低波', 'quality': '红利质量/成长', 'other': '行业/其他'}
BASELINE = {'avg': 8.4, 'rate10': 30.1, 'rate15': 8.2, 'n': 3408}

CSS = """
body{background:var(--bg);color:var(--text-primary);font-family:var(--font-body);font-size:13px;margin:0;padding:24px}
.wrap{max-width:1300px;margin:0 auto}
h1{font-family:var(--font-display);font-size:1.4rem;font-weight:400;color:var(--accent);margin:0 0 6px}
.sub{font-size:0.7rem;color:var(--muted);margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px;margin-bottom:16px}
.card h2{font-size:0.85rem;font-weight:600;color:var(--text-primary);margin:0 0 12px}
.card h2 .tag{font-size:0.55rem;background:var(--color-accent-subtle);color:var(--color-accent);padding:2px 8px;border-radius:8px}
table{width:100%;border-collapse:collapse;font-size:0.7rem}
th{padding:7px 8px;border-bottom:2px solid var(--border);font-weight:600;color:var(--muted);font-size:0.58rem;text-transform:uppercase;letter-spacing:0.04em;text-align:right;background:var(--card)}
th:first-child,td:first-child{text-align:left}
td{padding:6px 8px;border-bottom:1px solid var(--border);text-align:right;font-family:var(--font-mono)}
td:first-child{font-family:var(--font-body)}
tr:hover td{background:var(--color-accent-subtle)}
.up{color:#ef4444;font-weight:600}
.dn{color:#10b981;font-weight:600}
.mid{color:#f59e0b;font-weight:600}
.muted{color:var(--muted)}
.warn{background:rgba(245,158,11,.07)!important}
.alert{background:rgba(239,68,68,.06)!important}
.badge{font-size:0.55rem;padding:2px 8px;border-radius:8px;font-weight:600}
.badge-no{background:rgba(139,139,144,.12);color:var(--muted)}
.conclusion{background:var(--card);border-left:3px solid #f59e0b;border-radius:0 14px 14px 0;padding:16px 20px;margin-bottom:16px}
.conclusion h3{font-size:0.8rem;color:#f59e0b;margin:0 0 8px}
.conclusion p{font-size:0.72rem;color:var(--text-secondary);line-height:1.7;margin:6px 0}
"""

def main():
    data = json.load(open(DATA, 'r', encoding='utf-8'))

    # 全池合并
    merged = defaultdict(lambda: {'dd20': [], 'dd60': []})
    by_cat = defaultdict(lambda: defaultdict(lambda: {'dd60': []}))
    for code, info in data.items():
        for sig, s in info['signals'].items():
            merged[sig]['dd20'].extend(s['dd20'])
            merged[sig]['dd60'].extend(s['dd60'])
            by_cat[info['cat']][sig]['dd60'].extend(s['dd60'])

    h = []
    h.append('<!DOCTYPE html>\n<html lang="zh-CN" class="dark">\n<head>\n<meta charset="UTF-8">\n<title>红利指数卖出信号回测</title>\n<link rel="stylesheet" href="/shared/css/hanako-glass.css">\n<style>' + CSS + '</style>\n</head>\n<body>\n<div class="wrap">')
    h.append('<h1>⚠️ 红利指数卖出信号回测报告</h1>')
    h.append('<div class="sub">2016-2026 全周期 · 信号触发后 60 日内最大回撤 · 次日生效 · 事件合并去重(20日)</div>')

    # 核心结论
    h.append('<div class="conclusion"><h3>📌 核心结论（诚实版）</h3>')
    h.append(f'<p><b>单一指标对"卖出择时"没有显著预测力。</b>所有信号触发后的平均回撤(7.6-8.4%)、踩雷率(23.9-31.1%)与<b>随机基准日</b>(平均回撤8.4%、踩雷率30.1%)几乎无差异。</p>')
    h.append('<p>这与买入信号形成鲜明对比：<b>回撤15%买入信号有65.8%胜率（显著优于随机）</b>，而<b>任何跌破类卖出信号都无法区分"后面会跌"和"后面不跌"</b>。红利指数的下跌更像随机游走——跌不跌不由"之前跌破什么"决定。</p>')
    h.append('<p><b>实操含义</b>：对红利指数，与其用技术指标择时卖出，不如用<b>估值分位区间</b>做仓位管理（如 PE分位>80% 时减仓至半仓），接受"可能卖早"的成本，换取"避开深跌"的保险。</p></div>')

    # 全池矩阵
    h.append('<div class="card"><h2>🎯 全红利池 · 卖出信号回撤矩阵（60日） <span class="tag">基准: 平均8.4% / 踩雷30.1%</span></h2>')
    h.append('<table><thead><tr><th>信号</th><th>次数</th><th>平均回撤</th><th>踩雷率(>10%)</th><th>深踩率(>15%)</th><th>vs基准</th></tr></thead><tbody>')
    for sig in SIG_LABELS:
        s = merged.get(sig)
        if not s or not s['dd60']:
            continue
        dd = s['dd60']
        n = len(dd)
        avg = sum(dd) / n
        r10 = sum(1 for d in dd if d > 10) / n * 100
        r15 = sum(1 for d in dd if d > 15) / n * 100
        diff = r10 - BASELINE['rate10']
        diff_cls = 'dn' if diff < -2 else ('mid' if abs(diff) <= 2 else 'up')
        row_cls = ' class="alert"' if diff >= 5 else (' class="warn"' if diff >= 2 else '')
        h.append('<tr' + row_cls + '><td>' + SIG_LABELS[sig] + '</td><td>' + str(n) + '</td>'
                 + '<td>' + f'{avg:.1f}%' + '</td><td>' + f'{r10:.1f}%' + '</td><td>' + f'{r15:.1f}%' + '</td>'
                 + '<td class="' + diff_cls + '">' + f'{diff:+.1f}pp' + '</td></tr>')
    h.append('</tbody></table><div class="sub" style="margin-top:8px">红色行=踩雷率显著高于基准(≥+5pp) · 黄色=略高(+2pp) · 绿色=低于基准（信号有效）</div></div>')

    # 分类明细
    for cat, cn in CAT_NAMES.items():
        sigs = by_cat.get(cat, {})
        if not sigs:
            continue
        h.append('<div class="card"><h2>🏷️ ' + cn + ' <span class="tag">' + str(len(sigs)) + ' 信号</span></h2>')
        h.append('<table><thead><tr><th>信号</th><th>次数</th><th>平均回撤</th><th>踩雷率(>10%)</th><th>深踩率(>15%)</th></tr></thead><tbody>')
        for sig in SIG_LABELS:
            s = sigs.get(sig)
            if not s or not s['dd60']:
                continue
            dd = s['dd60']
            n = len(dd)
            avg = sum(dd) / n
            r10 = sum(1 for d in dd if d > 10) / n * 100
            r15 = sum(1 for d in dd if d > 15) / n * 100
            h.append('<tr><td>' + SIG_LABELS[sig] + '</td><td>' + str(n) + '</td><td>' + f'{avg:.1f}%</td><td>{r10:.1f}%</td><td>{r15:.1f}%</td></tr>')
        h.append('</tbody></table></div>')

    h.append('</div></body></html>')
    html = '\n'.join(h)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"报告已生成: {OUT} ({len(html)} bytes)")

if __name__ == '__main__':
    main()
