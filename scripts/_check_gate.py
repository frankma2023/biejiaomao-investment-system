import sqlite3
conn = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db')

# 全周期
r = conn.execute("SELECT COUNT(*) tot, SUM(CASE WHEN h_rs250>=60 THEN 1 ELSE 0 END) pass FROM mw_signal_daily WHERE b1_date>='2016-01-01' AND stock_code!='_sentinel_'").fetchone()
print(f"全周期: {r[1]}/{r[0]} ({r[1]/r[0]*100:.0f}%) pass RS250>=60")

# 近一个月每天
rows = conn.execute("""
    SELECT b1_date, COUNT(*) tot, SUM(CASE WHEN h_rs250>=60 THEN 1 ELSE 0 END) pass
    FROM mw_signal_daily WHERE b1_date>='2026-06-01' AND stock_code!='_sentinel_'
    GROUP BY b1_date ORDER BY b1_date
""").fetchall()
print("\n近一个月每日:")
for r in rows:
    pct = r[2]/r[1]*100 if r[1] > 0 else 0
    bar = '█' * int(pct/5)
    print(f"  {r[0]}: {r[2]:>3d}/{r[1]:>3d} ({pct:>3.0f}%) {bar}")

# 按年统计
rows = conn.execute("""
    SELECT SUBSTR(b1_date,1,4) yr, COUNT(*) tot, SUM(CASE WHEN h_rs250>=60 THEN 1 ELSE 0 END) pass
    FROM mw_signal_daily WHERE b1_date>='2016-01-01' AND stock_code!='_sentinel_'
    GROUP BY yr ORDER BY yr
""").fetchall()
print("\n按年:")
for r in rows:
    pct = r[2]/r[1]*100 if r[1] > 0 else 0
    print(f"  {r[0]}: {r[2]:>6d}/{r[1]:>6d} ({pct:>3.0f}%)")

conn.close()
