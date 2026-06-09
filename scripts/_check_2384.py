import sqlite3
db=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db");db.row_factory=sqlite3.Row
rows=db.execute("SELECT date,open,high,low,close FROM daily_kline WHERE stock_code='002384' AND date>='2026-01-01' AND date<='2026-04-17' ORDER BY date").fetchall()
print("002384 东山精密 K线 (2026-01 ~ 04-17):")
for r in rows:
    print(f"  {r['date']} O={r['open']:.2f} H={r['high']:.2f} L={r['low']:.2f} C={r['close']:.2f}")

# Check MW structure
r=db.execute("SELECT h_date,h_price,l_date,l_price,decline_pct,b1_date,b2_date FROM mw_signal_daily WHERE stock_code='002384' ORDER BY b2_date DESC LIMIT 1").fetchone()
print(f"\nMW structure:")
if r: print(f"  H={r['h_date']} ¥{r['h_price']} L={r['l_date']} ¥{r['l_price']} decline={r['decline_pct']}% B1={r['b1_date']} B2={r['b2_date']}")

db.close()
