"""
从通达信本地文件读取ETF日K线，写入lixinger.db

通达信 .day 文件格式（每行32字节）：
  [0:4]  date    uint32 (YYYYMMDD)
  [4:8]  open    uint32 (价格×100)
  [8:12] high    uint32 (价格×100)
  [12:16] low     uint32 (价格×100)
  [16:20] close   uint32 (价格×100)
  [20:24] amount  float32 (成交额/元)
  [24:28] volume  uint32 (成交量/股)
  [28:32] reserved

与理杏仁 daily_kline 表格式对齐：
  - open/high/low/close: 通达信 int÷100 = 实际价格
  - adj_open/adj_high/adj_low/adj_close: ETF无复权，直接用原价
  - change_pct: (close - prev_close) / prev_close × 100
  - amount: 通达信 float32 (元)，理杏仁也是元，直接存
  - volume: 通达信 uint32 (股)，理杏仁也是股，直接存
  - turnover_rate: 通达信无此数据，设 NULL
  - complex_factor: ETF无复权因子，设 1.0
"""
import struct, os, sqlite3

TDX_DIR = r'D:\new_tdx\vipdoc'
DB = r'D:\hanako\investment-system\data\lixinger.db'

ETF_MAP = {}
try:
    import yaml
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'index_style.yaml')
    with open(cfg_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    etfs = cfg.get('categories', {}).get('etf', [])
    for item in etfs:
        if isinstance(item, dict) and 'code' in item:
            code = item['code']
            name = item.get('name', code)
            market = 'sh' if code.startswith(('5','6','9')) else 'sz'
            ETF_MAP[code] = {'market': market, 'name': name}
except Exception as e:
    print(f'读取 index_style.yaml 失败: {e}')

if not ETF_MAP:
    print('未找到 ETF 配置，退出')
    import sys; sys.exit(0)

def read_day_file(path):
    if not os.path.exists(path):
        print(f'  文件不存在: {path}')
        return []
    data = open(path, 'rb').read()
    rows = []
    for i in range(0, len(data), 32):
        if i + 32 > len(data): break
        d, o, h, l, c, amt, vol = struct.unpack('IIIIIfI', data[i:i+28])
        date_str = str(d)
        if len(date_str) != 8: continue
        date_fmt = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}'
        rows.append((date_fmt, o/100.0, h/100.0, l/100.0, c/100.0, vol, amt))
    return rows

# 先清旧数据
db = sqlite3.connect(DB, timeout=30)
db.execute("PRAGMA journal_mode=WAL")
cur = db.cursor()
for code in ETF_MAP:
    cur.execute("DELETE FROM index_daily_kline WHERE stock_code=?", (code,))
db.commit()

# 导入
for code, info in ETF_MAP.items():
    path = os.path.join(TDX_DIR, info['market'], 'lday', f"{info['market']}{code}.day")
    print(f'{code} {info["name"]}: {path}')
    
    rows = read_day_file(path)
    if not rows:
        print(f'  无数据')
        continue
    
    print(f'  {len(rows)} 条 ({rows[0][0]} ~ {rows[-1][0]})')
    
    prev_close = None
    inserted = 0
    for date, o, h, l, c, vol, amt in rows:
        # 计算涨跌幅
        chg = None
        if prev_close and prev_close > 0:
            chg = round((c - prev_close) / prev_close * 100, 2)
        prev_close = c
        
        cur.execute("""
            INSERT OR REPLACE INTO index_daily_kline
            (stock_code, date, kline_type, open, high, low, close, volume, amount, change)
            VALUES (?, ?, 'normal', ?, ?, ?, ?, ?, ?, ?)
        """, (code, date, o, h, l, c, vol, amt, chg))
        inserted += 1
    db.commit()
    print(f'  写入 {inserted} 行')

# stock_basic
for code, info in ETF_MAP.items():
    cur.execute("INSERT OR IGNORE INTO stock_basic (stock_code, name, market, listing_status) VALUES (?,?,?,?)",
              (code, info['name'], info['market'], 'normally_listed'))
db.commit()
db.close()
print('\n完成。打开 pattern-scan 输入 513120 扫描。')
