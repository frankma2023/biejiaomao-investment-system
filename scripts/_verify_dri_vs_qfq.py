# -*- coding: utf-8 -*-
"""验证 lxr_fc_rights 是否就是理杏仁「分红再投入」口径

方法：用四种口径各自算 N 年年化收益，与官方 tr_dri 接口的 cagr_p_r_yN 对照。
如果 lxr_fc 匹配 DRI，说明它就是分红再投入口径（乘法）；fc/bc 是加法分红口径，必然对不上。
"""
import sqlite3, sys, os
from datetime import date, timedelta

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, 'scripts')
from common import api_post  # noqa: E402

CODES = ['600309', '002648']
PERIODS = [(1, 'cagr_p_r_y1', False), (3, 'cagr_p_r_y3', True),
           (5, 'cagr_p_r_y5', True), (10, 'cagr_p_r_y10', True)]

print('拉取 tr_dri（分红再投入收益率）...')
dri = {}
for res in api_post('/company/hot/tr_dri', {'stockCodes': CODES}):
    dri[res['stockCode']] = res
print('  last_data_date:', {k: v.get('last_data_date') for k, v in dri.items()})

c = sqlite3.connect('data/lixinger.db')
c.row_factory = sqlite3.Row

COLS = {'ex(不复权)': 'ex_close', 'lxr_fc(理杏仁前复权)': 'lxr_fc_close',
        'fc(前复权·加法)': 'fc_close', 'bc(后复权·加法)': 'bc_close'}


def close_on(code, d, col):
    """首个交易日 >= d"""
    r = c.execute(f"SELECT {col} v, date FROM daily_kline WHERE stock_code=? AND date>=? "
                  f"AND {col} IS NOT NULL ORDER BY date LIMIT 1", (code, d)).fetchone()
    return (r['v'], r['date']) if r else (None, None)


END_DATE = c.execute("SELECT MAX(date) d FROM daily_kline").fetchone()['d']


def last_close(code, col, upto=END_DATE):
    """最後一个交易日 <= upto"""
    r = c.execute(f"SELECT {col} v, date FROM daily_kline WHERE stock_code=? AND date<=? "
                  f"AND {col} IS NOT NULL ORDER BY date DESC LIMIT 1", (code, upto)).fetchone()
    return (r['v'], r['date']) if r else (None, None)


for code in CODES:
    r = dri.get(code)
    if not r:
        print(f'\n{code}: tr_dri 无返回')
        continue
    print(f"\n{'='*78}\n{code}   官方 DRI（分红再投入）")
    for n, key, annual in PERIODS:
        print(f"   近{n}年{'年化' if annual else '累计'}: {r.get(key)}")
    print(f"{'-'*78}")
    print(f"   对照区间终点：{END_DATE}")
    for n, key, annual in PERIODS:
        start_date = (date(2026, 9, 10) - timedelta(days=int(365.25 * n))).isoformat()
        line = f"   近{n}年{'年化' if annual else '累计'}  "
        for label, col in COLS.items():
            v0, d0 = close_on(code, start_date, col)
            v1, d1 = last_close(code, col)
            if not v0 or not v1 or v0 <= 0:
                line += f"{label}=n/a  "
                continue
            tot = v1 / v0 - 1
            val = ((1 + tot) ** (1 / n) - 1) if annual else tot
            line += f"{label}={val*100:>8.2f}%  "
        print(line)
    print(f"   （起始日取 {close_on(code, (date(2026,9,10)-timedelta(days=365)).isoformat(), 'ex_close')[1]} / "
          f"终点 {last_close(code, 'ex_close')[1]}）")
c.close()
