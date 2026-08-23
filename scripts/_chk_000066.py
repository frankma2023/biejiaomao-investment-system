# -*- coding: utf-8 -*-
import sqlite3
db = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db')
db.row_factory = sqlite3.Row
# 000066 在指数表吗
for t in ['index_fundamental_daily', 'index_daily_kline']:
    try:
        r = db.execute(f"SELECT COUNT(*) c, MIN(date) mn, MAX(date) mx FROM {t} WHERE stock_code='000066'").fetchone()
        print(f'{t} 000066: {r["c"]} 行 ({r["mn"]} ~ {r["mx"]})')
    except Exception as e:
        print(t, 'ERR', e)
# index_fundamental_daily 有哪些指数
rows = db.execute("SELECT DISTINCT stock_code FROM index_fundamental_daily").fetchall()
print(f'\nindex_fundamental_daily 指数数: {len(rows)}')
print('样例:', [r['stock_code'] for r in rows[:20]])
# 510170 相关：有没有以 510170 为代码的估值
r = db.execute("SELECT COUNT(*) c FROM index_fundamental_daily WHERE stock_code='510170'").fetchone()
print('510170 估值:', r['c'])
