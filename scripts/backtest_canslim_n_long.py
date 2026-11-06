# -*- coding: utf-8 -*-
"""CANSLIM N 维度回测（长样本版）。

隔离变量：只比新旧 N 算法（N1 新高分档 + N2 形态窗口/衰减/上限/趋势延续），
其余维度不参与，避免 C/S/L/I 噪声。

用法：
  python scripts/backtest_canslim_n_long.py                 # 2017-01 起
  python scripts/backtest_canslim_n_long.py --start 2020-01

口径：
  采样      每月最后一个交易日（各月独立截面）
  过滤      采样日前 20 日均成交额 >= --min-amt（默认 5000 万，剔微盘）
  前瞻      close[t+h] / close[t] - 1，h ∈ {60,120,250} 交易日
  日窗口    用日历日差值（与线上 canslim_score.py 一致）
"""
import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import date

import numpy as np

DB = 'file:data/lixinger.db?mode=ro'
LOOKBACK_OLD = 5
LOOKBACK_NEW = 15
DECAY_OLD = [1.0, 0.8, 0.6, 0.4, 0.4]

FAM = {'base_breakout_v2': ('base', 7), 'base_breakout': ('base', 3),
       'double_bottom': ('base', 2), 'flat_base': ('base', 2),
       'saucer_base': ('base', 2), 'cup_handle_v2': ('base', 2),
       'pocket_pivot_v2': ('pivot', 7), 'pocket_pivot': ('pivot', 5),
       'mw_signal': ('mw', 4), 'box_breakout': ('misc', 3),
       'cdl': ('misc', 2), 'cdl_bullish': ('misc', 2),
       'talib': ('misc', 1), 'talib_bullish': ('misc', 1)}


def day_raw(base, fams):
    return min(base + min(fams - 1, 3), 10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2017-01')
    ap.add_argument('--min-amt', type=float, default=5e7)
    a = ap.parse_args()

    con = sqlite3.connect(DB, uri=True)
    con.row_factory = sqlite3.Row

    cal = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM daily_kline_adj WHERE date>=? ORDER BY date",
        (a.start,))]
    ORD = {d: date.fromisoformat(d).toordinal() for d in cal}
    HOR = [60, 120, 250]

    monthend = []
    for i, d in enumerate(cal):
        nxt = cal[i + 1] if i + 1 < len(cal) else None
        if (nxt is None or nxt[:7] != d[:7]) and i + 250 < len(cal):
            monthend.append(d)
    print(f'日历 {len(cal)} 日 {cal[0]}~{cal[-1]} ｜ 采样 {len(monthend)} 个月末 '
          f'{monthend[0]}~{monthend[-1]}', flush=True)

    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT stock_code FROM daily_kline_adj WHERE date>=? ORDER BY stock_code",
        (a.start,))]
    print(f'股票 {len(codes):,} 只，逐只流式处理…', flush=True)

    recs = []
    done = 0
    for code in codes:
        kl = con.execute(
            "SELECT date, close, high, amount FROM daily_kline_adj "
            "WHERE stock_code=? AND date>=? ORDER BY date", (code, a.start)).fetchall()
        if len(kl) < 260:
            continue
        ds = [r['date'] for r in kl]
        cl = np.array([r['close'] or 0.0 for r in kl])
        hi = np.array([r['high'] or 0.0 for r in kl])
        am = np.array([r['amount'] or 0.0 for r in kl])
        pos = {d: i for i, d in enumerate(ds)}

        raw = con.execute(
            "SELECT date, signals_json FROM pattern_scan_signals "
            "WHERE stock_code=? AND date>=? ORDER BY date", (code, a.start)).fetchall()
        sg = []
        for r in raw:
            try:
                arr = json.loads(r['signals_json'] or '[]')
            except Exception:
                continue
            if not isinstance(arr, list):
                continue
            best = {}
            for s in arr:
                if not isinstance(s, dict) or s.get('type') == 'bearish':
                    continue
                e = FAM.get(s.get('source', ''))
                if not e:
                    continue
                fam, base = e
                if fam not in best or base > best[fam]:
                    best[fam] = base
            if best:
                sg.append((r['date'], max(best.values()), len(best)))

        for d in monthend:
            i = pos.get(d)
            if i is None or i < 250:
                continue
            if i >= 20 and am[i - 19:i + 1].mean() < a.min_amt:
                continue
            close = cl[i]
            if close <= 0:
                continue
            high52 = cl[i - 249:i + 1].max()
            if high52 <= 0:
                continue
            dist = (close - high52) / high52 * 100
            n1o = 6 if dist > -5 else 4 if dist > -15 else 0
            n1n = (10 if dist > -3 else 8 if dist > -5 else
                   5 if dist > -10 else 2 if dist > -15 else 0)

            tot_o = 0.0
            per_day = {}
            for sd, base, fams in sg:
                if sd not in ORD:
                    continue
                days = ORD[d] - ORD[sd]
                if days < 0 or days >= LOOKBACK_NEW:
                    continue
                val = day_raw(base, fams)
                if days < LOOKBACK_OLD:
                    tot_o += val * DECAY_OLD[min(days, 4)]
                if sd not in per_day or val > per_day[sd][0]:
                    per_day[sd] = (val, days)
            parts = sorted(per_day.items(), key=lambda kv: ORD[kv[0]])[-3:]
            tot_n = min(sum(v[0] * max(0.2, 1.0 - 0.08 * v[1]) for _, v in parts), 7)
            ma20 = cl[i - 19:i + 1].mean()
            hh20 = hi[i - 19:i + 1].max()
            if close >= ma20 and close >= hh20 * 0.95:
                tot_n = max(tot_n, 3.5)

            fwd, ok = [], True
            for h in HOR:
                if i + h < len(cl):
                    fwd.append(cl[i + h] / cl[i] - 1)
                else:
                    ok = False
                    break
            if ok:
                recs.append((d, code, n1o + min(tot_o, 17), n1n + tot_n, fwd))
        done += 1
        if done % 800 == 0:
            print(f'  {done:,}/{len(codes):,} … 记录 {len(recs):,}', flush=True)

    print(f'有效记录 {len(recs):,} 条')
    con.close()

    by_date = defaultdict(list)
    for r in recs:
        by_date[r[0]].append(r)
    dates = sorted(by_date)
    print(f'覆盖 {len(dates)} 个月末 ｜ 平均截面 {np.mean([len(v) for v in by_date.values()]):.0f} 只')

    def spearman(x, y):
        if len(x) < 50:
            return None
        rx = np.argsort(np.argsort(x, kind='stable'), kind='stable').astype(float)
        ry = np.argsort(np.argsort(y, kind='stable'), kind='stable').astype(float)
        rx -= rx.mean()
        ry -= ry.mean()
        den = (rx @ rx) ** 0.5 * (ry @ ry) ** 0.5
        return float(rx @ ry / den) if den else None

    print()
    print('=' * 78)
    for hi_, h in enumerate(HOR):
        ics_o, ics_n = [], []
        for d in dates:
            v = by_date[d]
            y = [r[4][hi_] for r in v]
            ic_o = spearman([r[2] for r in v], y)
            ic_n = spearman([r[3] for r in v], y)
            if ic_o is not None:
                ics_o.append(ic_o)
            if ic_n is not None:
                ics_n.append(ic_n)

        def stat(x):
            m, s = np.mean(x), np.std(x, ddof=1)
            return m, (m / (s / len(x) ** 0.5) if s else 0.0)

        mo, to = stat(ics_o)
        mn, tn = stat(ics_n)

        def dec(key):
            ax, ay = [], []
            for d in dates:
                for r in by_date[d]:
                    ax.append(r[key])
                    ay.append(r[4][hi_])
            ax, ay = np.array(ax, float), np.array(ay, float)
            order = np.argsort(np.argsort(ax, kind='stable'), kind='stable')
            bins = np.clip(order * 10 // len(order), 0, 9)
            return [ay[bins == k].mean() * 100 for k in range(10)], ay.mean() * 100

        do, allm = dec(2)
        dn, _ = dec(3)
        print(f'前瞻 {h:>3} 日 ｜ 月数 {len(ics_o)} ｜ 全样本均值 {allm:+.2f}%')
        print(f'  IC    旧 {mo:+.4f} (t={to:+.2f})   新 {mn:+.4f} (t={tn:+.2f})   '
              f'新−旧 {mn - mo:+.4f}')
        print('  十分位 旧 ' + ' '.join(f'{v:>6.1f}' for v in do))
        print('        新 ' + ' '.join(f'{v:>6.1f}' for v in dn))
        print(f'  多空差  旧 {do[-1] - do[0]:+.2f}pp   新 {dn[-1] - dn[0]:+.2f}pp')
        print()


if __name__ == '__main__':
    main()
