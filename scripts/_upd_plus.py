import sqlite3
c=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
cur=c.cursor()
cur.execute("UPDATE mw_signal_daily SET is_plus=CASE WHEN score>=80 AND score_d=15 AND score_i1=15 AND score_i2=15 THEN 1 ELSE 0 END")
print(f"updated: {cur.rowcount}")
c.commit()
cur.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE is_plus=1")
print(f"PLUS: {cur.fetchone()[0]}")
cur.execute("SELECT stock_code,stock_name,score,score_h,score_d,score_p,score_i1,score_i2 FROM mw_signal_daily WHERE is_plus=1 ORDER BY score DESC LIMIT 5")
for r in cur.fetchall():
    print(f"  {r[0]} {r[1]:8s} {r[2]} H={r[3]} D={r[4]} P={r[5]} I1={r[6]} I2={r[7]}")
c.close()
