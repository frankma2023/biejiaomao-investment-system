import sys, sqlite3, json
sys.path.insert(0, 'D:/hanako/investment-system/src')

db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row

import scanners.mw_signal as mw
from collections import defaultdict
from datetime import datetime, timedelta

scan_date = '2019-01-02'

# Load RS
mw._rs_cache = {}
for r in db.execute("SELECT stock_code, rps_20, rps_250 FROM stock_rs_daily WHERE date=? AND rps_250 IS NOT NULL", (scan_date,)).fetchall():
    mw._rs_cache[r['stock_code']] = (r['rps_20'], r['rps_250'])
print('RS loaded:', len(mw._rs_cache), 'stocks')

# Load K-lines (400 days)
min_date = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=400)).strftime('%Y-%m-%d')
mw._kline_cache = defaultdict(list)
for r in db.execute("SELECT stock_code, date, open, high, low, close, volume FROM daily_kline WHERE date>=? AND date<=? ORDER BY stock_code, date", (min_date, scan_date)).fetchall():
    mw._kline_cache[r['stock_code']].append(dict(r))
print('K-lines loaded:', sum(len(v) for v in mw._kline_cache.values()), 'rows,', len(mw._kline_cache), 'stocks')

# Load chanlun
import orjson
mw._chanlun_cache = {}
for r in db.execute("SELECT stock_code, bi_json FROM chanlun_bi_json WHERE scan_date=?", (scan_date,)).fetchall():
    try:
        mw._chanlun_cache[(r['stock_code'], scan_date)] = orjson.loads(r['bi_json'])
    except:
        pass
print('Chanlun loaded:', len(mw._chanlun_cache), 'stocks')

# Test scan_stock for a high-RS stock
high_rs = sorted(mw._rs_cache.items(), key=lambda x: x[1][1], reverse=True)
top_code = high_rs[0][0]
print('Top RS stock:', top_code, 'RS250=', high_rs[0][1][1])

code = top_code
klines = mw._kline_cache.get(code, [])
print(code, 'K-lines:', len(klines))
if klines:
    print('  First:', klines[0]['date'], 'close=', klines[0]['close'])
    print('  Last:', klines[-1]['date'], 'close=', klines[-1]['close'])

# Check if bi data exists
bi = mw._chanlun_cache.get((code, scan_date))
has_bi = 'YES' if bi else 'NO'
tops = len([b for b in bi if b['direction'] == '向下']) if bi else 0
print(code, 'bi data:', has_bi, 'tops=', tops)

# Run scan_stock
from scanners.mw_signal import scan_stock
passed, result = scan_stock(klines, scan_date, code, db)
print('scan_stock result: passed=', passed)
if result:
    print('  B1:', result.get('b1_date'), 'B2:', result.get('b2_date'))
    print('  H:', result.get('h_date'), 'L:', result.get('l_date'))
else:
    print('  NO SIGNAL')

db.close()
