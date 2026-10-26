# -*- coding: utf-8 -*-
"""中证红利（000922）：为什么「长期年化约 9%」和「看图感觉没那么高」不一致？

从三方面拆解：
  1. 口径差：价格指数 vs 全收益指数（分红再投贡献多少）
  2. 任意起点、不同持有期的年化收益分布（持有 1 年 vs 10 年差多少）
  3. 水下时长：买入后要多久才回本
"""
import sqlite3

CON = sqlite3.connect('file:data/lixinger.db?mode=ro', uri=True)
CODE = '000922'


def ser(table):
    return CON.execute(
        f"SELECT date, close FROM {table} WHERE stock_code=? AND close>0 ORDER BY date",
        (CODE,)).fetchall()


px = ser('index_daily_kline')
tr = ser('index_full_return_daily')
CON.close()


def cagr(s):
    y = (int(s[-1][0][:4]) + int(s[-1][0][5:7]) / 12) - \
        (int(s[0][0][:4]) + int(s[0][0][5:7]) / 12)
    return ((s[-1][1] / s[0][1]) ** (1 / y) - 1) * 100, y


def med(xs):
    s = sorted(xs)
    k = len(s)
    return s[k // 2] if k % 2 else (s[k // 2 - 1] + s[k // 2]) / 2


print('=' * 74)
print(f'  中证红利 {CODE}  价格 {px[0][0]}~{px[-1][0]}  全收益 {tr[0][0]}~{tr[-1][0]}')
print('=' * 74)

c1, y1 = cagr(px)
c2, y2 = cagr(tr)
print()
print('  一、口径差：为什么「看图」和「看年化」不一样')
print(f'    价格指数年化   {c1:>6.2f}%   （{y1:.1f} 年）  ← K 线图上画的是这条')
print(f'    全收益年化     {c2:>6.2f}%   （{y2:.1f} 年）  ← 常说的「长期 9%」是这条')
print(f'    分红贡献       {c2-c1:>6.2f}pp/年')
print(f'    → 只看 K 线，会系统性低估 {c2-c1:.2f}pp/年，20 年累计差 '
      f'{((1+c2/100)**y2/(1+c1/100)**y1-1)*100:.0f}%')

# 二、不同持有期的年化分布
print()
print('  二、任意起点买入、持有 N 年的年化收益分布（价格口径，不含分红）')
print(f'    {"持有期":>6}{"样本数":>8}{"中位":>9}{"最差":>9}{"最好":>9}{"亏损占比":>10}{"胜率":>8}')
for yrs in (1, 3, 5, 10, 15):
    rs = []
    for i in range(len(px)):
        j = None
        for k in range(i + 1, len(px)):
            y = (int(px[k][0][:4]) + int(px[k][0][5:7]) / 12) - \
                (int(px[i][0][:4]) + int(px[i][0][5:7]) / 12)
            if y >= yrs:
                j = k
                break
        if j is None:
            continue
        y = (int(px[j][0][:4]) + int(px[j][0][5:7]) / 12) - \
            (int(px[i][0][:4]) + int(px[i][0][5:7]) / 12)
        rs.append(((px[j][1] / px[i][1]) ** (1 / y) - 1) * 100)
    if not rs:
        continue
    neg = sum(1 for x in rs if x < 0) / len(rs) * 100
    print(f'    {yrs:>4} 年{len(rs):>8,}{med(rs):>8.1f}%{min(rs):>8.1f}%'
          f'{max(rs):>8.1f}%{neg:>9.0f}%{100-neg:>7.0f}%')

# 全收益口径同样分布
print()
print('  三、同上，但用全收益口径（含分红再投）')
print(f'    {"持有期":>6}{"中位":>9}{"最差":>9}{"最好":>9}{"亏损占比":>10}')
for yrs in (1, 3, 5, 10):
    rs = []
    for i in range(len(tr)):
        for k in range(i + 1, len(tr)):
            y = (int(tr[k][0][:4]) + int(tr[k][0][5:7]) / 12) - \
                (int(tr[i][0][:4]) + int(tr[i][0][5:7]) / 12)
            if y >= yrs:
                rs.append(((tr[k][1] / tr[i][1]) ** (1 / y) - 1) * 100)
                break
    if not rs:
        continue
    neg = sum(1 for x in rs if x < 0) / len(rs) * 100
    print(f'    {yrs:>4} 年{med(rs):>8.1f}%{min(rs):>8.1f}%{max(rs):>8.1f}%{neg:>9.0f}%')

# 四、水下时长
print()
print('  四、买入后多久回本（价格口径，水下时长）')
peak = -1e18
start = None
worst = (0, '', '')
under = []
for d, c in px:
    if c >= peak:
        if start:
            under.append((d, start))
        peak = c
        start = None
    else:
        if start is None:
            start = d
        if peak > 0 and c / peak - 1 < worst[0]:
            worst = (c / peak - 1, d, start or d)
print(f'    最长水下：从 {worst[2]} 到 {worst[1]}，最大回撤 {worst[0]*100:.1f}%')
if under:
    durs = []
    for d, s in under:
        m = (int(d[:4]) - int(s[:4])) * 12 + (int(d[5:7]) - int(s[5:7]))
        durs.append(m)
    durs.sort()
    print(f'    回本耗时：中位 {durs[len(durs)//2]} 个月，最长 {max(durs)} 个月，'
          f'共 {len(durs)} 次')
