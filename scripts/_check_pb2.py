import sqlite3
db=sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c=db.cursor()
c.execute("SELECT date,pb FROM index_fundamental_daily WHERE stock_code='980092' AND pb IS NOT NULL ORDER BY date DESC LIMIT 15")
for r in c.fetchall():
    print(r[0], round(r[1], 4))
db.close()
