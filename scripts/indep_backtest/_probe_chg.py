# -*- coding: utf-8 -*-
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
con = sqlite3.connect(r"D:\hanako\investment-system\data\lixinger.db")
# 取贵州茅台连续几天，核对 change_pct 单位与 close 变化是否一致
rows = con.execute("SELECT date,open,close,change_pct FROM daily_kline WHERE stock_code='600519' ORDER BY date DESC LIMIT 6").fetchall()
print("600519 最近6日: date, open, close, change_pct")
prev = None
for d,o,cl,chg in rows[::-1]:
    if prev:
        real = (cl/prev - 1)*100
        print(f"  {d}  close={cl:.2f}  change_pct字段={chg}  实际涨幅%={real:.3f}")
    else:
        print(f"  {d}  close={cl:.2f}  change_pct字段={chg}")
    prev = cl
# 看 change_pct 的分布范围
mn,mx,av = con.execute("SELECT MIN(change_pct),MAX(change_pct),AVG(ABS(change_pct)) FROM daily_kline WHERE change_pct IS NOT NULL").fetchone()
print(f"\nchange_pct 全表: min={mn} max={mx} avg|.|={av:.4f}")
