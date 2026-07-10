#!/usr/bin/env python
"""
股本变动数据回填
从理杏仁拉取全市场股票的股本变动历史（2016-2026）
"""
import sys, os, time, sqlite3, argparse, requests

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')
ENV_PATH = os.path.join(os.path.dirname(PROJECT), '.env')
BASE_URL = 'https://open.lixinger.com/api/cn'

def get_token():
    with open(ENV_PATH) as f:
        for line in f:
            if line.startswith('LIXINGER_TOKEN='):
                return line.split('=', 1)[1].strip()
    raise RuntimeError('LIXINGER_TOKEN not found')

def lx_post(path, payload):
    payload['token'] = get_token()
    r = requests.post(BASE_URL + path, json=payload, timeout=30)
    d = r.json()
    if d.get('code') != 1:
        raise RuntimeError(f"API error: {d.get('message','')}")
    return d.get('data', [])

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def create_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_equity_change (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            change_date TEXT NOT NULL,
            declaration_date TEXT,
            change_reason TEXT,
            capitalization REAL,
            outstanding_shares_a REAL,
            limited_shares_a REAL,
            cap_change_ratio REAL,
            outstanding_a_change_ratio REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sec_code ON stock_equity_change(stock_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sec_date ON stock_equity_change(change_date)")
    conn.commit()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fast', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()

    conn = get_db()
    create_table(conn)
    
    rows = conn.execute("""
        SELECT stock_code, name FROM stock_basic 
        WHERE listing_status='normally_listed' AND name NOT LIKE '%ST%'
        ORDER BY stock_code
    """).fetchall()
    stocks = [(r['stock_code'], r['name']) for r in rows]
    
    if args.resume:
        done = set(r[0] for r in conn.execute("SELECT DISTINCT stock_code FROM stock_equity_change").fetchall())
        stocks = [(c,n) for c,n in stocks if c not in done]
        print(f"续传: {len(stocks)} 只待处理")
    elif args.fast:
        stocks = stocks[:100]
        print(f"快速: {len(stocks)} 只")
    else:
        print(f"全量: {len(stocks)} 只")
    
    t0 = time.time()
    success = empty = errors = 0
    
    for i, (code, name) in enumerate(stocks):
        try:
            data = lx_post('/company/equity-change', {
                'stockCode': code,
                'startDate': '2016-01-01',
                'endDate': '2026-12-31',
            })
            if data:
                for c in data:
                    conn.execute("""
                        INSERT OR REPLACE INTO stock_equity_change
                        (stock_code, change_date, declaration_date, change_reason,
                         capitalization, outstanding_shares_a, limited_shares_a,
                         cap_change_ratio, outstanding_a_change_ratio)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        code,
                        str(c.get('date',''))[:10],
                        str(c.get('declarationDate',''))[:10] if c.get('declarationDate') else None,
                        c.get('changeReason',''),
                        c.get('capitalization'),
                        c.get('outstandingSharesA'),
                        c.get('limitedSharesA'),
                        c.get('capitalizationChangeRatio'),
                        c.get('outstandingSharesAChangeRatio'),
                    ))
                conn.commit()
                success += 1
            else:
                empty += 1
            
            if (i+1) % 200 == 0:
                elapsed = time.time() - t0
                rate = (i+1)/elapsed
                remain = (len(stocks)-i-1)/rate if rate>0 else 0
                print(f"  进度: {i+1}/{len(stocks)} ({success}有,{empty}空) {rate:.1f}只/s 剩余{remain:.0f}s")
            
            time.sleep(0.08)  # API限速
            
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  [{code}] {e}")
            time.sleep(1)
    
    elapsed = time.time() - t0
    print(f"\n完成: {success}有 {empty}空 {errors}错  耗时{elapsed/60:.1f}min")
    
    # 样例
    row = conn.execute("SELECT stock_code, COUNT(*) cnt FROM stock_equity_change GROUP BY stock_code ORDER BY cnt DESC LIMIT 5").fetchall()
    print("变动最多的5只:")
    for r in row:
        print(f"  {r['stock_code']}: {r['cnt']}次")
    
    total = conn.execute("SELECT COUNT(*) FROM stock_equity_change").fetchone()[0]
    stocks_with = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM stock_equity_change").fetchone()[0]
    print(f"总计: {total}条记录, {stocks_with}只股票")
    
    conn.close()

if __name__ == '__main__':
    main()
