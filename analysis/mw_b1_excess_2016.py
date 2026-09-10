# -*- coding: utf-8 -*-
"""MW B1 超额收益回测：逐信号对照同期中证全指（信号日买入 vs 同日买指数）2016 vs 2017"""
import sys, io, os, sqlite3, statistics
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\hanako\investment-system\src')
os.chdir(r'D:\hanako\investment-system\src')
DB = r'D:\hanako\investment-system\data\lixinger.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

idx = conn.execute("SELECT date, close FROM index_daily_kline WHERE stock_code='000985' AND date>='2015-06-01' ORDER BY date").fetchall()
idx_dates = [r['date'] for r in idx]
idx_close = {r['date']: r['close'] for r in idx}

def px_at(ds, dmap, d, offset):
    # d 当日价 + offset 个交易日后的价 → 收益
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

px_cache = {}
def stock_px(code, start):
    if code in px_cache:
        return px_cache[code]
    rows = conn.execute("SELECT date, adj_close FROM daily_kline WHERE stock_code=? AND date>=? ORDER BY date", (code, start)).fetchall()
    d = {r['date']: r['adj_close'] for r in rows}
    px_cache[code] = (d, sorted(d.keys()))
    return px_cache[code]

def backtest(year):
    sigs = conn.execute("SELECT stock_code, b1_date FROM mw_signal_daily WHERE b1_date>=? AND b1_date<=?",
                        (year + '-01-01', year + '-12-31')).fetchall()
    out = {w: {'ex': [], 'ret': [], 'mkt': []} for w in (20, 60, 120)}
    for s in sigs:
        code, d = s['stock_code'], s['b1_date']
        dmap, ds = stock_px(code, '2015-06-01')
        ret = px_at(ds, dmap, d, 1)  # 先取信号日价格（offset 1 需 ≥d 当日——用精确 d）
        if d not in dmap:
            continue
        p0 = dmap[d]
        for w in (20, 60, 120):
            i0 = ds.index(d)
            j = i0 + w - 1
            if j >= len(ds):
                continue
            p1 = dmap[ds[j]]
            r_sig = p1 / p0 - 1
            r_mkt = px_at(idx_dates, idx_close, d, w)
            if r_mkt is None:
                continue
            out[w]['ex'].append(r_sig - r_mkt)
            out[w]['ret'].append(r_sig)
            out[w]['mkt'].append(r_mkt)
    return out

def fmt(vals):
    if not vals:
        return 'n=0'
    win = sum(1 for v in vals if v > 0) / len(vals)
    return 'n=%d 胜率=%.1f%% 平均=%+.2f%% 中位=%+.2f%%' % (len(vals), win * 100, statistics.mean(vals) * 100, statistics.median(vals) * 100)

for year in ('2016', '2017'):
    res = backtest(year)
    print('==== %s B1 超额回测（信号收益 - 同期中证全指）====' % year)
    for w in (20, 60, 120):
        print('  %s日: 信号%s | 同期指数%s | 超额%s' % (w, fmt(res[w]['ret']), fmt(res[w]['mkt']), fmt(res[w]['ex'])))
conn.close()
