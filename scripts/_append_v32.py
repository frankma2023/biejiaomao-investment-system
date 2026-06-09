"""Append sections 13-16 to MW v3.0 report with v3.2 weight restructuring data"""
report_path = r"D:\hanako\investment-system\docs\analysis\MW信号v3.0回测分析报告.html"

sections = """
<h2>13 PLUS 延迟入场 — B2+2日开盘买入</h2>
<p style="font-size:.64rem;color:var(--text-muted);margin-bottom:12px;">PLUS信号 (≥80 + D满分 + I1满分) 共39只，38只有效。B2后第2个交易日开盘价买入。</p>

<div class="insight-grid">
<div class="insight-card"><div class="label">5d 胜率</div><div class="value" style="color:var(--green)">57.9%</div><div class="detail">随机 37.5% · +20.4pp</div></div>
<div class="insight-card"><div class="label">10d 胜率</div><div class="value" style="color:var(--green)">65.8%</div><div class="detail">随机 37.2% · +28.6pp</div></div>
<div class="insight-card"><div class="label">10d 中位</div><div class="value" style="color:var(--green)">+9.26%</div><div class="detail">随机 -0.53% · 超额 +9.79pp</div></div>
<div class="insight-card"><div class="label">20d 中位</div><div class="value" style="color:var(--green)">+11.44%</div><div class="detail">随机 -2.30% · 超额 +13.74pp</div></div>
<div class="insight-card"><div class="label">20d 胜率</div><div class="value" style="color:var(--green)">57.6%</div><div class="detail">随机 37.2% · +20.4pp</div></div>
<div class="insight-card"><div class="label">MW 平均</div><div class="value" style="color:var(--green)">+11.37%</div><div class="detail">随机 +1.33% · 超额 +10.04pp</div></div>
</div>

<div class="table-wrap"><table>
<tr><th>窗口</th><th>MW 胜率</th><th>MW 中位</th><th>随机胜率</th><th>随机中位</th><th>胜率差</th><th>中位差</th><th>跑赢</th></tr>
<tr><td>5d</td><td class="c-great">57.9%</td><td class="c-great">+4.33%</td><td class="c-bad">37.5%</td><td>+0.19%</td><td class="c-great">+20.4pp</td><td class="c-great">+4.14pp</td><td class="c-great">✅✅</td></tr>
<tr class="row-best"><td><b>10d</b></td><td class="c-great"><b>65.8%</b></td><td class="c-great"><b>+9.26%</b></td><td class="c-bad">37.2%</td><td class="c-bad">-0.53%</td><td class="c-great"><b>+28.6pp</b></td><td class="c-great"><b>+9.79pp</b></td><td class="c-great"><b>✅✅</b></td></tr>
<tr><td>20d</td><td class="c-great">57.6%</td><td class="c-great">+11.44%</td><td class="c-bad">37.2%</td><td class="c-bad">-2.30%</td><td class="c-great">+20.4pp</td><td class="c-great">+13.74pp</td><td class="c-great">✅✅</td></tr>
</table></div>

<div class="callout callout-tip"><strong>🏆 权重重构后 B2+2 表现跃升。</strong>10d 中位从 +6.06% 跳至 <b>+9.26%</b>，胜率 65.8%。D 从 5 提到 15 分、Sig 从 25 砍到 10 分，让真正有爆发力的信号得到了更高的总分排名。随机基准依然被碾压（胜率差 +28.6pp）。</div>

<h2>14 PLUS 延迟入场 — B2+3日开盘买入</h2>
<p style="font-size:.64rem;color:var(--text-muted);margin-bottom:12px;">同样 38 只有效信号。</p>

<div class="insight-grid">
<div class="insight-card"><div class="label">5d 胜率</div><div class="value" style="color:var(--green)">47.4%</div><div class="detail">随机 37.2% · +10.2pp</div></div>
<div class="insight-card"><div class="label">10d 胜率</div><div class="value" style="color:var(--green)">57.9%</div><div class="detail">随机 37.3% · +20.6pp</div></div>
<div class="insight-card"><div class="label">10d 中位</div><div class="value" style="color:var(--green)">+13.10%</div><div class="detail">随机 -0.69% · 超额 +13.79pp</div></div>
<div class="insight-card"><div class="label">20d 胜率</div><div class="value" style="color:var(--green)">54.5%</div><div class="detail">随机 35.4% · +19.1pp</div></div>
</div>

<div class="callout callout-note"><strong>有趣：B2+3 的 10d 中位 +13.10% 甚至超过 B2+2 的 +9.26%。</strong>但胜率下降（57.9% vs 65.8%），说明延迟3天是一个更高风险更高回报的选择——少数信号爆发力极强但整体胜率不如延迟2天稳健。</div>

<h2>15 延迟入场综合对比</h2>
<div class="table-wrap"><table>
<tr><th>入场时机</th><th>有效信号</th><th>5d胜率</th><th>10d胜率</th><th>10d中位</th><th>20d胜率</th><th>20d中位</th></tr>
<tr><td>B2次日开盘</td><td>74</td><td>—</td><td class="c-great">58.3%</td><td class="c-great">+4.1%</td><td>47.6%</td><td>+0.8%</td></tr>
<tr class="row-best"><td><b>B2+2日开盘 🏆</b></td><td>38</td><td class="c-great"><b>57.9%</b></td><td class="c-great"><b>65.8%</b></td><td class="c-great"><b>+9.26%</b></td><td class="c-great"><b>57.6%</b></td><td class="c-great"><b>+11.44%</b></td></tr>
<tr><td>B2+3日开盘</td><td>38</td><td>47.4%</td><td>57.9%</td><td class="c-great">+13.10%</td><td>54.5%</td><td class="c-great">+11.27%</td></tr>
</table></div>

<div class="callout callout-tip"><strong>结论：权重重构后，PLUS + B2+2 策略达到历史最优。</strong>10d 胜率 65.8%、中位 +9.26%、20d 中位 +11.44%。D 权重的提升让真正深度调整到位（15~35%）的信号获得了应有的排名优势。</div>

<h2>16 模拟盘 — PLUS B2+2 5%仓位 10日持有</h2>
<p style="font-size:.64rem;color:var(--text-muted);margin-bottom:12px;">初始资金 ¥1,000,000，每个 PLUS 信号在 B2+2 日开盘买入 5% 现金，持有 10 日卖出。</p>

<div class="insight-grid">
<div class="insight-card"><div class="label">最终市值</div><div class="value" style="color:var(--green)">¥1,129,635</div><div class="detail">总收益率 +12.96%</div></div>
<div class="insight-card"><div class="label">胜率</div><div class="value" style="color:var(--green)">71.1%</div><div class="detail">38笔交易，27胜11负</div></div>
<div class="insight-card"><div class="label">中位收益</div><div class="value" style="color:var(--green)">+9.26%</div><div class="detail">平均收益 +10.36%</div></div>
<div class="insight-card"><div class="label">最大回撤</div><div class="value">-3.45%</div><div class="detail">收益回撤比 3.8:1</div></div>
<div class="insight-card"><div class="label">最大同时持仓</div><div class="value" style="color:var(--accent)">18只</div><div class="detail">4月下旬高峰期</div></div>
<div class="insight-card"><div class="label">>20%收益占比</div><div class="value" style="color:var(--green)">26.3%</div><div class="detail">10笔交易收益超20%</div></div>
</div>

<h3>月度表现</h3>
<div class="table-wrap"><table>
<tr><th>月份</th><th>交易笔数</th><th>胜率</th><th>累计收益</th></tr>
<tr class="row-worst"><td><b>2月</b></td><td>1</td><td class="c-bad">0%</td><td class="c-bad">-8.87%</td></tr>
<tr class="row-worst"><td><b>3月</b></td><td>5</td><td class="c-bad">40.0%</td><td class="c-bad">-33.23%</td></tr>
<tr class="row-best"><td><b>4月</b></td><td><b>23</b></td><td class="c-great"><b>78.3%</b></td><td class="c-great"><b>+344.69%</b></td></tr>
<tr><td><b>5月</b></td><td>9</td><td class="c-great">77.8%</td><td class="c-great">+91.05%</td></tr>
</table></div>

<div class="callout callout-tip"><strong>🏆 权重重构后模拟盘达历史最优：+12.96% 收益，71.1% 胜率。</strong>相比重构前的 +7.69%/65.6%，收益提升了 68%，胜率提升了 5.5pp。D 权重从 5→15 是核心驱动因素——深度调整信号终于获得了应有的高分权重。5 月胜率从 62.5% 跳到 77.8%，说明非 4 月窗口的信号质量也大幅改善。</div>
"""

with open(report_path, 'r', encoding='utf-8') as f:
    html = f.read()

insert_pos = html.find('</div></body></html>')
new_html = html[:insert_pos] + sections

with open(report_path, 'w', encoding='utf-8') as f:
    f.write(new_html)

print(f"Appended. Final size: {len(new_html)} bytes")
