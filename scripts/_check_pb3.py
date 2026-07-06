import sqlite3
db=sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c=db.cursor()
c.execute("SELECT date,pb FROM index_fundamental_daily WHERE stock_code='980092' AND pb BETWEEN 1.55 AND 1.62 ORDER BY date DESC LIMIT 5")
rows = c.fetchall()
if rows:
    for r in rows: print(r[0], round(r[1], 4))
else:
    print('No PB between 1.55-1.62 found')

# Check if maybe it's PE not PB
c.execute("SELECT date,pe_ttm FROM index_fundamental_daily WHERE stock_code='980092' AND pe_ttm BETWEEN 1.55 AND 1.62 ORDER BY date DESC LIMIT 5")
rows2 = c.fetchall()
if rows2:
    print('\nPE around 1.58:')
    for r in rows2: print(r[0], round(r[1], 4))

# Check entire range
c.execute("SELECT date,pb FROM index_fundamental_daily WHERE stock_code='980092' ORDER BY date LIMIT 5")
print('\nEarliest:')
for r in c.fetchall(): print(r[0], round(r[1], 4))
db.close()
