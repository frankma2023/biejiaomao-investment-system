# -*- coding: utf-8 -*-
"""510170 数据确认 + 网格适配性研究"""
import sqlite3
db = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db')
db.row_factory = sqlite3.Row
r = db.execute("SELECT COUNT(*) c, MIN(date) mn, MAX(date) mx FROM index_daily_kline WHERE stock_code='510170'").fetchone()
print(f'510170: {r["c"]} 行 ({r["mn"]} ~ {r["mx"]})')
rows = db.execute("SELECT date, close FROM index_daily_kline WHERE stock_code='510170' ORDER BY date DESC LIMIT 21").fetchall()
if len(rows) >= 21:
    print(f'最新 {rows[0]["close"]} @ {rows[0]["date"]} | 20日 {(rows[0]["close"]/rows[20]["close"]-1)*100:+.1f}%')
