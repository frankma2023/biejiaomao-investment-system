import sqlite3
db = sqlite3.connect('data/lixinger.db')
cur = db.cursor()

# 找缠论相关表
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%chanlun%' OR name LIKE '%bi%'")
print('=== 缠论相关表 ===')
for r in cur.fetchall():
    print(f'  {r[0]}')

# 找信号相关表
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%signal%' OR name LIKE '%backfill%'")
print('\n=== 信号/回填相关表 ===')
for r in cur.fetchall():
    print(f'  {r[0]}')

# mw_signal_daily schema
print('\n=== mw_signal_daily schema ===')
cur.execute("PRAGMA table_info(mw_signal_daily)")
for r in cur.fetchall():
    print(f'  {r[1]} ({r[2]})')

# market_breakout_daily schema
print('\n=== market_breakout_daily schema ===')
cur.execute("PRAGMA table_info(market_breakout_daily)")
for r in cur.fetchall():
    print(f'  {r[1]} ({r[2]})')

# backfill_v2_progress schema
print('\n=== backfill_v2_progress schema ===')
cur.execute("PRAGMA table_info(backfill_v2_progress)")
for r in cur.fetchall():
    print(f'  {r[1]} ({r[2]})')

# mw_signal_daily sample
print('\n=== mw_signal_daily sample (3 rows) ===')
cur.execute("SELECT * FROM mw_signal_daily LIMIT 3")
cols = [d[0] for d in cur.description]
print(f'  columns: {cols}')
for r in cur.fetchall():
    print(f'  {r}')

# check if there's a signals table
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%breakout%' OR name LIKE '%pocket%'")
print('\n=== breakout/pocket tables ===')
for r in cur.fetchall():
    print(f'  {r[0]}')

# check backfill progress in another way
print('\n=== backfill_v2_progress sample ===')
try:
    cur.execute("SELECT * FROM backfill_v2_progress LIMIT 5")
    cols = [d[0] for d in cur.description]
    print(f'  columns: {cols}')
    for r in cur.fetchall():
        print(f'  {r}')
except Exception as e:
    print(f'  ERROR: {e}')

db.close()
