import sqlite3, sys
sys.path.insert(0, 'D:/hanako/investment-system/src')

db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row

# 检查 get_all_stocks 返回什么
from scanners.mw_signal import get_all_stocks
stocks = get_all_stocks(db, '2016-01-04')
print(f'get_all_stocks: {len(stocks)} stocks')

# 直接查 SQL
total = db.execute("SELECT COUNT(DISTINCT stock_code) FROM daily_kline WHERE date='2016-01-04'").fetchone()[0]
print(f'K-line stocks on 2016-01-04: {total}')

# 检查是否有 close 为 NULL 的
nulls = db.execute("SELECT COUNT(*) FROM daily_kline WHERE date='2016-01-04' AND close IS NULL").fetchone()[0]
print(f'NULL close: {nulls}')

# 检查 run_scan 是否能扫
from scanners.mw_signal import run_scan
print('Starting run_scan...')
run_scan('2016-01-04', silent=False)
print('run_scan completed')

# 查结果
cnt = db.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE scan_date='2016-01-04'").fetchone()[0]
b1 = db.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b1_date='2016-01-04'").fetchone()[0]
print(f'scan records: {cnt}, B1: {b1}')

db.close()
