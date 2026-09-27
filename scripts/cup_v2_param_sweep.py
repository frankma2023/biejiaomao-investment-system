# -*- coding: utf-8 -*-
"""
单变量收紧扫描：从**已有基线 CSV** 里切出「收紧后保留组 / 被砍掉组」，
按 §6.5 的两段同向判据判断哪条阈值收紧是成立的。

为什么不重跑：引擎在第一条不满足的规则上否决，但**收紧**只是从已发货样本里
再切一刀——被切掉的那部分就在基线 CSV 里（特征列已记录），所以可直接切分。
（**放宽**做不到这一点，必须重跑，见 cup_v2_oos.py 的说明。）

口径：A 口径（突破日挂买点单），TP15/SL10/H20，与 §6.3 / §6.5 一致。
判据：keep 段在样本内、样本外**都**优于 cut 段才算 PASS；并要求
      cut 组两段样本量都不小于 MIN_N（防止小样本噪声）。

用法:
    python scripts/cup_v2_param_sweep.py [CSV]
"""
import csv
import os
import sqlite3
import sys

import yaml

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    PROJECT_DIR, 'data', 'cup_v2_replay.csv')
SPLIT = '2024-01-01'
MIN_N = 50          # cut 组在两段上的最小样本量
MIN_EDGE = 0.50     # 两段各自的最小改善幅度（pp），防止选中噪声

# 发货值：从 YAML 读，用于（a）标签（b）判断传入的 CSV 是否与当前配置匹配
YML = yaml.safe_load(open(os.path.join(PROJECT_DIR, 'config/market/cup_handle_v2.yaml'),
                          encoding='utf-8'))['cup_handle_v2']
SHIP_VOL = float(YML['breakout_vol_ratio'])
SHIP_MTS_MAX = int(YML['mouth_to_signal_max'])
SHIP_MTS_MIN = int(YML['mouth_to_signal_min'])

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
    """A 口径；入场日只用收盘结算；窗口不足返回 None。"""
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
    di = {b['date']: i for i, b in enumerate(bars)}.get(s['date'])
    bp = float(s['buy_point'])
    if di is None or not (bars[di]['low'] <= bp <= bars[di]['high']):
        continue
    r = exitA(bars, di, bp)
    if r is None:
        continue
    rows.append({
        'date': s['date'], 'r': r,
        'vr': float(s['vol_ratio'] or 0),
        'hdd': float(s['handle_dd_pct']),
        'depth': float(s['depth_pct']),
        'mts': int(s['mouth_to_days']),
        'rdd': float(s['recovery_dd_pct']),
        'hd': int(s['handle_days']),
        'zb': int(s['zone_before']), 'za': int(s['zone_after']),
        'mspan': int(s['mouth_span_days']),
    })
IS = [x for x in rows if x['date'] < SPLIT]
OOS = [x for x in rows if x['date'] >= SPLIT]


def stat(g):
    if not g:
        return (0, 0.0, 0.0)
    rs = [x['r'] for x in g]
    m = sum(rs) / len(rs)
    if len(rs) < 2:
        return (len(rs), m * 100, 0.0)
    var = sum((v - m) ** 2 for v in rs) / (len(rs) - 1)
    return (len(rs), m * 100, (var / len(rs)) ** 0.5 * 100)   # n, 均值%, 均值标准误%


def diff_t(a, b):
    """两样本均值差的 t 值（Welch）。a、b 为 (n, 均值, 标准误) 三元组。"""
    se = (a[2] ** 2 + b[2] ** 2) ** 0.5
    return (a[1] - b[1]) / se if se > 0 else 0.0


def edge(keep, cut):
    kIS = stat([x for x in IS if keep(x)])
    cIS = stat([x for x in IS if cut(x)])
    kOO = stat([x for x in OOS if keep(x)])
    cOO = stat([x for x in OOS if cut(x)])
    return kIS, cIS, kOO, cOO


# 候选收紧：名称 / 现行值 / 拟收紧值 / 保留谓词 / 剔除谓词
# 「现行值」取自 YAML，改配置后标签自动跟随
CANDS = [
    ('breakout_vol_ratio  量比', '%.2f' % SHIP_VOL, '2.5',
     lambda x: x['vr'] >= 2.5, lambda x: x['vr'] < 2.5),
    ('breakout_vol_ratio  量比', '%.2f' % SHIP_VOL, '3.0',
     lambda x: x['vr'] >= 3.0, lambda x: x['vr'] < 3.0),
    ('mouth_to_signal_max 杯口→突破上限', str(SHIP_MTS_MAX), '12',
     lambda x: x['mts'] <= 12, lambda x: x['mts'] > 12),
    ('mouth_to_signal_max 杯口→突破上限', str(SHIP_MTS_MAX), '9',
     lambda x: x['mts'] <= 9, lambda x: x['mts'] > 9),
    ('mouth_to_signal_min 杯口→突破下限', str(SHIP_MTS_MIN), '6',
     lambda x: x['mts'] >= 6, lambda x: x['mts'] < 6),
    ('mouth_to_signal_min 杯口→突破下限', str(SHIP_MTS_MIN), '8',
     lambda x: x['mts'] >= 8, lambda x: x['mts'] < 8),
    ('handle_pm_min       柄撤上限', '0.80', '0.92',
     lambda x: x['hdd'] < 8.0, lambda x: x['hdd'] >= 8.0),
    ('handle_pm_min       柄撤上限', '0.80', '0.90',
     lambda x: x['hdd'] < 10.0, lambda x: x['hdd'] >= 10.0),
    ('depth_max           深度上限', '0.40', '0.25',
     lambda x: x['depth'] < 25.0, lambda x: x['depth'] >= 25.0),
    ('depth_max           深度上限', '0.40', '0.30',
     lambda x: x['depth'] < 30.0, lambda x: x['depth'] >= 30.0),
    ('recovery_dd_max     回升段回撤', '0.15', '0.12',
     lambda x: x['rdd'] < 12.0, lambda x: x['rdd'] >= 12.0),
    ('recovery_dd_max     回升段回撤', '0.15', '0.08',
     lambda x: x['rdd'] < 8.0, lambda x: x['rdd'] >= 8.0),
    ('handle_days_min     柄部日数下限', '3', '5',
     lambda x: x['hd'] >= 5, lambda x: x['hd'] < 5),
    ('bottom_zone_before_min 前侧下限', '0', '2',
     lambda x: x['zb'] >= 2, lambda x: x['zb'] < 2),
    ('bottom_zone_days_max 杯底区上限', '10', '7',
     lambda x: x['zb'] <= 7 and x['za'] <= 7,
     lambda x: x['zb'] > 7 or x['za'] > 7),
    ('mouth_span_max      前高→杯口上限', '100', '60',
     lambda x: x['mspan'] <= 60, lambda x: x['mspan'] > 60),
]

print('样本: %s' % SRC)
print('发货值: 量比 ≥ %.2f / 间隔 ∈ [%d, %d] / 深度 ≤ %.2f / 柄撤 ≥ %.2f'
      % (SHIP_VOL, SHIP_MTS_MIN, SHIP_MTS_MAX,
         float(YML['depth_max']), float(YML['handle_pm_min'])))
if rows:
    vmin, vmax = min(x['vr'] for x in rows), max(x['vr'] for x in rows)
    mmin, mmax = min(x['mts'] for x in rows), max(x['mts'] for x in rows)
    print('本 CSV 特征范围: vr %.2f~%.2f / mts %d~%d' % (vmin, vmax, mmin, mmax))
    if vmin > SHIP_VOL + 1e-9:
        print('  [WARN] CSV 的最小量比 %.2f > 发货值 %.2f —— 该 CSV 生成于更严的配置，'
              '「量比」方向不可用，请先按当前 YAML 重跑回放' % (vmin, SHIP_VOL))
    if mmax < SHIP_MTS_MAX:
        print('  [WARN] CSV 的最大间隔 %d < 发货上限 %d —— 同上，'
              '「上限」方向不可用' % (mmax, SHIP_MTS_MAX))
    if mmin < SHIP_MTS_MIN:
        print('  [WARN] CSV 的最小间隔 %d < 发货下限 %d —— 该 CSV 尚未应用'
              ' mouth_to_signal_min，其边际效果会被高估' % (mmin, SHIP_MTS_MIN))
print('')
print('A 口径 %d 条（样本内 %d / 样本外 %d）；基准 %+.2f%%\n'
      % (len(rows), len(IS), len(OOS), stat(rows)[1]))
print('%-32s %-6s %-6s %-7s %-7s %-7s %-7s %s'
      % ('收紧项', '现', '拟', '内Δ(pp)', '内t', '外Δ(pp)', '外t', '判定'))
print('-' * 118)
passed = []
for name, cur, new, keep, cut in CANDS:
    kIS, cIS, kOO, cOO = edge(keep, cut)
    if not cIS[0] or not cOO[0]:
        print('%-32s %-6s %-6s %-7s %-7s %-7s %-7s %s'
              % (name, cur, new, '-', '-', '-', '-', '无剔除组（不可切分）'))
        continue
    dIS, dOO = kIS[1] - cIS[1], kOO[1] - cOO[1]
    tIS, tOO = diff_t(kIS, cIS), diff_t(kOO, cOO)
    # 收紧后保留组才是「剩下的样本」，两段都必须够大；剔除组也要够大才有可比性
    ok = (dIS > 0 and dOO > 0 and tIS >= 2.0 and tOO >= 2.0
          and kIS[0] >= MIN_N and kOO[0] >= MIN_N
          and cIS[0] >= MIN_N and cOO[0] >= MIN_N)
    print('%-32s %-6s %-6s %+7.2f %7.2f %+7.2f %7.2f %s'
          % (name, cur, new, dIS, tIS, dOO, tOO,
             '**PASS**' if ok else ('两段同向但 t 不足' if dIS > 0 and dOO > 0 else '')))
    if ok:
        passed.append((name, cur, new, dIS, dOO, tIS, tOO, kIS[0] + kOO[0]))

print('-' * 118)
print('PASS 判据：两段均值差都 > 0 且 Welch t ≥ 2.0，四个分组（内/外 × 保留/剔除）样本量都 ≥ %d\n'
      % MIN_N)
if passed:
    for name, cur, new, dIS, dOO, tIS, tOO, n_keep in passed:
        print('  ** %s: %s -> %s   内 %+.2fpp(t=%.2f) / 外 %+.2fpp(t=%.2f)，收紧后剩 %d 条'
              % (name, cur, new, dIS, tIS, dOO, tOO, n_keep))
else:
    print('  无一项通过 —— 现行取值在可切分范围内已是最优。')
print('\n注意：本扫描检验了 %d 个假设，多重比较会抬高假阳性；'
      't 门槛取 2.0 是宽松的，PASS 项仍应视为「优先候选」而非定论。' % len(CANDS))

# 诊断：前侧下限那个 PASS 是不是「zb=1 很差」造成的池化假象
print('\n[诊断] 杯底区前侧天数的分档（检验 0 -> 2 那个 PASS 的成因）')
for lab, sel in (('前侧 = 0', lambda x: x['zb'] == 0),
                 ('前侧 = 1', lambda x: x['zb'] == 1),
                 ('前侧 >= 2', lambda x: x['zb'] >= 2)):
    a, b = stat([x for x in IS if sel(x)]), stat([x for x in OOS if sel(x)])
    print('  %-10s 样本内 n=%4d %+6.2f%%   样本外 n=%4d %+6.2f%%'
          % (lab, a[0], a[1], b[0], b[1]))
print('  → 若「前侧 = 0」本身并不差，则 0 -> 2 的改善来自砍掉「= 1」，'
      '而 bottom_zone_before_min=2 会把两者一起砍掉。')
