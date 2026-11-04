#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按区间重算大盘卖出评分（幂等，逐日覆盖写）。

用途：打分函数或阈值修正后，重算历史区间，使表内数值与新逻辑一致。
对应教训 184：存量的派生指标会随底层表补数据或逻辑修复而过时，必须定期重算。

用法：
  python scripts/recompute_sell_score_range.py                  # 重算表内全部日期
  python scripts/recompute_sell_score_range.py --start 2026-01-01
  python scripts/recompute_sell_score_range.py --start 2026-01-01 --end 2026-11-03

输出：逐日对比重算前后的值，只打印发生变化的日期。
"""
import argparse
import os
import sqlite3
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))

DB = os.path.join(PROJECT, 'data', 'lixinger.db')
COLS = ['total_score', 'position_advice', 'divergence_count',
        'divergence_leader', 'divergence_score', 'signal_details']


def snapshot(dates):
    """读取重算前的值（只读连接，不影响写库）。"""
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    try:
        ph = ','.join('?' * len(dates))
        q = (f"SELECT date, {', '.join(COLS)} FROM market_sell_score_daily "
             f"WHERE date IN ({ph})")
        return {r[0]: tuple(r[1:]) for r in con.execute(q, dates)}
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description='按区间重算大盘卖出评分')
    ap.add_argument('--start', default=None)
    ap.add_argument('--end', default=None)
    a = ap.parse_args()

    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    where, params = [], []
    if a.start:
        where.append('date >= ?')
        params.append(a.start)
    if a.end:
        where.append('date <= ?')
        params.append(a.end)
    sql = ('SELECT date FROM market_sell_score_daily'
           + (' WHERE ' + ' AND '.join(where) if where else '')
           + ' ORDER BY date')
    dates = [r[0] for r in con.execute(sql, params)]
    con.close()

    if not dates:
        print('区间内无已存储日期，退出')
        return

    print(f'重算 {len(dates)} 个交易日：{dates[0]} ~ {dates[-1]}', flush=True)
    before = snapshot(dates)
    print(f'已留存重算前快照 {len(before)} 行', flush=True)
    print()

    from scanners.market_sell_score import compute_all, ensure_tables
    ensure_tables()

    for i, d in enumerate(dates, 1):
        compute_all(d)
        if i % 25 == 0 or i == len(dates):
            print(f'  进度 {i}/{len(dates)}', flush=True)

    after = snapshot(dates)

    print()
    print('=== 数值发生变化的交易日 ===')
    hdr = (f'{"日期":<12}{"总分 前→后":<18}{"仓位建议 前→后":<46}'
           f'背离 (count,leader,score) 前→后')
    print(hdr)
    print('-' * 108)
    changed = 0
    for d in dates:
        b, af = before.get(d), after.get(d)
        if b is None or af is None or b == af:
            continue
        changed += 1
        print(f'{d:<12}{f"{b[0]} → {af[0]}":<18}'
              f'{f"{b[1]} → {af[1]}":<46}'
              f'({b[2]},{b[3]},{b[4]}) → ({af[2]},{af[3]},{af[4]})')

    print()
    print(f'共 {len(dates)} 个交易日，{changed} 天数值变化，'
          f'{len(dates) - changed} 天不变')


if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    main()
