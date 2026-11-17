# -*- coding: utf-8 -*-
"""市场底部判断：把人工的底部判据固化成每日可算的量化结果。

设计见 docs/product/市场底部判断_PRD.md。

判据分两层：
  分指数（沪深300 / 创业板指 / 科创50）：回撤深度、下跌历时、估值分位
  全市场：缩量比、破净率、杠杆出清、右侧确认
两层各占一半，合成 0~100 的综合分，映射为「底部区域 / 接近底部 / 未到底部」。

口径要点：
  · 回撤用「近 250 交易日收盘最高点」做锚，不用历史最高点。沪深300 价格指数
    从未超过 2007 年 5,891 点，用历史最高点会把 2016 年之后每次下跌都算成
    距 2007 年泡沫的回撤。
  · PE / PB 分位用该指数自身全部可用历史，科创50 只有 2020-07 起。
  · 缩量比与破净率是全市场口径，不能按指数拆分。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

DB_PATH = 'data/lixinger.db'

INDICES = [
    ('000300', '沪深300', 0.30, 0.20),
    ('399006', '创业板指', 0.50, 0.40),
    ('000688', '科创50', 0.50, 0.40),
]

LOOKBACK = 750          # 周期高点回看交易日（约 3 年，覆盖一轮牛熊）
IDX_W = 0.7             # 指数层权重：位置主要由价格与估值决定
MKT_W = 0.3             # 全市场层权重：缩量、破净、杠杆、右侧做确认
VERDICT_FULL = 65.0     # 综合分 >= 该值判「底部区域」
VERDICT_NEAR = 45.0     # 综合分 >= 该值判「接近底部」

TABLE = """
CREATE TABLE IF NOT EXISTS market_bottom_daily (
    date       TEXT PRIMARY KEY,
    verdict    TEXT,
    score      REAL,
    summary    TEXT,
    payload    TEXT,
    updated_at TEXT
)
"""


# ── 基础工具 ────────────────────────────────────────────────
def _rows(conn, sql, args=()):
    return conn.execute(sql, args).fetchall()


def _one(conn, sql, args=()):
    r = conn.execute(sql, args).fetchone()
    return r[0] if r else None


def _pctile(vals, v):
    """升序分位：小于 v 的占比，0~100。"""
    if v is None or not vals:
        return None
    return round(sum(1 for x in vals if x < v) / len(vals) * 100, 1)


def _score(v, full, half, higher_better=False):
    """三档打分：满 1 / 半 0.5 / 0。higher_better 表示值越大越好。"""
    if v is None:
        return 0.0
    if higher_better:
        if v >= full:
            return 1.0
        return 0.5 if v >= half else 0.0
    if v <= full:
        return 1.0
    return 0.5 if v <= half else 0.0


def _avg(vals):
    """只对可用项求均值。缺数据的判据不进分母，否则历史日期永远判不到底部。"""
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _closes(conn, code, until):
    return _rows(conn, "SELECT date, close FROM index_daily_kline "
                       "WHERE stock_code=? AND kline_type='normal' AND date<=? AND close>0 "
                       "ORDER BY date", (code, until))


# ── 分指数 ──────────────────────────────────────────────────
def _index_block(conn, code, name, dd_full, dd_half, date):
    rows = _closes(conn, code, date)
    if len(rows) < 60:
        return None
    cur_date, cur = rows[-1]
    window = rows[-LOOKBACK:]
    peak_date, peak = max(window, key=lambda x: x[1])
    dd = (peak - cur) / peak * 100 if peak else None
    days = sum(1 for d, _ in rows if d > peak_date)

    pe_hist = [r[0] for r in _rows(
        conn, "SELECT pe_ttm FROM index_fundamental_daily WHERE stock_code=? AND date<=? "
              "AND pe_ttm IS NOT NULL AND pe_ttm>0 ORDER BY date", (code, date))]
    pb_hist = [r[0] for r in _rows(
        conn, "SELECT pb FROM index_fundamental_daily WHERE stock_code=? AND date<=? "
              "AND pb IS NOT NULL AND pb>0 ORDER BY date", (code, date))]
    dyr_hist = [r[0] for r in _rows(
        conn, "SELECT dyr FROM index_fundamental_daily WHERE stock_code=? AND date<=? "
              "AND dyr IS NOT NULL AND dyr>0 ORDER BY date", (code, date))]

    def _last(seq):
        return seq[-1] if seq else None

    pe, pb, dyr = _last(pe_hist), _last(pb_hist), _last(dyr_hist)
    pe_p = _pctile(pe_hist, pe)
    pb_p = _pctile(pb_hist, pb)
    dyr_p = _pctile(dyr_hist, dyr)
    if dyr is not None and dyr < 1:
        dyr = dyr * 100

    best_val_pct = min([x for x in (pe_p, pb_p) if x is not None], default=None)
    val_date = _one(conn, "SELECT MAX(date) FROM index_fundamental_daily "
                          "WHERE stock_code=? AND date<=? AND pe_ttm IS NOT NULL", (code, date))

    items = [
        {'key': 'dd', 'label': '回撤深度',
         'value': round(dd, 1) if dd is not None else None,
         'unit': '%', 'threshold': '≥%.0f%% 满分，≥%.0f%% 半分' % (dd_full * 100, dd_half * 100),
         'score': _score(dd, dd_full * 100, dd_half * 100, higher_better=True),
         'detail': '高点 %s %.2f' % (peak_date, peak)},
        {'key': 'days', 'label': '下跌历时',
         'value': days, 'unit': '交易日', 'threshold': '≥150 满分，≥100 半分',
         'score': _score(days, 150, 100, higher_better=True),
         'detail': '自高点起算'},
        {'key': 'value', 'label': '估值分位',
         'value': best_val_pct, 'unit': '%',
         'threshold': 'PE 或 PB 分位 ≤10% 满分，≤20% 半分',
         'score': _score(best_val_pct, 10, 20),
         'detail': 'PE %.2f（%s%% 分位） / PB %.2f（%s%% 分位），估值日 %s' % (
             pe or 0, pe_p, pb or 0, pb_p, val_date or '—')},
    ]
    sc = _avg([i['score'] for i in items if i['value'] is not None])
    return {
        'code': code, 'name': name, 'date': cur_date, 'close': round(cur, 2),
        'peak': round(peak, 2), 'peak_date': peak_date,
        'dd': round(dd, 1) if dd is not None else None, 'days': days,
        'pe': round(pe, 2) if pe else None, 'pe_pct': pe_p,
        'pb': round(pb, 2) if pb else None, 'pb_pct': pb_p,
        'dyr': round(dyr, 2) if dyr else None, 'dyr_pct': dyr_p,
        'val_date': val_date,
        'score': round(sc * 100, 1), 'items': items,
    }


def _ensure_amount(conn, date):
    """保证派生表 mv_market_amount 有当日行；缺则现算一次（单日聚合，走索引）。"""
    conn.execute("CREATE TABLE IF NOT EXISTS mv_market_amount (date TEXT PRIMARY KEY, amt REAL)")
    if not _one(conn, "SELECT 1 FROM mv_market_amount WHERE date=?", (date,)):
        r = conn.execute("SELECT SUM(amount) FROM daily_kline WHERE date=? AND amount>0",
                         (date,)).fetchone()
        if r and r[0]:
            conn.execute("INSERT OR REPLACE INTO mv_market_amount(date, amt) VALUES (?,?)",
                         (date, r[0]))
            conn.commit()


def _ensure_break_net(conn, date):
    """派生表 mv_break_net：缓存每日破净家数，避免每次全表扫 1,090 万行。

    个股基本面比指数晚一个交易日，所以当日往往还没有数据。没有数据时不写缓存
    （写 0/0 哨兵会让这一天永远不再重算），交给上层回退到最近有数据的日期。
    """
    conn.execute("CREATE TABLE IF NOT EXISTS mv_break_net "
                 "(date TEXT PRIMARY KEY, n_break INTEGER, n_total INTEGER)")
    row = conn.execute("SELECT n_total FROM mv_break_net WHERE date=?", (date,)).fetchone()
    if row and row[0]:
        return
    r = conn.execute("SELECT SUM(CASE WHEN value<1 THEN 1 ELSE 0 END), COUNT(*) "
                     "FROM fundamental_indicator WHERE date=? AND metric_code='pb' AND value>0",
                     (date,)).fetchone()
    if not r or not r[1]:
        conn.execute("DELETE FROM mv_break_net WHERE date=?", (date,))
        return
    conn.execute("INSERT OR REPLACE INTO mv_break_net(date, n_break, n_total) VALUES (?,?,?)",
                 (date, r[0] or 0, r[1]))
    conn.commit()


# ── 全市场 ──────────────────────────────────────────────────
def _market_block(conn, date):
    items = []
    meta = {}
    _ensure_amount(conn, date)
    _ensure_break_net(conn, date)

    # M1 缩量比
    amt_dates = [r[0] for r in _rows(conn, "SELECT date FROM mv_market_amount WHERE date<=? "
                                           "ORDER BY date", (date,))]
    amt_map = dict(_rows(conn, "SELECT date, amt FROM mv_market_amount WHERE date<=?", (date,)))
    ratio = None
    if amt_dates:
        d0 = amt_dates[-1]
        hist = amt_dates[:-1][-250:]
        base = sum(amt_map[x] for x in hist) / len(hist) if hist else None
        if base:
            ratio = amt_map[d0] / base
    meta['amount_ratio'] = round(ratio, 2) if ratio else None
    items.append({
        'key': 'vol', 'label': '缩量比',
        'value': round(ratio, 2) if ratio else None, 'unit': '倍',
        'threshold': '≤0.70 满分，≤0.90 半分',
        'score': _score(ratio, 0.70, 0.90),
        'detail': '全市场成交额 ÷ 前 250 日均值（截至 %s）' % (amt_dates[-1] if amt_dates else '—'),
    })

    # M2 破净率（个股基本面数据比指数晚一天，缺则回退到最近有 PB 的交易日）
    pb_row = conn.execute("SELECT date, n_break, n_total FROM mv_break_net "
                          "WHERE date<=? AND n_total>0 ORDER BY date DESC LIMIT 1",
                          (date,)).fetchone()
    pb_date = pb_row[0] if pb_row else None
    r = (pb_row[1], pb_row[2]) if pb_row else None
    net = (r[0] or 0) / r[1] * 100 if r and r[1] else None
    meta['break_net'] = round(net, 1) if net else None
    meta['break_net_n'] = r[1] if r else 0
    meta['break_net_date'] = pb_date
    items.append({
        'key': 'break_net', 'label': '破净率',
        'value': round(net, 1) if net else None, 'unit': '%',
        'threshold': '≥12% 满分，≥10% 半分',
        'score': _score(net, 12, 10, higher_better=True),
        'detail': '%d 只破净 / %d 只有 PB（截至 %s）' % ((r[0] or 0), (r[1] or 0), pb_date or '—'),
    })

    # M3 杠杆出清
    mg = _rows(conn, "SELECT date, SUM(fb_balance)/1e8 AS v FROM daily_review_margin "
                     "WHERE date<=? GROUP BY date ORDER BY date", (date,))
    margin_dd = None
    stop_falling = False
    peak_date = peak_v = None
    if mg:
        win = mg[-LOOKBACK:]
        peak_date, peak_v = max(win, key=lambda x: x[1])
        cur_v = mg[-1][1]
        margin_dd = (peak_v - cur_v) / peak_v * 100 if peak_v else None
        # 「不再新低」= 最新一天不是最近 5 日的低点。不能写成 >= min(全部 5 日)，
        # 那是恒真（今天必然 >= 自己的最小值），会让杠杆项永远至少拿半分。
        last5 = [x[1] for x in mg[-5:]]
        stop_falling = len(last5) >= 5 and last5[-1] > min(last5[:-1])
        meta.update({'margin': round(cur_v, 0), 'margin_peak': round(peak_v, 0),
                     'margin_peak_date': peak_date, 'margin_date': mg[-1][0]})
    meta['margin_dd'] = round(margin_dd, 1) if margin_dd is not None else None
    if margin_dd is None:
        m3 = None          # 无数据 → 该判据不进分母
    elif margin_dd >= 15 and stop_falling:
        m3 = 1.0
    elif margin_dd >= 15 or stop_falling:
        m3 = 0.5
    else:
        m3 = 0.0
    items.append({
        'key': 'margin', 'label': '杠杆出清',
        'value': round(margin_dd, 1) if margin_dd is not None else None, 'unit': '%',
        'threshold': '较 %d 日高点回落 ≥15%%，且近 5 日不再新低 = 满分' % LOOKBACK,
        'score': m3,
        'detail': '两融 %s 亿，高点 %s 亿（%s）%s' % (
            meta.get('margin', '—'), meta.get('margin_peak', '—'),
            meta.get('margin_peak_date', '—'),
            '，近 5 日仍在新低' if (m3 is not None and not stop_falling) else
            ('，近 5 日止跌' if m3 is not None else '，无两融数据')),
    })

    # M4 右侧确认
    health = _one(conn, "SELECT total_score FROM market_health_daily WHERE date<=? "
                        "ORDER BY date DESC LIMIT 1", (date,))
    sell = _one(conn, "SELECT total_score FROM market_sell_score_daily WHERE date<=? "
                      "ORDER BY date DESC LIMIT 1", (date,))
    rows = _rows(conn, "SELECT close FROM index_daily_kline WHERE stock_code='000300' "
                       "AND kline_type='normal' AND date<=? AND close>0 ORDER BY date DESC LIMIT 60",
                 (date,))
    ma60 = sum(x[0] for x in rows) / len(rows) if len(rows) >= 60 else None
    above = (rows[0][0] > ma60) if (rows and ma60) else None
    conds = [bool(health and health >= 50), bool(sell is not None and sell <= 60), bool(above)]
    if health is None or sell is None:
        m4 = None          # 两融/健康分只从 2026-01 起，历史日期这题没数据，不进分母
    else:
        m4 = 1.0 if all(conds) else (0.5 if sum(conds) == 2 else 0.0)
    meta.update({'health': health, 'sell': sell,
                 'ma60': round(ma60, 2) if ma60 else None,
                 'above_ma60': above})
    items.append({
        'key': 'right', 'label': '右侧确认',
        'value': sum(conds) if m4 is not None else None, 'unit': '/3 项',
        'threshold': '健康分≥50 且 卖出分≤60 且 站上 MA60',
        'score': m4,
        'detail': '健康分 %s · 卖出分 %s · %s MA60（%s）' % (
            health if health is not None else '—',
            sell if sell is not None else '—',
            ('站上' if above else '低于') if above is not None else '无',
            round(ma60, 2) if ma60 else '—'),
    })

    return items, meta


# ── 汇总 ────────────────────────────────────────────────────
def _verdict(score):
    if score >= VERDICT_FULL:
        return '底部区域'
    if score >= VERDICT_NEAR:
        return '接近底部'
    return '未到底部'


def _summary(verdict, score, idx, mkt):
    by = {i['name']: i for i in idx}

    def _one_txt(name):
        it = by.get(name)
        if not it:
            return None
        dd = it.get('dd')
        pe_p = it.get('pe_pct')
        if dd is None:
            return '%s 无行情' % name
        if pe_p is None:
            return '%s 回撤 %.1f%%' % (name, dd)
        return '%s 回撤 %.1f%%、PE 分位 %.0f%%' % (name, dd, pe_p)

    parts = [x for x in (_one_txt('沪深300'), _one_txt('创业板指'), _one_txt('科创50')) if x]
    got = [i['label'] for i in mkt if i['score'] is not None and i['score'] >= 1.0]
    miss = [i['label'] for i in mkt if i['score'] is not None and i['score'] < 1.0]
    tail = ''
    if got:
        tail += '；已满足 ' + '、'.join(got)
    if miss:
        tail += '；未满足 ' + '、'.join(miss)
    return '%s · 综合分 %.0f。%s%s' % (verdict, score, '，'.join(parts), tail)


def compute(conn, date=None):
    """算某日的底部判断。date 缺省取最近交易日。"""
    conn.row_factory = sqlite3.Row
    if not date:
        date = _one(conn, "SELECT MAX(date) FROM index_daily_kline WHERE kline_type='normal'")
    idx = []
    for code, name, full, half in INDICES:
        b = _index_block(conn, code, name, full, half, date)
        if b:
            idx.append(b)
    if not idx:
        return None
    mkt, meta = _market_block(conn, date)

    idx_avg = _avg([i['score'] / 100.0 for i in idx])
    mkt_avg = _avg([m['score'] for m in mkt])
    score = round((idx_avg * IDX_W + mkt_avg * MKT_W) * 100, 1)
    verdict = _verdict(score)
    meta['data_date'] = date
    meta['idx_avg'] = round(idx_avg * 100, 1)
    meta['mkt_avg'] = round(mkt_avg * 100, 1)
    meta['missing'] = [m['label'] for m in mkt if m['score'] is None]
    meta['dropped_indices'] = [n for c, n, _a, _b in INDICES if c not in {i['code'] for i in idx}]
    return {
        'date': date, 'verdict': verdict, 'score': score,
        'summary': _summary(verdict, score, idx, mkt),
        'indices': idx, 'market': mkt, 'meta': meta,
    }


def save(conn, d):
    conn.execute(TABLE)
    conn.execute(
        "INSERT OR REPLACE INTO market_bottom_daily(date, verdict, score, summary, payload, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (d['date'], d['verdict'], d['score'], d['summary'],
         json.dumps({'indices': d['indices'], 'market': d['market'], 'meta': d['meta']},
                    ensure_ascii=False),
         datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()


def main():
    import argparse
    ap = argparse.ArgumentParser(description='市场底部判断')
    ap.add_argument('--date', help='YYYY-MM-DD，缺省最近交易日')
    ap.add_argument('--db', default=DB_PATH)
    a = ap.parse_args()
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    d = compute(conn, a.date)
    if not d:
        print('[market_bottom] 无数据')
        return 1
    save(conn, d)
    print('[market_bottom] %s  %s  综合分 %.0f' % (d['date'], d['verdict'], d['score']))
    print('  ' + d['summary'])
    for i in d['indices']:
        print('  %-8s 回撤 %5.1f%%  历时 %3d  PE %6.2f (%s%%)  PB %5.2f (%s%%)  得分 %.0f' % (
            i['name'], i['dd'], i['days'], i['pe'] or 0, i['pe_pct'],
            i['pb'] or 0, i['pb_pct'], i['score']))
    for m in d['market']:
        print('  %-8s %s  %s' % (m['label'], m['value'], m['detail']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
