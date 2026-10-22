"""拉取九指数股息率 + 分红金额，落库。"""
import sqlite3
import sys

sys.path.insert(0, 'scripts')
import common

NINE = ['000922', 'H30269', '930955', '931468', '000015', '931848',
        '930914', '930839', '980081']
SEGS = [('2015-01-01', '2024-12-31'), ('2025-01-01', '2026-09-29')]


def pull(path, payload):
    try:
        r = common.api_post(path, payload)
        return r if isinstance(r, list) else r.get('data', [])
    except Exception as e:
        return f'ERR {str(e)[:150]}'


con = sqlite3.connect('data/lixinger.db')
con.execute('CREATE TABLE IF NOT EXISTS index_dyr_cache '
            '(stock_code TEXT, date TEXT, dyr REAL, PRIMARY KEY(stock_code, date))')
tot = 0
print('=== 股息率（dyr.mcw）===')
for c in NINE:
    got = {}
    for a, b in SEGS:
        d = pull('/index/fundamental', {
            'stockCodes': [c], 'startDate': a, 'endDate': b,
            'metricsList': ['dyr.mcw']})
        if isinstance(d, str):
            print(f'  ✗ {c} {a}~{b}: {d[:90]}')
            continue
        for x in d:
            v = x.get('dyr.mcw')
            if v is not None:
                got[x['date'][:10]] = v * 100
    for dt, v in got.items():
        con.execute('INSERT OR REPLACE INTO index_dyr_cache VALUES (?,?,?)', (c, dt, v))
    tot += len(got)
    ks = sorted(got)
    print(f'  {c}: {len(got):>5} 条  {ks[0] if ks else "—"} ~ {ks[-1] if ks else "—"}')
con.commit()

print()
print('=== 分红金额（y.ps.da_om.t，年度）===')
con.execute('CREATE TABLE IF NOT EXISTS index_dividend_cache '
            '(stock_code TEXT, date TEXT, da_om REAL, PRIMARY KEY(stock_code, date))')
for c in ('000922', '000015', 'H30269'):
    d = pull('/index/fs/hybrid', {
        'stockCodes': [c], 'startDate': '2015-01-01', 'endDate': '2026-09-29',
        'metricsList': ['y.ps.da_om.t']})
    if isinstance(d, str):
        print(f'  ✗ {c}: {d[:90]}')
        continue
    n = 0
    for x in d:
        v = ((x.get('y') or {}).get('ps') or {}).get('da_om', {}).get('t')
        if v is None:
            continue
        con.execute('INSERT OR REPLACE INTO index_dividend_cache VALUES (?,?,?)',
                    (c, x['date'][:10], v))
        n += 1
    print(f'  {c}: {n} 个年度')
con.commit()
con.close()
print(f'\n  股息率落库 {tot} 条')
