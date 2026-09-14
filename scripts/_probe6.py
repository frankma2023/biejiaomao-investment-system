# -*- coding: utf-8 -*-
"""fc_rights 实际返回值：看清它到底返回什么"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, 'scripts')
from common import api_post

CODE = '600309'
A, B = '2016-01-01', '2016-12-31'


def get(**kw):
    p = dict(stockCode=CODE, startDate=A, endDate=B)
    p.update(kw)
    return api_post('/company/candlestick', p, timeout=120)


ex = {r['date'][:10]: r for r in get(type='ex_rights')}
fc = {r['date'][:10]: r for r in get(type='fc_rights', adjustForwardDate='2026-12-31')}
fc2 = {r['date'][:10]: r for r in get(type='fc_rights', adjustForwardDate='2017-01-01')}

print('  %-12s | %-30s | %-30s | %-30s' % ('date', 'ex_rights(o/h/l/c)', 'fc_rights a=2026-12-31', 'fc_rights a=2017-01-01'))
for dt in sorted(ex)[:8] + sorted(ex)[-6:]:
    def f(m):
        r = m.get(dt)
        return '     -' if not r else '%8.4f %8.4f %8.4f %8.4f' % (r['open'], r['high'], r['low'], r['close'])
    print('  %-12s | %-30s | %-30s | %-30s' % (dt, f(ex), f(fc), f(fc2)))
