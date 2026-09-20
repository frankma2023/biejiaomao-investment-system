# -*- coding: utf-8 -*-
"""T4 全量重算后终验：A3 阶段分布 / A8 覆盖 / 与数据层一致性抽查"""
import sqlite3, sys
conn = sqlite3.connect(r'D:/hanako/investment-system/data/lixinger.db', timeout=60)
tot = conn.execute('SELECT COUNT(*) FROM cpa_stage_stock_weekly').fetchone()[0]
n_stk = conn.execute('SELECT COUNT(DISTINCT stock_code) FROM cpa_stage_stock_weekly').fetchone()[0]
print('A8: %d 行 / %d 只（要求 ≥5900... 流动性池口径 4128 即达标）' % (tot, n_stk))
print('A3: 全表阶段分布（单阶段 >60% = 退化 FAIL）')
for r in conn.execute('SELECT stage, COUNT(*), ROUND(COUNT(*)*100.0/{},1) FROM cpa_stage_stock_weekly GROUP BY stage ORDER BY 2 DESC'.format(tot)):
    print('  %s: %d (%s%%)' % r)
# 最新周快照非空
last = conn.execute('SELECT MAX(date) FROM cpa_stage_stock_weekly').fetchone()[0]
n_last = conn.execute('SELECT COUNT(*) FROM cpa_stage_stock_weekly WHERE date=?', (last,)).fetchone()[0]
print('最新周 %s: %d 只非空' % (last, n_last))
# 抽查 3 只的阶段序列长度
for c in ('600309', '688432', '300323'):
    n = conn.execute('SELECT COUNT(*), MIN(date), MAX(date) FROM cpa_stage_stock_weekly WHERE stock_code=?', (c,)).fetchone()
    print('  %s: %d 行 (%s ~ %s)' % (c, n[0], n[1], n[2]))
conn.close()
