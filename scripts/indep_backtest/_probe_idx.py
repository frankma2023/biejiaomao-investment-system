# -*- coding: utf-8 -*-
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
con = sqlite3.connect(r"D:\hanako\investment-system\data\lixinger.db")
for r in con.execute("SELECT date,close,change,kline_type FROM index_daily_kline WHERE stock_code='000985' ORDER BY date DESC LIMIT 3").fetchall():
    print(r)
print("distinct kline_type:", con.execute("SELECT DISTINCT kline_type FROM index_daily_kline WHERE stock_code='000985'").fetchall())
