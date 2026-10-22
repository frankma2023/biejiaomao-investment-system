"""补：全收益点位 r_cp（可回溯到 2015）+ 分红金额分段拉取。"""
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
        return f'ERR {str(e)[:130]}'


con = sqlite3.connect('data/lixinger.db')
con.execute('CREATE TABLE IF NOT EXISTS index_tr_cache '
            '(stock_code TEXT, date TEXT, r_cp REAL, PRIMARY KEY(stock_code, date))')
tot = 0
print('=== 全收益点位 r_cp ===')
for c in NINE:
    got = {}
    for a, b in SEGS:
        d = pull('/index/fundamental', {
            'stockCodes': [c], 'startDate': a, 'endDate': b,
            'metricsList': ['r_cp']})
        if isinstance(d, str):
            print(f'  ✗ {c} {a}~{b}: {d[:80]}')
            continue
        for x in d:
            v = x.get('r_cp')
            if v is not None:
                got[x['date'][:10]] = v
    for dt, v in got.items():
        con.execute('INSERT OR REPLACE INTO index_tr_cache VALUES (?,?,?)', (c, dt, v))
    tot += len(got)
    ks = sorted(got)
    print(f'  {c}: {len(got):>5} 条  {ks[0] if ks else "—"} ~ {ks[-1] if ks else "—"}')
con.commit()

print()
print('=== 分红金额（分段）===')
con.execute('CREATE TABLE IF NOT EXISTS index_dividend_cache '
            '(stock_code TEXT, date TEXT, da_om REAL, PRIMARY KEY(stock_code, date))')
for c in ('000922', '000015', 'H30269', '930955', '930839', '930914'):
    n = 0
    for a, b in SEGS:
        d = pull('/index/fs/hybrid', {
            'stockCodes': [c], 'startDate': a, 'endDate': b,
            'metricsList': ['y.ps.da_om.t']})
        if isinstance(d, str):
            print(f'  ✗ {c} {a}: {d[:70]}')
            continue
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
print(f'\n  全收益点位落库 {tot} 条')
