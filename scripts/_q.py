import sqlite3
c=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
cur=c.cursor()
for q,label in [
    ("SELECT score_d,COUNT(*) FROM mw_signal_daily GROUP BY score_d ORDER BY score_d DESC", "score_d"),
    ("SELECT score_i2,COUNT(*) FROM mw_signal_daily GROUP BY score_i2 ORDER BY score_i2 DESC", "score_i2"),
    ("SELECT confidence,COUNT(*) FROM mw_signal_daily GROUP BY confidence", "conf"),
    ("SELECT COUNT(*),ROUND(AVG(score),1),MAX(score),MIN(score) FROM mw_signal_daily", "score"),
    ("SELECT COUNT(*) FROM mw_signal_daily WHERE is_plus=1", "PLUS"),
]:
    cur.execute(q)
    rows = cur.fetchall()
    print(f"{label}: {rows}")
c.close()
