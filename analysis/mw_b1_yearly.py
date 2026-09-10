# -*- coding: utf-8 -*-
"""MW B1 分年超额画像 2016-2025（60 日窗口，找信号有效拐点）"""
import sys, io, os, sqlite3, statistics
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\hanako\investment-system\src')
os.chdir(r'D:\hanako\investment-system\src')
DB = r'D:\hanako\investment-system\data\lixinger.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

idx = conn.execute("SELECT date, close FROM index_daily_kline WHERE stock_code='000985' AND date>='2015-01-01' ORDER BY date").fetchall()
idx_dates = [r['date'] for r in idx]
idx_close = {r['date']: r['close'] for r in idx}

def px_at(ds, dmap, d, offset):
    i0 = None
    for i, k in enumerate(ds):
        if k >= d:
            i0 = i
            break
    if i0 is None:
        return None
    j = i0 + offset - 1
    if j < len(ds) and ds[i0] in dmap and ds[j] in dmap:
        return dmap[ds[j]] / dmap[ds[i0]] - 1
    return None

cache = {}
def stock_px(code, start):
    if code in cache:
        return cache[code]
    rows = conn.execute("SELECT date, adj_close FROM daily_kline WHERE stock_code=? AND date>=? ORDER BY date", (code, start)).fetchall()
    d = {r['date']: r['adj_close'] for r in rows}
    cache[code] = (d, sorted(d.keys()))
    return cache[code]

W = 60
print('年份 | B1数 | 信号60日 | 同期指数60日 | 超额60日 | 超额胜率')
for year in range(2016, 2026):
    sigs = conn.execute("SELECT stock_code, b1_date FROM mw_signal_daily WHERE b1_date>=? AND b1_date<=?",
                        (str(year) + '-01-01', str(year) + '-12-31')).fetchall()
    if not sigs:
        print(year, '| 0 | -')
        continue
    ex, mkt = [], []
    for s in sigs:
        code, d = s['stock_code'], s['b1_date']
        dmap, ds = stock_px(code, '2015-01-01')
        if d not in dmap:
            continue
        i0 = ds.index(d)
        j = i0 + W - 1
        if j >= len(ds):
            continue
        r_sig = dmap[ds[j]] / dmap[d] - 1
        r_mkt = px_at(idx_dates, idx_close, d, W)
        if r_mkt is None:
            continue
        ex.append(r_sig - r_mkt)
        mkt.append(r_mkt)
    if not ex:
        print(year, '|', len(sigs), '| 样本不足')
        continue
    win = sum(1 for v in ex if v > 0) / len(ex)
    print('%d | %d | %+.1f%% | %+.1f%% | %+.1f%% | %.0f%%' % (
        year, len(sigs), statistics.mean(mkt) * 100 + statistics.mean(ex) * 100,
        statistics.mean(mkt) * 100, statistics.mean(ex) * 100, win * 100))
conn.close()
