import sqlite3
c=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
r=c.execute("SELECT COUNT(DISTINCT date), MIN(date), MAX(date) FROM pocket_pivot_daily").fetchone()
print(f"已入库: {r[0]} 天, {r[1]} ~ {r[2]}")
r=c.execute("SELECT COUNT(*) FROM pocket_pivot_daily").fetchone()
print(f"总信号数: {r[0]}")
c.close()
