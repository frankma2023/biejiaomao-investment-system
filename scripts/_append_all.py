"""
Append delayed entry + simulation sections to MW v3.0 report
"""
report_path = r"D:\hanako\investment-system\docs\analysis\MW信号v3.0回测分析报告.html"

new_sections = """
<h2>13 PLUS 延迟入场分析 — B2后第2天开盘买入</h2>
<p style="font-size:.64rem;color:var(--text-muted);margin-bottom:12px;">PLUS信号 (≥80 + D满分 + I1满分=15) 共34只，33只有效延迟入场。在B2日后的第2个交易日以开盘价买入，持有至目标窗口。</p>

<div class="insight-grid">
<div class="insight-card"><div class="label">5d 胜率</div><div class="value" style="color:var(--green)">54.5%</div><div class="detail">随机 38.1% · 跑赢 +16.4pp</div></div>
<div class="insight-card"><div class="label">10d 胜率</div><div class="value" style="color:var(--green)">62.5%</div><div class="detail">随机 39.6% · 跑赢 +22.9pp</div></div>
<div class="insight-card"><div class="label">5d 中位</div><div class="value" style="color:var(--green)">+2.11%</div><div class="detail">随机 +0.28% · 超额 +1.83pp</div></div>
<div class="insight-card"><div class="label">10d 中位</div><div class="value" style="color:var(--green)">+6.06%</div><div class="detail">随机 -0.32% · 超额 +6.38pp</div></div>
<div class="insight-card"><div class="label">20d 中位</div><div class="value" style="color:var(--green)">+6.65%</div><div class="detail">随机 -2.90% · 超额 +9.55pp</div></div>
<div class="insight-card"><div class="label">20d 胜率</div><div class="value" style="color:var(--green)">53.6%</div><div class="detail">随机 37.0% · 跑赢 +16.6pp</div></div>
</div>

<div class="table-wrap"><table>
<tr><th>窗口</th><th>MW 胜率</th><th>MW 中位</th><th>随机胜率</th><th>随机中位</th><th>胜率差</th><th>中位差</th><th>跑赢</th></tr>
<tr><td>5d</td><td class="c-great">54.5%</td><td class="c-great">+2.11%</td><td class="c-bad">38.1%</td><td>+0.28%</td><td class="c-great">+16.4pp</td><td class="c-good">+1.83pp</td><td class="c-great">✅</td></tr>
<tr class="row-best"><td><b>10d</b></td><td class="c-great"><b>62.5%</b></td><td class="c-great"><b>+6.06%</b></td><td class="c-bad">39.6%</td><td class="c-bad">-0.32%</td><td class="c-great"><b>+22.9pp</b></td><td class="c-great"><b>+6.38pp</b></td><td class="c-great"><b>✅✅</b></td></tr>
<tr><td>20d</td><td class="c-great">53.6%</td><td class="c-great">+6.65%</td><td class="c-bad">37.0%</td><td class="c-bad">-2.90%</td><td class="c-great">+16.6pp</td><td class="c-great">+9.55pp</td><td class="c-great">✅✅</td></tr>
</table></div>

<div class="callout callout-tip"><strong>🏆 B2+2日延迟入场依然是表现最强的策略。</strong>10d胜率62.5%，中位+6.06%，与随机基准的胜率差+22.9pp。I1门槛提高后PLUS从45只缩到34只，B2+2的10d胜率从65.1%微降到62.5%，但中位收益保持+6%不变——说明砍掉的主要是边缘信号，核心优质信号的表现不受影响。</div>

<h2>14 PLUS 延迟入场分析 — B2后第3天开盘买入</h2>
<p style="font-size:.64rem;color:var(--text-muted);margin-bottom:12px;">同样33只有效信号，在B2日后的第3个交易日以开盘价买入。</p>

<div class="insight-grid">
<div class="insight-card"><div class="label">5d 胜率</div><div class="value" style="color:var(--green)">42.4%</div><div class="detail">随机 39.5% · 跑赢 +2.9pp</div></div>
<div class="insight-card"><div class="label">10d 胜率</div><div class="value" style="color:var(--green)">53.1%</div><div class="detail">随机 39.3% · 跑赢 +13.8pp</div></div>
<div class="insight-card"><div class="label">10d 中位</div><div class="value" style="color:var(--green)">+4.18%</div><div class="detail">随机 -0.19% · 超额 +4.37pp</div></div>
<div class="insight-card"><div class="label">20d 胜率</div><div class="value" style="color:var(--green)">51.9%</div><div class="detail">随机 35.8% · 跑赢 +16.1pp</div></div>
</div>

<div class="table-wrap"><table>
<tr><th>窗口</th><th>MW 胜率</th><th>MW 中位</th><th>随机胜率</th><th>随机中位</th><th>胜率差</th><th>中位差</th><th>跑赢</th></tr>
<tr><td>5d</td><td class="c-good">42.4%</td><td>+0.97%</td><td class="c-bad">39.5%</td><td>+0.38%</td><td>+2.9pp</td><td>+0.59pp</td><td class="c-good">✅</td></tr>
<tr><td>10d</td><td class="c-great">53.1%</td><td class="c-great">+4.18%</td><td class="c-bad">39.3%</td><td>-0.19%</td><td class="c-great">+13.8pp</td><td class="c-great">+4.37pp</td><td class="c-great">✅✅</td></tr>
<tr><td>20d</td><td class="c-great">51.9%</td><td class="c-good">+2.96%</td><td class="c-bad">35.8%</td><td class="c-bad">-2.71%</td><td class="c-great">+16.1pp</td><td class="c-great">+5.67pp</td><td class="c-great">✅✅</td></tr>
</table></div>

<div class="callout callout-note"><strong>延迟3天全面弱于延迟2天。</strong>5d胜率从54.5%降至42.4%，10d胜率从62.5%降至53.1%。B2+2仍然是唯一最优入场时机。</div>

<h2>15 延迟入场综合对比</h2>
<div class="table-wrap"><table>
<tr><th>入场时机</th><th>有效信号</th><th>5d胜率</th><th>10d胜率</th><th>10d中位</th><th>20d胜率</th><th>20d中位</th></tr>
<tr><td>B2次日开盘</td><td>70</td><td>40.3%</td><td class="c-great">58.3%</td><td class="c-great">+4.1%</td><td>47.6%</td><td>+0.8%</td></tr>
<tr class="row-best"><td><b>B2+2日开盘 🏆</b></td><td>33</td><td class="c-great"><b>54.5%</b></td><td class="c-great"><b>62.5%</b></td><td class="c-great"><b>+6.06%</b></td><td class="c-great"><b>53.6%</b></td><td class="c-great"><b>+6.65%</b></td></tr>
<tr><td>B2+3日开盘</td><td>33</td><td>42.4%</td><td>53.1%</td><td class="c-great">+4.18%</td><td>51.9%</td><td>+2.96%</td></tr>
</table></div>

<div class="callout callout-tip"><strong>结论：PLUS信号 + B2+2日开盘买入 + 持有10日 = 62.5%胜率 +6.06%中位。</strong></div>

<h2>16 模拟盘 — PLUS B2+2 5%仓位 10日持有</h2>
<p style="font-size:.64rem;color:var(--text-muted);margin-bottom:12px;">策略：每个PLUS信号在B2日后第2个交易日以开盘价买入当前现金的5%，持有10个交易日后以收盘价卖出。初始资金¥1,000,000。</p>

<div class="insight-grid">
<div class="insight-card"><div class="label">最终市值</div><div class="value" style="color:var(--green)">¥1,076,933</div><div class="detail">总收益率 +7.69%</div></div>
<div class="insight-card"><div class="label">胜率</div><div class="value" style="color:var(--green)">65.6%</div><div class="detail">32笔交易，21胜11负</div></div>
<div class="insight-card"><div class="label">中位收益</div><div class="value" style="color:var(--green)">+6.06%</div><div class="detail">平均收益 +7.33%</div></div>
<div class="insight-card"><div class="label">最大回撤</div><div class="value" style="color:var(--green)">-2.68%</div><div class="detail">收益回撤比 2.9:1</div></div>
<div class="insight-card"><div class="label">最大同时持仓</div><div class="value" style="color:var(--accent)">14只</div><div class="detail">4月下旬高峰期</div></div>
<div class="insight-card"><div class="label">总投入资金</div><div class="value" style="color:var(--accent)">¥1,192,172</div><div class="detail">资金周转 1.19x</div></div>
</div>

<h3>月度表现</h3>
<div class="table-wrap"><table>
<tr><th>月份</th><th>交易笔数</th><th>胜率</th><th>累计收益</th><th>定性</th></tr>
<tr class="row-worst"><td><b>2月</b></td><td>1</td><td class="c-bad">0%</td><td class="c-bad">-8.87%</td><td class="c-muted">无样本意义</td></tr>
<tr class="row-worst"><td><b>3月</b></td><td>4</td><td class="c-bad">25.0%</td><td class="c-bad">-32.65%</td><td class="c-muted">寒冬</td></tr>
<tr class="row-best"><td><b>4月</b></td><td><b>19</b></td><td class="c-great"><b>78.9%</b></td><td class="c-great"><b>+253.89%</b></td><td class="c-great"><b>🏆 丰收月</b></td></tr>
<tr><td><b>5月</b></td><td>8</td><td class="c-great">62.5%</td><td class="c-good">+22.15%</td><td>稳健</td></tr>
</table></div>

<div class="callout callout-tip"><strong>100万实盘模拟：+7.69%收益，-2.68%最大回撤，65.6%胜率。</strong>I1门槛提高到≥85后，PLUS从45只缩到34只（-24%），模拟盘收益从+10.39%降到+7.69%，但胜率从67.4%微降至65.6%基本持平，回撤控制依然优秀（-2.68%不变）。砍掉的11只边缘PLUS信号虽然降低了绝对收益，但核心策略质量并未受损。</div>

</div></body></html>
"""

with open(report_path, 'r', encoding='utf-8') as f:
    html = f.read()

insert_pos = html.find('</div></body></html>')
new_html = html[:insert_pos] + new_sections

with open(report_path, 'w', encoding='utf-8') as f:
    f.write(new_html)

print(f"Appended sections 13-16. Final size: {len(new_html)} bytes")
