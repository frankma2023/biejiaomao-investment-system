# -*- coding: utf-8 -*-
"""打印当前引擎跑出的 4 条信号：完整结构点 + 关键日原始收盘，供人工复核。"""
import csv
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


def link(d):
    y, m, day = (int(x) for x in d.split('-'))
    m -= 18
    while m < 1:
        m += 12
        y -= 1
    return 'http://localhost:8772/pattern-scan/?code=%s&start=%04d-%02d-%02d&end=%s' \
           % (code, y, m, min(day, 28), d)


for s in sigs:
    code, D = s['code'], s['date']
    name = conn.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
    daily = [k for k in ch._load_daily(conn, code, D, 2500) if k['close'] is not None]
    recs = ch.detect(daily, params, stock_code=code, as_of=D, record_types=('SIGNAL',))
    if not recs:
        print('%s %s  [重算无信号]' % (code, D))
        continue
    r = recs[0]
    d = r['details']
    print('=' * 96)
    print('%s %s   突破日 %s' % (code, name['name'] if name else '', D))
    print('=' * 96)
    print('  前高 H  %.3f  @ %s' % (d['prior_high'], d['prior_high_date']))
    print('  杯底 B  %.3f  @ %s' % (d['bottom'], d['bottom_date']))
    print('  杯口 M  %.3f  @ %s' % (d['mouth'], d['mouth_date']))
    print('  柄低 P  %.3f  @ %s' % (d['handle_low'], d['handle_low_date']))
    print('  买点    %.3f      （= 杯口 + %.2f 元）' % (d['buy_point'], params['buy_point_buffer']))
    print('  突破日收 %.3f      量比 %.2f' % (r['breakout_close'], r['breakout_vol_ratio']))
    print('')
    print('  杯身深度 %.2f%%   柄部回撤 %.2f%%   前高→杯口 %d 日   杯口→突破 %d 日   柄部 %d 日'
          % (r['depth_pct'], r['handle_dd_pct'], r.get('mouth_span_days'),
             r['mouth_to_date_days'], r.get('handle_days')))
    print('  杯底区停留：前侧 %d 日 / 后侧 %d 日   回升段最大回撤 %.2f%%'
          % (r.get('zone_before_days'), r.get('zone_after_days'), r.get('recovery_dd_pct')))
    print('')
    print('  关键日原始收盘（原样，供你逐条核对）:')
    key = {d['prior_high_date']: '前高', d['bottom_date']: '杯底',
           d['mouth_date']: '杯口', d['handle_low_date']: '柄低', D: '突破日'}
    rows = [k for k in daily if min(key) <= k['date'] <= D]
    for k in rows:
        if k['date'] in key:
            print('     %s  收 %.3f   <== %s' % (k['date'], k['close'], key[k['date']]))
    print('')
    print('  复核链接: %s' % link(D))
    print('')

print('（%d 条信号；库内 daily_kline_adj 的最新交易日为 %s）'
      % (len(sigs), conn.execute("SELECT MAX(date) FROM daily_kline_adj").fetchone()[0]))
conn.close()
