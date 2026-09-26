# -*- coding: utf-8 -*-
"""
逐规则漏斗：跑逐日回放，统计每条规则各否决多少候选，并给出「到达率 / 放行率」。

到达率 = 走到这一层的候选数 / 全部候选数
放行率 = 通过这一层的比例（1 - 该层否决率）
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

# _evaluate 里的判定顺序（与代码一致）
ORDER = ['3_前高>杯口', '10_前高→杯口≤100日', '5_杯底是区间最低收盘', '4_深度15~40%',
         '6_回升段回撤≤15%', '9a_杯底区前后≤10日', '9b_杯底区前侧≥2日',
         'x_柄部为空', 'V7_杯口已回落', '7b_柄部≥3日', '7_柄撤≤20%',
         '8_柄低在杯身上半部', '12/13_突破失败(收盘或放量)',
         '11_候选超跟踪窗口', 'W3_跌破杯底失效']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=500)
    ap.add_argument('--lookback', type=int, default=2500)
    args = ap.parse_args()

    params = ch.load_params()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    end = conn.execute("SELECT MAX(date) FROM daily_kline_adj").fetchone()[0]
    codes = [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_basic "
        "WHERE stock_code GLOB '[036][0-9][0-9][0-9][0-9][0-9]' ORDER BY stock_code")]
    if args.limit:
        codes = codes[:args.limit]

    tot = {}
    n_cand = n_sig = n_day = 0
    t0 = time.time()
    for ci, code in enumerate(codes, 1):
        daily = ch._load_daily(conn, code, end, args.lookback)
        daily = [k for k in daily if k['close'] is not None]
        if len(daily) < 120:
            continue
        date_idx = {k['date']: i for i, k in enumerate(daily)}
        closes = [k['close'] for k in daily]
        volumes = [k['volume'] for k in daily]
        ctx = {'closes': closes, 'volumes': volumes,
               'vol_ma': ch._rolling_mean(volumes, params['vol_ma_window']),
               'ma': {w: ch._rolling_mean(closes, w) for w in (10, 20, 50)}}
        snaps = {}
        for r in conn.execute("SELECT scan_date, bi_json FROM chanlun_bi_json "
                              "WHERE stock_code=?", (code,)):
            snaps[r['scan_date'][:10]] = r['bi_json']
        if not snaps:
            continue
        snap_dates = sorted(snaps)
        cur_sd, bi = None, None
        for i, k in enumerate(daily):
            D = k['date']
            if i < 60:
                continue
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
                while snap_dates and snap_dates[0] <= D:
                    s = snap_dates.pop(0)
                    cur_sd, bi = s, json.loads(snaps[s])
            if not bi:
                continue
            n_day += 1
            d1s = ch.prepare_d1(
                ch._build_d1_candidates(bi, date_idx, closes, params), closes)
            for d1 in d1s:
                t1, t0i = d1['t1_idx'], d1['t0_idx']
                if not (t0i < i) or t1 >= i:
                    continue
                if i - t0i > params['mouth_span_max'] + params['mouth_to_signal_max']:
                    continue
                lo = t1
                hi = i - 1
                if hi < lo:
                    continue
                p2, t2 = None, None
                for kk in range(lo, hi + 1):
                    v = closes[kk]
                    if p2 is None or v > p2:
                        p2, t2 = v, kk
                n_cand += 1
                f = {}
                rec = ch._evaluate(daily, ctx, d1, i, p2, t2, params, f)
                for rule, c in f.items():
                    tot[rule] = tot.get(rule, 0) + c
                if rec is not None and rec['record_type'] == 'SIGNAL':
                    n_sig += 1
        if ci % 200 == 0:
            print('  ...%d/%d  候选 %d  信号 %d  (%.0fs)'
                  % (ci, len(codes), n_cand, n_sig, time.time() - t0), flush=True)
    conn.close()

    print('\n判定 %d 个(股票,交易日)   候选(股票,日,D1) 合计 %d   最终 SIGNAL %d\n'
          % (n_day, n_cand, n_sig))
    print('%-30s%10s%10s%10s' % ('规则（按判定顺序）', '否决数', '到达率', '通过率'))
    print('-' * 62)
    survived = n_cand
    for rule in ORDER:
        c = tot.get(rule, 0)
        if survived <= 0:
            break
        print('%-30s%10d%9.1f%%%9.1f%%'
              % (rule, c, survived / n_cand * 100, (1 - c / survived) * 100))
        survived -= c
    print('-' * 62)
    print('%-30s%10d%9.1f%%' % ('走到分类（SIGNAL/CANDIDATE）', survived,
                                survived / n_cand * 100))
    print('其中 SIGNAL %d 条（占候选 %.4f%%）' % (n_sig, n_sig / n_cand * 100))


if __name__ == '__main__':
    main()
