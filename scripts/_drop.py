import sqlite3
c=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
c.execute("DROP TABLE IF EXISTS pocket_pivot_daily");c.commit();c.close()
print("dropped")
