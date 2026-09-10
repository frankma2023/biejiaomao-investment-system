# -*- coding: utf-8 -*-
"""MW v5.4 复测（修正版）：decline 用引擎门槛口径重算（H后到b1_date最低收盘 vs h_price）
不用存表 decline_pct 字段（L328 笔底口径与门槛 L147 收盘口径不一致——字段偏浅）
表当前 = 纯 v5.4（28,074 今天全部重跑）；对照锚：旧基线 43.3%/-0.12%
"""
import sys, io, os, sqlite3, statistics
from bisect import bisect_left, bisect_right
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\hanako\investment-system\src')
os.chdir(r'D:\hanako\investment-system\src')
DB = r'D:\hanako\investment-system\data\lixinger.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

idx = conn.execute("SELECT date, close FROM index_daily_kline WHERE stock_code='000985' ORDER BY date").fetchall()
idx_d = [r['date'] for r in idx]
idx_c = {r['date']: r['close'] for r in idx}
ma200 = {}
for i in range(199, len(idx_d)):
    ma200[idx_d[i]] = sum(idx_c[idx_d[j]] for j in range(i - 199, i + 1)) / 200
def idx_ret(d, off):
    if d not in idx_d: return None
    i0 = idx_d.index(d)
    j = i0 + off - 1
    if j < len(idx_d): return idx_c[idx_d[j]] / idx_c[d] - 1
    return None
def bull(d):
    m = ma200.get(d)
    return (m is not None and idx_c[d] > m) if d in idx_c else None

sigs = conn.execute("""SELECT stock_code, b1_date, h_date, h_price FROM mw_signal_daily
    WHERE b1_date >= '2016-01-01' AND b1_date <= '2026-12-31' AND stock_code != '_sentinel'""").fetchall()
print('v5.4 B1 样本:', len(sigs))

k_cache = {}
def kline_win(code, d):
    if code not in k_cache:
        rows = conn.execute("SELECT date, adj_open, adj_close FROM daily_kline WHERE stock_code=? AND date>='2015-06-01' ORDER BY date", (code,)).fetchall()
        k_cache[code] = ([r['date'] for r in rows], rows)
    ds, rows = k_cache[code]
    i0 = bisect_left(ds, d)
    if i0 is None or i0 + 1 >= len(rows):
        return None
    entry = rows[i0 + 1]['adj_open']
    j = i0 + 20
    if j >= len(rows):
        return None
    exit_ = rows[j]['adj_close']
    if not entry or not exit_:
        return None
    return entry, exit_

# 引擎门槛口径 decline：h_date 后、b1_date 前最低收盘 vs h_price
decl_cache = {}
def calc_decline(code, h_date, b1_date, h_price):
    key = 'c_' + code
    if key not in decl_cache:
        rows = conn.execute("SELECT date, close FROM daily_kline WHERE stock_code=? ORDER BY date", (code,)).fetchall()
        decl_cache[key] = ([r['date'] for r in rows], [r['close'] for r in rows])
    ds, cs = decl_cache[key]
    i1 = bisect_right(ds, h_date)
    i2 = bisect_right(ds, b1_date)
    seg = cs[i1:i2]
    if not seg or not h_price:
        return None
    return (h_price - min(seg)) / h_price * 100  # 百分数

out = []
for s in sigs:
    win = kline_win(s['stock_code'], s['b1_date'])
    if not win:
        continue
    entry, exit_ = win
    ret = exit_ / entry - 1 - 0.003
    mkt = idx_ret(s['b1_date'], 20)
    if mkt is None:
        continue
    decl = calc_decline(s['stock_code'], s['h_date'], s['b1_date'], s['h_price'])
    out.append({'code': s['stock_code'], 'b1': s['b1_date'], 'h_date': s['h_date'],
                'decline': decl, 'ret': ret, 'mkt': mkt, 'ex': ret - mkt,
                'bull': bull(s['b1_date'])})
print('有效样本:', len(out), '| decline 重算有值:', sum(1 for r in out if r['decline'] is not None))

def stat(rows, name, cond=None):
    sub = [r for r in rows if (cond(r) if cond else True)]
    if len(sub) < 30:
        print(f'  {name}: n={len(sub)} 样本不足')
        return
    exs = [r['ex'] for r in sub]
    win = sum(1 for v in exs if v > 0) / len(exs)
    print(f'  {name}: n={len(sub):6d} 超额胜率={win*100:5.1f}% 平均={statistics.mean(exs)*100:+6.2f}% 中位={statistics.median(exs)*100:+6.2f}%')

print('\n===== v5.4 全量（对照旧基线 43.3%/-0.12%）=====')
stat(out, 'v5.4 全量 B1')
print('\n===== 市场分档 =====')
stat(out, '熊市', lambda r: r['bull'] is False)
stat(out, '牛市', lambda r: r['bull'] is True)
print('\n===== decline 门槛口径分档（引擎口径）=====')
for lo, hi in [(40, 50), (50, 60), (60, 999), (0, 40)]:
    stat(out, 'decline %d-%d%%' % (lo, hi), lambda r, a=lo, b=hi: r['decline'] is not None and a <= r['decline'] < b)
print('\n===== 分年（v5.4 全量）=====')
by_y = {}
for r in out:
    by_y.setdefault(r['b1'][:4], []).append(r['ex'])
for y in sorted(by_y):
    v = by_y[y]
    win = sum(1 for x in v if x > 0) / len(v)
    print('  %s: n=%d 胜率=%.0f%% 平均=%+.2f%%' % (y, len(v), win * 100, statistics.mean(v) * 100))
conn.close()
print('\n完成')
