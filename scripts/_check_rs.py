import sqlite3
c=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
r=c.execute("SELECT MIN(date),MAX(date),COUNT(*),COUNT(DISTINCT date) FROM stock_rs_daily WHERE date>='2023-05-01' AND date<='2023-05-31'").fetchone()
print(f"2023-05: {r[0]} ~ {r[1]}, {r[2]} rows, {r[3]} 个交易日")
# sample
for r2 in c.execute("SELECT date,COUNT(*) FROM stock_rs_daily WHERE date>='2023-05-01' AND date<='2023-05-31' GROUP BY date ORDER BY date LIMIT 5").fetchall():
    print(f"  {r2[0]}: {r2[1]} stocks")
c.close()
