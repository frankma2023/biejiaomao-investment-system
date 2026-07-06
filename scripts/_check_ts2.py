import sqlite3
db=sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c=db.cursor()
c.execute("SELECT COUNT(*), MIN(tech_score), MAX(tech_score) FROM mw_signal_daily WHERE tech_score > 0")
print('Total TS>0:', c.fetchone())
c.execute("SELECT b1_date, tech_score, stock_name FROM mw_signal_daily WHERE tech_score > 0 ORDER BY b1_date DESC LIMIT 5")
for r in c.fetchall():
    print(f'  {r[0]} {r[2]} TS={r[1]}')
db.close()
