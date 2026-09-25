# -*- coding: utf-8 -*-
"""把当前引擎跑出的信号连结构点一起打出来，供人工复核。"""
import csv
import json
import os
import sqlite3
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))
from scanners import cup_handle_v2 as ch  # noqa: E402

DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
SRC = os.path.join(PROJECT_DIR, 'data', 'cup_v2_replay.csv')

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
sigs = list(csv.DictReader(open(SRC, encoding='utf-8')))
params = ch.load_params()

print('共 %d 条信号\n' % len(sigs))
print('%-8s %-11s %-19s %-19s %-19s %-19s %8s %6s %6s %6s %4s%4s'
      % ('代码', '突破日', '前高', '杯底', '杯口', '柄低', '买点', '深度', '柄撤',
         '量比', '前', '后'))
print('-' * 145)
for s in sigs:
    code, D = s['code'], s['date']
    # 用当前引擎重算一遍，拿到带日期的完整结构
    daily = ch._load_daily(conn, code, D, 2500)
    daily = [k for k in daily if k['close'] is not None]
    if not daily:
        continue
    recs = ch.detect(daily, params, stock_code=code, as_of=D,
                     record_types=('SIGNAL',))
    if not recs:
        print('%-8s %-11s  [引擎重算无信号]' % (code, D))
        continue
    r = recs[0]
    d = r['details']
    print('%-8s %-11s %6.3f(%s) %6.3f(%s) %6.3f(%s) %6.3f(%s) %8.3f %5.1f%% %5.1f%% %5.2f %4s%4s'
          % (code, D,
             d['prior_high'], d['prior_high_date'],
             d['bottom'], d['bottom_date'],
             d['mouth'], d['mouth_date'],
             d['handle_low'], d['handle_low_date'],
             d['buy_point'], r['depth_pct'], r['handle_dd_pct'],
             r['breakout_vol_ratio'] or 0,
             r.get('zone_before_days'), r.get('zone_after_days')))
conn.close()

print('\n盘后复核链接（形态识别页，自动定位到该信号）:')
for s in sigs[:12]:
    print('  http://localhost:8772/pattern-scan/?code=%s&start=%s&end=%s'
          % (s['code'], '2023-01-01', s['date']))
