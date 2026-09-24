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
ds = sorted(s['date'] for s in sigs)
print('信号数: %d   日期跨度 %s ~ %s   涉及 %d 只股票'
      % (len(sigs), ds[0], ds[-1], len({s['code'] for s in sigs})))

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


def simulate(bars, j, tp, sl, hold, entry=None):
    """
    j 为入场日索引；entry 为 None 时按当日开盘价成交（B 口径）。

    入场日盘中路径的处理是**保守性关键**：
      · B 口径成交价 = 当日开盘，当天的后续路径完全可知 → 入场日即可判止盈止损
      · A 口径成交价 = 盘中触及买点的价格，**该日 high 是先于还是后于成交无从得知**
        → 入场日只用收盘结算，止盈止损从次日才开始判。不这样处理会系统性高估
          （实测 TP5/SL10/H30 由 +2.46% 虚高到 +3.55%，胜率由 82.7% 虚高到 90.0%）

    Args:
        bars: 该股日线（已剔除 OHLC 为 None 的占位行）。
        j: 入场日索引。
        tp/sl/hold: 止盈/止损幅度、最长持有交易日。
        entry: 指定成交价；None 表示按 bars[j].open 成交。

    Returns:
        每笔收益率；样本不足或数据缺失时返回 None。
    """
    if j >= len(bars) or j + hold > len(bars):
        return None                      # 窗口不足，丢弃而不是静默截断
    e = bars[j]['open'] if entry is None else entry
    if not e:
        return None
    start = j if entry is None else j + 1
    if entry is not None and bars[j]['close'] <= e * (1 - sl):
        return -sl                       # 入场日只用收盘结算
    for k in range(start, j + hold):
        if bars[k]['low'] <= e * (1 - sl):
            return -sl
        if bars[k]['high'] >= e * (1 + tp):
            return tp
    return bars[min(j + hold, len(bars)) - 1]['close'] / e - 1


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

entriesA = []       # A 口径：突破日按买点（杯口×1.01）挂单成交
for s in sigs:
    bars = by_code.get(s['code']) or []
    di = (idx.get(s['code']) or {}).get(s['date'])
    if di is None:
        continue
    bp = float(s['buy_point'])
    k = bars[di]
    if k['low'] <= bp <= k['high']:
        entriesA.append((s, bars, di, bp))
print('A 口径可成交样本（挂杯口单被触及）: %d / %d'
      % (len(entriesA), len(entries)))

print('\n=== §6.4 退出规则 · A 口径（突破日挂杯口单成交）===')
print('%5s%5s%5s%10s%9s%9s' % ('TP', 'SL', 'H', '均值/笔', '胜率', '盈亏比'))
for tp, sl, hold in ((0.05, 0.10, 30), (0.08, 0.05, 20), (0.10, 0.05, 30),
                     (0.10, 0.10, 20), (0.15, 0.05, 30), (0.15, 0.08, 10),
                     (0.15, 0.10, 10), (0.15, 0.10, 20)):
    rs = [simulate(b, j, tp, sl, hold, entry=e) for _, b, j, e in entriesA]
    rs = [x for x in rs if x is not None]
    if not rs:
        continue
    wins = [x for x in rs if x > 0]
    loss = [x for x in rs if x <= 0]
    pf = (sum(wins) / -sum(loss)) if loss and sum(loss) < 0 else float('inf')
    print('%4d%%%4d%%%5d%9.2f%%%8.1f%%%9.2f'
          % (tp * 100, sl * 100, hold, sum(rs) / len(rs) * 100,
             len(wins) / len(rs) * 100, pf))

print('\n=== 对照：不做止盈止损，单纯持有 N 个交易日 ===')
print('%8s%9s%9s%11s%11s' % ('持有', '均值', '中位', '为正占比', '最大涨幅中位'))
for hold in (5, 10, 20, 30, 60, 120):
    rs = []
    mxs = []
    for _, b, j in entries:
        if j + hold > len(b):
            continue
        k = j + hold - 1
        e = b[j]['open']
        if not e:
            continue
        rs.append(b[k]['close'] / e - 1)
        mxs.append(max(x['high'] for x in b[j:k + 1]) / e - 1)
    if not rs:
        continue
    rs.sort()
    mxs.sort()
    print('%7d日%8.2f%%%8.2f%%%10.1f%%%10.2f%%'
          % (hold, sum(rs) / len(rs) * 100, rs[len(rs) // 2] * 100,
             sum(1 for x in rs if x > 0) / len(rs) * 100, mxs[len(mxs) // 2] * 100))

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
