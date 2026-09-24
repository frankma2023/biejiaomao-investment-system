# -*- coding: utf-8 -*-
"""只在已有回放 CSV 上统计：规则 9 加下限会剩多少信号、质量如何。不跑回测。"""
import csv
import os
import sqlite3

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
SRC = os.path.join(PROJECT_DIR, 'data', 'cup_v2_replay.csv')

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
sigs = list(csv.DictReader(open(SRC, encoding='utf-8')))
bars_by = {}
for code in {s['code'] for s in sigs}:
    bars_by[code] = [dict(r) for r in conn.execute(
        "SELECT date,open,high,low,close FROM daily_kline_adj WHERE stock_code=? "
        "AND date>='2020-01-01' AND close IS NOT NULL "
        "AND high IS NOT NULL AND low IS NOT NULL ORDER BY date", (code,))]
conn.close()


def exitA(bars, di, bp, tp=0.15, sl=0.10, hold=20):
    """
    A 口径：di 日按 bp（买点）成交，逐日判止盈/止损，同日双触按先止损。

    **入场日只用收盘结算**：bp 是盘中触及买点的价格，该日 high 是先于还是后于
    成交无从得知。用全天 high/low 会产生系统性高估。
    窗口不足（di+hold 超出数据）时返回 None，丢弃样本而非静默截断。
    """
    if di + hold > len(bars):
        return None
    e = bp
    if bars[di]['close'] <= e * (1 - sl):
        return -sl
    for k in range(di + 1, di + hold):
        if bars[k]['low'] <= e * (1 - sl):
            return -sl
        if bars[k]['high'] >= e * (1 + tp):
            return tp
    return bars[di + hold - 1]['close'] / e - 1


rows = []
for s in sigs:
    bars = bars_by.get(s['code']) or []
    idx = {b['date']: i for i, b in enumerate(bars)}
    di = idx.get(s['date'])
    bp = float(s['buy_point'])
    if di is None or not (bars[di]['low'] <= bp <= bars[di]['high']):
        continue
    rows.append({'zb': int(s['zone_before']), 'za': int(s['zone_after']),
                 'r': exitA(bars, di, bp)})


def show(sel, label):
    g = [x for x in rows if sel(x)]
    if not g:
        print('  %-32s n=0' % label)
        return
    rs = [x['r'] for x in g]
    print('  %-32s n=%4d  %+6.2f%%  胜%5.1f%%'
          % (label, len(g), sum(rs) / len(rs) * 100,
             sum(1 for v in rs if v > 0) / len(rs) * 100))


print('全样本 n=%d' % len(rows))
show(lambda x: True, '现状（只有上限各<=10）')
print('')
print('加下限后的候选：')
for lo in (1, 2, 3):
    show(lambda x, lo=lo: x['zb'] >= lo and x['za'] >= lo,
         '前侧>=%d 且 后侧>=%d' % (lo, lo))
print('')
print('只限前侧（后侧不限）：')
for lo in (1, 2, 3):
    show(lambda x, lo=lo: x['zb'] >= lo, '前侧>=%d' % lo)
print('')
print('明细分布（前侧 x 后侧）：')
from collections import Counter
c = Counter((min(x['zb'], 5), min(x['za'], 5)) for x in rows)
print('   前\\后   0    1    2    3    4   >=5')
for zb in range(6):
    line = '   %s   ' % ('>=5' if zb == 5 else zb)
    for za in range(6):
        line += '%5d' % c.get((zb, za), 0)
    print(line)
