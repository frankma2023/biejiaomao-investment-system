import sqlite3
c = sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
cur = c.cursor()

cur.execute("""UPDATE mw_signal_daily SET score_i1 = 
    CASE WHEN ind_rs250 >= 85 THEN 15 WHEN ind_rs250 >= 80 THEN 10 
         WHEN ind_rs250 >= 75 THEN 5 ELSE 0 END""")
print(f"score_i1: {cur.rowcount}")

cur.execute("""UPDATE mw_signal_daily SET score = 
    COALESCE(score_h,0)+COALESCE(score_d,0)+COALESCE(score_c,0)+
    COALESCE(score_p,0)+COALESCE(score_i1,0)+COALESCE(score_i2,0)+
    COALESCE(score_sig,0)+COALESCE(score_gap,0)""")
print(f"score: {cur.rowcount}")

cur.execute("""UPDATE mw_signal_daily SET confidence = 
    CASE WHEN score >= 80 THEN '高' WHEN score >= 55 THEN '中' ELSE '低' END""")
print(f"confidence: {cur.rowcount}")

cur.execute("""UPDATE mw_signal_daily SET is_plus = 
    CASE WHEN score >= 80 AND score_d = 5 AND score_i1 = 15 THEN 1 ELSE 0 END""")
print(f"is_plus: {cur.rowcount}")
c.commit()

cur.execute("SELECT score_i1, COUNT(*) FROM mw_signal_daily GROUP BY score_i1 ORDER BY score_i1 DESC")
print("score_i1 dist:", cur.fetchall())

cur.execute("SELECT confidence, COUNT(*) FROM mw_signal_daily GROUP BY confidence")
print("confidence:", cur.fetchall())

cur.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE is_plus=1")
print(f"PLUS: {cur.fetchone()[0]}")

# 抽样
cur.execute("""SELECT stock_code, b2_date, ind_rs250, score_i1, score, confidence 
    FROM mw_signal_daily WHERE is_plus=1 ORDER BY b2_date DESC LIMIT 5""")
print("PLUS samples:")
for r in cur.fetchall():
    print(f"  {r[0]} {r[1]} ind_rs250={r[2]} score_i1={r[3]} score={r[4]} {r[5]}")

c.close()
print("Done.")
