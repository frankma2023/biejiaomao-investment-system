import sqlite3
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c = db.cursor()
c.execute("SELECT * FROM mw_signal_daily WHERE stock_code='688548' AND b1_date='2026-04-08'")
r = c.fetchone()
cols = [d[0] for d in c.description]
d = dict(zip(cols, r))

print('=== 688548 广钢气体 2026-04-08 ===')
print(f'b1_date: {d["b1_date"]}')
print(f'b2_date: {d["b2_date"]}')
print(f'score: {d["score"]}')
print(f'confidence: {d["confidence"]}')
print(f'is_plus: {d["is_plus"]}')
print()
print('--- 评分明细 ---')
print(f'  H(前高趋势): {d["score_h"]}/15  h_date={d["h_date"]} h_price={d["h_price"]} h_rs250={d["h_rs250"]}')
print(f'  D(调整深度): {d["score_d"]}/15  decline_pct={d["decline_pct"]}%  l_date={d["l_date"]} l_price={d["l_price"]}')
print(f'  C(横盘质量): {d["score_c"]}/5   c_amplitude={d["c_amplitude_pct"]}%  c_start={d["c_start"]}~c_end={d["c_end"]}')
print(f'  P(整理回撤): {d["score_p"]}/15  p_max_dd={d["p_max_dd_pct"]}%')
print(f'  I1(行业RS):  {d["score_i1"]}/15  ind_rs250={d["ind_rs250"]}  ind_code={d["ind_code"]}  ind_name={d["ind_name"]}')
print(f'  I2(个股RS):  {d["score_i2"]}/15  h_rs250={d["h_rs250"]}')
print(f'  Sig(共振):   {d["score_sig"]}/10')
print(f'  Gap(跳空):   {d["score_gap"]}/10  b2_is_gap={d["b2_is_gap"]}')
print()
print(f'  总分: {d["score"]} = {d["score_h"]}+{d["score_d"]}+{d["score_c"]}+{d["score_p"]}+{d["score_i1"]}+{d["score_i2"]}+{d["score_sig"]}+{d["score_gap"]}')

# Check if this is B1-only or has B2
if d['b2_date']:
    print(f'\n⚠ 此信号有B2！b2_date={d["b2_date"]}  b2_return={d["b2_return_pct"]}%')
    print(f'  因为有B2，P和Gap评分参与，所以能达到86分')
else:
    print(f'\n纯B1信号（无B2），但评分可能已包含B2确认后的更新')
db.close()
