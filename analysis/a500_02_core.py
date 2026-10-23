# -*- coding: utf-8 -*-
"""A500 研究：核心分析（skill §三）。

关键处理：把「回溯期(2004-12-31~2024-09-22)」与「实盘期(2024-09-23~)」分开统计。
回溯期由指数公司用当前成分回算，带幸存者偏差，不可当作可投资历史。
"""
import math
import sqlite3

CON = sqlite3.connect('file:data/lixinger.db?mode=ro', uri=True)
LAUNCH = '2024-09-23'   # 中证A500 发布日


def series(code, start=None):
    q = ("SELECT date, close FROM index_daily_kline WHERE stock_code=? "
         "AND kline_type='normal' AND close>0")
    a = [code]
    if start:
        q += " AND date>=?"; a.append(start)
    q += " ORDER BY date"
    return CON.execute(q, a).fetchall()


def cagr(d0, v0, d1, v1):
    y = (int(d1[:4]) + int(d1[5:7]) / 12) - (int(d0[:4]) + int(d0[5:7]) / 12)
    return ((v1 / v0) ** (1 / y) - 1) * 100 if y > 0 and v0 > 0 else None


def mad(s):
    peak, out = -1e18, []
    for _, c in s:
        peak = max(peak, c)
        out.append(c / peak - 1)
    return out


def vol(s):
    r = [math.log(s[i][1] / s[i - 1][1]) for i in range(1, len(s)) if s[i - 1][1] > 0]
    m = sum(r) / len(r)
    return (sum((x - m) ** 2 for x in r) / (len(r) - 1)) ** 0.5 * math.sqrt(244) * 100


print('=' * 74)
print('  一、收益（价格口径 —— 无全收益序列，分红贡献无法计算）')
print('=' * 74)
print(f'  {"区间":<16}{"起点":>10}{"终点":>10}{"年化%":>9}{"波动%":>8}{"最大回撤%":>10}')
for lab, start in (('全样本(回溯)', None), ('实盘期', LAUNCH)):
    s = series('000510', start)
    if len(s) < 30:
        print(f'  {lab}: 数据不足')
        continue
    dd = min(mad(s))
    cg = cagr(s[0][0], s[0][1], s[-1][0], s[-1][1])
    print(f'  {lab:<16}{s[0][1]:>10.1f}{s[-1][1]:>10.1f}'
          f'{cg if cg is not None else 0:>9.2f}{vol(s):>8.2f}{dd*100:>10.1f}')

print()
print('=' * 74)
print('  二、与宽基/同类的关系（日收益回归）')
print('=' * 74)
base = {}
for code, name in (('000300', '沪深300'), ('000905', '中证500'),
                   ('000852', '中证1000'), ('000985', '中证全指')):
    base[code] = dict(series(code, LAUNCH) or series(code))

a5 = {d: c for d, c in series('000510', LAUNCH)}
print(f'  {"对照":<12}{"重叠日":>8}{"R²":>8}{"beta":>8}{"A500年化":>10}{"对照年化":>10}')
for code, name in (('000300', '沪深300'), ('000905', '中证500'),
                   ('000852', '中证1000'), ('000985', '中证全指')):
    b = base[code]
    common = sorted(set(a5) & set(b))
    if len(common) < 60:
        print(f'  {name:<12}{len(common):>8}  样本不足')
        continue
    da = [math.log(a5[common[i]] / a5[common[i - 1]]) for i in range(1, len(common))]
    db = [math.log(b[common[i]] / b[common[i - 1]]) for i in range(1, len(common))]
    n = len(da)
    ma, mb = sum(da) / n, sum(db) / n
    cov = sum((da[i] - ma) * (db[i] - mb) for i in range(n)) / (n - 1)
    va = sum((x - ma) ** 2 for x in da) / (n - 1)
    vb = sum((x - mb) ** 2 for x in db) / (n - 1)
    beta = cov / vb
    r2 = cov ** 2 / (va * vb)
    sa = cagr(common[0], a5[common[0]], common[-1], a5[common[-1]])
    sb = cagr(common[0], b[common[0]], common[-1], b[common[-1]])
    print(f'  {name:<12}{n:>8}{r2:>8.3f}{beta:>8.2f}{sa:>10.2f}{sb:>10.2f}')

print()
print('=' * 74)
print('  三、分年度（全样本，回溯期需谨慎）')
print('=' * 74)
s = series('000510')
years = {}
for d, c in s:
    years.setdefault(d[:4], []).append(c)
rows = []
for y in sorted(years):
    v = years[y]
    if len(v) < 20:
        continue
    rows.append((y, (v[-1] / v[0] - 1) * 100))
for i in range(0, len(rows), 6):
    chunk = rows[i:i + 6]
    print('   ' + '  '.join(f'{y}:{r:>+7.1f}%' for y, r in chunk))

print()
print('=' * 74)
print('  四、锚定回撤分档（实盘期样本太少，改用全样本并标注）')
print('=' * 74)
dd = mad(s)
fine = 'A500 无全收益序列，本表为价格口径；回溯期含幸存者偏差'
print(f'  {fine}')
print(f'  {"档位":<12}{"段数":>6}{"入场年中位":>12}{"胜率":>8}')
BINS = [(0, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20),
        (0.20, 0.30), (0.30, 0.40), (0.40, 0.50), (0.50, 1.0)]
for lo, hi in BINS:
    idx = [i for i in range(len(s)) if lo <= -dd[i] < hi]
    if not idx:
        continue
    fwd = []
    for i in idx:
        if i + 250 < len(s):
            fwd.append((s[i + 250][1] / s[i][1] - 1) * 100)
    if not fwd:
        continue
    # 连续段计数
    segs = 1
    for k in range(1, len(idx)):
        if idx[k] - idx[k - 1] > 1:
            segs += 1
    fs = sorted(fwd)
    med = fs[len(fs) // 2]
    win = sum(1 for x in fwd if x > 0) / len(fwd) * 100
    print(f'  {lo*100:>3.0f}~{hi*100:>3.0f}%     {segs:>6}{med:>12.1f}{win:>7.0f}%')

CON.close()
