"""查 stock_margin 表丢失原因 + market_health 依赖"""
import sqlite3, os

# 1. 表是否存在
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("stock_margin 存在:", 'stock_margin' in tables)
print("daily_review_margin 存在:", 'daily_review_margin' in tables)
db.close()

# 2. market_health.py 对 stock_margin 的依赖
with open('D:/hanako/investment-system/src/scanners/market_health.py', 'r', encoding='utf-8') as f:
    c = f.read()
import re
for m in re.finditer(r'stock_margin', c):
    start = max(0, m.start()-100)
    print(f"\n...{c[start:m.end()+100]}...")
