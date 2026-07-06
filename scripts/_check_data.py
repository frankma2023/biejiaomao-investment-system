import sqlite3
db = sqlite3.connect('data/lixinger.db')
cur = db.cursor()

tables = [
    'chanlun_bi',
    'mw_signal_daily', 
    'pocket_pivot_daily',
    'market_breakout_daily',
    'market_breakout_v2_daily',
    'pattern_scan_signals',
]

for t in tables:
    try:
        cur.execute(f'SELECT COUNT(*), MIN(date), MAX(date) FROM {t}')
        cnt, dmin, dmax = cur.fetchone()
        print(f'{t}: {cnt} rows, {dmin} ~ {dmax}')
    except Exception as e:
        print(f'{t}: ERROR - {e}')

# 按年份看信号覆盖
print('\n--- 按年份信号数量 ---')
signal_tables = {
    'mw_signal_daily': 'mw',
    'pocket_pivot_daily': 'pp',
    'market_breakout_daily': 'bo_v1',
    'market_breakout_v2_daily': 'bo_v2',
    'pattern_scan_signals': 'sell',
}
for t, label in signal_tables.items():
    print(f'\n{label}:')
    try:
        cur.execute(f"SELECT substr(date,1,4) as yr, COUNT(*) FROM {t} WHERE date >= '2023-01-01' AND date <= '2026-06-22' GROUP BY yr ORDER BY yr")
        for row in cur.fetchall():
            print(f'  {row[0]}: {row[1]}')
    except Exception as e:
        print(f'  ERROR: {e}')

# 检查 backtest_v2_progress
print('\n--- backfill_v2_progress ---')
try:
    cur.execute("SELECT engine, COUNT(*), MIN(date), MAX(date) FROM backfill_v2_progress GROUP BY engine")
    for row in cur.fetchall():
        print(f'  {row[0]}: {row[1]} dates, {row[2]} ~ {row[3]}')
except Exception as e:
    print(f'  ERROR: {e}')

db.close()
