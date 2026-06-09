import sqlite3
c=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
r=c.execute("SELECT COUNT(*) FROM pocket_pivot_daily").fetchone()
print(f"Rows: {r[0]}")
c.close()
