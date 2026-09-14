# -*- coding: utf-8 -*-
"""补掉 2 行孤立百仞数形态（位于小数形态区间内的单日异常）"""
import sqlite3, sys, os, csv
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
c = sqlite3.connect('data/lixinger.db', timeout=300)
c.row_factory = sqlite3.Row

TARGETS = [('000502', '2020-04-10'), ('002711', '2021-07-14')]
with open('analysis/_change_pct_backup.csv', 'a', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    for code, d in TARGETS:
        r = c.execute("SELECT change_pct FROM daily_kline WHERE stock_code=? AND date=?",
                      (code, d)).fetchone()
        if r and r['change_pct'] is not None:
            w.writerow([code, d, r['change_pct']])
            n = c.execute("UPDATE daily_kline SET change_pct = change_pct/100.0 "
                          "WHERE stock_code=? AND date=?", (code, d)).rowcount
            print(f'  {code} {d}: {r["change_pct"]} -> {r["change_pct"]/100}  ({n} 行)')
c.commit()

# 全量复核
FLAT = 0.005
codes = [r['stock_code'] for r in csv.DictReader(
    open('analysis/change_pct_unit_manifest.csv', encoding='utf-8'))]
left = []
for code in codes:
    rows = c.execute("""WITH t AS (SELECT date, close, change_pct,
                              LAG(close) OVER (ORDER BY date) pc
                              FROM daily_kline WHERE stock_code=?)
                        SELECT date, close/pc-1 AS tr, change_pct FROM t
                        WHERE change_pct IS NOT NULL AND pc>0""", (code,)).fetchall()
    for r in rows:
        t = r['tr']
        if abs(t) <= FLAT:
            continue
        tol = max(0.0006, abs(t) * 0.03)
        if (abs(r['change_pct'] / 100 - t) <= tol) and (abs(r['change_pct'] - t) > tol):
            left.append((code, r['date'], r['change_pct'], round(t * 100, 4)))
print(f'\n复核：95 只中剩余百仞数形态行 {len(left)}')
for x in left[:10]:
    print('  ', x)

# 全表体检：还有多少行 |change_pct| 超出合理小数范围
n = c.execute("SELECT COUNT(*) n FROM daily_kline WHERE abs(change_pct) > 0.31").fetchone()['n']
n21 = c.execute("SELECT COUNT(*) n FROM daily_kline WHERE abs(change_pct) > 0.31 "
                "AND date >= '2016-01-01'").fetchone()['n']
print(f'\n全表 |change_pct| > 0.31 的行：{n:,}（其中 2016 起 {n21:,}）')
print('（> 0.31 即超过创业板 30% 涨跌停上限，只在北交所/新股首日属正常）')
c.close()
