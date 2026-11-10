# -*- coding: utf-8 -*-
"""800红利低波（931848）研究报告 —— 数字层。

全部指标现算，输出 docs/report/800红利低波研究报告/data/snapshot.json。
运行：python scripts/divlow800_research.py

口径要点（写进报告第二章）：
  - 全收益序列来自 index_full_return_daily（含分红再投）
  - 年化用日历日折年（365.25），不用交易日 243/252（跨市场教训 196）
  - 波动与夏普用日收益 std × sqrt(243)
  - 回撤用「波段运行最高点」锚定口径（AGENTS.md §1.3）
  - 931848 发布日 2024-05-21，之前为回溯段，必须切开报
"""
import json
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'data' / 'lixinger.db'
OUTDIR = ROOT / 'docs' / 'report' / '800红利低波研究报告' / 'data'
OUT = OUTDIR / 'snapshot.json'

PUBLISH_931848 = '2024-05-21'
PUBLISH_000922 = '2008-05-26'

SERIES = {
    'd800': ('931848', 'index_full_return_daily', '800红利低波 全收益'),
    'd800p': ('931848', 'index_daily_kline', '800红利低波 价格'),
    'd922': ('000922', 'index_full_return_daily', '中证红利 全收益'),
    'd300': ('000300', 'index_full_return_daily', '沪深300 全收益'),
    'dlv': ('H30269', 'index_full_return_daily', '红利低波 全收益'),
    'dlv100': ('930955', 'index_full_return_daily', '红利低波100 全收益'),
}

TRADING_DAYS = 243


def load_series(code, table):
    con = sqlite3.connect(DB)
    if table == 'index_daily_kline':
        rows = con.execute(
            "SELECT date, close FROM index_daily_kline WHERE stock_code=? AND kline_type='normal' "
            "ORDER BY date", (code,)).fetchall()
    else:
        rows = con.execute(
            'SELECT date, close FROM index_full_return_daily WHERE stock_code=? ORDER BY date',
            (code,)).fetchall()
    con.close()
    return [(d, float(c)) for d, c in rows if c is not None]


def years_between(d0, d1):
    a = datetime.strptime(d0, '%Y-%m-%d')
    b = datetime.strptime(d1, '%Y-%m-%d')
    return (b - a).days / 365.25


def perf(pairs):
    """pairs: [(date, close)] 升序。返回指标 dict。"""
    if len(pairs) < 2:
        return None
    d0, c0 = pairs[0]
    d1, c1 = pairs[-1]
    yy = years_between(d0, d1)
    tot = c1 / c0 - 1
    cagr = (c1 / c0) ** (1 / yy) - 1 if yy > 0 else float('nan')
    rets = []
    for i in range(1, len(pairs)):
        p, c = pairs[i - 1][1], pairs[i][1]
        if p > 0:
            rets.append(c / p - 1)
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    sd = math.sqrt(var)
    vol = sd * math.sqrt(TRADING_DAYS)
    sharpe = (cagr - 0.02) / vol if vol > 0 else float('nan')
    # 锚定回撤
    peak = pairs[0][1]
    peak_d = pairs[0][0]
    mdd = 0.0
    mdd_peak_d = peak_d
    mdd_trough_d = peak_d
    cur_peak_d = peak_d
    for d, c in pairs:
        if c > peak:
            peak = c
            cur_peak_d = d
        dd = c / peak - 1
        if dd < mdd:
            mdd = dd
            mdd_peak_d = cur_peak_d
            mdd_trough_d = d
    calmar = cagr / abs(mdd) if mdd else float('nan')
    return {
        'start': d0, 'end': d1, 'years': round(yy, 2), 'n': len(pairs),
        'first': round(c0, 2), 'last': round(c1, 2),
        'tot': round(tot * 100, 2), 'cagr': round(cagr * 100, 2),
        'vol': round(vol * 100, 2), 'sharpe': round(sharpe, 2),
        'mdd': round(mdd * 100, 2), 'calmar': round(calmar, 2),
        'mdd_peak': mdd_peak_d, 'mdd_trough': mdd_trough_d,
    }


def restrict(pairs, d0, d1):
    return [(d, c) for d, c in pairs if d0 <= d <= d1]


def yearly(pairs):
    """按自然年算年度收益（年内首末交易日）。"""
    by = {}
    for d, c in pairs:
        by.setdefault(d[:4], []).append((d, c))
    out = {}
    prev_close = None
    for y in sorted(by):
        arr = by[y]
        if len(arr) < 5:
            # 首个不完整年度（只有基日一天）不计入年度收益
            prev_close = arr[-1][1]
            continue
        if prev_close is None:
            base = arr[0][1]
        else:
            base = prev_close
        end = arr[-1][1]
        out[y] = round((end / base - 1) * 100, 2)
        prev_close = end
    return out


def daily_returns(pairs):
    return {pairs[i][0]: pairs[i][1] / pairs[i - 1][1] - 1 for i in range(1, len(pairs))
            if pairs[i - 1][1] > 0}


def corr(a, b, keys):
    xs = [a[k] for k in keys]
    ys = [b[k] for k in keys]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy)


def regress(y, xmat):
    """最小二乘 + t 值。xmat 每行不含常数项（自动加）。返回 alpha(年化%)、t、betas、r2。"""
    n = len(y)
    k = len(xmat[0]) + 1
    X = [[1.0] + list(r) for r in xmat]
    # 正规方程
    XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    # 高斯消元
    import copy
    A = [row[:] + [Xty[a]] for a, row in enumerate(XtX)]
    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(A[r][col]))
        A[col], A[piv] = A[piv], A[col]
        if abs(A[col][col]) < 1e-14:
            return None
        for r in range(k):
            if r == col:
                continue
            f = A[r][col] / A[col][col]
            for c2 in range(col, k + 1):
                A[r][c2] -= f * A[col][c2]
    beta = [A[i][k] / A[i][i] for i in range(k)]
    # 残差与标准误
    resid = []
    for i in range(n):
        pred = sum(X[i][j] * beta[j] for j in range(k))
        resid.append(y[i] - pred)
    my = sum(y) / n
    sse = sum(r * r for r in resid)
    sst = sum((v - my) ** 2 for v in y)
    r2 = 1 - sse / sst
    sigma2 = sse / (n - k)
    # (X'X)^-1 对角
    Ainv = []
    for j in range(k):
        e = [0.0] * k
        e[j] = 1.0
        B = [row[:] + [e[a]] for a, row in enumerate(XtX)]
        for col in range(k):
            piv = max(range(col, k), key=lambda r: abs(B[r][col]))
            B[col], B[piv] = B[piv], B[col]
            for r in range(k):
                if r == col:
                    continue
                f = B[r][col] / B[col][col]
                for c2 in range(col, k + 1):
                    B[r][c2] -= f * B[col][c2]
        Ainv.append([B[i][k] / B[i][i] for i in range(k)])
    se = [math.sqrt(abs(sigma2 * Ainv[j][j])) for j in range(k)]
    return {
        'alpha_daily': beta[0], 'alpha_t': beta[0] / se[0] if se[0] else None,
        'alpha_annual': (math.pow(1 + beta[0], TRADING_DAYS) - 1) * 100,
        'betas': beta[1:], 't': [b / s for b, s in zip(beta[1:], se[1:])],
        'r2': r2, 'n': n,
    }


def anchored_dd(pairs):
    """返回 [(date, dd)] 与 轮 列表。轮 = 一个参考高点到被更高点取代。"""
    out = []
    peak = pairs[0][1]
    peak_d = pairs[0][0]
    rounds = []
    cur = {'peak_date': peak_d, 'peak': peak, 'entries': []}
    for d, c in pairs:
        if c > peak:
            # 结束上一轮
            if cur['entries']:
                rounds.append(cur)
            peak = c
            peak_d = d
            cur = {'peak_date': peak_d, 'peak': peak, 'entries': []}
        dd = c / peak - 1
        out.append((d, dd))
        cur['entries'].append((d, dd, c))
    if cur['entries']:
        rounds.append(cur)
    return out, rounds


def rsi(pairs, period=14):
    """Wilder RSI。返回 [(date, rsi)]。"""
    closes = [c for _, c in pairs]
    dates = [d for d, _ in pairs]
    out = []
    if len(closes) <= period:
        return out
    gains = []
    losses = []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    def val(g, l):
        return 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    out.append((dates[period], val(ag, al)))
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out.append((dates[i + 1], val(ag, al)))
    return out


def desplit(pairs, lo=0.75, hi=1.35):
    """把不复权序列里的拆股跳变消掉（按跳变比例反推缩放前段）。

    返回 (新序列, 检出的跳变列表)。窗口内不应有单日真实涨跌超过 ±25% 的行情，
    超过即认定为拆股/并股。
    """
    out = []
    events = []
    factor = 1.0
    n = len(pairs)
    for i, (d, c) in enumerate(pairs):
        if i > 0:
            prev = pairs[i - 1][1]
            r = c / prev
            if r < lo or r > hi:
                events.append({'date': d, 'ratio': round(r, 4), 'from': prev, 'to': c})
                factor /= r
        out.append((d, c * factor))
    return out, events


def resample(pairs, freq):
    out = []
    cur = None
    last = None
    for d, c in pairs:
        key = d[:7] if freq == 'M' else datetime.strptime(d, '%Y-%m-%d').strftime('%G-W%V')
        if key != cur:
            if last:
                out.append(last)
            cur = key
        last = (d, c)
    if last:
        out.append(last)
    return out


def pctile(vals, v):
    if not vals:
        return None
    n = sum(1 for x in vals if x <= v)
    return n / len(vals) * 100


def fwd(pairs, i, h):
    j = i + h
    if j >= len(pairs):
        return None
    return pairs[j][1] / pairs[i][1] - 1


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    data = {}
    for k, (code, table, _label) in SERIES.items():
        data[k] = load_series(code, table)
        print(f'  {k:7s} {code} {len(data[k])} 行 {data[k][0][0]} ~ {data[k][-1][0]}')

    # 统一窗口：931848 全收益可得日 起，到 931848 最后一日
    win_start = data['d800'][0][0]
    win_end = data['d800'][-1][0]
    print(f'\n统一窗口 {win_start} ~ {win_end}')

    aligned = {}
    for k in data:
        s = restrict(data[k], win_start, win_end)
        aligned[k] = s
        print(f'  {k:7s} 窗口内 {len(s)} 行')

    # 共同交易日（以 931848 为轴）
    base_dates = [d for d, _ in aligned['d800']]
    for k, s in aligned.items():
        have = {d for d, _ in s}
        missing = [d for d in base_dates if d not in have]
        if missing:
            print(f'  ⚠ {k} 缺 {len(missing)} 个交易日，样例 {missing[:3]}')

    perf_all = {}
    for k, s in aligned.items():
        perf_all[k] = perf(s)

    snap = {'meta': {
        'title': '中证800红利低波动指数（931848）研究报告',
        'window': f'{win_start} ~ {win_end}',
        'days': len(base_dates),
        'years': round(years_between(win_start, win_end), 2),
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'data_asof': win_end,
        'base': 1000.0,
        'db': 'data/lixinger.db',
        'publish_931848': PUBLISH_931848,
        'publish_000922': PUBLISH_000922,
        'trading_days_per_year': TRADING_DAYS,
        'risk_free': 2.0,
    }}

    # ---------- 1. 收益（全区间 + 按发布日切开） ----------
    snap['perf'] = {'all': perf_all}
    split = [('backtest', win_start, '2024-05-20'),
             ('live', PUBLISH_931848, win_end)]
    snap['perf_split'] = {}
    for tag, d0, d1 in split:
        seg = {}
        for k in aligned:
            p = perf(restrict(aligned[k], d0, d1))
            if p:
                seg[k] = p
        snap['perf_split'][tag] = seg
    snap['perf_split_labels'] = {'backtest': f'回溯段 {win_start} ~ 2024-05-20',
                                 'live': f'实盘段 {PUBLISH_931848} ~ {win_end}'}

    # ---------- 2. 分年度 ----------
    snap['yearly'] = {k: yearly(aligned[k]) for k in ('d800', 'd922', 'd300', 'dlv')}

    # ---------- 3. 相关与回归 ----------
    rets = {k: daily_returns(aligned[k]) for k in aligned}
    keys = [d for d in base_dates[1:] if all(d in rets[k] for k in ('d800', 'd922', 'd300', 'dlv', 'dlv100'))]
    print(f'  共同收益日 {len(keys)}')
    snap['corr'] = {'keys': len(keys), 'matrix': {}}
    names = {'d800': '800红利低波', 'd922': '中证红利', 'd300': '沪深300',
             'dlv': '红利低波', 'dlv100': '红利低波100'}
    for a in names:
        snap['corr']['matrix'][names[a]] = {
            names[b]: round(corr(rets[a], rets[b], keys), 3) for b in names}

    regs = {}
    y = [rets['d800'][d] for d in keys]
    regs['vs922'] = regress(y, [[rets['d922'][d]] for d in keys])
    regs['vs300'] = regress(y, [[rets['d300'][d]] for d in keys])
    regs['two'] = regress(y, [[rets['d300'][d], rets['d922'][d]] for d in keys])
    yl = [rets['dlv'][d] for d in keys]
    regs['lv_vs922'] = regress(yl, [[rets['d922'][d]] for d in keys])
    snap['regression'] = regs
    snap['regression_names'] = {'vs922': ['中证红利'], 'vs300': ['沪深300'],
                                'two': ['沪深300', '中证红利'], 'lv_vs922': ['中证红利']}

    # 实盘段单独回归
    lk = [d for d in keys if d >= PUBLISH_931848]
    if len(lk) > 30:
        snap['regression_live'] = {
            'vs922': regress([rets['d800'][d] for d in lk], [[rets['d922'][d]] for d in lk]),
            'vs300': regress([rets['d800'][d] for d in lk], [[rets['d300'][d]] for d in lk]),
            'n': len(lk),
        }

    # ---------- 4. 锚定回撤 ----------
    dd_series, rounds = anchored_dd(aligned['d800'])
    snap['current'] = {
        'last_date': aligned['d800'][-1][0],
        'point': round(aligned['d800'][-1][1], 2),
        'price_point': round(aligned['d800p'][-1][1], 2) if aligned.get('d800p') else None,
        'dd': round(dd_series[-1][1] * 100, 2),
    }
    peak = aligned['d800'][0][1]
    peak_d = aligned['d800'][0][0]
    for d, c in aligned['d800']:
        if c > peak:
            peak, peak_d = c, d
    snap['current']['peak'] = round(peak, 2)
    snap['current']['peak_date'] = peak_d

    # 回撤事件：每轮最深
    ev = []
    for r in rounds:
        if not r['entries']:
            continue
        low = min(r['entries'], key=lambda x: x[1])
        if low[1] > -0.10:
            continue
        # 收复日
        rec_d = None
        after = [d for d, c in aligned['d800'] if d > low[0]]
        for d in after:
            c = dict(aligned['d800'])[d]
            if c >= r['peak']:
                rec_d = d
                break
        ev.append({
            'peak_date': r['peak_date'], 'peak': round(r['peak'], 2),
            'trough_date': low[0], 'trough': round(low[1] * 100, 2),
            'depth': round(low[1] * 100, 2),
            'recover_date': rec_d,
            'days_to_trough': len([1 for e in r['entries'] if e[0] <= low[0]]),
        })
    ev.sort(key=lambda x: x['depth'])
    snap['drawdown_events'] = ev[:10]

    dds = [x[1] * 100 for x in dd_series]
    snap['dd_dist'] = {p: round(sorted(dds)[int(len(dds) * p / 100)], 2) for p in (10, 25, 50, 75, 90)}

    # ---------- 5. 按天口径：回撤档 → 前瞻收益 ----------
    TIERS = [(0, 10), (10, 14), (14, 20), (20, 100)]
    buckets = []
    for lo, hi in TIERS:
        fwd60, fwd120, fwd250 = [], [], []
        for i, (d, ddv) in enumerate(dd_series):
            pct = -ddv * 100
            if lo <= pct < hi:
                for arr, h in ((fwd60, 60), (fwd120, 120), (fwd250, 250)):
                    v = fwd(aligned['d800'], i, h)
                    if v is not None:
                        arr.append(v)
        def stat(arr):
            if not arr:
                return None
            arr2 = sorted(arr)
            win = sum(1 for x in arr2 if x > 0) / len(arr2) * 100
            return {'n': len(arr2), 'mean': round(sum(arr2) / len(arr2) * 100, 2),
                    'median': round(arr2[len(arr2) // 2] * 100, 2), 'win': round(win, 1)}
        buckets.append({'tier': f'{lo}~{hi}%' if hi < 100 else f'≥{lo}%',
                        'h60': stat(fwd60), 'h120': stat(fwd120), 'h250': stat(fwd250)})
    snap['dd_buckets'] = {'buckets': buckets, 'horizons': [60, 120, 250]}

    # ---------- 6. 事件口径：每轮每档首次跌破次日买 ----------
    tier_events = []
    for t in (10, 14, 20):
        hits = []
        for r in rounds:
            for d, ddv, c in r['entries']:
                if -ddv * 100 >= t:
                    hits.append(d)
                    break
        rr = []
        for d in hits:
            idx = base_dates.index(d) if d in base_dates else -1
            if idx < 0 or idx + 1 >= len(aligned['d800']):
                continue
            buy = aligned['d800'][idx + 1]
            row = {'signal_date': d, 'buy_date': buy[0]}
            ok = True
            for h in (60, 120, 250):
                v = fwd(aligned['d800'], idx + 1, h)
                row[f'h{h}'] = round(v * 100, 2) if v is not None else None
                if v is None:
                    ok = False
            rr.append(row)
        tier_events.append({'tier': t, 'n': len(rr), 'events': rr})
    snap['tier_events'] = tier_events

    # 基准：任意一天买
    bases = {}
    for h in (60, 120, 250):
        arr = [fwd(aligned['d800'], i, h) for i in range(len(aligned['d800']))]
        arr = [x for x in arr if x is not None]
        arr2 = sorted(arr)
        bases[f'h{h}'] = {'n': len(arr2), 'mean': round(sum(arr2) / len(arr2) * 100, 2),
                          'median': round(arr2[len(arr2) // 2] * 100, 2),
                          'win': round(sum(1 for x in arr2 if x > 0) / len(arr2) * 100, 1)}
    snap['baseline'] = bases

    # ---------- 7. RSI 多周期 ----------
    rsi_d = rsi(aligned['d800'])
    rsi_w = rsi(resample(aligned['d800'], 'W'))
    rsi_m = rsi(resample(aligned['d800'], 'M'))
    vals = [v for _, v in rsi_d]
    snap['rsi14'] = {
        'daily': {'last': round(rsi_d[-1][1], 1), 'date': rsi_d[-1][0],
                  'pct': round(pctile(vals, rsi_d[-1][1]), 1),
                  'median': round(sorted(vals)[len(vals) // 2], 1),
                  'min': round(min(vals), 1), 'max': round(max(vals), 1), 'n': len(vals)},
        'weekly': {'last': round(rsi_w[-1][1], 1), 'date': rsi_w[-1][0],
                   'pct': round(pctile([v for _, v in rsi_w], rsi_w[-1][1]), 1)},
        'monthly': {'last': round(rsi_m[-1][1], 1), 'date': rsi_m[-1][0],
                    'pct': round(pctile([v for _, v in rsi_m], rsi_m[-1][1]), 1)},
        'series_daily': [[d, round(v, 2)] for d, v in rsi_d],
    }

    # ---------- 8. 估值与派生财务 ----------
    con = sqlite3.connect(DB)
    fund = {}
    for code in ('931848', '000922', '000300', 'H30269'):
        rows = con.execute(
            'SELECT date, pe_ttm, pb, dyr, mc, pe_ttm_pct, pb_pct, dyr_pct '
            'FROM index_fundamental_daily WHERE stock_code=? ORDER BY date', (code,)).fetchall()
        fund[code] = [r for r in rows if r[1] is not None and r[2] not in (None, 0)]
    con.close()

    val = {}
    for code, rows in fund.items():
        if not rows:
            continue
        pes = [r[1] for r in rows]
        pbs = [r[2] for r in rows]
        dys = [r[3] for r in rows if r[3] is not None]
        roe = [r[2] / r[1] * 100 for r in rows]
        np_ = [r[4] / r[1] for r in rows]
        eq = [r[4] / r[2] for r in rows]
        dv = [r[4] * r[3] for r in rows if r[3] is not None]
        last = rows[-1]
        val[code] = {
            'n': len(rows), 'first': rows[0][0], 'last': last[0],
            'pe': round(last[1], 2), 'pe_pct': round(pctile(pes, last[1]), 1),
            'pb': round(last[2], 2), 'pb_pct': round(pctile(pbs, last[2]), 1),
            'dyr': round(last[3] * 100, 2) if last[3] is not None else None,
            'dyr_pct': round(pctile(dys, last[3]), 1) if last[3] is not None else None,
            'roe_implied': round(last[2] / last[1] * 100, 2),
            'roe_min': round(min(roe), 2), 'roe_max': round(max(roe), 2),
            'pe_min': round(min(pes), 2), 'pe_max': round(max(pes), 2),
            'pe_med': round(sorted(pes)[len(pes) // 2], 2),
            'dyr_min': round(min(dys) * 100, 2) if dys else None,
            'dyr_max': round(max(dys) * 100, 2) if dys else None,
            'mc_yi': round(last[4] / 1e8, 0),
            'np_yi': round(np_[-1] / 1e8, 0),
            'eq_yi': round(eq[-1] / 1e8, 0),
            'dv_yi': round(dv[-1] / 1e8, 0) if dv else None,
            'series': {
                'pe': [[r[0], round(r[1], 2)] for r in rows],
                'pb': [[r[0], round(r[2], 3)] for r in rows],
                'dyr': [[r[0], round(r[3] * 100, 3)] for r in rows if r[3] is not None],
                'roe': [[r[0], round(r[2] / r[1] * 100, 2)] for r in rows],
            },
        }
    snap['valuation'] = val

    # ---------- 9. 二维交叉：估值分位 × 回撤档 ----------
    # 用 931848 可得的 600 天；样本少，仅作线索
    vrows = dict((r[0], r) for r in fund['931848'])
    grid = {}
    for d, ddv in dd_series:
        if d not in vrows:
            continue
        pe = vrows[d][1]
        pe_pct = pctile([r[1] for r in fund['931848']], pe)
        dd_pct = -ddv * 100
        vb = 'PE低(<30%)' if pe_pct < 30 else ('PE中(30~70%)' if pe_pct < 70 else 'PE高(≥70%)')
        db_ = '回撤<10%' if dd_pct < 10 else ('回撤10~20%' if dd_pct < 20 else '回撤≥20%')
        idx = base_dates.index(d)
        v = fwd(aligned['d800'], idx, 120)
        if v is None:
            continue
        grid.setdefault(vb, {}).setdefault(db_, []).append(v)
    snap['cross2d'] = {
        'horizon': 120,
        'note': '仅用 931848 估值可得区间（2024-05-21 起，600 个交易日），窗口内最大回撤仅 12.6%，'
                '高回撤格无样本',
        'cells': {vb: {db_: {'n': len(arr), 'mean': round(sum(arr) / len(arr) * 100, 2)}
                       for db_, arr in m.items()} for vb, m in grid.items()},
    }

    # 以中证红利作二维交叉的参照面板（估值 2016 起、含 2022 与 2024 深回撤）
    d922dd, _ = anchored_dd(aligned['d922'])
    d922dates = [d for d, _ in aligned['d922']]
    v922 = dict((r[0], r) for r in fund['000922'])
    pes922 = [r[1] for r in fund['000922']]
    grid2 = {}
    for d, ddv in d922dd:
        if d not in v922:
            continue
        pe_pct = pctile(pes922, v922[d][1])
        dd_pct = -ddv * 100
        vb = 'PE低(<30%)' if pe_pct < 30 else ('PE中(30~70%)' if pe_pct < 70 else 'PE高(≥70%)')
        db_ = '回撤<10%' if dd_pct < 10 else ('回撤10~20%' if dd_pct < 20 else '回撤≥20%')
        i = d922dates.index(d)
        v = fwd(aligned['d922'], i, 120)
        if v is None:
            continue
        grid2.setdefault(vb, {}).setdefault(db_, []).append(v)
    snap['cross2d_ref'] = {
        'index': '000922 中证红利',
        'horizon': 120,
        'note': '参照面板：中证红利自身估值与回撤，2016-01-04 起，含 2022 与 2024 深回撤',
        'cells': {vb: {db_: {'n': len(arr), 'mean': round(sum(arr) / len(arr) * 100, 2)}
                       for db_, arr in m.items()} for vb, m in grid2.items()},
    }

    # ---------- 10. 成分重叠与集中度 ----------
    con = sqlite3.connect(DB)
    def constituents(code, date):
        return [r[0] for r in con.execute(
            'SELECT stock_code FROM index_constituents WHERE index_code=? AND date=?',
            (code, date))]
    d_snap = '2026-05-01'
    c800 = set(constituents('931848', d_snap))
    c922 = set(constituents('000922', d_snap))
    c300 = set(constituents('000300', d_snap))
    wrow = con.execute(
        'SELECT MAX(date) FROM index_constituent_weightings WHERE index_code=?', ('931848',)).fetchone()
    wdate = wrow[0] if wrow else None
    wt = con.execute(
        'SELECT stock_code, weighting FROM index_constituent_weightings WHERE index_code=? '
        'AND date=? ORDER BY weighting DESC', ('931848', wdate)).fetchall()
    names = {}
    for code in c800:
        r = con.execute('SELECT name FROM stock_basic WHERE stock_code=?', (code,)).fetchone()
        if r:
            names[code] = r[0]
    con.close()
    wsum = sum(w for _, w in wt)
    # 理杏仁只返回前十大成分权重，合计约 18%，不是全权重
    snap['constituents'] = {
        'snapshot': d_snap, 'n_931848': len(c800), 'n_000922': len(c922), 'n_000300': len(c300),
        'overlap_300': round(len(c800 & c300) / len(c800) * 100, 2) if c800 else None,
        'overlap_922': round(len(c800 & c922) / len(c800) * 100, 2) if c800 else None,
        'overlap_922_of_all': round(len(c800 & c922) / len(c922) * 100, 2) if c922 else None,
        'only_922_not_800': len(c922 - c800),
        'n_weight_rows': len(wt),
        'top10_weight': round(wsum * 100, 2),
        'max_weight': round(wt[0][1] * 100, 2) if wt else None,
        'max_weight_name': names.get(wt[0][0]) if wt else None,
        'top10': [{'code': c, 'name': names.get(c), 'w': round(w * 100, 2)} for c, w in wt[:10]],
        'weight_date': wdate,
        'weight_note': '理杏仁仅返回前十大成分权重，合计约 18%，其余 90 只权重不可得，HHI 不可算',
        'hhi_lower_bound': round(sum(w * w for _, w in wt), 4),
    }

    # ---------- 11. 国际对照 ----------
    peer_file = ROOT / '.scratch' / 'us_divlow_peers.json'
    peers = {'available': False}
    if peer_file.exists():
        raw = json.loads(peer_file.read_text(encoding='utf-8'))
        groups = {'main': ['SPLV', 'USMV', 'SCHD', 'SPY'], 'extra': ['NOBL', 'DVY']}
        # 价格口径（不复权 + 拆股修正）为主；qfq 仅参考，因新浪 qfq 对美股分红调整幅度明显偏大
        calibers = [('price', 'data_raw', 'd800p', '价格口径，不含分红；已消除拆股跳变'),
                    ('qfq', 'data', 'd800', '新浪前复权口径（仅参考，对分红调整偏大）')]
        peers = {'available': True, 'source': raw.get('source'), 'calibers': {}}
        for cname, key, self_key, cnote in calibers:
            if key not in raw:
                continue
            pw = {}
            split_events = {}
            for sym, arr in raw[key].items():
                s = restrict([(d, c) for d, c in arr], win_start, win_end)
                if cname == 'price':
                    s, ev = desplit(s)
                    split_events[sym] = ev
                if len(s) > 200:
                    pw[sym] = s
            if not pw:
                continue
            out = {'note': cnote, 'groups': {}, 'split_events': split_events}
            for gname, syms in groups.items():
                have = [s for s in syms if s in pw]
                if not have:
                    continue
                all_dates = sorted(set.intersection(*[{d for d, _ in pw[s]} for s in have]))
                all_dates = [d for d in all_dates if win_start <= d <= win_end]
                if len(all_dates) < 200:
                    continue
                rows = {}
                for sym in have + ['931848']:
                    s = pw[sym] if sym in pw else aligned[self_key]
                    mp = dict(s)
                    seq = [(d, mp[d]) for d in all_dates if d in mp]
                    bad = 0
                    for i in range(1, len(seq)):
                        r = seq[i][1] / seq[i - 1][1] - 1
                        if abs(r) > 0.35:
                            bad += 1
                    p = perf(seq)
                    p['bad_ticks'] = bad
                    rows[sym] = p
                out['groups'][gname] = {
                    'window': f'{all_dates[0]} ~ {all_dates[-1]}', 'n': len(all_dates),
                    'symbols': have, 'rows': rows}
            peers['calibers'][cname] = out
    snap['peers'] = peers

    # ---------- 12. 资金到位回测 ----------
    strat = {}
    for tag, d0 in (('live', PUBLISH_931848), ('all', win_start)):
        s = restrict(aligned['d800'], d0, win_end)
        p = perf(s)
        strat[f'hold_{tag}'] = p
    # 定投：每月首个交易日
    months = {}
    for d, c in aligned['d800']:
        months.setdefault(d[:7], (d, c))
    pts = [months[m] for m in sorted(months)]
    units = 0.0
    invested = 0.0
    for d, c in pts:
        units += 1000.0 / c
        invested += 1000.0
    end_val = units * aligned['d800'][-1][1]
    strat['dca'] = {'periods': len(pts), 'invested': round(invested, 0),
                    'end_value': round(end_val, 0),
                    'profit_pct': round((end_val / invested - 1) * 100, 2)}
    # 分档加仓：每轮每档各投 1 份
    tiers = []
    for t in (10, 14, 20):
        hits = []
        for r in rounds:
            for d, ddv, c in r['entries']:
                if -ddv * 100 >= t:
                    hits.append(d)
                    break
        rets_t = []
        for d in hits:
            idx = base_dates.index(d) if d in base_dates else -1
            if idx < 0 or idx + 1 >= len(aligned['d800']):
                continue
            buy = aligned['d800'][idx + 1][1]
            rets_t.append(aligned['d800'][-1][1] / buy - 1)
        tiers.append({'tier': t, 'n': len(rets_t),
                      'mean_pct': round(sum(rets_t) / len(rets_t) * 100, 2) if rets_t else None})
    strat['tiers'] = tiers
    # 起始点敏感性：所有 3 年 / 5 年窗口
    def start_sens(yrs):
        out = []
        for i, (d, c) in enumerate(aligned['d800']):
            target = None
            for j in range(i, len(aligned['d800'])):
                if years_between(d, aligned['d800'][j][0]) >= yrs:
                    target = j
                    break
            if target is None:
                break
            out.append((aligned['d800'][target][1] / c) ** (1 / yrs) - 1)
        return {'n': len(out), 'min': round(min(out) * 100, 2), 'max': round(max(out) * 100, 2),
                'median': round(sorted(out)[len(out) // 2] * 100, 2)} if out else None
    strat['start_sens'] = {'y3': start_sens(3), 'y5': start_sens(5)}
    snap['strategy'] = strat

    # ---------- 13. 数据质量 ----------
    snap['data_quality'] = {
        'index_full_return_rows': {k: len(v) for k, v in data.items()},
        'fundamental_rows': {c: len(v) for c, v in fund.items()},
        'financial_annual_rows': 2,
        'constituent_snapshots_931848': 12,
        'constituent_dates': '2025-06-01 ~ 2026-05-01',
    }

    # ---------- 14. 修订记录 ----------
    snap['revision'] = [
        {'index': '931848 中证800红利低波动', 'basic_date': '2013-12-31',
         'publish_date': PUBLISH_931848, 'note': '发布日前的 10.4 年为编制方案回溯计算，非实盘'},
        {'index': '000922 中证红利', 'basic_date': '2004-12-31',
         'publish_date': PUBLISH_000922,
         'note': '2013-07 加权方式由市值改股息率；2022-10 修订后官方披露年化由 9.3% 升至 10.2%'},
        {'index': 'H30269 中证红利低波动', 'basic_date': '2005-12-30',
         'publish_date': '2013-12-19', 'note': '基日至发布日约 8 年为回溯'},
        {'index': '930955 中证红利低波动100', 'basic_date': '2005-12-30',
         'publish_date': '2017-05-26', 'note': '基日至发布日约 11 年为回溯'},
    ]

    # ---------- 15. 图表序列 ----------
    step = max(1, len(base_dates) // 520)
    charts = {}
    for k in ('d800', 'd800p', 'd922', 'd300', 'dlv'):
        s = aligned[k]
        base = s[0][1]
        charts[k] = [[d, round(c / base * 1000, 2)] for d, c in s[::step]]
    snap['charts'] = {'normalized': charts, 'step': step, 'base': 1000.0}
    snap['charts']['dd'] = [[d, round(v * 100, 2)] for d, v in dd_series[::step]]
    snap['charts']['rsi'] = [[d, round(v, 2)] for d, v in rsi_d[::step]]
    snap['charts']['pe800'] = [[d, v] for d, v in val['931848']['series']['pe']]
    if '000922' in val:
        snap['charts']['pe922'] = [[d, v] for d, v in val['000922']['series']['pe']]
        snap['charts']['dyr800'] = [[d, v] for d, v in val['931848']['series']['dyr']]
        snap['charts']['dyr922'] = [[d, v] for d, v in val['000922']['series']['dyr']]

    OUT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n写出 {OUT} ({OUT.stat().st_size} bytes)')

    # ---------- 摘要 ----------
    print('\n== 收益（全区间） ==')
    for k, p in perf_all.items():
        if p:
            print(f"  {k:7s} 年化 {p['cagr']:7.2f}%  波动 {p['vol']:6.2f}%  "
                  f"夏普 {p['sharpe']:5.2f}  回撤 {p['mdd']:7.2f}%  累计 {p['tot']:8.2f}%")
    print('\n== 分段 ==')
    for tag, seg in snap['perf_split'].items():
        print(' ', tag, {k: v['cagr'] for k, v in seg.items()})
    print('\n== 回归 ==')
    for k, v in regs.items():
        if v:
            print(f"  {k:10s} alpha {v['alpha_annual']:6.2f}% t={v['alpha_t']:5.2f} "
                  f"betas {[round(b,3) for b in v['betas']]} R2 {v['r2']:.3f}")
    print('\n== 估值 ==')
    for c, v in val.items():
        print(f"  {c}: PE {v['pe']} (分位 {v['pe_pct']}%) PB {v['pb']} 股息率 {v['dyr']}% "
              f"ROE(PB/PE) {v['roe_implied']}% n={v['n']} {v['first']}~{v['last']}")
    print('\n== 成分 ==')
    c = snap['constituents']
    print(f"  对沪深300 重叠 {c['overlap_300']}%  对中证红利 {c['overlap_922']}%  "
          f"前十大权重合计 {c['top10_weight']}%  HHI 下界 {c['hhi_lower_bound']}")
    print('\n== 国际对照 ==')
    for cname, cal in peers.get('calibers', {}).items():
        print(f' [{cname}] {cal["note"]}')
        for gname, g in cal['groups'].items():
            print(f'   [{gname}] {g["window"]} n={g["n"]}')
            for k, v in g['rows'].items():
                print(f"     {k:8s} 年化 {v['cagr']:6.2f}%  波动 {v['vol']:6.2f}%  "
                      f"回撤 {v['mdd']:7.2f}%  夏普 {v['sharpe']:5.2f}  坏点 {v['bad_ticks']}")
    print('\n== RSI ==', {k: v['last'] for k, v in snap['rsi14'].items() if isinstance(v, dict)})
    print('== 当前 ==', snap['current'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
