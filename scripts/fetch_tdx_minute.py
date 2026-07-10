#!/usr/bin/env python
"""
从通达信本地 .lc1 文件读取全A股1分钟K线，聚合为15/60分钟，写入数据库
替代 download_minute_kline.py（Baostock API）

用法:
  python fetch_tdx_minute.py          # 增量：只处理最近10天
  python fetch_tdx_minute.py --full   # 全量：处理全部历史数据
"""
import struct, os, sqlite3, sys, time
from datetime import datetime, timedelta
from collections import defaultdict

TDX_DIR = r'D:\new_tdx\vipdoc'
DB = r'D:\hanako\investment-system\data\lixinger.db'

FULL_MODE = '--full' in sys.argv

def parse_lc1(filepath):
    """读通达信1分钟K线，返回 [(datetime, open, high, low, close, volume, amount)]"""
    if not os.path.exists(filepath):
        return []
    data = open(filepath, 'rb').read()
    rows = []
    for i in range(0, len(data), 32):
        if i + 32 > len(data): break
        chunk = data[i:i+32]
        
        # 旧格式: [0:2]=date, [2:4]=minute
        d = struct.unpack('<H', chunk[0:2])[0]
        minute = struct.unpack('<H', chunk[2:4])[0]
        
        yr = d // 2048 + 2000
        md = d % 2048
        month = md // 100
        day = md % 100
        if not (1 <= month <= 12 and 1 <= day <= 31): continue
        
        o, h, l, c, vol = struct.unpack('<fffff', chunk[4:24])
        amt = struct.unpack('<f', chunk[20:24])[0]
        
        hh, mm = divmod(minute, 60)
        try:
            dt = datetime(yr, month, day, hh, mm)
        except:
            continue
        rows.append((dt, o, h, l, c, vol, amt))
    return rows

def aggregate(rows, minutes):
    """1分钟K线聚合为指定周期"""
    if not rows: return []
    rows.sort()
    buckets = defaultdict(list)
    for dt, o, h, l, c, vol, amt in rows:
        bucket_min = (dt.hour * 60 + dt.minute) // minutes * minutes
        buckets[(dt.date(), bucket_min)].append((o, h, l, c, vol, amt))
    
    result = []
    for (d, m), bars in sorted(buckets.items()):
        hh, mm = divmod(m, 60)
        result.append((
            d.strftime('%Y-%m-%d'), f'{hh:02d}:{mm:02d}',
            bars[0][0],                       # open
            max(b[1] for b in bars),          # high
            min(b[2] for b in bars),          # low
            bars[-1][3],                      # close
            sum(b[4] for b in bars if b[4]),  # volume
            sum(b[5] for b in bars if b[5]),  # amount
        ))
    return result

# ── 扫描所有 .lc1 文件 ──
files = []
for market in ['sh', 'sz']:
    d = os.path.join(TDX_DIR, market, 'minline')
    if os.path.exists(d):
        for f in os.listdir(d):
            if f.endswith('.lc1') and len(f) >= 8:
                code = f[2:8]  # sh600519.lc1 -> 600519
                files.append((code, market, os.path.join(d, f)))

print(f'找到 {len(files)} 个 .lc1 文件')

db = sqlite3.connect(DB, timeout=30)
db.execute("PRAGMA journal_mode=WAL")
cur = db.cursor()

cur.execute("""CREATE TABLE IF NOT EXISTS stock_kline_15min (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT, date TEXT, time TEXT,
    open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, date, time))""")
cur.execute("""CREATE TABLE IF NOT EXISTS stock_kline_60min (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT, date TEXT, time TEXT,
    open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, date, time))""")

t0 = time.time()
total_15 = total_60 = 0
updated = 0

for i, (code, market, path) in enumerate(files):
    if i % 500 == 0:
        print(f'  {i}/{len(files)}... ({time.time()-t0:.0f}s)')
    
    rows_1m = parse_lc1(path)
    if not rows_1m: continue
    
    # 增量模式：只处理最近10天的数据；全量模式：处理全部
    if not FULL_MODE:
        cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=10)
        rows_1m = [r for r in rows_1m if r[0] >= cutoff]
        if not rows_1m: continue
    
    rows_15 = aggregate(rows_1m, 15)
    rows_60 = aggregate(rows_1m, 60)
    
    for r in rows_15:
        cur.execute("INSERT OR REPLACE INTO stock_kline_15min (stock_code,date,time,open,high,low,close,volume,amount) VALUES (?,?,?,?,?,?,?,?,?)",
                    (code, *r))
    for r in rows_60:
        cur.execute("INSERT OR REPLACE INTO stock_kline_60min (stock_code,date,time,open,high,low,close,volume,amount) VALUES (?,?,?,?,?,?,?,?,?)",
                    (code, *r))
    
    total_15 += len(rows_15)
    total_60 += len(rows_60)
    updated += 1
    
    if i % 200 == 0:
        db.commit()

db.commit()
db.close()
print(f'\n完成: {updated} 只股票, 15min={total_15}条, 60min={total_60}条, {time.time()-t0:.0f}s')
