# -*- coding: utf-8 -*-
"""change_pct 单位体检：库里到底存小数(0.0123) 还是百分数(1.23)？脏值是否真脏？"""
import sqlite3, sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
c = sqlite3.connect('data/lixinger.db')
c.row_factory = sqlite3.Row

print('=== 1. 总体单位分布（用 close/prev_close 反算真值做对照）===')
rows = c.execute("""
    SELECT d.stock_code, d.date, d.close, d.change_pct,
           (SELECT p.close FROM daily_kline p WHERE p.stock_code=d.stock_code
              AND p.date < d.date ORDER BY p.date DESC LIMIT 1) prev_close
    FROM daily_kline d
    WHERE d.date >= '2019-01-01' AND d.close > 0
      AND d.stock_code IN (SELECT stock_code FROM daily_kline WHERE date='2026-09-10'
                           ORDER BY stock_code LIMIT 300)
""").fetchall()
as_dec = as_pct = neither = n = 0
bad_examples = []
for r in rows:
    if not r['prev_close'] or r['change_pct'] is None:
        continue
    n += 1
    true_pct = (r['close'] / r['prev_close'] - 1) * 100     # 真值，百分数
    cp = r['change_pct']
    if abs(cp - true_pct) <= max(0.05, abs(true_pct) * 0.02):
        as_pct += 1
    elif abs(cp * 100 - true_pct) <= max(0.05, abs(true_pct) * 0.02):
        as_dec += 1
    else:
        neither += 1
        if len(bad_examples) < 12:
            bad_examples.append((r['stock_code'], r['date'], r['prev_close'], r['close'],
                                 round(true_pct, 4), cp))
print(f'  样本 {n:,} 行：像百分数 {as_pct:,} ({as_pct/n*100:.2f}%)｜像小数 {as_dec:,} ({as_dec/n*100:.2f}%)｜都不像 {neither:,} ({neither/n*100:.2f}%)')
if bad_examples:
    print('  都不像的样本（代码/日期/前收/收盘/真涨跌%/库内change_pct）：')
    for b in bad_examples:
        print('   ', b)

print()
print('=== 2. 反推链风险：|change_pct|>0.5 且 date>=2016 的样本，单位归属 ===')
rows = c.execute("""
    SELECT d.stock_code, d.date, d.close, d.change_pct,
           (SELECT p.close FROM daily_kline p WHERE p.stock_code=d.stock_code
              AND p.date < d.date ORDER BY p.date DESC LIMIT 1) prev_close
    FROM daily_kline d
    WHERE d.date >= '2016-01-01' AND abs(d.change_pct) > 0.5
    ORDER BY abs(d.change_pct) DESC LIMIT 3000
""").fetchall()
dec = pct = nei = 0
for r in rows:
    if not r['prev_close'] or r['change_pct'] is None:
        continue
    true_pct = (r['close'] / r['prev_close'] - 1) * 100
    cp = r['change_pct']
    if abs(cp - true_pct) <= max(0.05, abs(true_pct) * 0.02):
        pct += 1
    elif abs(cp * 100 - true_pct) <= max(0.05, abs(true_pct) * 0.02):
        dec += 1
    else:
        nei += 1
print(f'  百分数形态 {pct:,}｜小数形态 {dec:,}｜都对不上 {nei:,}')
c.close()
