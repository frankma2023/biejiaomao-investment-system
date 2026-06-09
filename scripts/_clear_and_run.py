import sqlite3
c=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
c.execute("DELETE FROM pocket_pivot_daily")
c.commit()
print(f"Cleared. Running backfill...")
c.close()
