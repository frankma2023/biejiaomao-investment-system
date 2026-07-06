import sqlite3
db=sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c=db.cursor()
for t in ['market_health_daily','market_sell_score_daily','market_health_sector_daily','index_capital_flow_daily']:
    try:
        c.execute(f"PRAGMA table_info({t})")
        print(f'\n{t}:')
        for r in c.fetchall(): print(f'  {r[1]:30s} {r[2]}')
        c.execute(f"SELECT * FROM {t} LIMIT 1")
        print(f'  sample: {dict(zip([d[0] for d in c.description], c.fetchone()))}')
    except Exception as e: print(f'  ERROR: {e}')
db.close()
