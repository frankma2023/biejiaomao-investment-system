import sqlite3
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c = db.cursor()
c.execute("SELECT * FROM mw_signal_daily WHERE stock_code='688213' AND b1_date='2026-06-30'")
r = c.fetchone()
cols = [d[0] for d in c.description]
d = dict(zip(cols, r))

print(f'=== 688213 思特威 2026-06-30 ===')
print(f'b1_date: {d["b1_date"]}')
print(f'b2_date: {d["b2_date"]}')
print(f'score: {d["score"]}')
print(f'confidence: {d["confidence"]}')
print()
print(f'  H: {d["score_h"]}/15  D: {d["score_d"]}/15  C: {d["score_c"]}/5')
print(f'  P: {d["score_p"]}/15  I1: {d["score_i1"]}/15  I2: {d["score_i2"]}/15')
print(f'  Sig: {d["score_sig"]}/10  Gap: {d["score_gap"]}/10')
print(f'  总分: {d["score"]} = {d["score_h"]}+{d["score_d"]}+{d["score_c"]}+{d["score_p"]}+{d["score_i1"]}+{d["score_i2"]}+{d["score_sig"]}+{d["score_gap"]}')
print(f'  B1-only max=75, B1+B2 max=100')
print(f'  h_rs250={d["h_rs250"]}  ind_rs250={d["ind_rs250"]}  decline={d["decline_pct"]}%')
print(f'  h_date={d["h_date"]}  l_date={d["l_date"]}')

if d['b2_date']:
    print(f'\n⚠ B2存在: {d["b2_date"]}, P和Gap参与评分')
else:
    print(f'\n✅ B1-only: 57 = H({d["score_h"]})+D({d["score_d"]})+C({d["score_c"]})+I1({d["score_i1"]})+I2({d["score_i2"]})+Sig({d["score_sig"]})')
    print(f'   满分75，置信度中(55~79)')

db.close()
