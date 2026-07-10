# Update progress stats and add lessons
t = open('D:/hanako/investment-system/web/progress.html', 'r', encoding='utf-8').read()

# Update stats
t = t.replace('>105</div><div class="l">已完成</div>', '>108</div><div class="l">已完成</div>')

# Add new lessons
new = '''
<div class="lesson-card" style="border-left-color:#FF9800"><div class="ln" style="color:#F57F00">72. 通达信分钟数据替代Baostock</div><div class="ld">9003只A股.lc1文件→1分钟K线→聚合15/60分钟→写入stock_kline_15min/60min表。零网络依赖，本地毫秒级读取。daily_update.py已切换。</div></div>
<div class="lesson-card" style="border-left-color:#FF9800"><div class="ln" style="color:#F57F00">73. ETF数据源双轨制</div><div class="ld">index_style.yaml新增etf分类，理杏仁拉不到的ETF从通达信补日K线和分钟K线。fetch_tdx_kline.py自动读yaml配置，daily_update.py步骤3.5自动运行。</div></div>
<div class="lesson-card" style="border-left-color:#C62828"><div class="ln" style="color:#C62828">74. 通达信.day文件OHLC是int*100非float</div><div class="ld">struct.unpack('IfffffI')误当浮点解出e-42级价格。正确格式:'IIIIIfI'，价格÷100。教训：二进制解析前先验证数据范围。</div></div>
'''

t = t.replace('</div>\n<script>\nfunction toggleTheme', new + '\n</div>\n<script>\nfunction toggleTheme')
open('D:/hanako/investment-system/web/progress.html', 'w', encoding='utf-8').write(t)
print('Progress updated')
