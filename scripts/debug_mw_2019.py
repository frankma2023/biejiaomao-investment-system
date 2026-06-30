import sqlite3, sys
sys.path.insert(0, 'D:/hanako/investment-system/src')

db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row

# 先清掉可能存在的旧缓存放
import scanners.mw_signal as mw
mw._chanlun_cache = None
mw._kline_cache = None
mw._rs_cache = None

from scanners.mw_signal import run_scan, get_all_stocks

conn = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
conn.row_factory = sqlite3.Row

stocks = get_all_stocks(conn, '2019-01-02')
print(f'候选股票: {len(stocks)} 只')

print('开始 run_scan...')
run_scan('2019-01-02', silent=False)

cnt = db.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE scan_date='2019-01-02'").fetchone()[0]
b1 = db.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b1_date='2019-01-02'").fetchone()[0]
b2 = db.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b2_date='2019-01-02'").fetchone()[0]
print(f'结果: scan={cnt} B1={b1} B2={b2}')
db.close()
conn.close()
