"""检查两融数据更新链路"""
import sqlite3
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row

# 1. daily_margin_history 最新日期
r = db.execute("SELECT MAX(date) FROM daily_margin_history").fetchone()
print(f"daily_margin_history 最新: {r[0]}")

# 2. stock_margin 最新日期
r = db.execute("SELECT MAX(date) FROM stock_margin").fetchone()
print(f"stock_margin 最新: {r[0]}")

# 3. 最近5天数据量
rows = db.execute("SELECT date, COUNT(*) as cnt FROM daily_margin_history GROUP BY date ORDER BY date DESC LIMIT 8").fetchall()
print("\ndaily_margin_history 最近8天:")
for x in rows: print(f"  {x['date']}: {x['cnt']}")

# 4. daily_kline 最新（对照市场最新交易日）
r = db.execute("SELECT MAX(date) FROM daily_kline").fetchone()
print(f"\ndaily_kline 最新交易日: {r[0]}")

db.close()
