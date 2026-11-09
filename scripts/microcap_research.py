# -*- coding: utf-8 -*-
"""微盘股指数研究 —— 数字层。

现算全部报告数字，写入 docs/report/微盘股指数研究报告/data/snapshot.json。
呈现层（make_report.py）只读快照，不重算，不手抄。

用法：python scripts/microcap_research.py
"""
import os, sys, json, math, sqlite3, statistics, hashlib
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')
OUTDIR = os.path.join(PROJECT, 'docs', 'report', '微盘股指数研究报告', 'data')
PEER = os.path.join(PROJECT, '.scratch', 'us_microcap_peers.json')

WIN0, WIN1 = '2016-01-04', '2026-11-06'   # 全部指标的统一报告窗口
MF = os.path.join(PROJECT, 'docs', 'report', '微盘股指数研究报告', 'data', 'snapshot.json')


def main():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    def micro(t, tbl='microcap_index_daily'):
        return {d: v for d, v in c.execute(
            'SELECT date,point FROM %s WHERE index_type=?' % tbl, (t,))}

    def tr(code):
        return {d: v for d, v in c.execute(
            'SELECT date,close FROM index_full_return_daily WHERE stock_code=?', (code,))}

    def ext(code):
        return {d: v for d, v in c.execute(
            'SELECT date,close FROM index_ext_daily WHERE stock_code=?', (code,))}

    S = {
        '微盘400(等权·日再平衡)': micro('400'),
        '微盘400(等权·月内漂移)': micro('400', 'microcap_index_daily_drift'),
        '微盘400(市值加权)': micro('400', 'microcap_index_daily_capw'),
        '微盘100(等权·日再平衡)': micro('100'),
        '微盘100(等权·月内漂移)': micro('100', 'microcap_index_daily_drift'),
        '中证2000': ext('932000'),
        '中证1000': ext('000852'),
        '沪深300(全收益)': tr('000300'),
        '中证红利(全收益)': tr('000922'),
        '中证全指(全收益)': tr('000985'),
    }
    SHORT = {
        '微盘400(等权·日再平衡)': '微盘400（日再平衡）',
        '微盘400(等权·月内漂移)': '微盘400（漂移）',
        '微盘400(市值加权)': '微盘400（市值加权）',
        '微盘100(等权·日再平衡)': '微盘100（日再平衡）',
        '微盘100(等权·月内漂移)': '微盘100（漂移）',
    }

    common = set(S['微盘400(等权·日再平衡)'])
    for v in S.values():
        common &= set(v)
    win = sorted(d for d in common if d in common and WIN0 <= d <= WIN1)
    n = len(win)
    yrs = n / 243.0

    def slice_to_win(v):
        return [v[d] for d in win]

    def rets(v):
        x = slice_to_win(v)
        return [x[i] / x[i - 1] - 1 for i in range(1, len(x))]

    def perf(v):
        x = slice_to_win(v)
        tot = x[-1] / x[0] - 1
        cagr = (x[-1] / x[0]) ** (1 / yrs) - 1
        r = rets(v)
        vol = statistics.pstdev(r) * math.sqrt(243)
        mu = statistics.mean(r) * 243
        peak = x[0]; mdd = 0.0
        for q in x:
            peak = max(peak, q)
            mdd = min(mdd, q / peak - 1)
        return dict(tot=round(tot * 100, 2), cagr=round(cagr * 100, 2),
                    vol=round(vol * 100, 2),
                    sharpe=round((mu - 0.015) / vol, 2), mdd=round(mdd * 100, 2),
                    calmar=round(cagr / abs(mdd), 2) if mdd else None)

    SNAP = {}
    SNAP['meta'] = dict(
        title='微盘股指数研究', window=[WIN0, WIN1], days=n, years=round(yrs, 2),
        generated_at=datetime.now().strftime('%Y-%m-%d %H:%M'),
        data_asof=WIN1, base=1000.0,
        db=DB.replace('\\', '/'), db_size_hint='约 180 GB',
    )
    SNAP['perf'] = {SHORT.get(k, k): perf(v) for k, v in S.items()}

    # 分年度
    years = sorted({d[:4] for d in win})
    yt = {}
    for y in years:
        ds = [d for d in win if d[:4] == y]
        row = {}
        for k, v in S.items():
            if ds[0] in v and ds[-1] in v:
                row[SHORT.get(k, k)] = round((v[ds[-1]] / v[ds[0]] - 1) * 100, 2)
        yt[y] = row
    SNAP['yearly'] = yt

    # 相关
    keys = ['微盘400(等权·月内漂移)', '微盘100(等权·月内漂移)', '中证2000', '中证1000',
            '沪深300(全收益)', '中证红利(全收益)']
    R = {k: rets(S[k]) for k in keys}
    corr = {}
    for a in keys:
        corr[SHORT.get(a, a)] = {SHORT.get(b, b): round(statistics.correlation(R[a], R[b]), 3)
                                 for b in keys}
    SNAP['corr'] = corr

    # 回归归因
    def ols(y, xs):
        N = len(y); k = len(xs)
        X = [[1.0] + [xs[j][i] for j in range(k)] for i in range(N)]
        A = [[sum(X[i][a] * X[i][b] for i in range(N)) for b in range(k + 1)] for a in range(k + 1)]
        B = [sum(X[i][a] * y[i] for i in range(N)) for a in range(k + 1)]
        M = [row[:] + [B[i]] for i, row in enumerate(A)]
        for col in range(k + 1):
            piv = max(range(col, k + 1), key=lambda rr: abs(M[rr][col]))
            M[col], M[piv] = M[piv], M[col]
            dv = M[col][col]
            M[col] = [x / dv for x in M[col]]
            for rr in range(k + 1):
                if rr != col and M[rr][col]:
                    f = M[rr][col]
                    M[rr] = [M[rr][j] - f * M[col][j] for j in range(k + 2)]
        coef = [M[i][k + 1] for i in range(k + 1)]
        resid = [y[i] - sum(coef[j] * X[i][j] for j in range(k + 1)) for i in range(N)]
        sse = sum(q * q for q in resid)
        ym = statistics.mean(y)
        sst = sum((q - ym) ** 2 for q in y)
        r2 = 1 - sse / sst
        s2 = sse / (N - k - 1)
        se = []
        for j in range(k + 1):
            e = [0.0] * (k + 1); e[j] = 1.0
            MM = [row[:k + 1] + [e[i]] for i, row in enumerate(A)]
            for col in range(k + 1):
                piv = max(range(col, k + 1), key=lambda rr: abs(MM[rr][col]))
                MM[col], MM[piv] = MM[piv], MM[col]
                dv = MM[col][col]
                MM[col] = [x / dv for x in MM[col]]
                for rr in range(k + 1):
                    if rr != col and MM[rr][col]:
                        f = MM[rr][col]
                        MM[rr] = [MM[rr][q] - f * MM[col][q] for q in range(k + 2)]
            se.append(math.sqrt(s2 * MM[j][k + 1]))
        return coef, se, r2, N

    regs = []
    for label, y, xs, xl in (
            ('微盘400（等权）对中证2000', R['微盘400(等权·月内漂移)'], [R['中证2000']], ['中证2000']),
            ('微盘400（等权）对沪深300', R['微盘400(等权·月内漂移)'], [R['沪深300(全收益)']], ['沪深300']),
            ('微盘400（等权）对沪深300＋中证2000', R['微盘400(等权·月内漂移)'],
             [R['沪深300(全收益)'], R['中证2000']], ['沪深300', '中证2000']),
            ('微盘400（市值加权）对中证2000', rets(S['微盘400(市值加权)']), [R['中证2000']], ['中证2000']),
            ('微盘100（等权）对中证2000', R['微盘100(等权·月内漂移)'], [R['中证2000']], ['中证2000'])):
        coef, se, r2, N = ols(y, xs)
        regs.append(dict(label=label, alpha=round(coef[0] * 243 * 100, 2),
                         t=round(coef[0] / se[0], 2),
                         betas=[dict(name=xl[i], beta=round(coef[i + 1], 3),
                                     t=round(coef[i + 1] / se[i + 1], 2)) for i in range(len(xs))],
                         r2=round(r2, 3)))
    SNAP['regression'] = regs

    # 回撤事件
    v = S['微盘400(等权·月内漂移)']
    ks = sorted(v)
    peak = v[ks[0]]; peakd = ks[0]; low = (None, 1e9); start = None; ev = []
    for d in ks:
        if v[d] >= peak:
            if start:
                ev.append(dict(start=start, prev_high_date=peakd,
                               low_date=low[0], depth=round((low[1] / v[peakd] - 1) * 100, 1),
                               recover=('' if d == ks[-1] else d)))
                start = None
            peak = v[d]; peakd = d; low = (None, 1e9)
        else:
            if not start:
                start = d
            if v[d] < low[1]:
                low = (d, v[d])
    if start:
        ev.append(dict(start=start, prev_high_date=peakd, low_date=low[0],
                       depth=round((low[1] / v[peakd] - 1) * 100, 1), recover=''))
    ev.sort(key=lambda e: e['depth'])
    SNAP['drawdown_events'] = ev[:10]
    # 当前回撤
    pk = max(v[d] for d in ks)
    SNAP['current'] = dict(last_date=ks[-1], point=round(v[ks[-1]], 2),
                           peak=round(pk, 2), dd=round((v[ks[-1]] / pk - 1) * 100, 2))

    # 锚定回撤分档前瞻
    dd = {}
    pk = v[ks[0]]
    for d in ks:
        pk = max(pk, v[d])
        dd[d] = v[d] / pk - 1
    dq = sorted(dd.values())
    SNAP['dd_dist'] = {str(q): round(dq[int(q / 100 * (len(dq) - 1))] * 100, 2)
                       for q in (10, 25, 50, 75, 90)}
    buckets = []
    best = 1e9
    cur = None; cnt = 0
    for d in win:
        trig = None
        for lo, hi in ((-1.0, -0.30), (-0.30, -0.20), (-0.20, -0.10), (-0.10, 0.0)):
            if lo <= dd[d] < hi:
                trig = '%.0f%%~%.0f%%' % (lo * 100, hi * 100)
                break
        f60 = None
        i = win.index(d)
        if i + 60 < len(win):
            f60 = v[win[i + 60]] / v[d] - 1
        if trig and f60 is not None:
            buckets.append((trig, f60))
    agg = {}
    for t, x in buckets:
        agg.setdefault(t, []).append(x)
    bl = [dict(bucket=t, n=len(xs), mean=round(statistics.mean(xs) * 100, 2),
               median=round(statistics.median(xs) * 100, 2),
               win=round(sum(1 for q in xs if q > 0) / len(xs) * 100, 1))
          for t, xs in agg.items()]
    allf = [v[win[i + 60]] / v[win[i]] - 1 for i in range(len(win) - 60)]
    SNAP['dd_buckets'] = dict(buckets=bl,
                              base=dict(n=len(allf), mean=round(statistics.mean(allf) * 100, 2),
                                        median=round(statistics.median(allf) * 100, 2),
                                        win=round(sum(1 for q in allf if q > 0) / len(allf) * 100, 1)),
                              horizon=60)

    # RSI
    def rsi(vals, p=14):
        out = [None] * len(vals); ag = al = 0.0
        for i in range(1, len(vals)):
            ch = vals[i] - vals[i - 1]
            g = max(ch, 0.0); l = max(-ch, 0.0)
            if i <= p:
                ag += g / p; al += l / p
                if i == p:
                    out[i] = 100 - 100 / (1 + ag / al) if al else 100
            else:
                ag = (ag * (p - 1) + g) / p; al = (al * (p - 1) + l) / p
                out[i] = 100 - 100 / (1 + ag / al) if al else 100
        return out
    rsi_out = {}
    for k in ('微盘400(等权·月内漂移)', '微盘100(等权·月内漂移)', '中证2000'):
        rr = [x for x in rsi(slice_to_win(S[k])) if x is not None]
        rsi_out[SHORT.get(k, k)] = dict(now=round(rr[-1], 1),
                                        pct=round(sum(1 for x in rr if x <= rr[-1]) / len(rr) * 100, 1),
                                        median=round(statistics.median(rr), 1))
    SNAP['rsi14'] = rsi_out

    # 换手
    turn = {}
    for t in ('400', '100'):
        dates = [r[0] for r in c.execute(
            'SELECT DISTINCT eff_date FROM microcap_index_component WHERE index_type=? ORDER BY eff_date', (t,))]
        pools = {}
        for d, code in c.execute('SELECT eff_date,stock_code FROM microcap_index_component WHERE index_type=?', (t,)):
            pools.setdefault(d, set()).add(code)
        tos = [len(pools[dates[i]] - pools[dates[i - 1]]) / len(pools[dates[i]])
               for i in range(1, len(dates))]
        turn['微盘' + t] = dict(snapshots=len(dates), mean=round(statistics.mean(tos) * 100, 1),
                                median=round(statistics.median(tos) * 100, 1),
                                max=round(max(tos) * 100, 1),
                                annual=round(statistics.mean(tos) * 12 * 100, 0))
    SNAP['turnover'] = turn

    # 容量
    codes = [r[0] for r in c.execute(
        'SELECT stock_code FROM microcap_index_component WHERE index_type="400" '
        'AND eff_date=(SELECT MAX(eff_date) FROM microcap_index_component WHERE index_type="400")')]
    q = ','.join('?' * len(codes))
    last = c.execute('SELECT MAX(date) FROM daily_kline').fetchone()[0]
    amt = c.execute('SELECT SUM(amount) FROM daily_kline WHERE date=? AND stock_code IN (%s)' % q,
                    [last] + codes).fetchone()[0] or 0
    tot = c.execute('SELECT SUM(amount) FROM daily_kline WHERE date=?', (last,)).fetchone()[0] or 0
    namts = sorted([r[0] for r in c.execute(
        'SELECT amount FROM daily_kline WHERE date=? AND stock_code IN (%s)' % q, [last] + codes) if r[0]])
    caps = sorted(r[0] for r in c.execute(
        'SELECT mkt_cap FROM microcap_index_component WHERE index_type="400" '
        'AND eff_date=(SELECT MAX(eff_date) FROM microcap_index_component WHERE index_type="400")') if r[0])
    SNAP['capacity'] = dict(date=last, pool_amount_yi=round(amt / 1e8, 2),
                            market_amount_yi=round(tot / 1e8, 2),
                            share_pct=round(amt / tot * 100, 2),
                            per_stock_median_wan=round(statistics.median(namts) / 1e4, 0),
                            per_stock_min_wan=round(namts[0] / 1e4, 0),
                            per_stock_max_wan=round(namts[-1] / 1e4, 0),
                            cap_min_yi=round(caps[0], 1),
                            cap_median_yi=round(statistics.median(caps), 1),
                            cap_max_yi=round(caps[-1], 1),
                            cap_sum_yi=round(sum(caps), 0))

    # 入场策略
    st = {}
    st['hold'] = dict(total=round((v[ks[-1]] / v[ks[0]] - 1) * 100, 1),
                      cagr=round(((v[ks[-1]] / v[ks[0]]) ** (1 / yrs) - 1) * 100, 2), years=round(yrs, 2))
    months = {}
    for d in ks:
        months.setdefault(d[:7], []).append(d)
    firsts = [months[m][0] for m in sorted(months)]
    units = sum(1.0 / v[d] for d in firsts)
    st['dca'] = dict(periods=len(firsts), invested=len(firsts),
                     end_value=round(units * v[ks[-1]], 1),
                     profit=round((units * v[ks[-1]] / len(firsts) - 1) * 100, 1))
    tiers = []
    for th in (-0.15, -0.25, -0.35):
        trig = []; armed = True
        for d in ks:
            if dd[d] > th * 0.5:
                armed = True
            if armed and dd[d] <= th:
                trig.append(d); armed = False
        if not trig:
            tiers.append(dict(th=round(th * 100), n=0))
            continue
        f60 = []
        for d in trig:
            i = ks.index(d)
            if i + 60 < len(ks):
                f60.append(v[ks[i + 60]] / v[d] - 1)
        tiers.append(dict(th=round(th * 100), n=len(trig),
                          f60_mean=round(statistics.mean(f60) * 100, 2) if f60 else None,
                          f60_win=round(sum(1 for x in f60 if x > 0) / len(f60) * 100, 1) if f60 else None,
                          dates=trig))
    st['tiers'] = tiers
    SNAP['strategy'] = st

    # 国际对照
    if os.path.exists(PEER):
        J = json.load(open(PEER, encoding='utf-8'))
        US = {k: {r[0]: r[1] for r in vv} for k, vv in J['data'].items()}
        peers = []
        for nm in ('IWC', 'IWM', 'IJR', 'SPY'):
            if nm not in US:
                continue
            vv = US[nm]
            kk = [d for d in sorted(vv) if WIN0 <= d <= WIN1]
            x = [vv[d] for d in kk]
            rr = [x[i] / x[i - 1] - 1 for i in range(1, len(x))]
            yy = (datetime.strptime(WIN1, '%Y-%m-%d') -
                  datetime.strptime(kk[0], '%Y-%m-%d')).days / 365.25
            pk = x[0]; md = 0.0
            for q2 in x:
                pk = max(pk, q2); md = min(md, q2 / pk - 1)
            vol = statistics.pstdev(rr) * math.sqrt(243)
            peers.append(dict(name=nm, cagr=round(((x[-1] / x[0]) ** (1 / yy) - 1) * 100, 2),
                              vol=round(vol * 100, 2),
                              sharpe=round((statistics.mean(rr) * 243 - 0.015) / vol, 2),
                              mdd=round(md * 100, 1),
                              bad=sum(1 for r in rr if abs(r) > 0.35), years=round(yy, 2)))
        SNAP['peers'] = dict(source=J.get('source', {}), list=peers)

    # 数据质量
    n_delist = c.execute("""SELECT COUNT(*) FROM (
        SELECT stock_code, MAX(date) m FROM daily_kline WHERE date>='2016-01-01'
        GROUP BY stock_code) WHERE m < '2026-06-01'""").fetchone()[0]
    n_nobasic = c.execute("""SELECT COUNT(*) FROM (
        SELECT stock_code, MAX(date) m FROM daily_kline WHERE date>='2016-01-01'
        GROUP BY stock_code) t
        WHERE m < '2026-06-01' AND stock_code NOT IN (SELECT stock_code FROM stock_basic)""").fetchone()[0]
    SNAP['data_quality'] = dict(
        stopped_after_2016=n_delist, not_in_stock_basic=n_nobasic,
        delisted_date_filled=0,
        trade_days=n, first=win[0], last=win[-1])

    # 存量序列对照（v1 = 2026-09 建库时的旧序列，已备份）
    def micro_tbl(t, tbl):
        return {d: v for d, v in c.execute(
            'SELECT date,point FROM %s WHERE index_type=?' % tbl, (t,))}
    rev = []
    for t in ('400', '100'):
        old = micro_tbl(t, 'microcap_index_daily_v1')
        cur = micro_tbl(t, 'microcap_index_daily')
        ks2 = sorted(set(old) & set(cur))
        if not ks2:
            continue
        ro = [old[ks2[i]] / old[ks2[i - 1]] - 1 for i in range(1, len(ks2))]
        rc = [cur[ks2[i]] / cur[ks2[i - 1]] - 1 for i in range(1, len(ks2))]
        old_pool = {}
        for d, code in c.execute(
                'SELECT eff_date,stock_code FROM microcap_index_component_v1 WHERE index_type=?', (t,)):
            old_pool.setdefault(d, set()).add(code)
        new_pool = {}
        for d, code in c.execute(
                'SELECT eff_date,stock_code FROM microcap_index_component WHERE index_type=?', (t,)):
            new_pool.setdefault(d, set()).add(code)
        shared = sorted(set(old_pool) & set(new_pool))
        ov = [len(old_pool[d] & new_pool[d]) / len(new_pool[d]) for d in shared]
        rev.append(dict(
            type='微盘' + t,
            old_last=round(old[ks2[-1]], 2), new_last=round(cur[ks2[-1]], 2),
            old_total=round((old[ks2[-1]] / old[ks2[0]] - 1) * 100, 1),
            new_total=round((cur[ks2[-1]] / cur[ks2[0]] - 1) * 100, 1),
            corr=round(statistics.correlation(ro, rc), 4),
            mean_diff_pp=round(statistics.mean([rc[i] - ro[i] for i in range(len(rc))]) * 100, 4),
            overlap_mean=round(statistics.mean(ov) * 100, 1),
            overlap_min=round(min(ov) * 100, 1),
            overlap_dates=len(shared)))
    SNAP['revision'] = rev

    # 图表序列（每 5 个交易日取 1 点，归一化到 1000，避免超大 HTML）
    chart_keys = ['微盘400(等权·月内漂移)', '微盘100(等权·月内漂移)', '中证2000',
                  '沪深300(全收益)', '中证红利(全收益)']
    charts = {}
    for k in chart_keys:
        vv = S[k]
        base = vv[win[0]]
        pts = [[win[i], round(vv[win[i]] / base * 1000, 1)]
               for i in range(0, len(win), 5)]
        if pts[-1][0] != win[-1]:
            pts.append([win[-1], round(vv[win[-1]] / base * 1000, 1)])
        charts[SHORT.get(k, k)] = pts
    SNAP['charts'] = dict(normalized=charts, step_trading_days=5, base=1000.0)

    os.makedirs(OUTDIR, exist_ok=True)
    txt = json.dumps(SNAP, ensure_ascii=False, indent=1)
    open(MF, 'w', encoding='utf-8').write(txt)
    print('snapshot ->', MF, len(txt), 'bytes, sha1', hashlib.sha1(txt.encode()).hexdigest()[:16])


if __name__ == '__main__':
    main()
