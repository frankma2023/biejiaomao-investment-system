import sqlite3
db = sqlite3.connect('data/lixinger.db')
cur = db.cursor()

print('=== stock_basic ===')
cur.execute('PRAGMA table_info(stock_basic)')
for r in cur.fetchall():
    print(f'  {r[1]} ({r[2]})')

print('\n=== daily_kline (first 15 cols) ===')
cur.execute('PRAGMA table_info(daily_kline)')
for r in cur.fetchall()[:15]:
    print(f'  {r[1]} ({r[2]})')

print('\n=== index_daily_kline (first 15 cols) ===')
cur.execute('PRAGMA table_info(index_daily_kline)')
for r in cur.fetchall()[:15]:
    print(f'  {r[1]} ({r[2]})')

print('\n=== sample indices ===')
cur.execute('SELECT DISTINCT index_code FROM index_daily_kline LIMIT 10')
print([r[0] for r in cur.fetchall()])

# ST-related fields
print('\n=== check ST field in daily_kline ===')
cur.execute("SELECT COUNT(*) FROM daily_kline WHERE is_st=1 LIMIT 1")
print(f'is_st=1 rows: {cur.fetchone()[0]}')

# sample market breakout v2
print('\n=== market_breakout_v2_daily sample ===')
cur.execute('SELECT * FROM market_breakout_v2_daily LIMIT 1')
cols = [d[0] for d in cur.description]
print(f'cols: {cols}')

db.close()
