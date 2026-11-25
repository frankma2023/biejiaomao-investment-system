#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取海外同类宽基指数的长期表现（价格口径），供 A500 报告第三章使用。

数据源：东方财富全球指数（akshare index_global_hist_em）。
输出：.scratch/a500_intl.json
用法：python scripts/fetch_intl_indices.py
"""
import json
import warnings
from pathlib import Path

import akshare as ak

warnings.filterwarnings('ignore')

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / '.scratch' / 'a500_intl.json'

SYMBOLS = ['标普500', '日经225', '恒生指数', '道琼斯']


def cagr_window(dates, closes, years):
    """按整年数计算年化，起点取恰好 <= 目标日的最后一条。"""
    end_d, end_c = dates[-1], closes[-1]
    y, m, d = int(end_d[:4]), int(end_d[5:7]), int(end_d[8:10])
    target = f'{y - years:04d}-{m:02d}-{d:02d}'
    start = None
    for i, dt in enumerate(dates):
        if dt <= target:
            start = (dt, closes[i])
        else:
            break
    if not start:
        return None
    return ((end_c / start[1]) ** (1 / years) - 1) * 100


def main():
    res = {}
    for sym in SYMBOLS:
        df = ak.index_global_hist_em(symbol=sym).dropna(subset=['最新价'])
        dates = [str(x)[:10] for x in df['日期']]
        closes = [float(x) for x in df['最新价']]
        res[sym] = {
            'start': dates[0],
            'end': dates[-1],
            'r10': round(cagr_window(dates, closes, 10), 2),
            'r20': round(cagr_window(dates, closes, 20), 2),
        }
        print(f"{sym}: {dates[0]}~{dates[-1]} 10y={res[sym]['r10']}% 20y={res[sym]['r20']}%")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'✓ {OUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
