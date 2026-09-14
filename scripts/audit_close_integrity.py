# -*- coding: utf-8 -*-
"""daily_kline.close 内部一致性全市场审计
判据：close(t)/close(t-1) - 1  应等于 change_pct
     不等的日子 = close 列存在"凭空跳变"（无 change_pct 支撑）
按年统计，量化污染面。
"""
import sys, os, sqlite3
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

c = sqlite3.connect('data/lixinger.db')
c.row_factory = sqlite3.Row

print('扫描 daily_kline ...')
q = """
WITH k AS (
  SELECT stock_code, date, close, change_pct,
         LAG(close) OVER (PARTITION BY stock_code ORDER BY date) AS pc,
         LAG(date)  OVER (PARTITION BY stock_code ORDER BY date) AS pd
  FROM daily_kline
  WHERE close > 0
)
SELECT substr(date,1,4) AS yr,
       COUNT(*) AS n,
       SUM(CASE WHEN pc IS NULL THEN 0
                WHEN change_pct IS NULL THEN 0
                WHEN ABS(close/pc - 1 - change_pct) > 0.005 THEN 1 ELSE 0 END) AS bad,
       COUNT(DISTINCT CASE WHEN pc IS NOT NULL AND change_pct IS NOT NULL
                AND ABS(close/pc - 1 - change_pct) > 0.005 THEN stock_code END) AS bad_stk
FROM k GROUP BY yr ORDER BY yr
"""
print()
print('%-6s %12s %12s %8s %12s' % ('year', 'rows', 'bad_days', 'bad%', 'bad_stocks'))
print('-' * 56)
tot_n = tot_b = 0
for r in c.execute(q):
    pct = r['bad'] * 100.0 / r['n'] if r['n'] else 0
    print('%-6s %12d %12d %7.2f%% %12d' % (r['yr'], r['n'], r['bad'], pct, r['bad_stk']))
    tot_n += r['n']; tot_b += r['bad']
print('-' * 56)
print('%-6s %12d %12d %7.2f%%' % ('TOTAL', tot_n, tot_b, tot_b * 100.0 / tot_n))

print()
print('=' * 72)
print('污染最重的 20 只（按坏天数）')
print('=' * 72)
q2 = """
WITH k AS (
  SELECT stock_code, date, close, change_pct,
         LAG(close) OVER (PARTITION BY stock_code ORDER BY date) AS pc
  FROM daily_kline WHERE close > 0
)
SELECT stock_code,
       COUNT(*) AS bad,
       MIN(date) AS d0, MAX(date) AS d1
FROM k
WHERE pc IS NOT NULL AND change_pct IS NOT NULL
  AND ABS(close/pc - 1 - change_pct) > 0.005
GROUP BY stock_code ORDER BY bad DESC LIMIT 20
"""
for r in c.execute(q2):
    print('  %-8s 坏天 %4d  %s ~ %s' % (r['stock_code'], r['bad'], r['d0'], r['d1']))

print()
print('=' * 72)
print('坏天在年度上的分布（前 30 个日期）')
print('=' * 72)
q3 = """
WITH k AS (
  SELECT stock_code, date, close, change_pct,
         LAG(close) OVER (PARTITION BY stock_code ORDER BY date) AS pc
  FROM daily_kline WHERE close > 0
)
SELECT date, COUNT(*) AS n FROM k
WHERE pc IS NOT NULL AND change_pct IS NOT NULL
  AND ABS(close/pc - 1 - change_pct) > 0.005
GROUP BY date ORDER BY n DESC LIMIT 30
"""
for r in c.execute(q3):
    print('  %s  %d 只' % (r['date'], r['n']))

c.close()
