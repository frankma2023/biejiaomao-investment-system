import sqlite3
conn = sqlite3.connect('data/lixinger.db')
cursor = conn.execute("PRAGMA table_info(chanlun_scan_daily)")
for r in cursor.fetchall():
    print(r[1], r[2])
# Check sample
r = conn.execute("SELECT * FROM chanlun_scan_daily LIMIT 1").fetchone()
if r:
    print(f'\nSample: {dict(r)}')
conn.close()
