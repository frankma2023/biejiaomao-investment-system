# -*- coding: utf-8 -*-
"""市场底部判断 · 日更入口。

调用 scanners/market_bottom.py 算当日结果并写入 market_bottom_daily。
PRD: docs/product/市场底部判断_PRD.md

用法：
  python scripts/compute_market_bottom.py                 # 最近交易日
  python scripts/compute_market_bottom.py --date 2024-02-02
  python scripts/compute_market_bottom.py --dry-run       # 只打印，不入库
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from scanners.market_bottom import compute, save  # noqa: E402

DB = 'data/lixinger.db'


def main():
    ap = argparse.ArgumentParser(description='市场底部判断')
    ap.add_argument('--date', help='YYYY-MM-DD，缺省最近交易日')
    ap.add_argument('--db', default=DB)
    ap.add_argument('--dry-run', action='store_true', help='只打印，不写库')
    a = ap.parse_args()

    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    d = compute(conn, a.date)
    if not d:
        print('[bottom] 无数据，跳过')
        return 1
    if not a.dry_run:
        save(conn, d)
    print('[bottom] %s  %s  %g/%g 分（%g%%）' % (
        d['date'], d['verdict'], d['earned'], d['avail'], d['score']))
    print('  ' + d['summary'])
    for i in d['indices']:
        print('  %-8s 回撤 %5.1f%%  历时 %3d  PE分位 %5.1f%%  PB分位 %5.1f%%  ERP %.2fpp（%s%% 分位）  得分 %g/%g' % (
            i['name'], i['dd'] or 0, i['days'], i['pe_pct'] or 0, i['pb_pct'] or 0,
            i['erp'] or 0, i['erp_pct'], i['score'], i['max']))
        for it in i['items']:
            print('      %-6s %s%s  得分 %s' % (
                it['label'], '—' if it['value'] is None else it['value'],
                '' if it['value'] is None else it['unit'],
                '—（无数据）' if it['score'] is None else '%g/%g' % (it['score'], it['max'])))
    for m in d['market']:
        print('  %-8s %s  得分 %s' % (
            m['label'], '—' if m['value'] is None else '%s%s' % (m['value'], m['unit']),
            '—（无数据，不计入）' if m['score'] is None else '%g/%g' % (m['score'], m['max'])))
    miss = d['meta'].get('missing') or []
    if miss:
        print('  [缺口] %s 无数据，未计入分母' % '、'.join(miss))
    if d['meta'].get('dropped_indices'):
        print('  [缺口] %s 历史不足 60 根 K 线，未参与' % '、'.join(d['meta']['dropped_indices']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
