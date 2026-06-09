import sqlite3
c=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
r=c.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM pocket_pivot_daily").fetchone()
print(f"pocket_pivot_daily: {r[0]} rows, {r[1]} ~ {r[2]}")
r=c.execute("SELECT COUNT(DISTINCT date) FROM pocket_pivot_daily").fetchone()
print(f"distinct dates: {r[0]}")
c.close()
