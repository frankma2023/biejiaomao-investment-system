import sqlite3
db=sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c=db.cursor()
c.execute("SELECT metric_code, MAX(date), MIN(date) FROM fundamental_indicator WHERE metric_code IN ('pe_ttm','pb','ps_ttm','dyr') GROUP BY 1")
for r in c.fetchall(): print(f'{r[0]}: {r[1]} ~ {r[2]}')
db.close()
