# -*- coding: utf-8 -*-
"""读逐日回放结果，做 §6.3/§6.4 口径的收益统计（B 口径：信号次日开盘买）。"""
import csv
import os
import sqlite3
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    PROJECT_DIR, 'data', 'cup_v2_replay.csv')

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

sigs = list(csv.DictReader(open(SRC, encoding='utf-8')))
print('信号数: %d' % len(sigs))

# 载入涉及股票的日线
by_code = {}
for s in sigs:
    by_code.setdefault(s['code'], None)
for code in by_code:
    rows = conn.execute(
        "SELECT date,open,high,low,close FROM daily_kline_adj WHERE stock_code=? "
        "AND date>='2020-01-01' ORDER BY date", (code,)).fetchall()
    by_code[code] = [dict(r) for r in rows]
conn.close()


def simulate(bars, j, tp, sl, hold):
    """B 口径：j 为信号日的次日开盘买入。逐日判止盈/止损，同日双触按先止损。"""
    if j >= len(bars):
        return None
    e = bars[j]['open']
    if not e:
        return None
    for k in range(j, min(j + hold, len(bars))):
        if bars[k]['low'] <= e * (1 - sl):
            return -sl
        if bars[k]['high'] >= e * (1 + tp):
            return tp
    k = min(j + hold, len(bars)) - 1
    return bars[k]['close'] / e - 1


def hit(bars, j, tp, win):
    if j >= len(bars):
        return None
    e = bars[j]['open']
    if not e:
        return None
    for k in range(j, min(j + win, len(bars))):
        if bars[k]['high'] >= e * (1 + tp):
            return True
    return False


idx = {}
for code, bars in by_code.items():
    idx[code] = {b['date']: i for i, b in enumerate(bars)}

# 信号次日开盘买入的落点
entries = []
for s in sigs:
    bars = by_code.get(s['code']) or []
    di = (idx.get(s['code']) or {}).get(s['date'])
    if di is None or di + 1 >= len(bars):
        continue
    entries.append((s, bars, di + 1))
print('可入场样本: %d' % len(entries))
if not entries:
    sys.exit(0)

print('\n=== §6.3 目标位达到率（B 口径，自入场起算）===')
print('%-8s%10s%10s%10s' % ('目标', '20日', '30日', '60日'))
for tp in (0.05, 0.08, 0.10, 0.15, 0.20):
    cells = []
    for win in (20, 30, 60):
        v = [hit(b, j, tp, win) for _, b, j in entries]
        v = [x for x in v if x is not None]
        cells.append(100.0 * sum(v) / len(v) if v else 0)
    print('%-8s%9.1f%%%9.1f%%%9.1f%%' % ('+%d%%' % (tp * 100), *cells))

print('\n=== §6.4 退出规则（B 口径，同日双触按先止损）===')
print('%5s%5s%5s%10s%9s%9s' % ('TP', 'SL', 'H', '均值/笔', '胜率', '盈亏比'))
for tp, sl, hold in ((0.05, 0.10, 30), (0.08, 0.05, 20), (0.10, 0.05, 30),
                     (0.10, 0.10, 20), (0.15, 0.05, 30), (0.15, 0.08, 10),
                     (0.15, 0.10, 10), (0.15, 0.10, 20)):
    rs = [simulate(b, j, tp, sl, hold) for _, b, j in entries]
    rs = [x for x in rs if x is not None]
    if not rs:
        continue
    wins = [x for x in rs if x > 0]
    loss = [x for x in rs if x <= 0]
    pf = (sum(wins) / -sum(loss)) if loss and sum(loss) < 0 else float('inf')
    print('%4d%%%4d%%%5d%9.2f%%%8.1f%%%9.2f'
          % (tp * 100, sl * 100, hold, sum(rs) / len(rs) * 100,
             len(wins) / len(rs) * 100, pf))
