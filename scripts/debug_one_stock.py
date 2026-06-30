import sys, sqlite3, orjson
sys.path.insert(0, 'D:/hanako/investment-system/src')
from datetime import datetime, timedelta
from collections import defaultdict

db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row

scan_date = '2022-01-04'

# Minimal setup: load RS, 400d K-lines, chanlun for ONE stock
import scanners.mw_signal as mw

# Get a high RS stock
row = db.execute("SELECT stock_code, rps_250 FROM stock_rs_daily WHERE date=? AND rps_250>=95 LIMIT 1", (scan_date,)).fetchone()
if not row:
    print('No high RS stock found')
    db.close()
    sys.exit(0)

code = row['stock_code']
print('Testing:', code, 'RS250=', row['rps_250'])

# Load K-lines
min_date = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=400)).strftime('%Y-%m-%d')
klines = []
for r in db.execute("SELECT date, open, high, low, close, volume FROM daily_kline WHERE stock_code=? AND date>=? AND date<=? ORDER BY date", (code, min_date, scan_date)).fetchall():
    klines.append(dict(r))
print('K-lines:', len(klines), 'from', klines[0]['date'] if klines else 'NONE', 'to', klines[-1]['date'] if klines else 'NONE')

# Load chanlun
bi_row = db.execute("SELECT bi_json FROM chanlun_bi_json WHERE stock_code=? AND scan_date=?", (code, scan_date)).fetchone()
if bi_row and bi_row[0]:
    try:
        mw._chanlun_cache = {(code, scan_date): orjson.loads(bi_row[0])}
        print('Chanlun: loaded from cache')
    except:
        print('Chanlun: failed to parse')
        mw._chanlun_cache = {}
else:
    print('Chanlun: no data for', code, 'on', scan_date)
    mw._chanlun_cache = {}

# Set minimal RS for this stock
mw._rs_cache = {code: (50, row['rps_250'])}

# Run scan_stock
from scanners.mw_signal import scan_stock
print('Running scan_stock...')
passed, result = scan_stock(klines, scan_date, code, db)
print('Result: passed=', passed)
if result:
    for k in ['b1_date', 'b2_date', 'h_date', 'h_price', 'l_date', 'l_price', 'decline_pct', 'c_start', 'c_end']:
        print(f'  {k}: {result.get(k)}')
else:
    print('  NO SIGNAL - checking why...')
    # Check if H was found
    bi = mw._chanlun_cache.get((code, scan_date), [])
    tops = [b for b in bi if b['direction'] == '向下']
    print(f'  bi tops: {len(tops)}')
    if tops:
        top = tops[0]
        print(f'  first top: {top["sdt"][:10]} high={top["high"]}')
        # Check if this top is before scan_date
        if top['sdt'][:10] <= scan_date:
            print('  top is before/on scan_date - should work')
        else:
            print('  top is AFTER scan_date - wrong!')

db.close()
