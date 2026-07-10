"""从理杏仁 API 拉取货币资金(cabb)数据，写入 stock_financials_annual 表"""
import sqlite3, json, os, sys, requests
from datetime import datetime

DB_PATH = 'D:\\hanako\\investment-system\\data\\lixinger.db'
ENV_PATH = 'D:\\hanako\\.env'

# 读取 token
token = None
for line in open(ENV_PATH, encoding='utf-8'):
    if 'LIXINGER_TOKEN' in line:
        token = line.strip().split('=')[1]
        break
if not token:
    print('❌ 未找到 LIXINGER_TOKEN')
    sys.exit(1)

def fetch_cabb(code, start='2016-01-01', end=None):
    """从理杏仁API拉取某股票的货币资金历史数据"""
    if not end:
        end = datetime.now().strftime('%Y-%m-%d')
    url = 'https://open.lixinger.com/api/cn/company/fs/non_financial'
    payload = {
        'token': token,
        'stockCodes': [code],
        'startDate': start,
        'endDate': end,
        'metricsList': ['q.bs.cabb.t']  # 货币资金
    }
    resp = requests.post(url, json=payload, timeout=30)
    if resp.status_code != 200:
        print(f'  API错误 {resp.status_code}: {resp.text[:200]}')
        return []
    data = resp.json()
    if data.get('code') != 1:
        print(f'  API返回错误: {data}')
        return []
    records = data.get('data', [])
    result = []
    for rec in records:
        d = rec.get('date', '')
        cabb = rec.get('q', {}).get('bs', {}).get('cabb', {}).get('t')
        if d and cabb is not None:
            result.append({'date': d[:10], 'cabb': cabb})
    return result

def update_db(code, records):
    """将cabb数据更新到stock_financials_annual表"""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    updated = 0
    for r in records:
        # report_date 格式是 YYYY-12-31 (年报) 或 YYYY-06-30 (中报) 等
        # cabb 数据来自API按实际财报日期
        report_date = r['date']
        cabb = r['cabb']
        # 按 stock_code + report_date 匹配更新
        cur = db.execute('UPDATE stock_financials_annual SET cabb=? WHERE stock_code=? AND report_date=?',
                         (cabb, code, report_date))
        if cur.rowcount > 0:
            updated += 1
    db.commit()
    db.close()
    return updated

if __name__ == '__main__':
    codes = sys.argv[1:] if len(sys.argv) > 1 else ['002648']
    for code in codes:
        print(f'🔄 拉取 {code}...')
        records = fetch_cabb(code)
        if not records:
            print(f'  ⚠️ 无数据')
            continue
        print(f'  获取到 {len(records)} 条记录')
        updated = update_db(code, records)
        print(f'  更新了 {updated} 条到数据库')
        # 显示前3条
        for r in records[:3]:
            print(f'    {r["date"]}: cabb={r["cabb"]:,.0f}')
