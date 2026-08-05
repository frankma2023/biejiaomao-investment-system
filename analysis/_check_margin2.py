"""确认表存在性 + daily_update 日志"""
import sqlite3
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row

tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%margin%'").fetchall()
print("margin相关表:", [t['name'] for t in tables])

r = db.execute("SELECT MAX(date) FROM daily_margin_history").fetchone()
print(f"\ndaily_margin_history 最新: {r[0]}")

rows = db.execute("SELECT date, COUNT(*) as cnt FROM daily_margin_history GROUP BY date ORDER BY date DESC LIMIT 5").fetchall()
for x in rows: print(f"  {x['date']}: {x['cnt']}")

# daily_margin_total?
r = db.execute("SELECT MAX(date) FROM daily_margin_total").fetchone()
print(f"\ndaily_margin_total 最新: {r[0]}")
db.close()
