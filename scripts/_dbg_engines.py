# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'D:\hanako\investment-system')
from src.scanners.watchlist_report import get_db, _load_klines
from src.server import _compute_indicators
from src.engine_registry import run_all_engines

db = get_db()
klines = _load_klines(db, '600309', '2026-08-21')
print('K线:', len(klines), '最新', klines[-1]['date'] if klines else None)
for k in klines:
    k['stock_code'] = '600309'
try:
    indicators = _compute_indicators(klines)
    print('indicators 键:', list(indicators.keys())[:10] if isinstance(indicators, dict) else type(indicators))
except Exception as e:
    print('indicators 错误:', e)
    indicators = None

sigs = run_all_engines(klines=klines, indicators=indicators, silent=False)
print('引擎信号数:', len(sigs))
from collections import Counter
print(Counter(s['source'] for s in sigs))
for s in sigs:
    print('  ', s.get('source'), s.get('date'), s.get('type'), str(s.get('details', {}))[:60])
