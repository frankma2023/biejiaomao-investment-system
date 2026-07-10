import sqlite3
conn = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db')
conn.execute("ANALYZE backtest_results")
print("ANALYZE done")

# Single query, group by all dimensions
print("\nB1信号 全周期 正收益统计")
print("=" * 70)
rows = conn.execute("""
    SELECT entry_method, hold_days, 
           COUNT(*) tot, SUM(CASE WHEN net_ret_pct>0 THEN 1 ELSE 0 END) pos,
           ROUND(AVG(net_ret_pct),2) avg_r
    FROM backtest_results 
    WHERE signal_mask & 1 = 1
    GROUP BY entry_method, hold_days
    ORDER BY entry_method, hold_days
""").fetchall()
for r in rows:
    wr = r[3]/r[2]*100 if r[2] > 0 else 0
    print(f"  {r[0]:6s} H{r[1]:<3d} {r[2]:>8,d}笔  正{r[3]:>8,d}笔  胜率{wr:>5.1f}%  均收益{r[4]:>6.1f}%")

# By year
print(f"\nT+1_O 按年:")
for hd in [5, 10, 20]:
    print(f"\n  H{hd}:")
    rows = conn.execute(f"""
        SELECT SUBSTR(signal_date,1,4) yr, COUNT(*) tot, 
               SUM(CASE WHEN net_ret_pct>0 THEN 1 ELSE 0 END) pos
        FROM backtest_results 
        WHERE signal_mask & 1 = 1 AND entry_method='T+1_O' AND hold_days={hd}
        GROUP BY yr ORDER BY yr
    """).fetchall()
    for r in rows:
        wr = r[2]/r[1]*100 if r[1] > 0 else 0
        print(f"  {r[0]}: {r[1]:>7,d}笔  正{r[2]:>7,d}笔  胜率{wr:>5.1f}%")

conn.close()
