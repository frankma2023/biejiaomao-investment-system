# -*- coding: utf-8 -*-
"""
打印指定股票最近一次杯柄记录的全部结构点与关键日原始收盘，供人工复核。

它回答的是复核时唯一重要的问题：**引擎标的这五个日子，是不是真的对应
那五个价格**。所以除了结构点，还把每个结构点所在日的 `daily_kline_adj`
原始收盘原样打出来（不做任何调整），有出入一眼可见。

用法:
    python scripts/cup_v2_review_detail.py 600207 000811 300808
"""
import os
import sqlite3
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))
from scanners import cup_handle_v2 as ch  # noqa: E402

DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')


def link(code, d):
    y, m, day = (int(x) for x in d.split('-'))
    m -= 18
    while m < 1:
        m += 12
        y -= 1
    return ('http://localhost:8772/pattern-scan/?code=%s&start=%04d-%02d-%02d&end=%s'
            % (code, y, m, min(day, 28), d))


def main(codes):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    params = ch.load_params()
    for code in codes:
        row = conn.execute(
            "SELECT * FROM cup_handle_v2_daily WHERE stock_code=? "
            "ORDER BY date DESC, record_type LIMIT 1", (code,)).fetchone()
        if row is None:
            print('%s  库内无记录（先跑 scripts/scan_cup_handle_v2.py）' % code)
            continue
        r = dict(row)
        D = r['date']
        name = r['stock_name'] or ''
        daily = [k for k in ch._load_daily(conn, code, D, 2500) if k['close'] is not None]
        dates = [k['date'] for k in daily]
        closes = {k['date']: k['close'] for k in daily}
        print('=' * 92)
        print('%s %s   %s   %s' % (code, name, r['record_type'], D))
        print('=' * 92)
        key = [('前高 H', r['prior_high_date'], r['prior_high_price']),
               ('杯底 B', r['bottom_date'], r['bottom_price']),
               ('杯口 M', r['mouth_date'], r['mouth_price']),
               ('柄低 P', r['handle_low_date'], r['handle_low_price'])]
        ok = True
        for lab, dt, pr in key:
            actual = closes.get(dt)
            mark = ''
            if actual is None:
                mark, ok = '  [该日无K线]', False
            elif abs(actual - pr) > 1e-6:
                mark, ok = '  [与库值不符]', False
            print('  %-6s %7.3f @ %s   库内当日收盘 %.3f%s'
                  % (lab, pr, dt, actual if actual is not None else float('nan'), mark))
        buy = r['buy_point']
        print('  买点       %7.3f      = 杯口 + %.2f 元（规则 12）'
              % (buy, round(buy - r['mouth_price'], 4)))
        print('  柄低日→检测日 %d 日；杯口→检测日 %d 日；前高→杯口 %d 日'
              % (dates.index(D) - dates.index(r['handle_low_date']),
                 dates.index(D) - dates.index(r['mouth_date']),
                 dates.index(r['mouth_date']) - dates.index(r['prior_high_date'])))
        print('  深度 %.2f%%   柄部回撤 %.2f%%   柄部 %d 日'
              % (r['depth_pct'] or 0, r['handle_dd_pct'] or 0, r['handle_days'] or 0))
        print('  结构点自洽：%s' % ('是（库值与原始收盘逐一相符）' if ok else '否'))
        print('')
        print('  段内关键日原始收盘:')
        lo, hi = dates.index(r['prior_high_date']), dates.index(D)
        kmap = {k[1]: k[0] for k in key}
        kmap[D] = '检测日'
        for k in daily[lo:hi + 1]:
            tag = kmap.get(k['date'])
            if tag:
                print('     %s  收 %8.3f   <== %s' % (k['date'], k['close'], tag))
        print('')
        print('  复核链接: %s' % link(code, D))
        print('')
    conn.close()


if __name__ == '__main__':
    main(sys.argv[1:] or ['600207'])
