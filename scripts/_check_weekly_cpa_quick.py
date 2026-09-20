# -*- coding: utf-8 -*-
import sqlite3
conn = sqlite3.connect(r'D:/hanako/investment-system/data/lixinger.db', timeout=30)
print('=== 600309 阶段分布')
for r in conn.execute("SELECT stage, COUNT(*) FROM cpa_stage_stock_weekly WHERE stock_code='600309' GROUP BY stage ORDER BY 2 DESC"):
    print(' ', r)
print('=== 600309 最近3行')
for r in conn.execute("SELECT date, stage, days_in_stage, action, substr(metrics_json,1,60) FROM cpa_stage_stock_weekly WHERE stock_code='600309' ORDER BY date DESC LIMIT 3"):
    print(' ', r)
n_orig = conn.execute("SELECT COUNT(*) FROM cpa_stage_stock_weekly WHERE stock_code='600309' AND metrics_json LIKE '%original%'").fetchone()[0]
print('600309 original标记行:', n_orig)
print('=== 全表 A3（单阶段>60%？）')
tot = conn.execute('SELECT COUNT(*) FROM cpa_stage_stock_weekly').fetchone()[0]
print('总行:', tot)
for r in conn.execute('SELECT stage, COUNT(*)*100.0/{} FROM cpa_stage_stock_weekly GROUP BY stage ORDER BY 2 DESC LIMIT 10'.format(tot)):
    print('  %s: %.1f%%' % r)
conn.close()
