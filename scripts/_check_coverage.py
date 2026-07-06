import sqlite3
db = sqlite3.connect('data/lixinger.db')
cur = db.cursor()

print('=== 2023-2026 各表信号覆盖（按年） ===\n')

# MW: b2_date is the signal date
cur.execute("""
    SELECT substr(b2_date,1,4), COUNT(*) 
    FROM mw_signal_daily 
    WHERE b2_date >= '2023-01-01' AND b2_date <= '2026-06-22'
    GROUP BY 1 ORDER BY 1
""")
print('MW (b2_date):')
for r in cur.fetchall(): print(f'  {r[0]}: {r[1]}')

# MW B1
cur.execute("""
    SELECT substr(b1_date,1,4), COUNT(*) 
    FROM mw_signal_daily 
    WHERE b1_date >= '2023-01-01' AND b1_date <= '2026-06-22'
    GROUP BY 1 ORDER BY 1
""")
print('MW B1 (b1_date):')
for r in cur.fetchall(): print(f'  {r[0]}: {r[1]}')

# MW PLUS
cur.execute("""
    SELECT substr(b2_date,1,4), COUNT(*) 
    FROM mw_signal_daily 
    WHERE b2_date >= '2023-01-01' AND b2_date <= '2026-06-22' AND is_plus=1
    GROUP BY 1 ORDER BY 1
""")
print('MW PLUS:')
for r in cur.fetchall(): print(f'  {r[0]}: {r[1]}')

# PP
cur.execute("""
    SELECT substr(date,1,4), engine_version, COUNT(*) 
    FROM pocket_pivot_daily 
    WHERE date >= '2023-01-01' AND date <= '2026-06-22'
    GROUP BY 1, 2 ORDER BY 1, 2
""")
print('\nPP (by engine_version):')
for r in cur.fetchall(): print(f'  {r[0]} {r[1]}: {r[2]}')

# BO V2
cur.execute("""
    SELECT substr(date,1,4), COUNT(*) 
    FROM market_breakout_v2_daily 
    WHERE date >= '2023-01-01' AND date <= '2026-06-22'
    GROUP BY 1 ORDER BY 1
""")
print('\nBO V2:')
for r in cur.fetchall(): print(f'  {r[0]}: {r[1]}')

# Sell signals by source
cur.execute("""
    SELECT substr(date,1,4), source, COUNT(*) 
    FROM pattern_scan_signals 
    WHERE date >= '2023-01-01' AND date <= '2026-06-22'
      AND source IN ('climax_top','top_pattern','railroad_tracks','breakout_failure','volume_divergence')
    GROUP BY 1, 2 ORDER BY 1, 2
""")
print('\nSell signals (by source):')
for r in cur.fetchall(): print(f'  {r[0]} {r[1]}: {r[2]}')

# Chanlun
cur.execute("""
    SELECT substr(date,1,4), COUNT(*) 
    FROM chanlun_scan_daily 
    WHERE date >= '2023-01-01' AND date <= '2026-06-22'
    GROUP BY 1 ORDER BY 1
""")
print('\nChanlun scan:')
for r in cur.fetchall(): print(f'  {r[0]}: {r[1]}')

# backfill progress coverage
cur.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM backfill_v2_progress")
print(f'\nbackfill_v2_progress: {cur.fetchone()}')

# Total unique dates per year
cur.execute("SELECT COUNT(DISTINCT date) FROM backfill_v2_progress WHERE date >= '2023-01-01' AND date <= '2026-06-22'")
print(f'Unique dates 2023-2026: {cur.fetchone()[0]}')

db.close()
