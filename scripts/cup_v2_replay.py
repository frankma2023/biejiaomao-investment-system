# -*- coding: utf-8 -*-
"""
杯柄 V2 · 逐日快照回放回测（口径正确的那一种）。

为什么必须逐日回放
------------------
`chanlun_bi_json` 是 (stock_code, scan_date, bi_json)，每个快照只保留该股最近 50 笔，
且**笔的端点会随新K线继续调整**——「已完成的笔」并非跨快照稳定。实测 000007 的
2024-09-19 快照与 2026-09-22 快照，50 笔中仅 26 笔完全一致。
所以「用一个最新快照扫全历史」得到的信号集合，与「逐日各用当天快照」几乎不重合
（旧引擎实测重合 26/480）。§6.4 的绝对值只能靠逐日回放复现。

性能设计
--------
朴素做法是每天调一次 detect()，内部会对每个 D1 走 [t1, t0+100] 的 t_idx 循环，
2.6M 次调用要十几小时。本脚本改为：
  · 每只股票只载入一次K线，ctx（均线/均量）只算一次——均线是因果的，截断不影响；
  · 每个交易日 D **只判定 t_idx = D 这一根**，杯口取 max(close[t1..D-1]) 直接算，
    不走整段 t_idx 循环；
  · 笔快照按 scan_date 一次性读入内存，逐日取 ≤D 的最近一条。

用法
----
    python scripts/cup_v2_replay.py [--limit N] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
"""
import argparse
import json
import os
import sqlite3
import sys
import time

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))

from scanners import cup_handle_v2 as ch  # noqa: E402

DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
OUT = os.path.join(PROJECT_DIR, 'data', 'cup_v2_replay.csv')


def _argmax_max(seq, lo, hi):
    """返回 (max, argmax)，区间 [lo, hi]。hi < lo 时返回 (None, None)。"""
    best = None
    bi = None
    for k in range(lo, hi + 1):
        v = seq[k]
        if best is None or v > best:
            best, bi = v, k
    return best, bi


def main():
    ap = argparse.ArgumentParser(description='杯柄 V2 逐日快照回放回测')
    ap.add_argument('--limit', type=int, default=0, help='只跑前 N 只（调试/抽样）')
    ap.add_argument('--start', default=None)
    ap.add_argument('--end', default=None)
    ap.add_argument('--lookback', type=int, default=2500, help='每只股票载入的日历天数')
    args = ap.parse_args()

    params = ch.load_params()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    end = args.end or conn.execute(
        "SELECT MAX(date) FROM daily_kline_adj").fetchone()[0]
    codes = [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_basic "
        "WHERE stock_code GLOB '[036][0-9][0-9][0-9][0-9][0-9]' ORDER BY stock_code")]
    if args.limit:
        codes = codes[:args.limit]

    t0 = time.time()
    rows = []
    n_snap = n_day = n_lag = 0
    for ci, code in enumerate(codes, 1):
        daily = ch._load_daily(conn, code, end, args.lookback)
        if len(daily) < 120:
            continue
        date_idx = {k['date']: i for i, k in enumerate(daily)}
        closes = [k['close'] for k in daily]
        volumes = [k['volume'] for k in daily]
        ctx = {
            'closes': closes, 'volumes': volumes,
            'vol_ma': ch._rolling_mean(volumes, params['vol_ma_window']),
            'ma': {w: ch._rolling_mean(closes, w) for w in (10, 20, 50)},
        }
        # 该股窗口内全部笔快照
        snaps = {}
        for r in conn.execute(
                "SELECT scan_date, bi_json FROM chanlun_bi_json WHERE stock_code=?",
                (code,)):
            snaps[r['scan_date'][:10]] = r['bi_json']
        if not snaps:
            continue
        snap_dates = sorted(snaps)
        n_snap += 1
        cur_sd = None
        bi = None

        for i, k in enumerate(daily):
            D = k['date']
            if args.start and D < args.start:
                continue
            if i < 60:
                continue
            # 取 <= D 的最近一条快照
            if cur_sd is None or cur_sd > D:
                j = None
                for s in snap_dates:
                    if s <= D:
                        j = s
                    else:
                        break
                if j is None:
                    continue
                cur_sd, bi = j, json.loads(snaps[j])
            else:
                # 快照日期推进到最近一个 <= D 的
                while snap_dates and snap_dates[0] <= D:
                    s = snap_dates.pop(0)
                    cur_sd, bi = s, json.loads(snaps[s])
            if not bi:
                continue
            n_day += 1
            d1s = ch._build_d1_candidates(bi, date_idx, closes, params)
            for d1 in d1s:
                t1 = d1['t1_idx']
                t0i = d1['t0_idx']
                if not (t0i < i) or t1 >= i:
                    continue
                if i - t0i > params['mouth_span_max'] + 2:
                    continue
                p2, t2 = _argmax_max(closes, t1, i - 1)
                if p2 is None or t2 is None:
                    continue
                rec = ch._evaluate(daily, ctx, d1, i, p2, t2, params)
                if rec is not None and rec['record_type'] == 'SIGNAL':
                    rows.append((code, D, rec['buy_point'], rec['mouth_price'],
                                 rec['bottom_price'], rec['prior_high_price'],
                                 rec['depth_pct'], rec['handle_dd_pct'],
                                 rec['mouth_to_date_days'],
                                 rec['breakout_close'], rec['breakout_vol_ratio']))
        if ci % 200 == 0:
            print('  ...%d/%d  股票 %d  SIGNAL %d  (%.0fs)'
                  % (ci, len(codes), n_day, len(rows), time.time() - t0), flush=True)

    conn.close()
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('code,date,buy_point,mouth_price,bottom_price,prior_high,'
                'depth_pct,handle_dd_pct,mouth_to_days,breakout_close,vol_ratio\n')
        for r in rows:
            f.write(','.join(str(x) for x in r) + '\n')
    print('逐日回放：%d 只有笔快照  判定 %d 个(股票,交易日)  SIGNAL %d 条  耗时 %.0fs'
          % (n_snap, n_day, len(rows), time.time() - t0))
    print('明细:', OUT)


if __name__ == '__main__':
    main()
