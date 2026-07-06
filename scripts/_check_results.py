import sqlite3
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
cur = db.cursor()

print('=== backtest_results 概览 ===')
cur.execute("SELECT COUNT(*) FROM backtest_results")
print(f'总行数: {cur.fetchone()[0]}')

cur.execute("SELECT COUNT(DISTINCT combo_label) FROM backtest_results")
print(f'信号组合数: {cur.fetchone()[0]}')

cur.execute("""
    SELECT combo_label, COUNT(*) as cnt, 
           ROUND(AVG(net_ret_pct),2) as avg_ret, 
           ROUND(AVG(is_win)*100,1) as win_rate
    FROM backtest_results 
    WHERE signal_count=1
    GROUP BY combo_label 
    ORDER BY cnt DESC
""")
print('\n单信号表现 (全持有期+入场方式混合):')
for r in cur.fetchall():
    print(f'  {r[0]:12s}  {r[1]:>8,d}笔  avg={r[2]:>6.2f}%  win={r[3]:>5.1f}%')

cur.execute("""
    SELECT hold_days, COUNT(*), ROUND(AVG(net_ret_pct),2), ROUND(AVG(is_win)*100,1)
    FROM backtest_results
    GROUP BY hold_days ORDER BY hold_days
""")
print('\n按持有期:')
for r in cur.fetchall():
    print(f'  H{r[0]:<3d}  {r[1]:>8,d}笔  avg={r[2]:>6.2f}%  win={r[3]:>5.1f}%')

cur.execute("""
    SELECT market_regime, COUNT(*), ROUND(AVG(net_ret_pct),2), ROUND(AVG(is_win)*100,1)
    FROM backtest_results
    GROUP BY market_regime ORDER BY market_regime
""")
print('\n按市场环境:')
for r in cur.fetchall():
    print(f'  {r[0]:10s}  {r[1]:>8,d}笔  avg={r[2]:>6.2f}%  win={r[3]:>5.1f}%')

cur.execute("""
    SELECT entry_method, COUNT(*), ROUND(AVG(net_ret_pct),2), ROUND(AVG(is_win)*100,1)
    FROM backtest_results
    GROUP BY entry_method ORDER BY entry_method
""")
print('\n按入场方式:')
for r in cur.fetchall():
    print(f'  {r[0]:8s}  {r[1]:>8,d}笔  avg={r[2]:>6.2f}%  win={r[3]:>5.1f}%')

# Signal count groups
cur.execute("""
    SELECT signal_count, COUNT(*), ROUND(AVG(net_ret_pct),2), ROUND(AVG(is_win)*100,1)
    FROM backtest_results
    GROUP BY signal_count ORDER BY signal_count
""")
print('\n按信号共振数量:')
for r in cur.fetchall():
    print(f'  {r[0]}信号  {r[1]:>8,d}笔  avg={r[2]:>6.2f}%  win={r[3]:>5.1f}%')

db.close()
