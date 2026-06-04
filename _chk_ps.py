import sqlite3
conn = sqlite3.connect('data/lixinger.db')
cursor = conn.execute("PRAGMA table_info(pattern_scan_signals)")
for r in cursor.fetchall():
    print(r[1], r[2])
# Check sample data
cursor = conn.execute("SELECT * FROM pattern_scan_signals LIMIT 1")
r = cursor.fetchone()
if r:
    print('\nSample row:', dict(r))
conn.close()
