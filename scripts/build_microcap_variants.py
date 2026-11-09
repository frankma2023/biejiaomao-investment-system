# -*- coding: utf-8 -*-
"""微盘指数：月内权重漂移版（可执行口径）。

与 scripts/build_microcap_index.py 的唯一差别：权重在月内漂移，只在换池时
重置为等权。原脚本每日用「等权平均收益」连乘，等价于每天免费再平衡，
会凭空产生再平衡收益。本脚本给出真实持有口径，用于量化该差异。
"""
import sys, os, time, sqlite3, argparse
from bisect import bisect_right
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')
START = '2016-01-01'
BASE = 1000.0
NEW_DAYS = 60


def _valid_code(code):
    if not code or not code.isdigit():
        return False
    return code[0] in ('0', '3', '6') and not code.startswith(('4', '8', '92'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--suffix', default='_drift')
    ap.add_argument('--weight', default='equal', choices=['equal', 'cap'],
                    help='池内加权：equal=等权；cap=按总市值加权（隔离加权方式的影响）')
    args = ap.parse_args()
    SFX = args.suffix
    CAPW = (args.weight == 'cap')
    types = [('400', 400), ('100', 100)]
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("DROP TABLE IF EXISTS microcap_index_daily%s" % SFX)
    conn.execute("""CREATE TABLE microcap_index_daily%s (
        index_type TEXT NOT NULL, date TEXT NOT NULL, point REAL NOT NULL,
        pct_change REAL, avg_mkt_cap REAL, n INTEGER,
        PRIMARY KEY (index_type, date))""" % SFX)
    conn.commit()

    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date >= ? ORDER BY date", (START,))]

    basic = {}
    for r in conn.execute("SELECT stock_code, ipo_date, name FROM stock_basic"):
        c = r['stock_code']
        if _valid_code(c):
            basic[c] = ((r['ipo_date'] or '9999-12-31')[:10], r['name'] or c)

    eq = {}
    for r in conn.execute("""SELECT stock_code, change_date, capitalization
        FROM stock_equity_change WHERE capitalization IS NOT NULL AND capitalization > 0"""):
        eq.setdefault(r['stock_code'], []).append((r['change_date'], r['capitalization']))
    for c in eq:
        eq[c].sort()
    eq_ds = {c: [x[0] for x in v] for c, v in eq.items()}
    eq_cs = {c: [x[1] for x in v] for c, v in eq.items()}

    def cap_at(code, d):
        ds = eq_ds.get(code)
        if not ds:
            return None
        i = bisect_right(ds, d) - 1
        if i < 0:
            i = 0
        return eq_cs[code][i]

    last_of_month, first_of_month = {}, {}
    for i, d in enumerate(dates):
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        last_of_month[d] = (nxt is not None and nxt[:7] != d[:7])
        first_of_month[d] = (i == 0 or dates[i - 1][:7] != d[:7])

    pools = {t: None for t, _ in types}
    pending = {t: None for t, _ in types}
    weights = {t: {} for t, _ in types}
    prev_adj = {t: {} for t, _ in types}
    point = {t: None for t, _ in types}
    daily_rows = []

    def rank_all(d, kmap):
        cand = []
        d0 = datetime.strptime(d, '%Y-%m-%d')
        for c, (ipo, _n) in basic.items():
            if ipo > d:
                continue
            if (d0 - datetime.strptime(ipo, '%Y-%m-%d')).days < NEW_DAYS:
                continue
            kl = kmap.get(c)
            if not kl or not kl[0]:
                continue
            cap = cap_at(c, d)
            if not cap:
                continue
            cand.append((c, cap * kl[0]))
        cand.sort(key=lambda x: x[1])
        return cand

    def _wt(codes, d, kmap):
        if not CAPW:
            return {x: 1.0/len(codes) for x in codes}
        caps = {}
        for x in codes:
            kl = kmap.get(x)
            cp = cap_at(x, d)
            if kl and cp:
                caps[x] = cp * kl[0]
        tot = sum(caps.values()) or 1.0
        return {x: caps.get(x, 0.0)/tot for x in codes}

    def set_pool(t, n, d, kmap):
        rank = rank_all(d, kmap)
        codes = [x[0] for x in rank[:n]]
        eff = None
        di = dates.index(d)
        for dd in dates[di + 1:]:
            if dd[:7] != d[:7]:
                eff = dd
                break
        pending[t] = (eff or '9999-12-31', codes,
                      {x: (kmap[x][1] if kmap[x][1] is not None else kmap[x][0]) for x in codes},
                      _wt(codes, d, kmap))

    def compute_day(t, n, d, kmap):
        pool = pools[t]
        if pool is None:
            rank = rank_all(d, kmap)
            pool = [x[0] for x in rank[:n]]
            pools[t] = pool
            weights[t] = _wt(pool, d, kmap)
            prev_adj[t] = {}
        pa = prev_adj[t]
        w = weights[t]
        port = 0.0
        tot_cap, n_eff = 0.0, 0
        neww = {}
        for c in pool:
            kl = kmap.get(c)
            wi = w.get(c, 0.0)
            r = 0.0
            if kl:
                adj = kl[1] if kl[1] is not None else kl[0]
                p0 = pa.get(c)
                if p0 and p0 > 0 and adj and adj > 0:
                    r = adj / p0 - 1
                pa[c] = adj
                mcap_ = (cap_at(c, d) or 0) * kl[0]
                if mcap_ > 0:
                    tot_cap += mcap_
                    n_eff += 1
            port += wi * r
            neww[c] = wi * (1 + r)
        # 归一：组合价值增值后权重回到 1
        if 1 + port > 0:
            for c in neww:
                neww[c] /= (1 + port)
        weights[t] = neww
        pt = BASE if point[t] is None else point[t] * (1 + port)
        point[t] = pt
        avg_cap = (tot_cap / n_eff / 1e8) if n_eff else None
        daily_rows.append((t, d, round(pt, 2), round(port * 100, 3),
                           round(avg_cap, 2) if avg_cap else None, len(pool)))

    it = conn.execute(
        "SELECT stock_code, date, COALESCE(ex_close, lxr_fc_close) AS close, "
        "COALESCE(lxr_fc_close, ex_close) AS adj_close FROM daily_kline "
        "WHERE date >= ? ORDER BY date, stock_code", (dates[0],))
    row = it.fetchone()
    for di, d in enumerate(dates):
        kmap = {}
        while row and row['date'] == d:
            kmap[row['stock_code']] = (row['close'], row['adj_close'])
            row = it.fetchone()
        if not kmap:
            continue
        if first_of_month[d]:
            for t, _ in types:
                if pending[t] and pending[t][0] == d:
                    pools[t] = pending[t][1]
                    weights[t] = dict(pending[t][3])
                    prev_adj[t] = dict(pending[t][2])
        for t, n in types:
            compute_day(t, n, d, kmap)
        if last_of_month[d]:
            for t, n in types:
                set_pool(t, n, d, kmap)
        if di % 400 == 0:
            print('  %s (%.0fs)' % (d, time.time() - t0), flush=True)

    conn.executemany("INSERT OR REPLACE INTO microcap_index_daily%s VALUES (?,?,?,?,?,?)" % SFX, daily_rows)
    conn.commit()
    conn.close()
    print('完成 %d 行 %.0fs' % (len(daily_rows), time.time() - t0))


if __name__ == '__main__':
    main()
