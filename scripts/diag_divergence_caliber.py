# -*- coding: utf-8 -*-
"""指数背离的两种计数口径对照（可复跑，但较慢：逐日全市场扫描）。

背景：同一个「指数背离」信号，在不同口径下会得到差别很大的触发天数。
PRD §7.5 记载 17/199 天（9%），但当前实现对不上，原因就是口径不同：

  口径 A「单日成立」：某指数当日「涨 >0.5% 且成分股涨跌比 <1.0」即算一次
  口径 B「连续 2 日」：连续 2 个交易日都成立才计分 —— 这是代码现行口径

本脚本同时给出两种口径的触发天数，用于说明数字差异的来源，不作为决策依据。

用法：python scripts/diag_divergence_caliber.py [--start 2026-01-01]
"""
import argparse
import os
import sqlite3
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')

INDEXES = ['000985', '000001', '399001', '399006', '000300']


def main():
    ap = argparse.ArgumentParser(description='指数背离两种计数口径对照')
    ap.add_argument('--start', default='2026-01-01')
    a = ap.parse_args()

    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row

    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM index_daily_kline WHERE stock_code='000985' "
        "AND kline_type='normal' AND date >= ? ORDER BY date", (a.start,))]

    chg = {}
    for code in INDEXES:
        for r in con.execute(
                "SELECT date, change FROM index_daily_kline WHERE stock_code=? "
                "AND kline_type='normal' AND date >= ?", (code, a.start)):
            chg.setdefault(code, {})[r[0]] = r[1]

    def members_on(code, d):
        snap = con.execute(
            'SELECT MAX(date) FROM index_constituents WHERE index_code=? AND date<=?',
            (code, d)).fetchone()[0]
        if not snap:
            return set()
        return {r[0] for r in con.execute(
            'SELECT stock_code FROM index_constituents WHERE index_code=? AND date=?',
            (code, snap))}

    ud_cache = {}

    def day_ud(d):
        if d in ud_cache:
            return ud_cache[d]
        rows = con.execute("""
            SELECT stock_code, close,
                   LAG(close) OVER (PARTITION BY stock_code ORDER BY date) AS prev_close
            FROM daily_kline_adj
            WHERE date <= ? AND date >= date(?, '-5 days')
        """, (d, d)).fetchall()
        m = {}
        for x in rows:
            if x['prev_close'] is None:
                continue
            m[x['stock_code']] = (1 if x['close'] > x['prev_close']
                                  else (-1 if x['close'] < x['prev_close'] else 0))
        ud_cache[d] = m
        return m

    def diverged(code, d):
        """单日判定：指数涨 >0.5% 且成分股涨跌比 <1.0。"""
        c = chg.get(code, {}).get(d)
        if c is None or c <= 0.005:
            return False
        mem = members_on(code, d)
        if not mem:
            return False
        m = day_ud(d)
        up = sum(1 for s in mem if m.get(s) == 1)
        down = sum(1 for s in mem if m.get(s) == -1)
        return down > 0 and (up / down) < 1.0

    single_days = consec2_days = 0
    single_idx = consec2_idx = 0
    for i, d in enumerate(dates):
        hit_s, hit_c = [], []
        for code in INDEXES:
            if diverged(code, d):
                hit_s.append(code)
                if i > 0 and diverged(code, dates[i - 1]):
                    hit_c.append(code)
        single_idx += len(hit_s)
        consec2_idx += len(hit_c)
        single_days += 1 if hit_s else 0
        consec2_days += 1 if hit_c else 0

    n = len(dates)
    print(f'样本 {n} 个交易日（{dates[0]} ~ {dates[-1]}）')
    print(f'指数池 {len(INDEXES)} 个：{", ".join(INDEXES)}')
    print()
    print(f'{"口径":<28}{"触发天数":>10}{"触发率":>10}{"指数×日":>10}')
    print('-' * 58)
    print(f'{"A 单日成立":<28}{single_days:>10}{single_days / n * 100:>9.1f}%'
          f'{single_idx:>10}')
    print(f'{"B 连续 2 日（代码现行口径）":<28}{consec2_days:>10}'
          f'{consec2_days / n * 100:>9.1f}%{consec2_idx:>10}')
    print()
    print('注：PRD §7.5 记载的 17/199 天（9%）与上述两种口径均不符，'
          '原因未查明，已在 PRD 第七章记录为待核实项。')

    con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
