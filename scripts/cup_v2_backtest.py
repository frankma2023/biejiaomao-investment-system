#!/usr/bin/env python3
"""杯柄 V2 回测复现：用引擎自身的输出复算 PRD §6 的标定数字。

口径（与标定一致）：
  A 口径  挂杯口买入止损单   entry = max(信号日开盘, 买点)
  B 口径  盘后信号、次日开盘买  entry = 次日开盘
胜者定义：结构完成后 N 日内最高价 ≥ 杯口 × 1.20（冲高口径）。

用法：
    python scripts/cup_v2_backtest.py [股票数上限]
"""
import os
import sqlite3
import sys
import time

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))

from scanners import cup_handle_v2 as ch  # noqa: E402

DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
OUT = os.path.join(PROJECT_DIR, 'data', 'cup_v2_backtest.csv')
END = '2026-09-18'
FWD = [10, 15, 20, 30, 60, 120]
TARGETS = [0.05, 0.08, 0.10, 0.15, 0.20]

# 退出规则网格（TP, SL, 最长持有）
EXIT_GRID = [(0.05, 0.05, 10), (0.05, 0.10, 30), (0.08, 0.05, 20),
             (0.10, 0.05, 30), (0.10, 0.10, 20), (0.15, 0.05, 30),
             (0.15, 0.08, 10), (0.15, 0.10, 10), (0.15, 0.10, 20)]

# PRD §6.3 目标位标定（A 口径，breakout_vol_ratio=2.0 档，n=694）
PRD_TARGET = {
    '+5%':  {20: 87.0, 30: 89.3, 60: 91.9},
    '+8%':  {20: 79.8, 30: 82.6, 60: 86.9},
    '+10%': {20: 73.5, 30: 77.4, 60: 83.0},
    '+15%': {20: 57.6, 30: 63.3, 60: 72.0},
    '+20%': {20: 44.8, 30: 51.2, 60: 62.4},
}
# PRD §6.4 退出规则标定（B 口径，n=1065）：(avg%, 胜率%, 盈亏比)
PRD_EXIT = {
    (0.15, 0.10, 20): (2.18, 49.7, 1.51),
    (0.15, 0.10, 10): (2.11, 52.2, 1.44),
    (0.15, 0.08, 10): (1.95, 48.5, 1.64),
    (0.10, 0.10, 20): (1.89, 58.7, 1.06),
    (0.15, 0.05, 30): (1.57, 33.3, 2.95),
    (0.10, 0.05, 30): (1.54, 43.7, 2.00),
    (0.08, 0.05, 20): (1.18, 47.7, 1.59),
    (0.05, 0.10, 30): (0.90, 72.5, 0.50),
}


def simulate(daily, j, entry, tp, sl, hold):
    """
    逐日模拟止盈/止损。

    同日同时触及止盈与止损时按【先止损】处理（保守假设，与标定一致）。

    Args:
        daily: 日K列表。
        j: 信号日索引；从 j+1 开始模拟。
        entry: 入场价。
        tp: 止盈幅度。
        sl: 止损幅度。
        hold: 最长持有交易日。

    Returns:
        该笔交易的收益率（小数）。
    """
    n = len(daily)
    for w in range(1, hold + 1):
        k = j + w
        if k >= n:
            return daily[n - 1]['close'] / entry - 1
        if daily[k]['low'] <= entry * (1 - sl):
            return -sl
        if daily[k]['high'] >= entry * (1 + tp):
            return tp
    k = min(j + hold, n - 1)
    return daily[k]['close'] / entry - 1


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    params = ch.load_params()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    codes = [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_basic WHERE stock_code GLOB '[036][0-9][0-9][0-9][0-9][0-9]' "
        "ORDER BY stock_code")]
    if limit:
        codes = codes[:limit]

    t0 = time.time()
    rows = []
    exits = {g: [] for g in EXIT_GRID}
    for i, code in enumerate(codes, 1):
        daily = ch._load_daily(conn, code, END, 2500)
        if len(daily) < 400:
            continue
        for r in ch.detect(daily, params, stock_code=code):
            j = next((k for k, x in enumerate(daily) if x['date'] == r['date']), None)
            if j is None or j + 1 + max(FWD) >= len(daily):
                continue
            d = r['details']
            eA = max(daily[j]['open'], d['buy_point'])
            eB = daily[j + 1]['open']
            rec = {'code': code, 'date': r['date'], 'p2': d['mouth'],
                   'eA': round(eA, 4), 'eB': round(eB, 4),
                   'depth': r['depth_pct'], 'hdd': r['handle_dd_pct'],
                   'mts': r['mouth_to_date_days'], 'vr': r['breakout_vol_ratio']}
            for w in FWD:
                hi = max(x['high'] for x in daily[j + 1:j + 1 + w])
                rec[f'mxA{w}'] = round(hi / eA - 1, 4)
                rec[f'mxB{w}'] = round(hi / eB - 1, 4)
                rec[f'clB{w}'] = round(daily[j + w]['close'] / eB - 1, 4)
            rows.append(rec)
            for g in EXIT_GRID:
                exits[g].append(simulate(daily, j, eB, *g))
        if i % 800 == 0:
            print(f'  ...{i:,}/{len(codes):,}  SIGNAL {len(rows):,}  ({time.time()-t0:.0f}s)', flush=True)
    conn.close()

    n = len(rows)
    print(f'\n扫描 {len(codes):,} 只    SIGNAL {n:,}   耗时 {time.time()-t0:.0f}s\n')

    # ── §6.3 目标位复现 ──
    print('=' * 84)
    print('§6.3 目标位复现 —— A 口径（挂杯口买入止损单）  [PRD 表为 k=2.0 档，本引擎 k=1.5]')
    print('=' * 84)
    print(f'{"目标":<7}{"窗口":<6}{"复现":>9}{"PRD":>9}{"差":>10}')
    for t in TARGETS:
        key = f'+{int(t*100)}%'
        for w in (20, 30, 60):
            hit = sum(1 for r in rows if r[f'mxA{w}'] >= t) / n * 100
            prd = PRD_TARGET[key][w]
            print(f'{key:<7}{w:<6}{hit:>8.1f}%{prd:>8.1f}%{hit-prd:>+9.1f}pp')

    print('\n' + '=' * 84)
    print('§6.3 对照 —— B 口径（盘后信号、次日开盘买）')
    print('=' * 84)
    print(f'{"目标":<7}{"20日":>9}{"30日":>9}{"60日":>9}')
    for t in TARGETS:
        line = ''.join(f'{sum(1 for r in rows if r[f"mxB{w}"] >= t)/n*100:>8.1f}%'
                       for w in (20, 30, 60))
        print(f'+{int(t*100)}%{"":<3}{line}')

    # ── §6.4 退出规则复现 ──
    print('\n' + '=' * 84)
    print('§6.4 退出规则复现 —— B 口径（逐日判止盈/止损，同日双触按先止损）')
    print('=' * 84)
    print(f'{"TP":>5}{"SL":>5}{"H":>4}{"复现均值":>10}{"PRD":>8}{"差":>10}'
          f'{"复现胜率":>10}{"PRD":>8}{"复现盈亏比":>11}{"PRD":>7}')
    for g in EXIT_GRID:
        if g not in PRD_EXIT:
            continue
        rets = exits[g]
        avg = sum(rets) / len(rets)
        win = sum(1 for x in rets if x > 0) / len(rets) * 100
        gains = [x for x in rets if x > 0]
        losses = [x for x in rets if x <= 0]
        pf = (sum(gains) / len(gains)) / abs(sum(losses) / len(losses)) if gains and losses else 0
        p_avg, p_win, p_pf = PRD_EXIT[g]
        print(f'{g[0]:>5.0%}{g[1]:>5.0%}{g[2]:>4}{avg:>9.2%}{p_avg:>7.2f}%{avg*100-p_avg:>+9.2f}pp'
              f'{win:>9.1f}%{p_win:>7.1f}%{pf:>11.2f}{p_pf:>7.2f}')

    print('\n' + '=' * 84)
    print('B 口径基础收益特征')
    print('=' * 84)
    for lab, k in (('30日最大涨幅中位', 'mxB30'), ('30日收盘中位', 'clB30'),
                   ('60日收盘中位', 'clB60'), ('120日收盘中位', 'clB120')):
        v = sorted(r[k] for r in rows if r[k] is not None)
        if v:
            print(f'  {lab:<18} 中位 {v[len(v)//2]*100:6.2f}%  25% {v[len(v)//4]*100:6.2f}%  '
                  f'75% {v[len(v)*3//4]*100:6.2f}%')

    with open(OUT, 'w', encoding='utf-8') as f:
        keys = list(rows[0].keys()) if rows else []
        f.write(','.join(keys) + '\n')
        for r in rows:
            f.write(','.join(str(r[k]) for k in keys) + '\n')
    print(f'\n逐信号明细: {OUT}')


if __name__ == '__main__':
    main()
