# -*- coding: utf-8 -*-
"""把 follow_through_day.detect() 的结果写入 follow_through_days 表。

修复 market_sell_score 的「追盘日失效」信号：
  score_ftd_failure 查 follow_through_days WHERE index_code='000985'
  且要求 ftd_date >= target_date - 30 天。
  该表有 13 条历史数据，但最新仅 2026-04-01，且全库无任何写入脚本
  （src/detectors/follow_through_day.py 的 detect() 只算不落库）。

用法：
    python scripts/backfill_follow_through_days.py                 # 000985，近 3 年
    python scripts/backfill_follow_through_days.py --start 2025-01-01
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import yaml

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))
sys.path.insert(0, PROJECT_DIR)

from detectors.follow_through_day import detect  # noqa: E402

DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
CFG = os.path.join(PROJECT_DIR, 'config', 'market', 'follow_through_day.yaml')
NAME = {'000985': '中证全指', '000001': '上证指数', '000300': '沪深300',
        '399001': '深证成指', '399006': '创业板指', '000688': '科创50'}


def _inv_date(ftd):
    """从 failure_reason 中提取失效日期。score_ftd_failure 只认
    invalidated_date IS NOT NULL 的已失效 FTD，缺这个字段等于没写。"""
    import re
    m = re.search(r'(\d{4}-\d{2}-\d{2})', ftd.get('failure_reason') or '')
    return m.group(1) if m else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--index', default='000985')
    ap.add_argument('--start', default=None)
    ap.add_argument('--end', default=datetime.now().strftime('%Y-%m-%d'))
    a = ap.parse_args()

    end = a.end
    start = a.start or (datetime.strptime(end, '%Y-%m-%d') - timedelta(days=1095)).strftime('%Y-%m-%d')
    # detect 需要前置的下跌段才能识别 rally attempt，多取 400 天
    lookback = (datetime.strptime(start, '%Y-%m-%d') - timedelta(days=400)).strftime('%Y-%m-%d')

    cfg = yaml.safe_load(open(CFG, encoding='utf-8'))
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT date, open, high, low, close, volume, change FROM index_daily_kline "
        "WHERE stock_code=? AND kline_type='normal' AND date>=? AND date<=? ORDER BY date",
        (a.index, lookback, end)).fetchall()
    print(f'  {a.index} {NAME.get(a.index, "")} K线 {len(rows)} 条 '
          f'({rows[0]["date"] if rows else "-"} ~ {rows[-1]["date"] if rows else "-"})')
    if len(rows) < 60:
        print('  ✗ K线不足')
        return 1

    kl = []
    for i, x in enumerate(rows):
        d = dict(x)
        d['change_pct'] = d.pop('change')
        if d['change_pct'] is None and i > 0 and rows[i - 1]['close']:
            d['change_pct'] = (d['close'] / rows[i - 1]['close'] - 1) * 100
        if d['change_pct'] is None:
            d['change_pct'] = 0.0
        d['prev_close'] = rows[i - 1]['close'] if i > 0 else d['open']
        kl.append(d)

    _, active, failed = detect(kl, cfg)
    all_ftd = list(active) + list(failed)
    print(f'  检测到 FTD {len(all_ftd)} 个（有效 {len(active)} / 失效 {len(failed)}）')

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    written = 0
    for f in all_ftd:
        d = f.get('date')
        if not d or d < start:
            continue
        con.execute(
            "INSERT OR REPLACE INTO follow_through_days "
            "(ftd_date, low_point_date, day_count, index_code, index_name, close, "
            " gain_pct, is_valid, invalidated_date, invalidated_reason, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (d, f.get('rally_date'), f.get('days_from_d1'), a.index,
             NAME.get(a.index, a.index), f.get('close'), f.get('gain_pct'),
             0 if f.get('failed') else 1,
             _inv_date(f), (f.get('failure_reason') or None), now))
        written += 1
    con.commit()

    n, mn, mx = con.execute(
        "SELECT COUNT(*), MIN(ftd_date), MAX(ftd_date) FROM follow_through_days WHERE index_code=?",
        (a.index,)).fetchone()
    print(f'  ✓ 写入/更新 {written} 行')
    print(f'  现库内 {a.index}: {n} 行  {mn} ~ {mx}')
    recent = con.execute(
        "SELECT ftd_date, is_valid, invalidated_reason FROM follow_through_days "
        "WHERE index_code=? ORDER BY ftd_date DESC LIMIT 5", (a.index,)).fetchall()
    for r in recent:
        print(f'    {r["ftd_date"]}  有效={r["is_valid"]}  {(r["invalidated_reason"] or "")[:46]}')
    con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
