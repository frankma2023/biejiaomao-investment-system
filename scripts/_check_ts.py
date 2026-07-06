import sqlite3
db=sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c=db.cursor()
c.execute("SELECT b1_date,tech_score,confidence,stock_name FROM mw_signal_daily WHERE tech_score>0 AND b1_date>='2026-06-30' LIMIT 10")
for r in c.fetchall():
    tier = '极高' if r[1]>=85 else ('很高' if r[1]>=75 else ('高' if r[1]>=65 else ('中' if r[1]>=50 else '低')))
    print(f'{r[0]} {r[3]:<10s} TS={r[1]:>3d}({tier})  conf={r[2]}')
print(f'\nTotal with TS>0 on 2026-06-30:')
c.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE tech_score>0 AND b1_date='2026-06-30'")
print(c.fetchone()[0])
db.close()
