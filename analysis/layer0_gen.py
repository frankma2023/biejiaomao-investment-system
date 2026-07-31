"""第0层过滤：全A股基础池 → 输出MD文件"""
import sqlite3
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row

rows = db.execute("""
    SELECT stock_code, name, ipo_date, exchange FROM stock_basic
    WHERE market='a' AND listing_status='normally_listed'
      AND ipo_date IS NOT NULL AND ipo_date <= '2023-07-27'
""").fetchall()
base = {r['stock_code']: r for r in rows}

mc_rows = db.execute("""
    SELECT a.stock_code, a.value FROM fundamental_indicator a
    INNER JOIN (
        SELECT stock_code, MAX(date) AS md FROM fundamental_indicator
        WHERE metric_code='mc' GROUP BY stock_code
    ) b ON a.stock_code=b.stock_code AND a.date=b.md
    WHERE a.metric_code='mc' AND a.value > 30e8
""").fetchall()
mcs = {r['stock_code']: r['value'] for r in mc_rows if r['stock_code'] in base}

codes = sorted(mcs.keys())
amts = {}
for i in range(0, len(codes), 500):
    chunk = codes[i:i+500]
    ph = ','.join(['?']*len(chunk))
    rs = db.execute(f"""
        SELECT stock_code, AVG(amount) as amt FROM daily_kline
        WHERE stock_code IN ({ph})
          AND date >= '2026-05-28' AND date <= '2026-07-27'
        GROUP BY stock_code
    """, chunk).fetchall()
    for r in rs:
        if r['amt'] and r['amt'] > 1e7:
            amts[r['stock_code']] = r['amt']

final = sorted(amts.keys())
sz = [c for c in final if c.startswith(('3','0'))]
sh = [c for c in final if c.startswith('6')]
bj = [c for c in final if c not in set(sz+sh)]
db.close()

with open('D:/hanako/investment-system/analysis/layer0_result.md', 'w', encoding='utf-8') as f:
    f.write('# 四位大师选股漏斗 · 第0层基础池\n\n')
    f.write('**生成时间**: 2026-07-27\n\n')
    f.write('## 过滤条件\n\n')
    f.write('1. A股、正常上市（排除ST/*ST/退市）\n')
    f.write('2. 上市满3年（2023-07-27之前上市）\n')
    f.write('3. 总市值 > 30亿\n')
    f.write('4. 近60日均成交额 > 1000万\n\n')
    f.write('## 各步通过数\n\n')
    f.write('| 步骤 | 条件 | 通过数 |\n')
    f.write('|------|------|-------|\n')
    f.write(f'| 0 | 正常上市+满3年 | {len(base)} |\n')
    f.write(f'| 1 | 市值>30亿 | {len(mcs)} |\n')
    f.write(f'| 2 | 日均成交额>1000万 | **{len(final)}** |\n\n')
    f.write('## 交易所分布\n\n')
    f.write('| 交易所 | 数量 |\n')
    f.write('|--------|------|\n')
    f.write(f'| 深交所 | {len(sz)} |\n')
    f.write(f'| 上交所 | {len(sh)} |\n')
    f.write(f'| 北交所 | {len(bj)} |\n\n')
    f.write('## 股票列表\n\n')
    f.write('| 代码 | 名称 | 交易所 | 上市日期 | 市值(亿) | 近60日均额(万) |\n')
    f.write('|------|------|--------|----------|---------|---------------|\n')
    for code in final:
        info = base[code]
        mkt = '沪' if code.startswith('6') else ('深' if code.startswith(('3','0')) else '北')
        mc_v = mcs.get(code, 0)
        amt_v = amts.get(code, 0)
        f.write(f'| {code} | {info["name"]} | {mkt} | {info["ipo_date"]} | {mc_v/1e8:.0f} | {amt_v/1e4:.0f} |\n')

print(f'完成: 共 {len(final)} 只股票')
print(f'深交所: {len(sz)}, 上交所: {len(sh)}, 北交所: {len(bj)}')
print(f'结果已保存到 analysis/layer0_result.md')
