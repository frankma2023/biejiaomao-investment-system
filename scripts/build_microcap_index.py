# -*- coding: utf-8 -*-
"""
微盘股指数回算脚本 v1.0（PRD: docs/product/微盘股指数复刻模块_产品需求书.md）
────────────────────────────────────────────────────────────
万得口径复刻：月频调仓 / 等权 / 总市值排序 / 前复权收益连乘
双序列：400（官方口径）+ 100（极端口径）| 起点 2016-01-04 = 1000 点

口径要点：
- 选池市值 = 真实收盘 × 当日总股本（stock_equity_change, change_date 生效, 首条向前回填）
- 点位 = 成分前复权收益等权平均连乘（除权/分红自动处理）
- 调仓 = 每月最后交易日定池, 次月首个交易日起生效（月内不调, 权重漂移）
- 剔除：非上市(按 ipo/delisted 区间) / 北交所(4,8,92前缀) / 次新(ipo<60自然日) / B股
- ST 历史回算不剔（无历史 ST 状态, 避免前视偏差; 见 PRD §3.3）
- 流式单遍读 K 线（内存 O(当日股票数)）

用法:
  python scripts/build_microcap_index.py                # 全量回算
  python scripts/build_microcap_index.py --incremental  # 增量（幂等）
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
    # 沪深 A: 0/3/6 开头; 剔北交所(4/8/92前缀)与 B 股(200/900)
    if not code or not code.isdigit():
        return False
    return code[0] in ('0', '3', '6') and not code.startswith(('4', '8', '92'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--incremental', action='store_true')
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    conn.execute("""CREATE TABLE IF NOT EXISTS microcap_index_daily (
        index_type TEXT NOT NULL, date TEXT NOT NULL, point REAL NOT NULL,
        pct_change REAL, avg_mkt_cap REAL, n INTEGER,
        PRIMARY KEY (index_type, date))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS microcap_index_component (
        index_type TEXT NOT NULL, eff_date TEXT NOT NULL, stock_code TEXT NOT NULL,
        name TEXT, mkt_cap REAL, weight REAL,
        PRIMARY KEY (index_type, eff_date, stock_code))""")
    conn.commit()

    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date >= ? ORDER BY date", (START,))]
    if not dates:
        print('无交易日'); return
    if args.incremental:
        last = conn.execute("SELECT MAX(date) FROM microcap_index_daily").fetchone()[0]
        if not last or last >= dates[-1]:
            print('已是最新, 无需增量'); return
        dates = [d for d in dates if d > last]
        print('增量 %s ~ %s (%d 天, 承接 %s)' % (dates[0], dates[-1], len(dates), last))
    else:
        conn.execute("DELETE FROM microcap_index_daily")
        conn.execute("DELETE FROM microcap_index_component")
        conn.commit()
        print('全量回算 %s ~ %s (%d 天)' % (dates[0], dates[-1], len(dates)))

    # 基础数据
    basic = {}   # code -> (ipo, delist, name)
    for r in conn.execute("SELECT stock_code, ipo_date, delisted_date, name FROM stock_basic"):
        c = r['stock_code']
        if _valid_code(c):
            basic[c] = ((r['ipo_date'] or '9999-12-31')[:10],
                        (r['delisted_date'] or '')[:10] or None,
                        r['name'] or c)
    eq = {}      # code -> sorted[(change_date, cap)]
    for r in conn.execute("""SELECT stock_code, change_date, capitalization
        FROM stock_equity_change WHERE capitalization IS NOT NULL AND capitalization > 0"""):
        eq.setdefault(r['stock_code'], []).append((r['change_date'], r['capitalization']))
    for c in eq:
        eq[c].sort()
    eq_ds = {c: [x[0] for x in v] for c, v in eq.items()}
    eq_cs = {c: [x[1] for x in v] for c, v in eq.items()}
    print('基础: 股票 %d / 股本 %d' % (len(basic), len(eq)), flush=True)

    def cap_at(code, d):
        ds = eq_ds.get(code)
        if not ds:
            return None
        i = bisect_right(ds, d) - 1
        if i < 0:
            i = 0
        return eq_cs[code][i]

    # 月末/月初标记（月末必须存在下一个交易日才判定——数据末端最后一天不算月末）
    last_of_month = {}
    first_of_month = {}
    for i, d in enumerate(dates):
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        last_of_month[d] = (nxt is not None and nxt[:7] != d[:7])
        first_of_month[d] = (i == 0 or dates[i - 1][:7] != d[:7])

    pools = {t: None for t in ('400', '100')}      # 当前生效成分 (list)
    pending = {t: None for t in ('400', '100')}    # (eff, codes, pool_adj) 月末定的下月池+月末adj基准
    prev_adj = {t: {} for t in ('400', '100')}
    prev_point = {t: None for t in ('400', '100')}
    daily_rows, comp_rows = [], []
    # ── 增量模式：恢复状态（点位连续性 + 当前池 + 跨月衔接）──
    if args.incremental:
        last = conn.execute("SELECT MAX(date) FROM microcap_index_daily").fetchone()[0]
        last_kmap = {}
        for r in conn.execute("SELECT stock_code, close, adj_close FROM daily_kline WHERE date=?", (last,)):
            last_kmap[r[0]] = (r[1], r[2])

        def _prev_adj_of(code):
            """基准复权价：优先 last 日, 停牌则回溯最近交易日（消除与全量的停牌股偏差）"""
            if code in last_kmap:
                return last_kmap[code][1] if last_kmap[code][1] is not None else last_kmap[code][0]
            row2 = conn.execute(
                "SELECT adj_close, close FROM daily_kline WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1",
                (code, last)).fetchone()
            if not row2:
                return None
            return row2[0] if row2[0] is not None else row2[1]

        for t in ('400', '100'):
            p = conn.execute("SELECT point FROM microcap_index_daily WHERE index_type=? AND date=?", (t, last)).fetchone()
            prev_point[t] = p[0] if p else None
            eff = conn.execute("SELECT MAX(eff_date) FROM microcap_index_component WHERE index_type=? AND eff_date<='9999-12-31' AND eff_date<=?", (t, dates[0])).fetchone()[0]
            codes = []
            if eff:
                codes = [r[0] for r in conn.execute("SELECT stock_code FROM microcap_index_component WHERE index_type=? AND eff_date=? ORDER BY mkt_cap", (t, eff))]
            if codes:
                pools[t] = codes
                prev_adj[t] = {c: _prev_adj_of(c) for c in codes}
        # 跨月衔接：增量首日为新月份 → 用 last 日全市场排序定本月初生效池
        if dates[0][:7] != last[:7]:
            print('  跨月: 用 %s 数据定 %s 生效池' % (last, dates[0]), flush=True)
            _d0 = datetime.strptime(last, '%Y-%m-%d')
            _rank = []
            for c, (ipo, delist, _n) in basic.items():
                if delist and delist < last: continue
                if ipo > last: continue
                if (_d0 - datetime.strptime(ipo, '%Y-%m-%d')).days < NEW_DAYS: continue
                kl = last_kmap.get(c)
                if not kl or not kl[0]: continue
                _cap = cap_at(c, last)
                if not _cap: continue
                _rank.append((c, _cap * kl[0]))
            _rank.sort(key=lambda x: x[1])
            _cmap = dict(_rank)
            for t, n in (('400', 400), ('100', 100)):
                _codes = [x[0] for x in _rank[:n]]
                for c in _codes:
                    comp_rows.append((t, dates[0], c, basic[c][2], round(_cmap[c] / 1e8, 2), round(1.0 / len(_codes), 6)))
                pools[t] = _codes
                prev_adj[t] = {c: _prev_adj_of(c) for c in _codes}

    def rank_all(d, kmap):
        """全市场市值排序（升序）: [(code, mcap)]"""
        cand = []
        d0 = datetime.strptime(d, '%Y-%m-%d')
        for c, (ipo, delist, _n) in basic.items():
            if delist and delist < d:
                continue
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

    def set_pool(t, n, d, kmap):
        """当日定池（记录成分快照, 池效用于次月）"""
        rank = rank_all(d, kmap)
        codes = [x[0] for x in rank[:n]]
        cap_map = dict(rank)  # code -> mcap（O(n) 查找）
        eff = None
        di = dates.index(d)
        for dd in dates[di + 1:]:
            if dd[:7] != d[:7]:
                eff = dd
                break
        if eff is None:
            eff = '9999-12-31'
        for c in codes:
            comp_rows.append((t, eff, c, basic[c][2], round(cap_map[c] / 1e8, 2),
                              round(1.0 / len(codes), 6)))
        # 保存月末收盘 adj 作下月初收益基准（prev_adj 不重置为空——否则月初首日收益=0 丢一天）
        pool_adj = {c: (kmap.get(c)[1] if kmap.get(c) and kmap[c][1] is not None else kmap.get(c)[0]) for c in codes}
        pending[t] = (eff, codes, pool_adj)

    def compute_day(t, n, d, kmap):
        """用当前池算当日点位"""
        pool = pools[t]
        if pool is None:
            # 首日建池（直接定当前池）
            rank = rank_all(d, kmap)
            pool = [x[0] for x in rank[:n]]
            pools[t] = pool
            cap_map = dict(rank)
            # 首日成分也入快照（eff = 首日）
            for c in pool:
                comp_rows.append((t, d, c, basic[c][2], round(cap_map[c] / 1e8, 2),
                                  round(1.0 / len(pool), 6)))
            prev_adj[t] = {}
        rets, tot_cap, n_eff = [], 0.0, 0
        pa = prev_adj[t]
        for c in pool:
            kl = kmap.get(c)
            if not kl:
                continue
            adj = kl[1] if kl[1] is not None else kl[0]
            p0 = pa.get(c)
            if p0 and p0 > 0 and adj and adj > 0:
                rets.append(adj / p0 - 1)
            pa[c] = adj
            mcap_ = (cap_at(c, d) or 0) * kl[0]
            if mcap_ > 0:
                tot_cap += mcap_
                n_eff += 1
        ret = sum(rets) / len(rets) if rets else 0.0
        point = BASE if prev_point[t] is None else prev_point[t] * (1 + ret)
        prev_point[t] = point
        avg_cap = (tot_cap / n_eff / 1e8) if n_eff else None
        daily_rows.append((t, d, round(point, 2), round(ret * 100, 3),
                           round(avg_cap, 2) if avg_cap else None, len(pool)))

    # 流式逐日（增量时从增量首日起查, 避免全表扫描）
    it = conn.execute(
        "SELECT stock_code, date, close, adj_close FROM daily_kline WHERE date >= ? "
        "ORDER BY date, stock_code", (dates[0],))
    row = it.fetchone()
    for di, d in enumerate(dates):
        kmap = {}
        while row and row['date'] == d:
            kmap[row['stock_code']] = (row['close'], row['adj_close'])
            row = it.fetchone()
        if not kmap:
            continue
        if first_of_month[d]:
            for t in ('400', '100'):
                if pending[t] and pending[t][0] == d:
                    pools[t] = pending[t][1]
                    prev_adj[t] = dict(pending[t][2])  # 用月末 adj 作基准, 月初首日收益正确
        compute_day('400', 400, d, kmap)
        compute_day('100', 100, d, kmap)
        if last_of_month[d]:
            set_pool('400', 400, d, kmap)
            set_pool('100', 100, d, kmap)
        if di % 300 == 0:
            print('  %s (%.0fs)' % (d, time.time() - t0), flush=True)

    conn.executemany("INSERT OR REPLACE INTO microcap_index_daily VALUES (?,?,?,?,?,?)", daily_rows)
    conn.executemany("""INSERT OR REPLACE INTO microcap_index_component
        (index_type, eff_date, stock_code, name, mkt_cap, weight) VALUES (?,?,?,?,?,?)""", comp_rows)
    conn.commit()
    conn.close()
    print('完成: %d 日×2 序列, 成分 %d 条, %.0fs' % (len(daily_rows) // 2, len(comp_rows), time.time() - t0))


if __name__ == '__main__':
    main()
