import sqlite3
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
c = db.cursor()
c.execute("SELECT DISTINCT stock_code FROM index_daily_kline WHERE stock_code LIKE '%000985%' OR stock_code LIKE '%985%' LIMIT 5")
print('000985:', c.fetchall())
c.execute("SELECT DISTINCT stock_code FROM index_daily_kline WHERE stock_code LIKE '%CSI%' OR stock_code LIKE '%中证%' LIMIT 10")
print('CSI:', c.fetchall())
c.execute("SELECT DISTINCT stock_code FROM index_daily_kline LIMIT 10")
print('all indices:', c.fetchall())
c.execute("SELECT DISTINCT listing_status FROM stock_basic")
print('listing_status:', c.fetchall())
# All daily_kline columns
c.execute("SELECT * FROM daily_kline LIMIT 1")
cols = [d[0] for d in c.description]
print('daily_kline cols:', cols)
db.close()
