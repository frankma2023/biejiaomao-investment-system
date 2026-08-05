"""Fact调查: 隆基案例 + 数据可用性"""
import sqlite3

db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row

# 1. 隆基 601012 案例窗口
print("=== 隆基绿能 601012 案例 ===")
rows = db.execute("""
    SELECT date, close, change_pct, volume, amount FROM daily_kline
    WHERE stock_code='601012' AND date BETWEEN '2026-03-10' AND '2026-04-10'
    ORDER BY date
""").fetchall()
for r in rows:
    print(f"  {r['date']}: close={r['close']} chg={r['change_pct']*100:+.2f}% amount={r['amount']/1e8:.1f}亿")

# 2. 前20日均额（2026-03-20之前）
r = db.execute("""
    SELECT AVG(amount)/1e8 as avg_amt FROM daily_kline
    WHERE stock_code='601012' AND date<'2026-03-20' AND date>='2026-02-20'
""").fetchone()
print(f"\n2026-02-20~03-19 日均成交额: {r['avg_amt']:.1f}亿")
print(f"03-20(85.2亿) / 日均 = {85.23/r['avg_amt']:.1f}x")
print(f"03-23(77.0亿) / 日均 = {76.95/r['avg_amt']:.1f}x")

# 3. daily_kline 字段
r = db.execute("PRAGMA table_info(daily_kline)").fetchall()
print(f"\ndaily_kline 列: {[c['name'] for c in r]}")

# 4. 数据覆盖
r = db.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM daily_kline WHERE stock_code='601012'").fetchone()
print(f"601012: {r[0]} ~ {r[1]} ({r[2]}行)")
r = db.execute("SELECT COUNT(DISTINCT stock_code) FROM daily_kline WHERE date='2026-03-20'").fetchone()
print(f"2026-03-20 全市场股票数: {r[0]}")
r = db.execute("SELECT MIN(date), MAX(date) FROM daily_kline").fetchone()
print(f"全表日期范围: {r[0]} ~ {r[1]}")

# 5. 隆基后续走势（90日）
rows = db.execute("""
    SELECT date, close FROM daily_kline
    WHERE stock_code='601012' AND date>'2026-03-23' ORDER BY date LIMIT 5
""").fetchall()
print(f"\n03-23之后5日: {[(r['date'], r['close']) for r in rows]}")

db.close()
