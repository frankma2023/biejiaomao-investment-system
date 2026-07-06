# Add tech_score to INSERT and VALUES in save_signals
f = open('D:/hanako/investment-system/src/scanners/mw_signal.py', 'r', encoding='utf-8')
t = f.read(); f.close()

# Fix 1: Add tech_score column in INSERT
old1 = 'score_ma,score_sig,score_gap,score_m1,score_m2,score_m3,is_plus,\n                ind_rs20'
new1 = 'score_ma,score_sig,score_gap,score_m1,score_m2,score_m3,is_plus,\n                tech_score,\n                ind_rs20'
t = t.replace(old1, new1)

# Fix 2: Add ts to VALUES (need to count ? and add one more)
old2 = 's.get(\"score_ma\",0),s.get(\"score_sig\",0),s.get(\"score_gap\",0),s.get(\"score_m1\",0),s.get(\"score_m2\",0),s.get(\"score_m3\",0),s.get(\"is_plus\",0),\n                s.get(\"ind_rs20\")'
new2 = 's.get(\"score_ma\",0),s.get(\"score_sig\",0),s.get(\"score_gap\",0),s.get(\"score_m1\",0),s.get(\"score_m2\",0),s.get(\"score_m3\",0),s.get(\"is_plus\",0),ts,\n                s.get(\"ind_rs20\")'
t = t.replace(old2, new2)

# Fix 3: Add tech_score column in CREATE TABLE
old3 = 'is_plus INTEGER DEFAULT 0,\n            ind_rs20'
new3 = 'is_plus INTEGER DEFAULT 0,\n            tech_score INTEGER DEFAULT 0,\n            ind_rs20'
t = t.replace(old3, new3)

f = open('D:/hanako/investment-system/src/scanners/mw_signal.py', 'w', encoding='utf-8')
f.write(t); f.close()
print('Done')
