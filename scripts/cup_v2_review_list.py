# -*- coding: utf-8 -*-
"""
列出 engine 近期产出的杯柄记录，供人工复核。

数据来自 `cup_handle_v2_daily`（`scripts/scan_cup_handle_v2.py` 逐日全市场扫描落库）。
每条记录输出五个结构点（前高 H / 杯底 B / 杯口 M / 柄低 P / 买点）的日期与价格，
以及可直接打开的形态图链接——人工复核看的就是「这些点是不是真的在这几天」。

用法:
    python scripts/cup_v2_review_list.py                     # 最近一次扫描的全部记录
    python scripts/cup_v2_review_list.py --days 10           # 最近 10 个交易日
    python scripts/cup_v2_review_list.py --type SIGNAL       # 只看突破信号
    python scripts/cup_v2_review_list.py --created-today     # 只看今天新写入的行
"""
import argparse
import os
import sqlite3
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')

FIELDS = ('prior_high', 'bottom', 'mouth', 'handle_low', 'buy_point')


def _shift18(d):
    """突破日前 18 个月，作为形态图窗口起点。"""
    y, m, day = (int(x) for x in d.split('-'))
    m -= 18
    while m < 1:
        m += 12
        y -= 1
    return '%04d-%02d-%02d' % (y, m, min(day, 28))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=0, help='只取最近 N 个交易日（0=不限）')
    ap.add_argument('--type', default=None, help='SIGNAL / CONFIRM / CANDIDATE')
    ap.add_argument('--created-today', action='store_true', help='只看今天写入的行')
    ap.add_argument('--chart', action='store_true', help='追加形态图链接')
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM cup_handle_v2_daily WHERE 1=1"
    qp = []
    if args.created_today:
        sql += " AND date(created_at) = date('now','localtime')"
    if args.type:
        sql += " AND record_type = ?"
        qp.append(args.type.upper())
    if args.days:
        ds = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM daily_kline_adj WHERE close IS NOT NULL "
            "ORDER BY date DESC LIMIT ?", (args.days,))]
        sql += " AND date IN (%s)" % ','.join('?' * len(ds))
        qp.extend(ds)
    sql += " ORDER BY date DESC, record_type, stock_code"
    rows = conn.execute(sql, qp).fetchall()
    conn.close()

    print('共 %d 条记录\n' % len(rows))
    hdr = ('%-8s %-10s %-9s %-17s %-17s %-17s %-17s %-9s %6s %6s %6s'
           % ('代码', '名称', '日期', '记录', '前高 H', '杯底 B', '杯口 M',
              '柄低 P', '买点', '深度', '柄撤'))
    print(hdr)
    print('-' * len(hdr))
    for r in rows:
        name = (r['stock_name'] or '')[:8]
        cells = []
        for f in FIELDS[:4]:
            p, d = r[f + '_price'], (r[f + '_date'] or '')
            cells.append('%7.3f %s' % (p, d[5:]) if p is not None else ' ' * 14)
        print('%-8s %-10s %-9s %-9s %s %s %s %s %8.3f %5.1f%% %5.1f%%'
              % (r['stock_code'], name, r['date'], r['record_type'],
                 cells[0], cells[1], cells[2], cells[3], r['buy_point'] or 0,
                 r['depth_pct'] or 0, r['handle_dd_pct'] or 0))

    if args.chart:
        print('\n形态图（本地静态服务器 :8772）:')
        for r in rows:
            print('  %s %-9s http://localhost:8772/pattern-scan/?code=%s&start=%s&end=%s'
                  % (r['stock_code'], r['record_type'], r['stock_code'],
                     _shift18(r['date']), r['date']))


if __name__ == '__main__':
    main()
