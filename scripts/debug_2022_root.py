import sqlite3

db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row

scan_date = '2022-01-04'

# 1. 查 chanlun_bi_json 数据
r = db.execute("SELECT COUNT(*) FROM chanlun_bi_json WHERE scan_date=?", (scan_date,)).fetchone()[0]
print(f'chanlun_bi_json for {scan_date}: {r} rows')

# 2. 抽取5只股票的 bi_json 看结构
rows = db.execute("SELECT stock_code, bi_json FROM chanlun_bi_json WHERE scan_date=? LIMIT 5", (scan_date,)).fetchall()
print('Sample bi_json:')
for row in rows:
    bi_len = len(row['bi_json']) if row['bi_json'] else 0
    print(f'  {row["stock_code"]}: {bi_len} chars')

# 3. 查 MW scan_stock 的缓存键格式
# 从 daily_kline 和 chanlun_bi_json 各取一只股票对比 code 格式
k = db.execute("SELECT DISTINCT stock_code FROM daily_kline WHERE date=? LIMIT 3", (scan_date,)).fetchall()
c = db.execute("SELECT DISTINCT stock_code FROM chanlun_bi_json WHERE scan_date=? LIMIT 3", (scan_date,)).fetchall()
print('daily_kline codes:', [x[0] for x in k])
print('chanlun_bi_json codes:', [x[0] for x in c])

# 4. 查 MW engine 的缓存机制
# 模拟 set_all_caches 的加载
import sys, orjson
sys.path.insert(0, 'D:/hanako/investment-system/src')

# 模拟 backfill 的缓存加载方式
cache = {}
for row in db.execute("SELECT stock_code, bi_json FROM chanlun_bi_json WHERE scan_date=?", (scan_date,)).fetchall():
    try:
        cache[(row['stock_code'], scan_date)] = orjson.loads(row['bi_json'])
    except:
        pass

# 取第一只股票，检查 MW scan_stock 会用到的 cache_key
if k and c:
    sample_code = k[0][0]
    cache_key = (sample_code, scan_date)
    found = cache_key in cache
    print(f'\nsample_code={sample_code}, cache_key in cache: {found}')
    if not found:
        # 检查是否有相近的 key
        for key in list(cache.keys())[:3]:
            print(f'  cache key example: {key}')

db.close()
