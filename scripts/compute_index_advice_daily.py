#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日计算指数操作建议（RSI 多周期 + 锚定回撤 + 买入分/风险分），写入 index_advice_daily。

由 scripts/daily_update.py 调用（任务 37）。幂等：同一 (code, date) 重复跑覆盖。

**计算逻辑全部来自 src/calc/index_advice.py**，与 src/server.py 的 API 同一份实现，
因此不会再出现「日更表与页面数字对不上」的分叉。

用法：
    python scripts/compute_index_advice_daily.py
    python scripts/compute_index_advice_daily.py --date 2026-10-16
    python scripts/compute_index_advice_daily.py --backfill --start 2012-12-31
"""
import argparse
import json
import pathlib
import sqlite3
import sys
from datetime import datetime

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / 'src'))

from calc.index_advice import (  # noqa: E402
    FCF_DD_BINS, FCF_INDICES, FCF_LABEL,
    fcf_advice_v2, fcf_anchored_dd, fcf_rsi,
)

DB = str(_ROOT / 'data/lixinger.db')



def ensure_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS index_advice_daily (
            stock_code   TEXT NOT NULL,
            date         TEXT NOT NULL,
            close        REAL,
            close_tr     REAL,
            dd_anchored  REAL,
            dd_tr        REAL,
            rsi6         REAL,
            rsi14        REAL,
            rsi20        REAL,
            rsi60        REAL,
            dd_bin       TEXT,
            buy_score    INTEGER,
            risk_score   INTEGER,
            advice_level TEXT,
            advice_label TEXT,
            score_detail TEXT,
            updated_at   TEXT,
            PRIMARY KEY (stock_code, date)
        )
    """)
    con.commit()


def load_series(con, code, end_date):
    px = dict(con.execute(
        "SELECT date, close FROM index_daily_kline "
        "WHERE stock_code=? AND kline_type='normal' AND date<=? ORDER BY date",
        (code, end_date)))
    tr = dict(con.execute(
        "SELECT date, close FROM index_full_return_daily "
        "WHERE stock_code=? AND date<=? ORDER BY date", (code, end_date)))
    return px, tr


def _r(v, nd=2):
    return round(v, nd) if v is not None else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='目标日期，默认取库内最新交易日')
    ap.add_argument('--backfill', action='store_true', help='回补全部历史')
    ap.add_argument('--start', default='2012-12-31')
    a = ap.parse_args()

    con = sqlite3.connect(DB)
    ensure_table(con)

    now = datetime.now().isoformat(timespec='seconds')
    total = 0
    for code, name in FCF_INDICES:
        if a.date:
            end = a.date
        else:
            r = con.execute("SELECT MAX(date) FROM index_daily_kline "
                            "WHERE stock_code=? AND kline_type='normal'",
                            (code,)).fetchone()
            end = r[0] if r and r[0] else None
        if not end:
            print(f'  [SKIP] {code} 无数据')
            continue

        px, tr = load_series(con, code, end)
        dates = sorted(px)
        if len(dates) < 250:
            print(f'  [SKIP] {code} 样本不足（{len(dates)} 日）')
            continue

        closes = [px[d] for d in dates]
        last_tr, trs = None, []
        for d in dates:
            x = tr.get(d)
            if x is not None:
                last_tr = x
            trs.append(last_tr if last_tr is not None else closes[len(trs)])

        dd_px = fcf_anchored_dd(closes)
        dd_tr = fcf_anchored_dd(trs)
        rs = {p: fcf_rsi(trs, p) for p in (6, 14, 20, 60)}

        targets = range(len(dates)) if a.backfill else [len(dates) - 1]
        rows = []
        for i in targets:
            if a.backfill and dates[i] < a.start:
                continue
            dd = dd_tr[i]
            adv = fcf_advice_v2(dd, rs[60][i], rs[20][i])
            r20 = rs[20][i]
            lo_hi = next(((lo, hi) for lo, hi, *_ in FCF_DD_BINS if lo <= dd < hi),
                         (None, None))
            rows.append((
                code, dates[i], round(closes[i], 4), round(trs[i], 4),
                round(dd_px[i], 2), round(dd, 2),
                _r(rs[6][i]), _r(rs[14][i]), _r(r20), _r(rs[60][i]),
                f'{lo_hi[0]}~{lo_hi[1]}%' if lo_hi[0] is not None else None,
                adv['buy_score'], adv['risk_score'],
                adv['level'], FCF_LABEL[adv['level']],
                json.dumps(adv['score_detail'], ensure_ascii=False), now,
            ))

        con.executemany(
            'INSERT OR REPLACE INTO index_advice_daily VALUES '
            '(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
        con.commit()
        total += len(rows)
        last = rows[-1]
        print(f'  {code} {name}: {len(rows)} 行 | 最新 {last[1]} '
              f'回撤 {last[5]:.1f}% RSI20 {last[8]} 买{last[11]} 险{last[12]} {last[14]}')

    con.close()
    print(f'  完成，共写入 {total} 行')
    return 0


if __name__ == '__main__':
    sys.exit(main())
