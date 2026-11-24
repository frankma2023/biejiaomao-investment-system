# -*- coding: utf-8 -*-
"""
市赚率（PR = PE_TTM / ROE%）历史检验。

PR 的数据来源不需要财报：全库 fundamental_indicator 每日都有 pe_ttm 与 pb，
而 PB = PE × ROE  ⇒  ROE(小数) = pb / pe_ttm，
所以 PR = pe_ttm / (ROE×100) = pe_ttm² / (pb × 100)。

口径：
  1. 截面：每月最后一个交易日（2016-06 ~ 2025-06），全 A 股。
  2. 过滤：pe_ttm>0、pb>0、隐含 ROE 在 2%~80%、pe_ttm<300、pb<30；
     上市满 250 个交易日；20 日均成交额 ≥ 2000 万（可交易性）。
  3. 前瞻收益：用 daily_kline.lxr_fc_close（前复权）算 60/120/250 个交易日后。
  4. 基准：同一截面、同一过滤条件下全体股票的等权平均前瞻收益。

输出：docs/report/pr_backtest/data/pr_result.json 与控制台表格。
"""
import argparse
import json
import os
import sqlite3
from collections import defaultdict

import numpy as np

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')
OUT = os.path.join(PROJECT, 'docs', 'report', 'pr_backtest', 'data')

HORIZONS = (60, 120, 250)
START = '2016-06-01'
END = '2025-06-30'
MIN_AMT = 2000.0      # 万元，20 日均成交额下限
MIN_LIST_DAYS = 250


def connect():
    con = sqlite3.connect(DB, timeout=60)
    con.execute('PRAGMA busy_timeout=60000')
    con.row_factory = sqlite3.Row
    return con


def calendar(con):
    """交易日历：用 000300 的日线日期。"""
    rows = con.execute("""SELECT date FROM index_daily_kline
                          WHERE stock_code='000300' AND kline_type='normal'
                          ORDER BY date""").fetchall()
    return [r['date'] for r in rows]


def month_ends(cal, start, end):
    """每月最后一个交易日。"""
    out, last = [], None
    for d in cal:
        if d < start or d > end:
            continue
        if d[:7] != last:
            if last is not None:
                out.append(prev)
            last = d[:7]
        prev = d
    if last is not None:
        out.append(prev)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default=START)
    ap.add_argument('--end', default=END)
    ap.add_argument('--min-amt', type=float, default=MIN_AMT)
    ap.add_argument('--tag', default='')
    args = ap.parse_args()

    con = connect()
    cal = calendar(con)
    snaps = month_ends(cal, args.start, args.end)
    idx = {d: i for i, d in enumerate(cal)}
    # 需要的价格日期 = 快照日 + 各自的前瞻日
    need = set()
    plan = []
    for d in snaps:
        i = idx[d]
        tg = [cal[i + h] if i + h < len(cal) else None for h in HORIZONS]
        if tg[0] is None:
            continue
        plan.append((d, tg))
        need.add(d)
        need.update(x for x in tg if x)
    need = sorted(need)
    print(f'快照 {len(plan)} 个月，需价格日期 {len(need)} 个')

    # 1) 价格：只取需要的日期
    px = defaultdict(dict)
    CH = 400
    for k in range(0, len(need), CH):
        chunk = need[k:k + CH]
        ph = ','.join('?' * len(chunk))
        for r in con.execute(
                f"""SELECT stock_code, date, lxr_fc_close FROM daily_kline
                    WHERE date IN ({ph}) AND lxr_fc_close IS NOT NULL""", chunk):
            px[r['date']][r['stock_code']] = r['lxr_fc_close']
        print(f'  价格 {min(k + CH, len(need))}/{len(need)}', end='\r')
    print()

    # 2) 上市日期（首次出现）+ 20 日均成交额
    first_seen = {}
    for r in con.execute("SELECT stock_code, MIN(date) d FROM daily_kline GROUP BY stock_code"):
        first_seen[r['stock_code']] = r['d']
    print(f'  股票数 {len(first_seen)}')

    amt_cache = {}

    def amt20(date, codes):
        """该日之前 20 个交易日的日均成交额（万元）。"""
        key = date
        if key in amt_cache:
            return amt_cache[key]
        i = idx[date]
        lo = cal[max(0, i - 19)]
        rows = con.execute(
            """SELECT stock_code, AVG(amount)/1e4 v FROM daily_kline
               WHERE date BETWEEN ? AND ? GROUP BY stock_code""", (lo, date)).fetchall()
        amt_cache[key] = {r['stock_code']: r['v'] for r in rows}
        return amt_cache[key]

    # 3) 逐月取 pe_ttm / pb
    recs = []          # (date, code, pe, pb, pr, roe)
    for d, tg in plan:
        rows = con.execute(
            """SELECT stock_code, metric_code, value FROM fundamental_indicator
               WHERE date=? AND metric_code IN ('pe_ttm','pb')""", (d,)).fetchall()
        pe, pb = {}, {}
        for r in rows:
            if r['metric_code'] == 'pe_ttm':
                pe[r['stock_code']] = r['value']
            else:
                pb[r['stock_code']] = r['value']
        amt = amt20(d, None)
        kept = 0
        for c, p in pe.items():
            b = pb.get(c)
            if not b or p is None or p <= 0 or b <= 0:
                continue
            roe = b / p                      # 隐含 TTM ROE（小数）
            if not (0.02 <= roe <= 0.80):
                continue
            if p > 300 or b > 30:
                continue
            fs = first_seen.get(c)
            if not fs or (idx[d] - idx.get(fs, 0)) < MIN_LIST_DAYS:
                continue
            if args.min_amt > 0 and amt.get(c, 0) < args.min_amt:
                continue
            pr = p / (roe * 100)
            recs.append((d, c, p, b, pr, roe))
            kept += 1
        print(f'  {d}  有效 {kept}', end='\r')
    print()
    print(f'样本记录 {len(recs):,}')

    # 4) 前瞻收益
    plan_map = {d: t for d, t in plan}
    data = []
    for d, c, p, b, pr, roe in recs:
        tg = plan_map.get(d)
        if tg is None:
            continue
        p0 = px[d].get(c)
        if not p0:
            continue
        row = {'date': d, 'code': c, 'pe': p, 'pb': b, 'pr': pr, 'roe': roe}
        ok = False
        for h, td in zip(HORIZONS, tg):
            p1 = px[td].get(c) if td else None
            row[f'r{h}'] = (p1 / p0 - 1) * 100 if p1 else None
            if p1:
                ok = True
        if ok:
            data.append(row)

    print(f'有前瞻收益的样本 {len(data):,}')
    json.dump(data, open(os.path.join(OUT, 'pr_raw.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    return data, plan


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    main()
