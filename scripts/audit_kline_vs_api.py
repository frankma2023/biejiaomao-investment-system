# -*- coding: utf-8 -*-
"""daily_kline 三列 vs 理杏仁 API 地面真值（按年代抽日、全市场横截面）
用单日全市场查询（date 模式）拿不复权原始价，与库内 close / adj_close 对比。
"""
import sys, os, sqlite3, statistics
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, 'scripts')
from common import api_post

DATES = ['2005-06-15', '2010-06-15', '2015-06-15', '2016-06-15', '2017-06-15',
         '2018-06-15', '2019-06-17', '2021-06-15', '2023-06-15', '2026-06-16']

c = sqlite3.connect('data/lixinger.db'); c.row_factory = sqlite3.Row

print('%-12s %6s | %-18s | %-18s | %s' % ('date', 'n', 'DB.close==raw', 'DB.adj==raw', 'DB.close/raw 中位'))
print('-' * 92)
for d in DATES:
    try:
        rows = api_post('/company/candlestick', {'date': d, 'type': 'ex_rights'})
    except Exception as e:
        print('%-12s  API失败 %s' % (d, e)); continue
    api = {r['stockCode']: r['close'] for r in rows if r.get('close')}
    db = {r['stock_code']: r for r in c.execute(
        "SELECT stock_code, close, adj_close FROM daily_kline WHERE date=?", (d,))}

    n = ok_c = ok_a = null_a = 0
    ratios = []
    for code, raw in api.items():
        r = db.get(code)
        if not r or not r['close']:
            continue
        n += 1
        if abs(r['close'] - raw) < max(0.011, raw * 0.0002):
            ok_c += 1
        ratios.append(r['close'] / raw)
        if r['adj_close'] is None:
            null_a += 1
        elif abs(r['adj_close'] - raw) < max(0.011, raw * 0.0002):
            ok_a += 1
    if not n:
        print('%-12s  无重叠' % d); continue
    med = statistics.median(ratios)
    print('%-12s %6d | %7d %6.1f%% | %7d %6.1f%% (NULL %d) | %.6f' % (
        d, n, ok_c, ok_c * 100.0 / n, ok_a, ok_a * 100.0 / n, null_a, med))

c.close()
