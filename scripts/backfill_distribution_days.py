# -*- coding: utf-8 -*-
"""把 distribution_day.detect() 的结果写入 distribution_days_detail。

修复 market_sell_score 抛盘日信号恒为 0 的问题：
  引擎读 distribution_days_detail WHERE index_code='000985'
  但该表只有 000001 数据，且无任何写入脚本。

用法:
    python scripts/backfill_distribution_days.py                # 000985，2024-01-01 起
    python scripts/backfill_distribution_days.py --index 000001 --start 2026-01-01
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))
sys.path.insert(0, PROJECT_DIR)

from scanners.distribution_day import detect  # noqa: E402

DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
NAME = {'000985': '中证全指', '000001': '上证指数', '000300': '沪深300',
        '399001': '深证成指', '399006': '创业板指', '000688': '科创50'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--index', default='000985')
    ap.add_argument('--start', default='2024-01-01')
    ap.add_argument('--end', default=datetime.now().strftime('%Y-%m-%d'))
    a = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    # detect 需要足够前置窗口（25 日窗口 + 确认日抵消），多取 400 天
    from datetime import timedelta
    lookback = (datetime.strptime(a.start, '%Y-%m-%d') - timedelta(days=400)).strftime('%Y-%m-%d')
    k = con.execute(
        "SELECT date, open, high, low, close, volume FROM index_daily_kline "
        "WHERE stock_code=? AND kline_type='normal' AND date>=? AND date<=? ORDER BY date",
        (a.index, lookback, a.end)).fetchall()
    print(f'  {a.index} {NAME.get(a.index, "")} K线 {len(k)} 条 '
          f'({k[0]["date"] if k else "-"} ~ {k[-1]["date"] if k else "-"})')
    if len(k) < 60:
        print('  ✗ K线不足')
        return 1

    r = detect([dict(x) for x in k])
    daily = r['daily']
    print(f'  检测结果：{len(daily)} 个交易日，其中抛盘日 '
          f'{sum(1 for d in daily if d["dist_type"])} 天')

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    written = 0
    # 计算 change_pct 与 volume_ratio（相对前一日）
    for i, d in enumerate(daily):
        if d['date'] < a.start:
            continue
        chg = None
        vr = None
        if i > 0:
            p = daily[i - 1]
            if p['close']:
                chg = (d['close'] / p['close'] - 1) * 100
            if p['volume']:
                vr = d['volume'] / p['volume']
        con.execute(
            "INSERT OR REPLACE INTO distribution_days_detail "
            "(date, index_code, index_name, close, change_pct, volume, prev_volume, "
            " volume_ratio, is_distribution, distribution_type, decline_threshold_used, "
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d['date'], a.index, NAME.get(a.index, a.index), d['close'], chg,
             d['volume'], daily[i - 1]['volume'] if i > 0 else None, vr,
             1 if d['dist_type'] else 0, d['dist_type'],
             'v3.0', now))
        written += 1
    con.commit()

    n, mn, mx = con.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM distribution_days_detail "
        "WHERE index_code=?", (a.index,)).fetchone()
    nd = con.execute(
        "SELECT COUNT(*) FROM distribution_days_detail WHERE index_code=? "
        "AND is_distribution=1", (a.index,)).fetchone()[0]
    print(f'  ✓ 写入 {written} 行')
    print(f'  现库内 {a.index}：{n} 行 {mn} ~ {mx}，其中抛盘日 {nd} 天')
    con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
