import sqlite3
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c = db.cursor()
print('=== schema ===')
c.execute('PRAGMA table_info(index_fundamental_daily)')
for r in c.fetchall():
    print(f'  {r[1]:20s} {r[2]}')
print('\n=== 980092 latest 3 rows ===')
c.execute("SELECT * FROM index_fundamental_daily WHERE stock_code='980092' ORDER BY date DESC LIMIT 3")
cols = [d[0] for d in c.description]
for r in c.fetchall():
    d = dict(zip(cols, r))
    print(f"  date={d['date']} pe_ttm={d['pe_ttm']} pb={d['pb']} dyr={d['dyr']}")

# Check what granularity was stored
print('\n=== check if any metric_name column exists ===')
c.execute("SELECT DISTINCT metric_name FROM index_fundamental_daily WHERE metric_name IS NOT NULL LIMIT 5")
names = c.fetchall()
for n in names: print(f'  metric_name: {n[0]}')

# Also check: what's the raw lixinger API metric used
print('\n=== check fetch script for pb metric ===')
import os
scripts_dir = 'D:/hanako/investment-system/scripts'
for f in os.listdir(scripts_dir):
    if 'fundamental' in f.lower() and f.endswith('.py'):
        with open(os.path.join(scripts_dir, f), 'r', encoding='utf-8') as fh:
            content = fh.read()
        if 'pb' in content.lower() and 'index' in content.lower():
            print(f'  Found in: {f}')
            # Print lines with pb
            for line in content.split('\n'):
                if 'pb' in line.lower() and ('metric' in line.lower() or 'ew' in line.lower() or 'mcw' in line.lower()):
                    print(f'    {line.strip()[:120]}')

db.close()
