"""Debug PP V2 on 2024-01-02"""
import sys, sqlite3
sys.path.insert(0, 'D:/hanako/investment-system/src')

from datetime import datetime, timedelta
from collections import defaultdict

db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row
scan_date = '2024-01-02'

# Replicate PP V2's scan_date logic
CFG = {'min_amount': 50_000_000, 'max_distribution_days': 6, 'min_gain_pct': 3.0}

# 1. Get stocks (same as PP V2)
stocks = db.execute("""
    SELECT DISTINCT k.stock_code, b.name
    FROM daily_kline k JOIN stock_basic b ON k.stock_code=b.stock_code
    WHERE b.listing_status='normally_listed' AND b.name NOT LIKE '%ST%'
    AND k.date >= date(?, '-20 days')
    GROUP BY k.stock_code HAVING AVG(k.amount) >= ?
""", (scan_date, CFG['min_amount'])).fetchall()
print(f'PP V2 candidate stocks: {len(stocks)}')

# 2. Load K-lines (same as PP V2: 150 days)
start = (datetime.strptime(scan_date, '%Y-%m-%d') - timedelta(days=150)).strftime('%Y-%m-%d')
codes = [r['stock_code'] for r in stocks]
kline_cache = defaultdict(list)
for r in db.execute("SELECT stock_code, date, open, high, low, close, volume, amount FROM daily_kline WHERE date >= ? AND date <= ? ORDER BY stock_code, date", (start, scan_date)).fetchall():
    if r['stock_code'] in set(codes):
        kline_cache[r['stock_code']].append(dict(r))
print(f'K-lines: {len(kline_cache)} stocks, {sum(len(v) for v in kline_cache.values())} rows')

# 3. Simulate _target_date and test get_bi_list
import scanners.chanlun_structure as cls
cls._target_date = scan_date

from scanners.chanlun_structure import get_bi_list

# Pick 3 random stocks and check their bi data
import random
random.seed(42)
sample = random.sample([s[0] for s in stocks], min(3, len(stocks)))
for code in sample:
    klines = kline_cache.get(code, [])
    bi = get_bi_list(code)
    if bi:
        tops = [b for b in bi if b['direction'] == '向下']
        kline_start = klines[0]['date'] if klines else 'NONE'
        kline_end = klines[-1]['date'] if klines else 'NONE'
        tops_in_range = [t for t in tops if t['sdt'][:10] >= kline_start] if klines else []
        print(f'{code}: {len(klines)} K-lines ({kline_start}~{kline_end}), bi tops={len(tops)}, in_range={len(tops_in_range)}')
        if tops_in_range:
            print(f'  first in-range top: {tops_in_range[0]["sdt"][:10]}')
        else:
            print(f'  earliest top: {tops[0]["sdt"][:10] if tops else "NONE"} — OUTSIDE kline range!')
    else:
        print(f'{code}: NO bi data')

# 4. Count how many stocks have bi tops within their kline range
count_with_tops = 0
count_with_bi = 0
for code in codes:
    klines = kline_cache.get(code, [])
    if len(klines) < 60:
        continue
    bi = get_bi_list(code)
    if not bi:
        continue
    count_with_bi += 1
    tops = [b for b in bi if b['direction'] == '向下']
    kline_start = klines[0]['date']
    tops_in_range = [t for t in tops if t['sdt'][:10] >= kline_start]
    if tops_in_range:
        count_with_tops += 1

print(f'\nSummary: {count_with_bi}/{len(codes)} stocks have bi data, {count_with_tops} have tops within kline range')

db.close()
