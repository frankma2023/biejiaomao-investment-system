import sqlite3
db=sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c=db.cursor()
# Check latest fundamental data
c.execute("SELECT metric_code, MAX(date), COUNT(*) FROM fundamental_indicator WHERE metric_code IN ('pe_ttm','pb','ps_ttm','dyr') GROUP BY 1")
for r in c.fetchall(): print(f'{r[0]}: latest={r[1]} count={r[2]}')
# Check specific stock
c.execute("SELECT date, metric_code, value FROM fundamental_indicator WHERE stock_code='600519' AND metric_code='pe_ttm' ORDER BY date DESC LIMIT 5")
for r in c.fetchall(): print(f'600519 PE: {r[0]} = {r[1]}={r[2]}')
db.close()
