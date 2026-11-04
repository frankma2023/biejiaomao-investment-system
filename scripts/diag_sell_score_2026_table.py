# -*- coding: utf-8 -*-
"""卖出评分卡 13 信号触发率表（可复跑）。

用于复现 PRD §7.1 / HANDOFF §7.1 的那张「触发天数 / 触发率 / 日均贡献」表，
以及总分均值与档位分布。表格里的数字应从本脚本输出誊写，不得手算。

用法：
  python scripts/diag_sell_score_2026_table.py
  python scripts/diag_sell_score_2026_table.py --start 2026-01-01

口径：
  统计对象 = 每个交易日各计一次（按天口径）
  触发判据 = 该信号 *_score > 0
  日均贡献 = Σ分值 ÷ 样本交易日数
"""
import argparse
import os
import sqlite3
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')

# 信号列 → 中文名（顺序与 PRD §5.1 一致）
SIGNALS = [
    ('ma200_score', '破200日线'),
    ('ma50_score', '破50日线'),
    ('dist_score', '抛盘日'),
    ('ftd_score', '追盘日失效'),
    ('hlnl_score', 'NH/NL'),
    ('junk_score', '垃圾补涨'),
    ('ad_crash_score', 'AD崩盘'),
    ('margin_decline_score', '融资余额恶化'),
    ('ad_low5_score', 'AD低位'),
    ('leader_score', '龙头见顶'),
    ('death_cross_score', '均线死叉'),
    ('divergence_score', '指数背离'),
    ('vol_dry_score', '上涨无量'),
]


def main():
    ap = argparse.ArgumentParser(description='卖出评分卡 13 信号触发率表')
    ap.add_argument('--start', default='2026-01-01')
    a = ap.parse_args()

    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    n, lo, hi = con.execute(
        'SELECT COUNT(*), MIN(date), MAX(date) FROM market_sell_score_daily '
        'WHERE date >= ?', (a.start,)).fetchone()
    print(f'样本 {n} 个交易日（{lo} ~ {hi}）')
    print(f'口径：每个交易日各计一次；触发判据 = 该信号分值 > 0；'
          f'日均贡献 = Σ分值 ÷ {n}')
    print()

    rows = []
    for col, name in SIGNALS:
        trig, avg, ssum = con.execute(
            f'SELECT SUM(CASE WHEN {col} > 0 THEN 1 ELSE 0 END), AVG({col}), '
            f'SUM({col}) FROM market_sell_score_daily WHERE date >= ?',
            (a.start,)).fetchone()
        rows.append((name, col, trig or 0, avg or 0.0, ssum or 0))
    rows.sort(key=lambda r: -r[3])

    hdr = f'{"信号":<16}{"触发天数":>8}{"触发率":>9}{"日均贡献":>10}{"总贡献":>9}'
    print(hdr)
    print('-' * len(hdr))
    for name, col, trig, avg, ssum in rows:
        print(f'{name:<16}{trig:>8}{trig / n * 100:>8.1f}%{avg:>10.2f}{ssum:>9.0f}')
    print('-' * len(hdr))
    print(f'{"合计":<16}{"":>8}{"":>9}{sum(r[3] for r in rows):>10.2f}')

    print()
    avg_total = con.execute(
        'SELECT AVG(total_score) FROM market_sell_score_daily WHERE date >= ?',
        (a.start,)).fetchone()[0]
    print(f'总分均值 {avg_total:.2f}')
    print()
    print('档位分布（按 PRD §5.1：<60 正常 / 60~100 减仓50% / 100~140 减仓25% / ≥140 清仓）')
    print('  边界口径与 compute_all 一致：左闭右开，140 归「清仓」')
    # [0,60) [60,100) [100,140) [140,∞) —— 左闭右开，与代码 if total >= 140 一致
    for label, lo_b, hi_b in [('正常 <60', 0, 60), ('减仓50% 60~100', 60, 100),
                              ('减仓25% 100~140', 100, 140), ('清仓 ≥140', 140, 10 ** 6)]:
        k = con.execute(
            'SELECT COUNT(*) FROM market_sell_score_daily WHERE date >= ? '
            'AND total_score >= ? AND total_score < ?', (a.start, lo_b, hi_b)
        ).fetchone()[0]
        print(f'  {label:<18}{k:>5} 天  {k / n * 100:>5.1f}%')

    con.close()


if __name__ == '__main__':
    sys.exit(main())
