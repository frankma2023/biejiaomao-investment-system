t=open('D:/hanako/investment-system/web/progress.html','r',encoding='utf-8').read()
new_lessons='''
<div class="lesson-card" style="border-left-color:#FF9800"><div class="ln" style="color:#F57F00">65. MW信号回测体系化</div><div class="ld">37,978条信号x7维度xH20。B1单独入场全线亏损，B1+B2中置信79~83%/+17~22%。B2日追买不如B1日买(-15~31pp)。PP_V1唯一有效共现(+4pp)。震荡市>牛市(90% vs 71%)。</div></div>
<div class="lesson-card" style="border-left-color:#FF9800"><div class="ln" style="color:#F57F00">66. B1技术置信度评分(TS)</div><div class="ld">9因子满分100，五级分层。极高85+胜率56.9%，低<50胜率38.2%。mw_signal.py已集成自动计算，mw-signals页面B1 Tab显示TS标签。</div></div>
<div class="lesson-card" style="border-left-color:#FF9800"><div class="ln" style="color:#F57F00">67. PP_V1 vs PP_V2</div><div class="ld">V2更精确但确认滞后(gain中位数6.13% vs V1 4.05%)，胜率反而不如V1(42.9% vs 49.9%)。V2正确用法: PP_V1买入+V2确认。回测数据>理论直觉。</div></div>
<div class="lesson-card" style="border-left-color:#FF9800"><div class="ln" style="color:#F57F00">68. 管道xMW交叉验证</div><div class="ld">管道定仓位+筛行业(274指数/分级RPS/行业RS兜底)，MW定选股+定买点。v1.1将思特威等高分信号从v1.0筛掉中捞回。</div></div>
<div class="lesson-card" style="border-left-color:#FF9800"><div class="ln" style="color:#F57F00">69. 回测实验室</div><div class="ld">web/backtest-lab/ 信号多选+质量过滤+入场三方式并排对比+MW置信度。串行调用避免Flask单线程互锁。</div></div>
<div class="lesson-card" style="border-left-color:#FF9800"><div class="ln" style="color:#F57F00">70. SQLite生产级PRAGMA</div><div class="ld">server.py get_db() 加5行PRAGMA: WAL+synchronous=NORMAL+cache_size=64MB+busy_timeout=5s+foreign_keys=ON。140GB库性能提升30-60%。</div></div>
<div class="lesson-card" style="border-left-color:#C62828"><div class="ln" style="color:#C62828">71. 硬编码日期</div><div class="ld">index-valuation/stock-valuation均有2026-05-07/2026-05-10硬编码。全部改为动态new Date()。</div></div>
'''
# Insert before the closing </div> before the script section
t=t.replace('</div>\n<script>\nfunction toggleTheme', new_lessons+'\n</div>\n<script>\nfunction toggleTheme')
open('D:/hanako/investment-system/web/progress.html','w',encoding='utf-8').write(t)
print('Done')
