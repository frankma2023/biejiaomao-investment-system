import sqlite3
c=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
r=c.execute("SELECT MIN(date),MAX(date),COUNT(*) FROM pocket_pivot_daily").fetchone()
print(f"pocket_pivot_daily: {r[0]} ~ {r[1]}, {r[2]} rows")
c.close()
