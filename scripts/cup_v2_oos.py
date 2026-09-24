# -*- coding: utf-8 -*-
"""
样本外验证：把逐日回放样本按时间切分，检验四条待改参数在**两段上都成立**。

切分：样本内 2020-02 ~ 2023-12-31；样本外 2024-01-01 ~ 2026-09
口径：A 口径（突破日按杯口买点成交），结局 TP15/SL10/H20。

判据：一条改动算「成立」，必须**两段的每笔均值都朝同一方向改善**，且样本外的
改善幅度不能远小于样本内（否则就是拟合噪声）。
"""
import csv
import os
import sqlite3

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
SRC = os.path.join(PROJECT_DIR, 'data', 'cup_v2_replay.csv')
SPLIT = '2024-01-01'

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
sigs = list(csv.DictReader(open(SRC, encoding='utf-8')))
bars_by = {}
for code in {s['code'] for s in sigs}:
    bars_by[code] = [dict(r) for r in conn.execute(
        "SELECT date,open,high,low,close FROM daily_kline_adj WHERE stock_code=? "
        "AND date>='2020-01-01' ORDER BY date", (code,))]
conn.close()


def exitA(bars, di, bp, tp=0.15, sl=0.10, hold=20):
    e = bp
    for k in range(di, min(di + hold, len(bars))):
        if bars[k]['low'] <= e * (1 - sl):
            return -sl
        if bars[k]['high'] >= e * (1 + tp):
            return tp
    k = min(di + hold, len(bars)) - 1
    return bars[k]['close'] / e - 1


rows = []
for s in sigs:
    bars = bars_by.get(s['code']) or []
    idx = {b['date']: i for i, b in enumerate(bars)}
    di = idx.get(s['date'])
    bp = float(s['buy_point'])
    if di is None or not (bars[di]['low'] <= bp <= bars[di]['high']):
        continue
    rows.append({'date': s['date'], 'r': exitA(bars, di, bp),
                 'depth': float(s['depth_pct']), 'hdd': float(s['handle_dd_pct']),
                 'mts': int(s['mouth_to_date_days']) if 'mouth_to_date_days' in s
                 else int(s['mouth_to_days']),
                 'vr': float(s['vol_ratio'] or 0)})

IS = [x for x in rows if x['date'] < SPLIT]
OOS = [x for x in rows if x['date'] >= SPLIT]


def stat(g):
    if not g:
        return (0, 0.0, 0.0)
    rs = [x['r'] for x in g]
    return (len(g), sum(rs) / len(rs) * 100,
            sum(1 for v in rs if v > 0) / len(rs) * 100)


print('切分点 %s   样本内 n=%d   样本外 n=%d\n' % (SPLIT, len(IS), len(OOS)))


def check(label, keep, cut):
    print('=== %s ===' % label)
    for name, sel in (('保留（新规则）', keep), ('剔除', cut)):
        a = stat([x for x in IS if sel(x)])
        b = stat([x for x in OOS if sel(x)])
        print('  %-12s 样本内 n=%4d %+6.2f%% 胜%4.1f%%   样本外 n=%4d %+6.2f%% 胜%4.1f%%'
              % (name, a[0], a[1], a[2], b[0], b[1], b[2]))
    print('')


check('① 突破量比：1.5（现）→ 2.0（拟）',
      lambda x: x['vr'] >= 2.0, lambda x: x['vr'] < 2.0)
check('② 柄部回撤：<=20%（现）→ <=8%（拟）',
      lambda x: x['hdd'] < 8.0, lambda x: x['hdd'] >= 8.0)
check('③ 杯身深度：<=40%（现）→ <=25%（拟）',
      lambda x: x['depth'] < 25.0, lambda x: x['depth'] >= 25.0)
check('④ 杯口→突破：<=12 日（现）→ 不限制（拟去掉）',
      lambda x: x['mts'] <= 12, lambda x: x['mts'] > 12)

print('=== 合并 ① + ②（量比>=2.0 且 柄部回撤<8%）===')
for name, sel in (('保留', lambda x: x['vr'] >= 2.0 and x['hdd'] < 8.0),
                  ('剔除', lambda x: not (x['vr'] >= 2.0 and x['hdd'] < 8.0))):
    a = stat([x for x in IS if sel(x)])
    b = stat([x for x in OOS if sel(x)])
    print('  %-6s 样本内 n=%4d %+6.2f%% 胜%4.1f%%   样本外 n=%4d %+6.2f%% 胜%4.1f%%'
          % (name, a[0], a[1], a[2], b[0], b[1], b[2]))
print('\n=== 对照：全部信号 ===')
a, b = stat(IS), stat(OOS)
print('  全样本 样本内 n=%4d %+6.2f%% 胜%4.1f%%   样本外 n=%4d %+6.2f%% 胜%4.1f%%'
      % (a[0], a[1], a[2], b[0], b[1], b[2]))
print('\nmts 最大值 = %d（引擎把杯口→突破封顶在 12，故样本里没有 >12 的，④ 无法验证）'
      % max(x['mts'] for x in rows))
