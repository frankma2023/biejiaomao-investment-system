"""检查隆基 2026-03-20 案例未命中的原因"""
import sqlite3
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row

rows = db.execute("""
    SELECT date, close, open, change_pct, amount FROM daily_kline
    WHERE stock_code='601012' AND date BETWEEN '2026-03-16' AND '2026-03-26'
    ORDER BY date
""").fetchall()
for r in rows:
    print(f"  {r['date']}: chg={r['change_pct']*100:+.4f}% amount={r['amount']/1e8:.1f}亿")

# 前20日均额
r = db.execute("""
    SELECT AVG(amount)/1e8 as avg FROM daily_kline
    WHERE stock_code='601012' AND date<'2026-03-20' AND date>='2026-02-20'
""").fetchone()
print(f"\n前20日均额: {r['avg']:.1f}亿")

# 03-20 和 03-23 的量比
r20 = db.execute("SELECT amount FROM daily_kline WHERE stock_code='601012' AND date='2026-03-20'").fetchone()
r23 = db.execute("SELECT amount FROM daily_kline WHERE stock_code='601012' AND date='2026-03-23'").fetchone()
print(f"03-20量比: {r20['amount']/(r['avg']*1e8):.2f}x")
print(f"03-23量比: {r23['amount']/(r['avg']*1e8):.2f}x")

# 03-23 的 change_pct 精确值
r23b = db.execute("SELECT change_pct FROM daily_kline WHERE stock_code='601012' AND date='2026-03-23'").fetchone()
print(f"03-23 change_pct: {r23b['change_pct']} ({r23b['change_pct']*100:.4f}%)")
print(f"阈值 -1% (-0.01): 是否满足 <= -0.01? {r23b['change_pct'] <= -0.01}")

db.close()
