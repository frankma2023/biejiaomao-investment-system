# -*- coding: utf-8 -*-
"""四口径调用参数实测：谁需要锚点、锚点取什么、返回是否可用"""
import sys, os, time
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, 'scripts')
from common import api_post

CODE = '600309'
A, B = '2016-01-01', '2016-12-31'
SHOW = ['2016-09-28', '2016-09-29', '2016-09-30', '2016-10-10', '2016-10-11', '2016-10-12']


def call(tag, payload):
    t0 = time.time()
    try:
        d = api_post('/company/candlestick', dict(payload), timeout=120)
    except Exception as e:
        print('  %-56s X %.1fs %s' % (tag, time.time() - t0, str(e)[:70]))
        return None
    print('  %-56s OK %.1fs %d 行' % (tag, time.time() - t0, len(d))) 
    return d


def dump(d):
    if not d:
        return
    m = {r['date'][:10]: r for r in d}
    for s in SHOW:
        r = m.get(s)
        if not r:
            print('      %s  无' % s)
            continue
        print('      %s  o=%-9s h=%-9s l=%-9s c=%-9s chg=%s' % (
            s, r.get('open'), r.get('high'), r.get('low'), r.get('close'), r.get('change')))


base = dict(stockCode=CODE, startDate=A, endDate=B)

print('【A】ex_rights 不复权（地面真值）')
dump(call('ex_rights', dict(type='ex_rights', **base)))

print('\n【B】lxr_fc_rights 理杏仁前复权 + adjustForwardDate=2026-12-31')
dump(call('lxr_fc_rights anchor=2026-12-31',
          dict(type='lxr_fc_rights', adjustForwardDate='2026-12-31', **base)))

print('\n【C】fc_rights 前复权 + adjustForwardDate=2026-12-31')
dump(call('fc_rights anchor=2026-12-31',
          dict(type='fc_rights', adjustForwardDate='2026-12-31', **base)))

print('\n【D】fc_rights 前复权 不传锚点')
dump(call('fc_rights 无锚点', dict(type='fc_rights', **base)))

print('\n【E】bc_rights 后复权 + adjustBackwardDate=1996-01-02')
dump(call('bc_rights anchor=1996-01-02',
          dict(type='bc_rights', adjustBackwardDate='1996-01-02', **base)))

print('\n【F】bc_rights 后复权 + adjustBackwardDate=2016-01-01')
dump(call('bc_rights anchor=2016-01-01',
          dict(type='bc_rights', adjustBackwardDate='2016-01-01', **base)))

print('\n【G】bc_rights 不传锚点')
dump(call('bc_rights 无锚点', dict(type='bc_rights', **base)))
