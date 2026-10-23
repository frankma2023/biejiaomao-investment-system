# -*- coding: utf-8 -*-
"""A500 研究：增量检验（skill §3.2 / §3.6）——它到底比沪深300 强在哪。"""
import math
import sqlite3

CON = sqlite3.connect('file:data/lixinger.db?mode=ro', uri=True)
LAUNCH = '2024-09-23'


def series(code, start=None):
    q = ("SELECT date, close FROM index_daily_kline WHERE stock_code=? "
         "AND kline_type='normal' AND close>0")
    a = [code]
    if start:
        q += " AND date>=?"; a.append(start)
    q += " ORDER BY date"
    return CON.execute(q, a).fetchall()


print('=' * 74)
print('  一、成分重叠（A500 vs 沪深300 / 中证500）')
print('=' * 74)
try:
    a5 = {r[0] for r in CON.execute(
        "SELECT DISTINCT stock_code FROM index_constituents WHERE index_code='000510'")}
    hs = {r[0] for r in CON.execute(
        "SELECT DISTINCT stock_code FROM index_constituents WHERE index_code='000300'")}
    zz = {r[0] for r in CON.execute(
        "SELECT DISTINCT stock_code FROM index_constituents WHERE index_code='000905'")}
    print(f'  A500 成分 {len(a5)} 只 | 沪深300 {len(hs)} 只 | 中证500 {len(zz)} 只')
    if a5 and hs:
        ov = a5 & hs
        print(f'  与沪深300 重叠 {len(ov)} 只 → 占A500 {len(ov)/len(a5)*100:.1f}%，'
              f'占300 {len(ov)/len(hs)*100:.1f}%')
    if a5 and zz:
        ov = a5 & zz
        print(f'  与中证500 重叠 {len(ov)} 只 → 占A500 {len(ov)/len(a5)*100:.1f}%')
    if a5 and hs and zz:
        only = a5 - hs - zz
        print(f'  A500 独有（不在300也不在500里）{len(only)} 只 → {len(only)/len(a5)*100:.1f}%')
except sqlite3.Error as e:
    print('  成分表不可用:', e)

print()
print('=' * 74)
print('  二、Alpha 分解（实盘期，Jensen）')
print('=' * 74)
a = dict(series('000510', LAUNCH))
b = dict(series('000300', LAUNCH))
common = sorted(set(a) & set(b))
if len(common) >= 60:
    ra = [math.log(a[common[i]] / a[common[i - 1]]) for i in range(1, len(common))]
    rb = [math.log(b[common[i]] / b[common[i - 1]]) for i in range(1, len(common))]
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n)) / (n - 1)
    vb = sum((x - mb) ** 2 for x in rb) / (n - 1)
    beta = cov / vb
    ann_a = ma * 244 * 100
    ann_b = mb * 244 * 100
    alpha = ann_a - beta * ann_b
    print(f'  重叠 {n} 日')
    print(f'  A500 年化 {ann_a:.2f}%   沪深300 年化 {ann_b:.2f}%   beta {beta:.2f}')
    print(f'  Jensen alpha = {alpha:+.2f}%/年')
    print(f'  解读：beta {beta:.2f} 意味着 A500 的波动放大 {beta:.2f} 倍；')
    print(f'        {ann_a-ann_b:+.2f}pp 的超额里，beta 解释了 {abs((beta-1)*ann_b):.2f}pp，')
    print(f'        真正的选股/结构贡献（alpha）约 {alpha:+.2f}pp/年。')

print()
print('=' * 74)
print('  三、回溯期 vs 实盘期：同样的规则，两个世界')
print('=' * 74)


def cagr(s):
    if len(s) < 30:
        return None
    y = (int(s[-1][0][:4]) + int(s[-1][0][5:7]) / 12) - \
        (int(s[0][0][:4]) + int(s[0][0][5:7]) / 12)
    return ((s[-1][1] / s[0][1]) ** (1 / y) - 1) * 100 if y > 0 else None


for code, name in (('000510', '中证A500'), ('000300', '沪深300')):
    full = series(code)
    live = series(code, LAUNCH)
    back = [x for x in full if x[0] < LAUNCH]
    cf, cl, cb = cagr(full), cagr(live), cagr(back)
    print(f'  {name}: 回溯期 {cb if cb else 0:>6.2f}%  实盘期 {cl if cl else 0:>6.2f}%  '
          f'全样本 {cf if cf else 0:>6.2f}%')

print()
print('  关键：A500 回溯期年化 vs 沪深300 回溯期年化 —— 差异就是「回算带来的」部分。')

CON.close()
