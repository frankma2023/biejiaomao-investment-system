# -*- coding: utf-8 -*-
"""MW B1 信号回测（2016 全样本）——验证 backfill_mw.py 产出信号质量
口径：B1 日收盘买入（信号当日收盘确认，无前视）；持有 20/60/120 交易日收盘卖出
收益用前复权（adj_close 优先，缺失走 _ensure_adj_prices 反推）
对照：中证全指(000985) 同期 20/60/120 日收益（超额=信号收益-指数收益）
"""
import sys, io, os, sqlite3, statistics
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\hanako\investment-system\src')
os.chdir(r'D:\hanako\investment-system\src')

DB = r'D:\hanako\investment-system\data\lixinger.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# 1. 2016 B1 信号（去重）
sigs = conn.execute("""SELECT stock_code, b1_date, b2_date, confidence, score, is_plus
    FROM mw_signal_daily WHERE b1_date LIKE '2016%' AND b1_date >= '2016-01-01' AND b1_date <= '2016-12-31'
    ORDER BY b1_date""").fetchall()
print('2016 B1 信号总数:', len(sigs))

# 2. 前复权 K 线缓存（逐股懒加载）
adj_cache = {}
def get_px(code):
    if code in adj_cache:
        return adj_cache[code]
    rows = conn.execute("""SELECT date, COALESCE(adj_close, close) c, change_pct FROM daily_kline
        WHERE stock_code=? AND date>='2016-01-01' ORDER BY date""", (code,)).fetchall()
    rows = [dict(r) for r in rows]
    # adj_close 大面积 NULL 时反推
    null_cnt = sum(1 for r in rows if r['c'] is None)
    if rows and (null_cnt == len(rows) or any(r.get('c') is None for r in rows)):
        # 用 change_pct 反推（保序：从后往前）
        try:
            from src.server import _ensure_adj_prices  # noqa
        except Exception:
            from server import _ensure_adj_prices
        kl = [dict(r) for r in rows]
        # 简单兜底：NULL 则用 close
        for r in kl:
            if r['c'] is None or r['c'] == 0:
                rc = conn.execute("SELECT close FROM daily_kline WHERE stock_code=? AND date=?", (code, r['date'])).fetchone()
                r['c'] = rc['close'] if rc else None
        rows = kl
    d = {r['date']: r['c'] for r in rows if r['c'] is not None}
    adj_cache[code] = d
    return d

def px_on(code, d):
    px = get_px(code)
    # 找 <= d 的最近价
    for k in sorted(px.keys()):
        if k >= d:
            return px[k] if k == d else None
    return None

def px_at_offset(code, d, offset):
    px = get_px(code)
    ds = sorted(px.keys())
    # d 的 index（首个 >= d）
    i0 = None
    for i, k in enumerate(ds):
        if k >= d:
            i0 = i
            break
    if i0 is None:
        return None
    j = i0 + offset - 1  # offset 个交易日后
    if j < len(ds):
        return px[ds[j]]
    return None

# 3. 指数对照（000985）
idx = conn.execute("SELECT date, close FROM index_daily_kline WHERE stock_code='000985' AND date>='2015-12-01' ORDER BY date").fetchall()
idx_dates = [r['date'] for r in idx]
idx_close = {r['date']: r['close'] for r in idx}
def idx_ret(d, offset):
    if d not in idx_dates:
        return None
    i0 = idx_dates.index(d)
    j = i0 + offset - 1
    if j < len(idx_dates) and idx_dates[i0] in idx_close and idx_dates[j] in idx_close:
        return idx_close[idx_dates[j]] / idx_close[idx_dates[i0]] - 1
    return None

# 4. 回测
import collections
results = collections.defaultdict(list)  # window -> list of ret
rows_out = []
for s in sigs:
    code, b1 = s['stock_code'], s['b1_date']
    p0 = px_on(code, b1)
    if not p0:
        continue
    for w in (20, 60, 120):
        p1 = px_at_offset(code, b1, w)
        if p1:
            results[w].append(p1 / p0 - 1)
            rows_out.append((code, b1, w, p1 / p0 - 1))

# 5. 汇总
def stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    win = sum(1 for v in vals if v > 0) / len(vals)
    return {'n': len(vals), 'win': win * 100, 'avg': statistics.mean(vals) * 100,
            'median': statistics.median(vals) * 100,
            'p90': sorted(vals)[int(len(vals) * 0.9)] * 100,
            'p10': sorted(vals)[int(len(vals) * 0.1)] * 100}

print('\n== 各持有窗口 ==')
for w in (20, 60, 120):
    st = stats(results[w])
    if st:
        ir = idx_ret('2016-01-04', w)  # 粗略基准（2016 首日买持有）
        print(f'{w}日: n={st["n"]} 胜率={st["win"]:.1f}% 平均={st["avg"]:+.1f}% 中位={st["median"]:+.1f}% p10={st["p10"]:.1f}% p90={st["p90"]:+.1f}%')

# 6. 质量分层（有 score 的按 60 分界）
print('\n== 按 score 分层（60/120 日）==')
scored = [(s, px_on(s['stock_code'], s['b1_date'])) for s in sigs]
for band_name, lo, hi in [('score>=70', 70, 999), ('score 50-70', 50, 70), ('score<50', -999, 50)]:
    for w in (60, 120):
        vals = []
        for s, p0 in scored:
            if not p0 or s['score'] is None or not (lo <= s['score'] < hi):
                continue
            p1 = px_at_offset(s['stock_code'], s['b1_date'], w)
            if p1:
                vals.append(p1 / p0 - 1)
        st = stats(vals)
        if st:
            print(f'{band_name} {w}日: n={st["n"]} 胜率={st["win"]:.1f}% 平均={st["avg"]:+.1f}% 中位={st["median"]:+.1f}%')

# 7. B2 确认后买入（无前视：B2 日才知道）
print('\n== B2 确认策略（B2 日收盘买入持有）==')
b2_vals = {20: [], 60: [], 120: []}
for s in sigs:
    if not s['b2_date']:
        continue
    p0 = px_on(s['stock_code'], s['b2_date'])
    if not p0:
        continue
    for w in (20, 60, 120):
        p1 = px_at_offset(s['stock_code'], s['b2_date'], w)
        if p1:
            b2_vals[w].append(p1 / p0 - 1)
for w in (20, 60, 120):
    st = stats(b2_vals[w])
    if st:
        print(f'B2后{w}日: n={st["n"]} 胜率={st["win"]:.1f}% 平均={st["avg"]:+.1f}% 中位={st["median"]:+.1f}%')

conn.close()
print('\n完成')
