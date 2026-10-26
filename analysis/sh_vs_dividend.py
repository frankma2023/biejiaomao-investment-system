# -*- coding: utf-8 -*-
"""上证指数「20年4000点魔咒」的本质：口径、加权方式、成分结构三重差异。"""
import sqlite3

CON = sqlite3.connect('file:data/lixinger.db?mode=ro', uri=True)


def ser(table, code, start='2005-01-01'):
    return CON.execute(
        f"SELECT date, close FROM {table} WHERE stock_code=? AND close>0 "
        f"AND date>=? ORDER BY date", (code, start)).fetchall()


def cagr(s):
    if len(s) < 30:
        return None
    y = (int(s[-1][0][:4]) + int(s[-1][0][5:7]) / 12) - \
        (int(s[0][0][:4]) + int(s[0][0][5:7]) / 12)
    return ((s[-1][1] / s[0][1]) ** (1 / y) - 1) * 100, y


print('=' * 74)
print('  一、同样起点（2005 起）三条线的年化')
print('=' * 74)
rows = []
for code, name, table in (
        ('000001', '上证指数（价格）', 'index_daily_kline'),
        ('000922', '中证红利（价格）', 'index_daily_kline'),
        ('000922', '中证红利（全收益）', 'index_full_return_daily'),
        ('000985', '中证全指（价格）', 'index_daily_kline'),
        ('000300', '沪深300（价格）', 'index_daily_kline')):
    s = ser(table, code)
    if not s:
        continue
    c = cagr(s)
    if not c:
        continue
    rows.append((name, c[0], c[1], s[0][0], s[-1][0], s[-1][1]))
    print(f'  {name:<20}{c[0]:>7.2f}%   ({c[1]:.1f} 年, {s[0][0]} ~ {s[-1][0]})')

# 上证指数是否有全收益
tr001 = ser('index_full_return_daily', '000001')
print()
print(f'  上证指数全收益序列：{"有" if tr001 else "无"}'
      f'{f"（{tr001[0][0]} 起）" if tr001 else "（本地库无，无法直接算分红贡献）"}')

# 二、上证指数历史高点回访
print()
print('=' * 74)
print('  二、上证指数：突破 4000 点之后都发生了什么')
print('=' * 74)
sh = ser('index_daily_kline', '000001', '2005-01-01')
above = [(d, c) for d, c in sh if c >= 4000]
if above:
    print(f'    历史上站上 4000 点的交易日：{len(above)} 天')
    print(f'    首次：{above[0][0]} ({above[0][1]:.0f})   最近：{above[-1][0]} ({above[-1][1]:.0f})')
    # 分段
    segs = []
    cur = [above[0]]
    for x in above[1:]:
        if (int(x[0][:4]) - int(cur[-1][0][:4])) >= 1:
            segs.append(cur)
            cur = [x]
        else:
            cur.append(x)
    segs.append(cur)
    for sg in segs:
        print(f'      {sg[0][0]} ~ {sg[-1][0]}：{len(sg)} 天')
    # 每次之后的最大跌幅
    print()
    print('    每次站上 4000 后，后续最大回撤：')
    idx = {d: i for i, (d, _) in enumerate(sh)}
    seen = set()
    for d, c in above:
        yr = d[:4]
        if yr in seen:
            continue
        seen.add(yr)
        i = idx[d]
        after = sh[i:i + 500]
        if len(after) < 20:
            continue
        low = min(x[1] for x in after)
        print(f'      {d} 站上 {c:.0f} 后 2 年内最低 {low:.0f}，回撤 {(low/c-1)*100:.1f}%')

# 三、成分结构差异
print()
print('=' * 74)
print('  三、加权与选样方式差异')
print('=' * 74)
print('    上证指数：总市值加权（含全部上交所股票，大市值股主导）')
print('    中证红利：股息率加权（越便宜、股息越高，买得越多）')
print()
print('    → 前者「谁大买谁」，后者「谁便宜买谁」；前者不调仓剔除高估，后者每期强制换血。')

CON.close()
