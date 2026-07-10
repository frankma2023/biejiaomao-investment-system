import sqlite3, numpy as np
from datetime import datetime

conn = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db')
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT stock_code, b1_date, b2_date,
           h_rs250, decline_pct, b1_return_pct, h_date, c_amount_avg
    FROM mw_signal_daily
    WHERE b1_date >= '2026-01-01' AND b1_date <= '2026-07-07'
      AND stock_code != '_sentinel_'
""").fetchall()

bt = conn.execute("""
    SELECT stock_code, signal_date, net_ret_pct, is_win
    FROM backtest_results
    WHERE signal_mask & 1 = 1 AND entry_method='T+1_O' AND hold_days=20
      AND signal_date >= '2026-01-01'
""").fetchall()
bt_map = {(r['stock_code'], r['signal_date']): r for r in bt}

recs = []
for s in rows:
    bt_r = bt_map.get((s['stock_code'], s['b1_date']))
    if not bt_r: continue
    days_h = 0
    if s['h_date'] and s['h_date'] > '2000':
        days_h = (datetime.strptime(s['b1_date'],'%Y-%m-%d') - datetime.strptime(s['h_date'],'%Y-%m-%d')).days
    # c_amount_avg 单位是元，转换为亿元
    amt_yi = (s['c_amount_avg'] or 0) / 1e8
    recs.append({
        'rs': s['h_rs250'] or 0,
        'decline': s['decline_pct'] or 0,
        'b1_ret': s['b1_return_pct'] or 0,
        'amt_yi': amt_yi,
        'days_h': days_h,
        'has_b2': bool(s['b2_date'] and s['b2_date'] > s['b1_date']),
        'ret': bt_r['net_ret_pct'],
        'win': bt_r['is_win'],
    })

print(f"2026年: {len(recs)}条")
base = np.mean([r['ret'] for r in recs])
base_wr = np.mean([r['ret']>0 for r in recs]) * 100
print(f"全量基准: 胜率{base_wr:.1f}% 收益{base:.1f}%")

# 全周期最优组合（等效移植到DB可用字段）
# 全周期: h_rs250≥59 + log_amt<8.13(≈成交<1.35亿/日) + ma_bullish=0 + days_since_h>36
# 2026用: h_rs250≥60 + c_amount_avg<1.35亿(日均) + days_since_h>35
# c_amount_avg 是横盘期日均成交额，不是20日均额，近似替代

tests = [
    ("全量", lambda r: True),
    ("RS≥60", lambda r: r['rs'] >= 60),
    ("RS≥70", lambda r: r['rs'] >= 70),
    ("RS≥80", lambda r: r['rs'] >= 80),
    ("RS≥90", lambda r: r['rs'] >= 90),
    ("日均成交<2亿", lambda r: 0 < r['amt_yi'] < 2.0),
    ("日均成交<1亿", lambda r: 0 < r['amt_yi'] < 1.0),
    ("跌幅>15%", lambda r: r['decline'] > 15),
    ("跌幅>20%", lambda r: r['decline'] > 20),
    ("跌幅>25%", lambda r: r['decline'] > 25),
    ("距H>35天", lambda r: r['days_h'] > 35),
    ("距H>50天", lambda r: r['days_h'] > 50),
    # 2因子
    ("RS≥60 + 成交<2亿", lambda r: r['rs'] >= 60 and 0 < r['amt_yi'] < 2.0),
    ("RS≥70 + 成交<2亿", lambda r: r['rs'] >= 70 and 0 < r['amt_yi'] < 2.0),
    # 3因子
    ("RS≥70 + 成交<2亿 + 距H>35", lambda r: r['rs'] >= 70 and 0 < r['amt_yi'] < 2.0 and r['days_h'] > 35),
    # 4因子
    ("RS≥70 + 成交<2亿 + 距H>35 + 跌幅>20%", lambda r: r['rs'] >= 70 and 0 < r['amt_yi'] < 2.0 and r['days_h'] > 35 and r['decline'] > 20),
]

for label, rule in tests:
    f = [r for r in recs if rule(r)]
    if len(f) < 5: 
        print(f"{label:40s} {len(f):>5d} (样本不足)")
        continue
    wr = np.mean([x['ret']>0 for x in f]) * 100
    ar = np.mean([x['ret'] for x in f])
    b2_rate = np.mean([x['has_b2'] for x in f]) * 100
    print(f"{label:40s} {len(f):>5d} 胜率={wr:>5.1f}% 收益={ar:>5.1f}% B2率={b2_rate:.0f}%  vs全量{wr-base_wr:>+5.1f}pp")

# B2确认子集
b2 = [r for r in recs if r['has_b2']]
nob2 = [r for r in recs if not r['has_b2']]
print(f"\nB2确认子集: {len(b2)}条 胜率={np.mean([x['ret']>0 for x in b2])*100:.1f}% 收益={np.mean([x['ret'] for x in b2]):.1f}%")
print(f"无B2: {len(nob2)}条 胜率={np.mean([x['ret']>0 for x in nob2])*100:.1f}%")

# 最佳组合的B2确认率
for label, rule in tests[-3:]:
    f = [r for r in recs if rule(r)]
    b2f = [r for r in f if r['has_b2']]
    if len(f) > 0 and len(b2f) > 0:
        print(f"{label}: {len(f)}条 B2确认{len(b2f)}条({len(b2f)/len(f)*100:.0f}%) B2胜率={np.mean([x['ret']>0 for x in b2f])*100:.1f}%")
conn.close()
