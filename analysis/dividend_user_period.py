# -*- coding: utf-8 -*-
"""还原用户实际持有区间（2024 初买、2026-07 卖）的真实收益，并解释与长期年化的差异。"""
import sqlite3

CON = sqlite3.connect('file:data/lixinger.db?mode=ro', uri=True)


def ser(table, code, start, end):
    return CON.execute(
        f"SELECT date, close FROM {table} WHERE stock_code=? AND close>0 "
        f"AND date>=? AND date<=? ORDER BY date", (code, start, end)).fetchall()


def ret(s):
    return (s[-1][1] / s[0][1] - 1) * 100 if s else None


def ann(s):
    if not s:
        return None
    y = (int(s[-1][0][:4]) + int(s[-1][0][5:7]) / 12) - \
        (int(s[0][0][:4]) + int(s[0][0][5:7]) / 12)
    return ((s[-1][1] / s[0][1]) ** (1 / y) - 1) * 100, y


B, S = '2024-01-02', '2026-07-31'
print('=' * 72)
print(f'  实际持有区间：{B} ~ {S}')
print('=' * 72)
print()
print(f'  {"标的":<16}{"起点":>10}{"终点":>10}{"累计":>9}{"年化":>9}')
for code, name, table in (
        ('000922', '中证红利 价格', 'index_daily_kline'),
        ('000922', '中证红利 全收益', 'index_full_return_daily'),
        ('000300', '沪深300 全收益', 'index_full_return_daily'),
        ('000985', '中证全指 全收益', 'index_full_return_daily')):
    s = ser(table, code, B, S)
    if not s:
        print(f'  {name:<16}{"无数据":>10}')
        continue
    a = ann(s)
    print(f'  {name:<16}{s[0][1]:>10.1f}{s[-1][1]:>10.1f}'
          f'{ret(s):>8.1f}%{a[0]:>8.2f}%')

# 中证红利全收益 分年度
print()
print('  中证红利全收益 逐年表现（2024 起）')
s = ser('index_full_return_daily', '000922', '2023-12-31', '2026-12-31')
years = {}
for d, c in s:
    years.setdefault(d[:4], []).append((d, c))
prev = None
for y in sorted(years):
    v = years[y]
    if len(v) < 5:
        continue
    base = prev if prev else v[0][1]
    r = (v[-1][1] / base - 1) * 100
    print(f'    {y}: {r:>+7.2f}%   （{v[0][0]} ~ {v[-1][0]}）')
    prev = v[-1][1]

# 长期年化是靠哪些年撑起来的
print()
print('  中证红利全收益：长期年化的来源拆解')
full = ser('index_full_return_daily', '000922', '2012-12-31', '2026-12-31')
fy = {}
for d, c in full:
    fy.setdefault(d[:4], []).append(c)
rows = []
prev = None
for y in sorted(fy):
    v = fy[y]
    if prev:
        rows.append((y, (v[-1] / prev - 1) * 100))
    prev = v[-1]
rows.sort(key=lambda x: -x[1])
print(f'    最好的 5 年: ' + '  '.join(f'{y}:{r:+.1f}%' for y, r in rows[:5]))
print(f'    最差的 5 年: ' + '  '.join(f'{y}:{r:+.1f}%' for y, r in rows[-5:]))
# 剔除最好 3 年后的年化
keep = [r for y, r in rows if (y, r) not in rows[:3]]
prod = 1.0
for r in keep:
    prod *= (1 + r / 100)
n = len(keep)
print(f'    剔除最好的 3 年后，剩余 {n} 年年化 ≈ '
      f'{(prod ** (1/n) - 1) * 100:.2f}%')
CON.close()
