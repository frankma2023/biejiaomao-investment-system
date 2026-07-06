import sqlite3
db=sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c=db.cursor()

# Check latest dates across indices
c.execute("SELECT stock_code, MAX(date) FROM index_fundamental_daily GROUP BY stock_code ORDER BY MAX(date) DESC LIMIT 20")
print('=== 各指数最新数据日期 ===')
for r in c.fetchall():
    print(f'  {r[0]:12s} {r[1]}')

# Check counts by date
c.execute("SELECT date, COUNT(DISTINCT stock_code) FROM index_fundamental_daily WHERE date >= '2026-05-01' GROUP BY date ORDER BY date DESC LIMIT 15")
print('\n=== 2026-05-01 起每日覆盖指数数 ===')
for r in c.fetchall():
    print(f'  {r[0]}  {r[1]} 个指数')

# Check the fetch script
import os
fetch_path = 'D:/hanako/investment-system/scripts/fetch_index_fundamental.py'
if os.path.exists(fetch_path):
    with open(fetch_path, 'r', encoding='utf-8') as f:
        content = f.read()
    # Find date-related logic
    for line in content.split('\n'):
        if 'date' in line.lower() and ('start' in line.lower() or 'end' in line.lower() or 'arg' in line.lower()):
            print(f'\nFETCH: {line.strip()[:150]}')

# Check daily_update if it calls the fetch
update_path = 'D:/hanako/investment-system/scripts/daily_update.py'
if os.path.exists(update_path):
    with open(update_path, 'r', encoding='utf-8') as f:
        content = f.read()
    for line in content.split('\n'):
        if 'fundamental' in line.lower():
            print(f'\nDAILY_UPDATE: {line.strip()[:150]}')

db.close()
