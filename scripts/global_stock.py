# -*- coding: utf-8 -*-
"""global_stock 命令行入口。

用法：
    python scripts/global_stock.py health             # 数据源可用性
    python scripts/global_stock.py brief NVDA         # 外部事件快照（喂给分析）
    python scripts/global_stock.py filings NVDA --days 30
    python scripts/global_stock.py filing <URL>       # 读一份申报原文
    python scripts/global_stock.py insider NVDA --days 90
    python scripts/global_stock.py rates --rows 5
    python scripts/global_stock.py vix
    python scripts/global_stock.py quote NVDA
"""
from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from src.data import global_stock as G   # noqa: E402


def dump(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1)[:20000])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('health')
    p = sub.add_parser('brief')
    p.add_argument('ticker')
    p.add_argument('--days', type=int, default=14)

    p = sub.add_parser('filings')
    p.add_argument('ticker')
    p.add_argument('--forms', default='8-K,4')
    p.add_argument('--days', type=int, default=60)

    p = sub.add_parser('filing')
    p.add_argument('url')
    p.add_argument('--chars', type=int, default=3000)

    p = sub.add_parser('insider')
    p.add_argument('ticker')
    p.add_argument('--days', type=int, default=90)

    p = sub.add_parser('rates')
    p.add_argument('--rows', type=int, default=5)

    sub.add_parser('vix')

    p = sub.add_parser('quote')
    p.add_argument('ticker')

    a = ap.parse_args()
    if a.cmd == 'health':
        h = G.health(verbose=True)
        print('可用 %d / %d' % (h['ok'], h['total']))
    elif a.cmd == 'brief':
        print(G.events_brief(a.ticker, a.days))
    elif a.cmd == 'filings':
        dump(G.filings(a.ticker, forms=[x.strip() for x in a.forms.split(',')],
                       days=a.days))
    elif a.cmd == 'filing':
        ft = G.filing_text(a.url, max_chars=a.chars)
        print(ft.get('text') or ft.get('error'))
    elif a.cmd == 'insider':
        dump(G.insider(a.ticker, a.days))
    elif a.cmd == 'rates':
        dump(G.treasury_curve(a.rows))
    elif a.cmd == 'vix':
        dump(G.vix_structure())
    elif a.cmd == 'quote':
        dump(G.quote_us(a.ticker))


if __name__ == '__main__':
    main()
