import sqlite3
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c = db.cursor()

print('=== B1-only (b2_date IS NULL) ===')
c.execute("SELECT confidence, COUNT(*) FROM mw_signal_daily WHERE b2_date IS NULL AND b1_date IS NOT NULL AND stock_code!='_sentinel_' GROUP BY confidence")
for r in c.fetchall(): print(f'  {r[0]}: {r[1]}')

c.execute("SELECT MIN(score), MAX(score), ROUND(AVG(score),1) FROM mw_signal_daily WHERE b2_date IS NULL AND b1_date IS NOT NULL AND stock_code!='_sentinel_'")
print(f'  score: {c.fetchone()}')

print('\n=== B1+B2 (b2_date IS NOT NULL) ===')
c.execute("SELECT confidence, COUNT(*) FROM mw_signal_daily WHERE b2_date IS NOT NULL AND stock_code!='_sentinel_' GROUP BY confidence")
for r in c.fetchall(): print(f'  {r[0]}: {r[1]}')

c.execute("SELECT MIN(score), MAX(score), ROUND(AVG(score),1) FROM mw_signal_daily WHERE b2_date IS NOT NULL AND stock_code!='_sentinel_'")
print(f'  score: {c.fetchone()}')

print('\n=== 按B1日期年份 + B2有无 + 置信度 ===')
c.execute("""
    SELECT substr(b1_date,1,4) yr,
           CASE WHEN b2_date IS NOT NULL THEN 'hasB2' ELSE 'B1only' END as b2flag,
           confidence, COUNT(*)
    FROM mw_signal_daily WHERE stock_code!='_sentinel_' AND b1_date>='2024-01-01'
    GROUP BY 1,2,3 ORDER BY 1,2,3
""")
for r in c.fetchall(): print(f'  {r[0]} {r[1]:6s} {r[2]:4s}: {r[3]}')

# Check front-end HTML for B1 confidence thresholds
import os
with open('D:/hanako/investment-system/web/mw-signals/index.html', 'r', encoding='utf-8') as f:
    html = f.read()
    # Find confidence-related text
    for line in html.split('\n'):
        if 'confidence' in line.lower() or '置信' in line or 'score' in line.lower() or '评分' in line:
            if len(line.strip()) > 20:
                print(f'\nPAGE: {line.strip()[:150]}')

db.close()
