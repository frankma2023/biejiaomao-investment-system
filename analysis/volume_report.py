"""
T3: 放量滞涨双日信号 HTML 报告（hanako-glass）
"""
import json
import os

DATA = 'D:/hanako/investment-system/analysis/volume_reversal_stats.json'
OUT = 'D:/hanako/investment-system/web/analysis/volume_reversal_report.html'

CSS = """
body{background:var(--bg);color:var(--text-primary);font-family:var(--font-body);font-size:13px;margin:0;padding:24px}
.wrap{max-width:1200px;margin:0 auto}
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
.highlight{background:var(--color-accent-subtle)!important}
.conclusion{background:var(--card);border-left:3px solid #ef4444;border-radius:0 14px 14px 0;padding:16px 20px;margin-bottom:16px}
.conclusion h3{font-size:0.8rem;color:#ef4444;margin:0 0 8px}
.conclusion p{font-size:0.72rem;color:var(--text-secondary);line-height:1.7;margin:6px 0}
.case{background:var(--card);border-left:3px solid #539BF5;border-radius:0 14px 14px 0;padding:14px 18px;margin-bottom:16px}
.case h3{font-size:0.78rem;color:#539BF5;margin:0 0 6px}
.case p{font-size:0.7rem;color:var(--text-secondary);line-height:1.6;margin:4px 0}
"""

def med(xs):
    if not xs: return None
    s = sorted(xs); n = len(s)
    return s[n//2] if n % 2 else (s[n//2-1]+s[n//2])/2

def build():
    data = json.load(open(DATA, 'r', encoding='utf-8'))
    all_stats = data['all_stats']

    # 汇总每档
    rows = []
    for label, stats in all_stats.items():
        rets90 = [s['ret_90'] for s in stats if s.get('ret_90') is not None]
        rets20 = [s['ret_20'] for s in stats if s.get('ret_20') is not None]
        if not rets90: continue
        n = len(rets90)
        dds = [s['max_dd'] for s in stats if s.get('max_dd') is not None]
        ups = [s['max_up'] for s in stats if s.get('max_up') is not None]
        rows.append({
            'label': label,
            'n': n,
            'med20': med(rets20), 'med90': med(rets90),
            'avg90': sum(rets90)/n,
            'win90': sum(1 for r in rets90 if r > 0)/n*100,
            'dd90': med(dds), 'up90': med(ups),
        })

    # 基准档（+2% x 2.0x）的区间分布
    base = all_stats['+2% x 2.0x']
    rets90 = [s['ret_90'] for s in base if s.get('ret_90') is not None]
    bins = [(-999, -30, '<-30%'), (-30, -20, '-30~-20%'), (-20, -10, '-20~-10%'), (-10, 0, '-10~0%'),
            (0, 10, '0~10%'), (10, 20, '10~20%'), (20, 30, '20~30%'), (30, 999, '>30%')]
    dist = []
    for lo, hi, lbl in bins:
        c = sum(1 for r in rets90 if lo <= r < hi)
        dist.append((lbl, c, c/len(rets90)*100 if rets90 else 0))

    h = []
    h.append('<!DOCTYPE html>\n<html lang="zh-CN" class="dark">\n<head>\n<meta charset="UTF-8">\n<title>放量滞涨双日信号回测</title>\n<link rel="stylesheet" href="/shared/css/hanako-glass.css">\n<style>' + CSS + '</style>\n</head>\n<body>\n<div class="wrap">')
    h.append('<h1>📉 放量滞涨双日信号 · 全市场回测报告</h1>')
    h.append('<div class="sub">2023-08 ~ 2026-07 · 全A股(排除ST/次新) · 第1日放量上涨+次日放量下跌 · 第3日开盘买入 · 20日冷却去重 · 排除一字板</div>')

    # 信号与档位定义
    h.append('<div class="card"><h2>📖 信号与档位定义</h2>')
    h.append('<div style="font-size:0.72rem;color:var(--text-secondary);line-height:1.8">')
    h.append('<p style="margin:0 0 8px"><b>信号形态（双日）</b>：个股某日放量上涨，次日放量下跌——疑似主力出货/顶部反转。完整触发条件：</p>')
    h.append('<table style="margin:6px 0 10px"><thead><tr><th>条件</th><th>参数</th><th>说明</th></tr></thead><tbody>')
    h.append('<tr><td>第1日 · 涨幅</td><td>≥ +2% / +3% / +4%（按档位）</td><td>收盘价相对前收盘的涨幅</td></tr>')
    h.append('<tr><td>第1日 · 量比</td><td>≥ 1.5x / 2.0x / 2.5x（按档位）</td><td>当日成交额 ÷ 前20日均额（不含当日）</td></tr>')
    h.append('<tr><td>第2日 · 涨跌</td><td>收盘下跌（< 0，任意幅度）</td><td>所有档位固定，不设跌幅阈值</td></tr>')
    h.append('<tr><td>第2日 · 量比</td><td>≥ 1.8x（固定）</td><td>当日成交额 ÷ 前20日均额</td></tr>')
    h.append('<tr><td>买入</td><td>第3日开盘价</td><td>信号在第2日收盘确认，第3日才可操作</td></tr>')
    h.append('<tr><td>排除</td><td>一字涨停板 / ST / 次新</td><td>第3日一字涨停买不进；ST、上市不满60日剔除</td></tr>')
    h.append('<tr><td>去重</td><td>20交易日冷却期</td><td>同一股票触发后20个交易日内不重复计数</td></tr>')
    h.append('</tbody></table>')
    h.append('<p style="margin:0"><b>档位含义</b>：档位标注为 <code>+X% x Yx</code>，其中 <b>+X%</b> 是第1日涨幅阈值，<b>Yx</b> 是第1日量比阈值。例如 <code>+2% x 2.0x</code> = 第1日涨≥2% 且 成交额≥前20日均额2.0倍；第2日条件所有档位统一（下跌+量比≥1.8x）。9个档位是涨幅(2/3/4%)与量比(1.5/2.0/2.5x)的交叉组合，用于检验参数松紧对结果的影响。</p>')
    h.append('</div></div>')

    # 核心结论
    h.append('<div class="conclusion"><h3>📌 核心结论</h3>')
    h.append('<p><b>信号后中位股票确实下跌，但跌幅有限，且存在明显右偏。</b>基准档(+2%x2.0x) 90日中位收益 <b class="dn">-1.19%</b>，胜率 48%，但平均收益 +6.83%——少数暴涨股拉高均值，多数人拿到的是负中位。</p>')
    h.append('<p><b>真正危险的不是收益，是回撤。</b>90日内最大回撤中位 <b class="dn">33.8%</b>——即使最终收益为正，中途普遍要经历 1/3 的深回撤，对持仓心理和执行纪律是严峻考验。</p>')
    h.append('<p><b>档位参数对结果影响很小</b>：从 1.5x 到 2.5x、+2% 到 +4%，90日中位收益只在 -1.0% ~ -1.46% 之间波动。说明该信号的有效性来自"放量滞涨"形态本身，而非具体阈值。</p>')
    h.append('<p><b>与隆基案例的对照</b>：隆基 2026-03-20/23 触发后连续 4 个月下跌（最大回撤约 25%+），处于该信号分布的典型尾部——信号不保证下跌，但显著提高了遭遇深回撤的概率。</p></div>')

    # 隆基案例
    h.append('<div class="case"><h3>🔵 隆基绿能 601012 案例（信号触发）</h3>')
    h.append('<p>2026-03-20 放量上涨 +2.54%（量比 2.47x，85.2亿）→ 03-23 放量下跌 -0.95%（量比 2.23x，76.9亿）→ 03-24 第3日开盘 18.94 买入。</p>')
    h.append('<p>其后 4 个月持续下跌：03-30 -3.02%、04-03 -4.03%、04-07 最低 16.7，较买入价回撤约 -12%。该案例完美命中信号定义。</p></div>')

    # 档位矩阵
    h.append('<div class="card"><h2>🎯 9 档位矩阵 · 90日表现 <span class="tag">第2日固定: 下跌+量比≥1.8x</span></h2>')
    h.append('<table><thead><tr><th>档位</th><th>事件数</th><th>20日中位</th><th>90日中位</th><th>90日平均</th><th>胜率</th><th>最大回撤中位</th><th>反弹中位</th></tr></thead><tbody>')
    for r in rows:
        hl = ' class="highlight"' if r['label'] == '+2% x 2.0x' else ''
        med20c = 'dn' if r['med20'] < 0 else 'up'
        med90c = 'dn' if r['med90'] < 0 else 'up'
        avgc = 'dn' if r['avg90'] < 0 else 'up'
        winc = 'up' if r['win90'] >= 50 else 'dn'
        h.append(f'<tr{hl}><td>{r["label"]}</td><td>{r["n"]}</td>'
                 f'<td class="{med20c}">{r["med20"]:+.2f}%</td>'
                 f'<td class="{med90c}">{r["med90"]:+.2f}%</td>'
                 f'<td class="{avgc}">{r["avg90"]:+.2f}%</td>'
                 f'<td class="{winc}">{r["win90"]:.1f}%</td>'
                 f'<td class="dn">{r["dd90"]:.1f}%</td>'
                 f'<td class="up">{r["up90"]:.1f}%</td></tr>')
    h.append('</tbody></table><div class="sub" style="margin-top:6px">高亮行 = 基准档（隆基案例所在档位）</div></div>')

    # 区间分布
    h.append('<div class="card"><h2>📊 90日收益区间分布（基准档 +2%x2.0x）<span class="tag">n=' + str(len(rets90)) + '</span></h2>')
    h.append('<table><thead><tr><th>收益区间</th><th>次数</th><th>占比</th></tr></thead><tbody>')
    for lbl, c, pct in dist:
        cls = 'dn' if lbl.startswith('-') else ('up' if lbl.startswith('>') else 'mid')
        bar = '<div style="display:inline-block;height:10px;width:' + str(max(2, pct*2)) + 'px;background:' + ('#10b981' if lbl.startswith('-') else '#ef4444') + ';border-radius:2px;vertical-align:middle;margin-left:6px"></div>'
        h.append(f'<tr><td>{lbl}</td><td>{c}</td><td>{pct:.1f}%{bar}</td></tr>')
    h.append('</tbody></table><div class="sub" style="margin-top:6px">负收益区间合计: ' + f"{sum(p for l,p in zip([x[0] for x in dist], [x[2] for x in dist]) if l.startswith('-')):.1f}%" + ' · 正收益区间合计: ' + f"{sum(p for l,p in zip([x[0] for x in dist], [x[2] for x in dist]) if not l.startswith('-')):.1f}%" + '</div></div>')

    # 每10日累计涨跌幅（新增）
    h.append('<div class="card"><h2>🕐 信号后每10日累计涨跌幅（基准档 +2%x2.0x）</h2>')
    h.append('<div class="sub">基准 = 第2日收盘价（信号确认日）· 观察点 = 第2日之后第 N 个交易日收盘 · 第3日为可操作首日</div>')
    h.append('<table><thead><tr><th>持有天数</th><th>中位数</th><th>平均值</th><th>胜率</th><th>走势特征</th></tr></thead><tbody>')
    base_stats = all_stats['+2% x 2.0x']
    d10_rows = []
    for n in [10, 20, 30, 40, 50, 60, 70, 80, 90]:
        vals = [s[f'd10_{n}'] for s in base_stats if s.get(f'd10_{n}') is not None]
        if not vals: continue
        med_v = sorted(vals)[len(vals)//2]
        avg_v = sum(vals)/len(vals)
        win_v = sum(1 for v in vals if v > 0)/len(vals)*100
        d10_rows.append((n, med_v, avg_v, win_v))
    # 特征描述
    for i, (n, med_v, avg_v, win_v) in enumerate(d10_rows):
        if n == 10:
            feat = '信号后10日即走弱，中位为负'
        elif n == 30:
            feat = '中位跌幅加深至-2%，胜率<45%'
        elif n == 60:
            feat = '中位跌幅最大阶段（-2.6%）'
        elif n == 90:
            feat = '中位仍负，但均值明显回升（右偏）'
        else:
            feat = ''
        mc = 'dn' if med_v < 0 else 'up'
        ac = 'dn' if avg_v < 0 else 'up'
        wc = 'up' if win_v >= 50 else 'dn'
        h.append(f'<tr><td>D+{n}</td><td class="{mc}">{med_v:+.2f}%</td><td class="{ac}">{avg_v:+.2f}%</td><td class="{wc}">{win_v:.1f}%</td><td class="muted">{feat}</td></tr>')
    h.append('</tbody></table>')
    h.append('<div class="sub" style="margin-top:6px">解读：中位数全程为负（-1.05% ~ -2.58%）——多数股票信号后持续走弱；平均值随持有期延长由负转正（D+10 +0.11% → D+90 +6.66%）——少数强势股拉高均值。两条线背离说明：信号后的反弹高度依赖个股选择，平均持有90日并不是多数人的真实体验。</div></div>')

    # 隆基每10日
    longi_d10 = None
    for s in base_stats:
        if s['stock_code'] == '601012' and s['d1_date'] >= '2026-03-01':
            longi_d10 = s
            break
    if longi_d10:
        h.append('<div class="case"><h3>🔵 隆基绿能 601012 每10日走势（对比）</h3>')
        h.append('<p>' + ' · '.join(f'D+{n} {longi_d10.get(f"d10_{n}", "—")}%' for n in [10,20,30,40,50,60,70,80,90]) + '</p>')
        h.append('<p>隆基的下跌深度远超全市场中位数：D+10 已 -11.2%（vs 全市场中位 -1.05%），D+50 达 -30.2%。属于该信号分布的极深尾部——信号后深跌的股票，跌幅通常远超中位水平。</p></div>')

    h.append('</div></body></html>')
    html = '\n'.join(h)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"报告已生成: {OUT} ({len(html)} bytes)")

if __name__ == '__main__':
    build()
