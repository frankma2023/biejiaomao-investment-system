# -*- coding: utf-8 -*-
"""一次性 vs 标准定投：起点敏感性检验——验证"一次性最佳"的边界"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\hanako\investment-system\src')
from analysis.financial import get_db

IDX = 'H30269'
END = '2026-08-31'
CAP = 1000000.0
db = get_db()
fr = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code=? AND date<=? ORDER BY date", (IDX, END)).fetchall()
db.close()
fr_map = {r['date']: r['close'] for r in fr}
dates = [r['date'] for r in fr]

def month_first_days(from_date):
    out = []
    for r in fr:
        if r['date'] < from_date:
            continue
        m = r['date'][:7]
        if not out or out[-1][0] != m:
            out.append((m, r['date']))
    return [d for _, d in out]

def run_from(start):
    mdays = month_first_days(start)
    if len(mdays) < 24:
        return None
    n = len(mdays)
    q = CAP / n
    end_px = fr_map[END]
    # 一次性
    p0 = fr_map[mdays[0]]
    lump = CAP / p0 * end_px
    # 标准定投
    sh = 0.0
    for d in mdays:
        sh += q / fr_map[d]
    dca = sh * end_px
    yrs = n / 12
    cagr = lambda v: ((v / CAP) ** (1 / yrs) - 1) * 100
    return mdays[0], round(lump / 1e4, 1), cagr(lump), round(dca / 1e4, 1), cagr(dca)

print(f"{'起点':<12}{'一次性(万)':>12}{'一次性年化':>12}{'定投(万)':>12}{'定投年化':>12}{'胜者':>6}")
for y in ['2017', '2018', '2019', '2020', '2021', '2022', '2023', '2024']:
    r = run_from(y + '-01-01')
    if r:
        winner = '一次性' if r[3] < r[1] else '定投(或接近)'
        print(f"{r[0]:<12}{r[1]:>12.1f}{r[2]:>11.2f}%{r[3]:>12.1f}{r[4]:>11.2f}%{winner:>8}")
