import sqlite3
db=sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c=db.cursor()
c.execute("SELECT b1_date,b2_date,score,score_h,score_d,score_c,score_p,score_i1,score_i2,score_sig,score_gap,confidence,tech_score FROM mw_signal_daily WHERE stock_code='002466' AND b1_date='2026-06-22'")
cols=[d[0] for d in c.description]
r=c.fetchone()
d=dict(zip(cols,r))
print(f'b1_date={d["b1_date"]}')
print(f'b2_date={d["b2_date"]}')
print(f'score={d["score"]} (H={d["score_h"]}+D={d["score_d"]}+C={d["score_c"]}+P={d["score_p"]}+I1={d["score_i1"]}+I2={d["score_i2"]}+Sig={d["score_sig"]}+Gap={d["score_gap"]})')
print(f'confidence={d["confidence"]}')
print(f'tech_score={d["tech_score"]}')
if d['b2_date']:
    print(f'\nB1+B2信号：满分100，置信度阈值 高≥80/中55-79/低<55')
    print(f'score=75 在 55~79 区间 → 中置信')
else:
    print(f'\nB1-only：满分75，置信度阈值 高≥55/中40-54/低<40')
db.close()
