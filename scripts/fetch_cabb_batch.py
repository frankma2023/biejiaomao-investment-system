"""批量拉取全部A股近10年货币资金(cabb) — 多线程版"""
import sqlite3, json, os, sys, requests, time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_PATH = 'D:\\hanako\\investment-system\\data\\lixinger.db'
ENV_PATH = 'D:\\hanako\\.env'
MAX_WORKERS = 8  # 并发线程数

token = None
for line in open(ENV_PATH, encoding='utf-8'):
    if 'LIXINGER_TOKEN' in line:
        token = line.strip().split('=')[1]
        break
if not token:
    print('❌ 未找到 LIXINGER_TOKEN')
    sys.exit(1)

def fetch_one(code):
    """拉取一只股票的全部cabb历史"""
    url = 'https://open.lixinger.com/api/cn/company/fs/non_financial'
    payload = {
        'token': token,
        'stockCodes': [code],
        'startDate': '2016-01-01',
        'endDate': datetime.now().strftime('%Y-%m-%d'),
        'metricsList': ['q.bs.cabb.t']
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code != 200:
            return code, []
        data = resp.json()
        if data.get('code') != 1:
            return code, []
        records = data.get('data', [])
        result = []
        for rec in records:
            d = rec.get('date', '')
            cabb = rec.get('q', {}).get('bs', {}).get('cabb', {}).get('t')
            if d and cabb is not None:
                result.append({'date': d[:10], 'cabb': cabb})
        return code, result
    except Exception as e:
        return code, []

def update_db(results):
    """批量写入数据库"""
    db = sqlite3.connect(DB_PATH)
    total = 0
    for code, records in results:
        if not records:
            continue
        for r in records:
            cur = db.execute(
                'UPDATE stock_financials_annual SET cabb=? WHERE stock_code=? AND report_date=?',
                (r['cabb'], code, r['date'])
            )
            if cur.rowcount > 0:
                total += 1
    db.commit()
    db.close()
    return total

if __name__ == '__main__':
    t0 = time.time()
    
    # 取所有需要拉取的股票
    db = sqlite3.connect(DB_PATH)
    rows = db.execute(
        "SELECT DISTINCT a.stock_code FROM stock_financials_annual a "
        "WHERE a.report_date >= '2016-12-31' "
        "AND NOT EXISTS (SELECT 1 FROM stock_financials_annual b "
        "WHERE b.stock_code=a.stock_code AND b.cabb IS NOT NULL LIMIT 1)"
    ).fetchall()
    codes = [r[0] for r in rows]
    db.close()
    
    total = len(codes)
    print(f'📊 需拉取 {total} 只股票')
    
    all_results = []
    done = 0
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_one, code): code for code in codes}
        for f in as_completed(futures):
            code, records = f.result()
            done += 1
            all_results.append((code, records))
            if done % 50 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                print(f'  [{done}/{total}] {code}: {len(records)}条 | {rate:.1f}只/秒 预计剩余{eta/60:.0f}分')
    
    # 写入数据库
    print(f'\n💾 写入数据库...')
    updated = update_db(all_results)
    elapsed = time.time() - t0
    
    print(f'\n✅ 完成！')
    print(f'  处理: {total} 只股票')
    print(f'  更新: {updated} 条记录')
    print(f'  耗时: {elapsed/60:.1f} 分钟')
