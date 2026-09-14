# -*- coding: utf-8 -*-
"""生成 change_pct 单位异常清单（manifest）

判定：用相邻收盘价反算真值 true=close/pc-1（小数量纲）
  小数形态  |cp - true| <= tol
  百分数形态|cp/100 - true| <= tol
  其他     都不匹配（除权日：change_pct 是除权调整后收益率，与原始环比本就不等，属正常）
为避免平盘噪声，仅在 |true| > 0.005（0.5%）时参与判定。

输出：analysis/change_pct_unit_manifest.csv
  stock_code, n_rows, n_decimal, n_percent, n_other, pct_ratio,
  first_pct_date, last_pct_date, is_segmented(是否整段形态)
"""
import sqlite3, sys, os, csv
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
c = sqlite3.connect('data/lixinger.db', timeout=300)
c.row_factory = sqlite3.Row

SQL = """
WITH t AS (
  SELECT stock_code, date, close, change_pct,
         LAG(close) OVER (PARTITION BY stock_code ORDER BY date) AS pc
  FROM daily_kline
)
SELECT stock_code, date, close, pc, change_pct
FROM t WHERE change_pct IS NOT NULL AND pc IS NOT NULL AND pc > 0
"""

agg = defaultdict(lambda: {'n': 0, 'dec': 0, 'pct': 0, 'oth': 0,
                           'd0': None, 'd1': None})
n_skip = 0
for r in c.execute(SQL):
    true = r['close'] / r['pc'] - 1
    cp = r['change_pct']
    a = agg[r['stock_code']]
    a['n'] += 1
    if abs(true) <= 0.005:
        n_skip += 1
        continue                       # 平盘附近无法区分，不参与判定
    tol = max(0.0006, abs(true) * 0.03)
    if abs(cp - true) <= tol:
        a['dec'] += 1
    elif abs(cp / 100 - true) <= tol:
        a['pct'] += 1
        if a['d0'] is None:
            a['d0'] = r['date']
        a['d1'] = r['date']
    else:
        a['oth'] += 1

rows = []
for code, a in agg.items():
    judged = a['dec'] + a['pct']
    if a['pct'] == 0:
        continue
    ratio = a['pct'] / judged if judged else 0
    rows.append({
        'stock_code': code, 'n_rows': a['n'], 'n_decimal': a['dec'],
        'n_percent': a['pct'], 'n_other': a['oth'],
        'pct_ratio': round(ratio, 4),
        'first_pct_date': a['d0'], 'last_pct_date': a['d1'],
        'is_segmented': 1 if ratio > 0.5 else 0,
    })
rows.sort(key=lambda x: -x['n_percent'])

os.makedirs('analysis', exist_ok=True)
out = 'analysis/change_pct_unit_manifest.csv'
with open(out, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

tot_pct = sum(r['n_percent'] for r in rows)
tot_dec = sum(r['n_decimal'] for r in rows)
seg = [r for r in rows if r['is_segmented']]
print(f'平盘跳过 {n_skip:,} 行（|真实涨跌| <= 0.5%，无法区分单位）')
print(f'涉及股票 {len(rows):,} 只（有 percent 形态行）')
print(f'  percent 行合计 {tot_pct:,}')
print(f'  其中「整段形态」股票 {len(seg):,} 只，覆盖 {sum(r["n_percent"] for r in seg):,} 行 '
      f'({sum(r["n_percent"] for r in seg)/tot_pct*100:.1f}%)')
print(f'  零星形态（<50%）股票 {len(rows)-len(seg):,} 只，覆盖 {tot_pct-sum(r["n_percent"] for r in seg):,} 行')
print(f'\n前 20 只：')
for r in rows[:20]:
    print(f'  {r["stock_code"]}  percent {r["n_percent"]:>6,} / 判定 {r["n_decimal"]+r["n_percent"]:>6,}'
          f' ({r["pct_ratio"]*100:5.1f}%)  {r["first_pct_date"]} ~ {r["last_pct_date"]}')
print(f'\n已写出：{out}')
c.close()
