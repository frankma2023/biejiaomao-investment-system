# -*- coding: utf-8 -*-
"""
样本外验证：把逐日回放样本按时间切分，检验待改参数在**样本内 / 样本外两段上都成立**。

切分：样本内 2020-02 ~ 2023-12-31；样本外 2024-01-01 ~ 2026-09
口径：A 口径（突破日按买点 = 杯口 + buy_point_buffer 挂单成交），结局 TP15/SL10/H20。
判据：一条改动算「成立」，必须两段的每笔均值朝**同一方向**改善。

为什么要「放宽跑一遍」再切分（而不是每个阈值都重跑全市场）
--------------------------------------------------------
引擎在第一条不满足的规则上就否决，所以**已发货阈值以下的信号根本不在基线 CSV 里**
（例如 `breakout_vol_ratio = 2.0` 时没有 `vol_ratio < 2.0` 的行），
「剔除组 n=0」会让这项检查静默失去意义（历史缺陷 M9）。

因此改为：用 `cup_v2_replay.py --param` 把**待检验的那两个阈值**一并放宽跑一遍
（量比 → `--vol-cut`，杯口→突破 → `--mts-cut`），得到超集；再在超集里按特征列
切「保留组 / 剔除组」。放宽只影响 `_signal_reject` 的出口与回放的 D1 窗口上界，
不会改变结构的判定路径，故超集里 `vr ≥ 发货值 且 mts ≤ 发货值` 的那部分
**恰好等于**基线信号集。

用法:
    python scripts/cup_v2_oos.py [CSV]      # CSV 缺省为 data/cup_v2_replay.csv
    # 正式口径：跑放宽版再喂进来。**放宽值必须小于当前发货值**，否则跑出来与基线相同。
    #   python scripts/cup_v2_replay.py --out data/cup_v2_replay_loose.csv \
    #       --param breakout_vol_ratio=1.0 --param mouth_to_signal_max=999
    #   python scripts/cup_v2_oos.py data/cup_v2_replay_loose.csv
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

# 发货值：从 YAML 读，用于判断「保留组」是否等于当前线上配置
_yml = yaml.safe_load(open(os.path.join(PROJECT_DIR, 'config/market/cup_handle_v2.yaml'),
                           encoding='utf-8'))['cup_handle_v2']
VOL_KEEP = float(_yml['breakout_vol_ratio'])
MTS_KEEP = int(_yml['mouth_to_signal_max'])
MTS_MIN = int(_yml['mouth_to_signal_min'])
# 「拟」值（§6.5 的待检验项）：收紧方向
HDD_KEEP, DEPTH_KEEP = 8.0, 25.0

print('样本: %s' % SRC)
print('当前发货值: 量比 ≥ %.2f、杯口→突破 ∈ [%d, %d] 日'
      % (VOL_KEEP, MTS_MIN, MTS_KEEP))

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
    rows.append({'date': s['date'], 'r': exitA(bars, di, bp),
                 'depth': float(s['depth_pct']), 'hdd': float(s['handle_dd_pct']),
                 'mts': int(s['mouth_to_date_days']) if 'mouth_to_date_days' in s
                 else int(s['mouth_to_days']),
                 'vr': float(s['vol_ratio'] or 0)})

# 只有当 CSV 确实是按当前发货值跑出来的，「发货口径子集」这个说法才成立。
# 引擎在第一条不满足的规则上否决，所以若 CSV 生成于更严的配置，它的特征范围
# 会明显窄于发货值 —— 此时 regime == rows 是**假象**，不能声称「这就是现行配置的信号集」。
_CSV_MATCHES = True
if rows:
    vmin, mmax = min(x['vr'] for x in rows), max(x['mts'] for x in rows)
    mmin = min(x['mts'] for x in rows)
    print('本 CSV 特征范围: vr %.2f~%.2f / mts %d~%d'
          % (vmin, max(x['vr'] for x in rows), mmin, mmax))
    if vmin > VOL_KEEP + 1e-9:
        _CSV_MATCHES = False
        print('  [WARN] CSV 最小量比 %.2f > 发货值 %.2f —— 该 CSV 生成于更严的配置，'
              '「量比」方向不可用，请先按当前 YAML 重跑回放' % (vmin, VOL_KEEP))
    if mmax < MTS_KEEP:
        _CSV_MATCHES = False
        print('  [WARN] CSV 最大间隔 %d < 发货上限 %d —— 同上，「上限」方向不可用'
              % (mmax, MTS_KEEP))
    if mmin < MTS_MIN:
        _CSV_MATCHES = False
        print('  [WARN] CSV 最小间隔 %d < 发货下限 %d —— 该 CSV 尚未应用 '
              'mouth_to_signal_min' % (mmin, MTS_MIN))
print('')

# A 口径可成交的样本。CSV 与发货值一致时它就是基线信号集；否则只是「CSV 覆盖的那部分」。
regime = [x for x in rows if x['vr'] >= VOL_KEEP and x['mts'] <= MTS_KEEP]
if _CSV_MATCHES:
    print('A 口径可成交 %d 条，与当前发货口径一致（量比 ≥ %.2f 且间隔 ≤ %d 日）'
          % (len(regime), VOL_KEEP, MTS_KEEP))
else:
    print('A 口径可成交 %d 条；⚠️ 该 CSV **不是**当前发货配置的信号集，'
          '下列数字只描述该 CSV 覆盖的子集（这是历史样本，不是现行配置的样本外表现）'
          % len(regime))

IS = [x for x in regime if x['date'] < SPLIT]
OOS = [x for x in regime if x['date'] >= SPLIT]


def stat(g):
    """g 里窗口不足的样本 r 为 None，必须剔除后再统计（否则 sum 会撞 NoneType）。"""
    rs = [x['r'] for x in g if x['r'] is not None]
    if not rs:
        return (0, 0.0, 0.0)
    return (len(rs), sum(rs) / len(rs) * 100,
            sum(1 for v in rs if v > 0) / len(rs) * 100)


def check(label, keep, cut):
    """keep / cut 都在「发货口径」子集内选取，保证除待检验项外其余条件一致。"""
    kIS = [x for x in IS if keep(x)]
    kOOS = [x for x in OOS if keep(x)]
    cIS = [x for x in IS if cut(x)]
    cOOS = [x for x in OOS if cut(x)]
    print('=== %s ===' % label)
    for name, a, b in (('保留（拟收紧）', kIS, kOOS), ('剔除（被砍掉）', cIS, cOOS)):
        sa, sb = stat(a), stat(b)
        print('  %-14s 样本内 n=%4d %+6.2f%% 胜%4.1f%%   样本外 n=%4d %+6.2f%% 胜%4.1f%%'
              % (name, sa[0], sa[1], sa[2], sb[0], sb[1], sb[2]))
    if not cIS or not cOOS:
        print('  [WARN] 剔除组为空 —— 该阈值无从检验（请用 --param 放宽后重跑回放）')
    else:
        d1 = stat(kIS)[1] - stat(cIS)[1]
        d2 = stat(kOOS)[1] - stat(cOOS)[1]
        print('  改善：样本内 %+.2fpp，样本外 %+.2fpp  →  %s'
              % (d1, d2, '同向成立' if d1 > 0 and d2 > 0 else '未两段同向'))
    print('')


print('切分点 %s   样本 %d 条：样本内 n=%d   样本外 n=%d\n' % (SPLIT, len(regime), len(IS), len(OOS)))

# 本脚本是「§6.5 四项待检验改动」的检验器，不是当前发货口径的业绩报告。
# 它不取「拟改值」常量：cut 组用「比发货值更严」的固定门（<8% / 25% / >上限），
# 保留组用发货值本身。量比与间隔这两项要放宽才能形成 cut 组，必须换 CSV（见 docstring）。
if not _CSV_MATCHES:
    print('⛔ 本 CSV 与当前发货值不符，下面的 ①④ 两项必然得到无意义的对比，'
          '仅 ②③ 可用（它们不依赖发货阈值）。\n')
check('① 突破量比：≥%.2f（发货）vs <.%.2f' % (VOL_KEEP, VOL_KEEP),
      lambda x: x['vr'] >= VOL_KEEP, lambda x: x['vr'] < VOL_KEEP)
check('② 柄部回撤：≤20%%（现）→ <%g%%（拟）' % HDD_KEEP,
      lambda x: x['hdd'] < HDD_KEEP, lambda x: x['hdd'] >= HDD_KEEP)
check('③ 杯身深度：≤40%%（现）→ <%g%%（拟）' % DEPTH_KEEP,
      lambda x: x['depth'] < DEPTH_KEEP, lambda x: x['depth'] >= DEPTH_KEEP)
check('④ 杯口→突破：≤%d 日（现）→ 不限制（拟去掉）' % MTS_KEEP,
      lambda x: x['mts'] <= MTS_KEEP, lambda x: x['mts'] > MTS_KEEP)

print('=== 合并 ① + ②（量比 ≥ %.1f 且 柄部回撤 < %g%%）==='
      % (VOL_KEEP, HDD_KEEP))
for name, sel in (('保留', lambda x: x['vr'] >= VOL_KEEP and x['hdd'] < HDD_KEEP),
                  ('剔除', lambda x: not (x['vr'] >= VOL_KEEP
                                          and x['hdd'] < HDD_KEEP))):
    a, b = stat([x for x in IS if sel(x)]), stat([x for x in OOS if sel(x)])
    print('  %-6s 样本内 n=%4d %+6.2f%% 胜%4.1f%%   样本外 n=%4d %+6.2f%% 胜%4.1f%%'
          % (name, a[0], a[1], a[2], b[0], b[1], b[2]))

print('\n=== 对照：发货口径全部信号 ===')
a, b = stat(IS), stat(OOS)
print('  全样本 样本内 n=%4d %+6.2f%% 胜%4.1f%%   样本外 n=%4d %+6.2f%% 胜%4.1f%%'
      % (a[0], a[1], a[2], b[0], b[1], b[2]))
print('\nmts 最大值 = %d' % max(x['mts'] for x in rows))
print('vr  最小/最大 = %.2f / %.2f' % (min(x['vr'] for x in rows),
                                       max(x['vr'] for x in rows)))
