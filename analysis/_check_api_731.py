"""验证现在能否拉到 07-30/07-31 两融数据"""
import sys, os
sys.path.insert(0, 'scripts')
os.chdir('D:/hanako/investment-system')
from common import api_post

# 测试 07-31 是否有数据
for dt in ['2026-07-30', '2026-07-31']:
    r = api_post('/company/margin-trading-and-securities-lending', {
        'stockCode': '300750',
        'startDate': dt,
        'endDate': dt,
    })
    if r:
        item = r[0]
        d = item.get('date', '')[:10]
        fb = item.get('financingBalance', 0)
        print(f"{dt}: ✅ 有数据 date={d} fb={fb/1e8:.1f}亿")
    else:
        print(f"{dt}: ❌ 无数据")

# 对比已有数据日期
import sqlite3
db = sqlite3.connect('data/lixinger.db')
r = db.execute("SELECT MAX(date) FROM daily_margin_history").fetchone()
print(f"\ndaily_margin_history 最新: {r[0]}")
db.close()
