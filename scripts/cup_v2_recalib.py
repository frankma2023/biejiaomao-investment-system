# -*- coding: utf-8 -*-
"""
用逐日回放的可信样本，回头检验那几条「拍出来」的参数有没有区分度。

样本：data/cup_v2_replay.csv（1,668 条，2020-02 ~ 2026-09）
口径：A 口径（突破日按杯口买点成交）；结局用 TP15/SL10/H20 的每笔收益，
      并附 20 日内最大涨幅（冲高口径）作为对照。
"""
import csv
import os
import sqlite3
import sys

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
    成交无从得知。用全天 high/low 会产生系统性高估（TP5/SL10/H30 由 +2.46%
    虚高到 +3.55%）。窗口不足时返回 None，丢弃样本而非静默截断。
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


def maxgain(bars, di, bp, win=20):
    k = min(di + win, len(bars)) - 1
    return max(x['high'] for x in bars[di:k + 1]) / bp - 1


rows = []
for s in sigs:
    bars = bars_by.get(s['code']) or []
    idx = {b['date']: i for i, b in enumerate(bars)}
    di = idx.get(s['date'])
    bp = float(s['buy_point'])
    if di is None or not (bars[di]['low'] <= bp <= bars[di]['high']):
        continue
    _r = exitA(bars, di, bp)
    if _r is None:
        continue
    rows.append({
        'r': _r, 'mx': maxgain(bars, di, bp),
        'depth': float(s['depth_pct']), 'hdd': float(s['handle_dd_pct']),
        'mts': int(s['mouth_to_days']), 'vr': float(s['vol_ratio'] or 0),
        'zb': int(s['zone_before']), 'za': int(s['zone_after']),
        'zsum': int(s['zone_before']) + int(s['zone_after']),
        'rdd': float(s['recovery_dd_pct']),
        'mspan': int(s['mouth_span_days']),
        'hd': int(s['handle_days']),
    })

base = [x['r'] for x in rows]
print('A 口径样本 n=%d   基准 每笔均值 %+.2f%%   胜率 %.1f%%   20日最大涨幅中位 %.2f%%\n'
      % (len(rows), sum(base) / len(base) * 100,
         sum(1 for v in base if v > 0) / len(base) * 100,
         sorted(x['mx'] for x in rows)[len(rows) // 2] * 100))


def bucket(key, edges, label, fmt='%.0f'):
    print('--- %s ---' % label)
    print('%-16s%6s%11s%9s%13s' % ('档位', 'n', '每笔均值', '胜率', '20日最大涨幅中位'))
    for i in range(len(edges) + 1):
        lo = edges[i - 1] if i > 0 else None
        hi = edges[i] if i < len(edges) else None
        g = [x for x in rows
             if (lo is None or x[key] >= lo) and (hi is None or x[key] < hi)]
        if len(g) < 20:
            continue
        rs = [x['r'] for x in g]
        mx = sorted(x['mx'] for x in g)
        name = '[%s,%s)' % ('' if lo is None else fmt % lo, '' if hi is None else fmt % hi)
        print('%-16s%6d%10.2f%%%8.1f%%%12.2f%%'
              % (name, len(g), sum(rs) / len(rs) * 100,
                 sum(1 for v in rs if v > 0) / len(rs) * 100,
                 mx[len(mx) // 2] * 100))
    print('')


bucket('mts', [5, 9, 12, 16, 25], '杯口→突破 间隔（mouth_to_signal_max=12）')
bucket('depth', [15, 20, 25, 30, 35, 40], '杯身深度 pct（depth 15~40）')
bucket('hdd', [5, 8, 10, 12, 15, 20], '柄部回撤 pct（handle_dd_max=15 / P/M>=0.8）')
bucket('vr', [1.5, 2.0, 2.5, 3.5, 5.0], '突破量比（breakout_vol_ratio=1.5）')
bucket('zsum', [1, 2, 4, 7, 11], '杯底区停留总天数（规则 9，现值无下限、上限10）')
bucket('zb', [1, 2, 4, 7], '杯底区 前侧 天数（规则 9）')
bucket('za', [1, 2, 4, 7], '杯底区 后侧 天数（规则 9）')
bucket('rdd', [2, 5, 8, 12, 16], '回升段最大回撤 pct（规则 6 上限 = 杯深x50%）')
bucket('mspan', [30, 60, 100, 150], '前高→杯口 交易日（规则 10 上限 100）')
bucket('hd', [5, 10, 15, 20], '柄部交易日数（原 handle_days_max=15，已删）')
