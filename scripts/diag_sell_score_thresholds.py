# -*- coding: utf-8 -*-
"""卖出评分卡阈值校准诊断（可复跑）。

用于验证 market_sell_score.py 里各阈值的选取依据：
  1. 25 日内抛盘日数的分布 → 决定 5/7/9 三档
  2. 当日涨跌比 <0.7 在近 5 日中的出现次数 → 决定「≥4 日」
  3. 近 5 日上涨天数分布 → 决定「≥3 日」

用法：python scripts/diag_sell_score_thresholds.py [--start 2026-01-01]
"""
import os
import sqlite3
import sys
from collections import Counter

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))
DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')


def main(start='2026-01-01'):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM index_daily_kline WHERE stock_code='000985' "
        "AND kline_type='normal' AND date >= ? ORDER BY date", (start,))]
    print(f'  样本 {len(days)} 个交易日（{days[0]} ~ {days[-1]}）\n')

    # ── 1. 抛盘日数分布 ──
    rows = con.execute(
        "SELECT dist_days_25d FROM market_sell_score_daily "
        "WHERE date >= ? AND dist_days_25d IS NOT NULL", (start,)).fetchall()
    if rows:
        v = sorted(r[0] for r in rows)
        n = len(v)
        print('【1】25 日内抛盘日数分布')
        c = Counter(v)
        cum = 0
        for k in sorted(c, reverse=True):
            cum += c[k]
            print(f'    {k:>2} 个: {c[k]:>3} 天  累计≥ {cum/n*100:>5.1f}%')
        print(f'    P25={v[int(n*.25)]} P50={v[n//2]} P75={v[int(n*.75)]} P90={v[int(n*.9)]}')
        print('    → 采用阈值 5/7/9（起点取中位数，高档取 P90 附近）\n')

    # ── 2. AD 低位分布 ──
    print('【2】当日涨跌比 <0.7 的出现次数（近 5 日窗口）')
    cnt_dist = Counter()
    for d in days:
        r = con.execute("""
            SELECT date,
                   SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) AS up,
                   SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) AS down
            FROM (
                SELECT date, close,
                       LAG(close) OVER (PARTITION BY stock_code ORDER BY date) AS prev_close
                FROM daily_kline_adj
                WHERE date <= ? AND date >= date(?, '-10 days')
            )
            WHERE prev_close IS NOT NULL
            GROUP BY date ORDER BY date DESC LIMIT 5
        """, (d, d)).fetchall()
        vals = [x['up'] / x['down'] for x in r if x['down'] and x['down'] > 0]
        if len(vals) >= 5:
            cnt_dist[sum(1 for x in vals if x < 0.7)] += 1
    tot = sum(cnt_dist.values())
    for k in sorted(cnt_dist, reverse=True):
        print(f'    {k} 日: {cnt_dist[k]:>3} 天 ({cnt_dist[k]/tot*100:>4.1f}%)')
    print('    → 采用「≥4 日」（连续 5 日在宽基指数上等于永假）\n')

    # ── 3. 近 5 日上涨天数分布 ──
    print('【3】近 5 日上涨天数分布')
    up_dist = Counter()
    for d in days:
        rows2 = con.execute(
            "SELECT change FROM index_daily_kline WHERE stock_code='000985' "
            "AND date <= ? ORDER BY date DESC LIMIT 5", (d,)).fetchall()
        up_dist[sum(1 for x in rows2 if x['change'] and x['change'] > 0)] += 1
    n2 = sum(up_dist.values())
    for k in sorted(up_dist):
        print(f'    {k} 日涨: {up_dist[k]:>3} 天 ({up_dist[k]/n2*100:>4.1f}%)')
    print('    → 采用「≥3 日」（连续 5 日涨 2026 年 0 次）')

    con.close()


if __name__ == '__main__':
    s = '2026-01-01'
    for i, a in enumerate(sys.argv):
        if a == '--start' and i + 1 < len(sys.argv):
            s = sys.argv[i + 1]
    main(s)
