# -*- coding: utf-8 -*-
"""市场底部判断：把人工的底部判据固化成每日可算的量化结果。

设计见 docs/product/市场底部判断_PRD.md。

判据分两层：
  分指数（沪深300 / 创业板指 / 科创50）：回撤深度、下跌历时、估值分位
  全市场：缩量比、破净率、杠杆出清、右侧确认
判定分全 5 分，三个指数各 15 分（合计 45），全市场四项各 5 分（合计 20），满分 65 分。
得分 = 实得 ÷ 可用满分，缺数据的判据不进分母。

口径要点：
  · 回撤锚点用 750 个交易日内的收盘最高点，不用历史最高点。沪深300 价格指数
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
ITEM_FULL = 8.0         # 指数层主判据满分（回撤、历时）
ITEM_HALF = 4.0         # 指数层主判据半分
SUB_FULL = 4.0          # 指数层细分判据满分（估值分位、股债差）
SUB_HALF = 2.0
IDX_MAX = 24.0          # 单个指数满分（8 + 8 + 4 + 4）
MKT_FULL = 7.0          # 全市场层单项满分（破净、杠杆、右侧）
MKT_HALF = 3.5
VOL_FULL = 4.0          # 全市场层量能子项满分（缩量）
VOL_HALF = 2.0
TURN_FULL = 3.0         # 全市场层量能子项满分（温和放量）
TURN_HALF = 1.5
MKT_MAX = 28.0          # 全市场满分（缩量 4 + 放量 3 + 破净 7 + 杠杆 7 + 右侧 7）
TOTAL_MAX = IDX_MAX * 3 + MKT_MAX   # 100
VERDICT_FULL = 65.0     # 得分率 >= 该值判「底部区域」
VERDICT_NEAR = 45.0     # 得分率 >= 该值判「接近底部」

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


def _score(v, full, half, higher_better=False, pf=ITEM_FULL, ph=ITEM_HALF):
    """三档给分：满分 pf / 半分 ph / 0。higher_better 表示值越大越好。"""
    if v is None:
        return None
    if higher_better:
        if v >= full:
            return pf
        return ph if v >= half else 0.0
    if v <= full:
        return pf
    return ph if v <= half else 0.0


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

    # 股债差（ERP）= 100/PE − 10 年国债，按指数自身历史取分位。
    # 绝对水平跨指数不可比（创业板 E/P 天然高），所以打分用分位不用绝对值；
    # 页面上同时显示绝对值，读者能看到「沪深300 5.8%」这个常被引用的数。
    bonds = dict(_rows(conn, "SELECT date, y10 FROM bond_yield_daily WHERE date<=? AND y10 IS NOT NULL",
                       (date,)))
    erp_hist = []
    for d, v in _rows(conn, "SELECT date, pe_ttm FROM index_fundamental_daily "
                            "WHERE stock_code=? AND date<=? AND pe_ttm>0 ORDER BY date", (code, date)):
        y = bonds.get(d)
        if y is not None:
            erp_hist.append(100.0 / v - y)
    erp = erp_hist[-1] if erp_hist else None
    erp_p = _pctile(erp_hist, erp)

    items = [
        {'key': 'dd', 'label': '回撤深度', 'max': ITEM_FULL,
         'value': round(dd, 1) if dd is not None else None,
         'unit': '%', 'threshold': '≥%.0f%% 满分，≥%.0f%% 半分' % (dd_full * 100, dd_half * 100),
         'score': _score(dd, dd_full * 100, dd_half * 100, higher_better=True),
         'detail': '高点 %s %.2f' % (peak_date, peak)},
        {'key': 'days', 'label': '下跌历时', 'max': ITEM_FULL,
         'value': days, 'unit': '交易日', 'threshold': '≥150 满分，≥100 半分',
         'score': _score(days, 150, 100, higher_better=True),
         'detail': '自高点起算'},
        {'key': 'value', 'label': '估值分位', 'max': SUB_FULL,
         'value': best_val_pct, 'unit': '%',
         'threshold': 'PE 或 PB 分位 ≤10% 满分，≤20% 半分',
         'score': _score(best_val_pct, 10, 20, pf=SUB_FULL, ph=SUB_HALF),
         'detail': 'PE %.2f（%s%% 分位） / PB %.2f（%s%% 分位），估值日 %s' % (
             pe or 0, pe_p, pb or 0, pb_p, val_date or '—')},
        {'key': 'erp', 'label': '股债差', 'max': SUB_FULL,
         'value': round(erp, 2) if erp is not None else None, 'unit': 'pp',
         'threshold': 'ERP 历史分位 ≥90% 满分，≥80% 半分',
         'score': _score(erp_p, 90, 80, higher_better=True, pf=SUB_FULL, ph=SUB_HALF),
         'detail': 'ERP = 100/PE − 10Y = %.2fpp（%s%% 分位，样本 %d 日）' % (
             erp if erp is not None else 0, erp_p, len(erp_hist))},
    ]
    sc = sum(i['score'] for i in items if i['score'] is not None)
    return {
        'code': code, 'name': name, 'date': cur_date, 'close': round(cur, 2),
        'peak': round(peak, 2), 'peak_date': peak_date,
        'dd': round(dd, 1) if dd is not None else None, 'days': days,
        'pe': round(pe, 2) if pe else None, 'pe_pct': pe_p,
        'pb': round(pb, 2) if pb else None, 'pb_pct': pb_p,
        'dyr': round(dyr, 2) if dyr else None, 'dyr_pct': dyr_p,
        'erp': round(erp, 2) if erp is not None else None, 'erp_pct': erp_p,
        'val_date': val_date,
        'score': round(sc, 1), 'max': IDX_MAX, 'items': items,
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

    # M1 量能：拆两段 —— 先缩量（抛压衰竭），再温和放量（资金承接）
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
    meta['amount_ratio'] = round(ratio, 2) if ratio is not None else None
    items.append({
        'key': 'vol', 'label': '缩量比', 'max': VOL_FULL,
        'value': round(ratio, 2) if ratio is not None else None, 'unit': '倍',
        'threshold': '≤0.70 满分，≤0.90 半分',
        'score': _score(ratio, 0.70, 0.90, pf=VOL_FULL, ph=VOL_HALF),
        'detail': '全市场成交额 ÷ 前 250 日均值（截至 %s）' % (amt_dates[-1] if amt_dates else '—'),
    })

    # M1b 温和放量：5 日均额高于 20 日均额 5% 以上，且最近 3 日逐日站在各自 20 日均额之上
    amt_vals = [amt_map[d] for d in amt_dates]
    ma20_now = sum(amt_vals[-20:]) / 20 if len(amt_vals) >= 20 else None
    ma5_now = sum(amt_vals[-5:]) / 5 if len(amt_vals) >= 5 else None
    expand = bool(ma20_now and ma5_now and ma5_now >= ma20_now * 1.05)
    up3 = False
    if len(amt_vals) >= 23:
        up3 = True
        for k in (1, 2, 3):
            seg = amt_vals[-k - 19:len(amt_vals) - k + 1]
            if not seg or sum(seg) / len(seg) >= amt_vals[-k]:
                up3 = False
                break
    if ma20_now is None or ma5_now is None:
        turn = None        # 上市初期样本不足，不进分母
    elif expand and up3:
        turn = TURN_FULL
    elif expand or up3:
        turn = TURN_HALF
    else:
        turn = 0.0
    meta.update({'amount_ma5': round(ma5_now / 1e8, 0) if ma5_now else None,
                 'amount_ma20': round(ma20_now / 1e8, 0) if ma20_now else None,
                 'amount_expand': expand, 'amount_up3': up3})
    items.append({
        'key': 'turn', 'label': '量能转向', 'max': TURN_FULL,
        'value': round(ma5_now / ma20_now, 2) if (ma5_now and ma20_now) else None, 'unit': '倍',
        'threshold': '5 日均 ≥20 日均 ×1.05 且连续 3 日放量 = 满分',
        'score': turn,
        'detail': '5 日均 %.0f 亿 / 20 日均 %.0f 亿%s%s' % (
            (ma5_now or 0) / 1e8, (ma20_now or 0) / 1e8,
            '，已放量' if expand else '，未放量',
            '，连续 3 日放量' if up3 else ''),
    })

    # M2 破净率（个股基本面数据比指数晚一天，缺则回退到最近有 PB 的交易日）
    pb_row = conn.execute("SELECT date, n_break, n_total FROM mv_break_net "
                          "WHERE date<=? AND n_total>0 ORDER BY date DESC LIMIT 1",
                          (date,)).fetchone()
    pb_date = pb_row[0] if pb_row else None
    r = (pb_row[1], pb_row[2]) if pb_row else None
    net = (r[0] or 0) / r[1] * 100 if r and r[1] else None
    meta['break_net'] = round(net, 1) if net is not None else None
    meta['break_net_n'] = r[1] if r else 0
    meta['break_net_date'] = pb_date
    items.append({
        'key': 'break_net', 'label': '破净率', 'max': MKT_FULL,
        'value': round(net, 1) if net is not None else None, 'unit': '%',
        'threshold': '≥12% 满分，≥10% 半分',
        'score': _score(net, 12, 10, higher_better=True, pf=MKT_FULL, ph=MKT_HALF),
        'detail': '%d 只破净 / %d 只有 PB（截至 %s）' % ((r[0] or 0), (r[1] or 0), pb_date or '—'),
    })

    # M3 杠杆出清
    # 用 daily_margin_history，不用 daily_review_margin：后者的 date 实际存的是前一交易日的
    # 余额（个股逐日核对 review[D] == history[D-1]，全样本 207 天平均绝对差 713 亿），
    # 且 2026-05~07 有未刷新的冻结段，会把年内峰值算低 3,000 亿、回撤算浅 9 个百分点。
    mg = _rows(conn, "SELECT date, SUM(financing_balance)/1e8 AS v FROM daily_margin_history "
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
        m3 = MKT_FULL
    elif margin_dd >= 15 or stop_falling:
        m3 = MKT_HALF
    else:
        m3 = 0.0
    items.append({
        'key': 'margin', 'label': '杠杆出清', 'max': MKT_FULL,
        'value': round(margin_dd, 1) if margin_dd is not None else None, 'unit': '%',
        'threshold': '较 %d 日高点回落 ≥15%%，且近 5 日不再新低 = 满分' % LOOKBACK,
        'score': m3,
        'detail': '两融 %s 亿（%s），%d 日高点 %s 亿（%s）%s' % (
            meta.get('margin', '—'), meta.get('margin_date', '—'), LOOKBACK,
            meta.get('margin_peak', '—'), meta.get('margin_peak_date', '—'),
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
        m4 = MKT_FULL if all(conds) else (MKT_HALF if sum(conds) == 2 else 0.0)
    meta.update({'health': health, 'sell': sell,
                 'ma60': round(ma60, 2) if ma60 else None,
                 'above_ma60': above})
    items.append({
        'key': 'right', 'label': '右侧确认', 'max': MKT_FULL,
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


def _summary(verdict, earned, avail, rate, idx, mkt):
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
    got = [i['label'] for i in mkt if i['score'] is not None and i['score'] >= i['max']]
    miss = [i['label'] for i in mkt if i['score'] is not None and i['score'] < i['max']]
    tail = ''
    if got:
        tail += '；已满足 ' + '、'.join(got)
    if miss:
        tail += '；未满足 ' + '、'.join(miss)
    return '%s · %s/%s 分（%s%%）。%s%s' % (
        verdict, _fmt_pts(earned), _fmt_pts(avail), _fmt_pts(rate), '，'.join(parts), tail)


def _fmt_pts(v):
    """12.0 → '12'，7.5 → '7.5'。"""
    return ('%g' % round(v, 1))


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

    idx_pts = sum(i['score'] for i in idx)
    mkt_pts = sum(m['score'] for m in mkt if m['score'] is not None)
    earned = round(idx_pts + mkt_pts, 1)
    avail = round(IDX_MAX * len(idx)
                  + sum(m['max'] for m in mkt if m['score'] is not None), 1)
    rate = round(earned / avail * 100, 1) if avail else 0.0
    verdict = _verdict(rate)
    meta['data_date'] = date
    meta['idx_pts'] = round(idx_pts, 1)
    meta['mkt_pts'] = round(mkt_pts, 1)
    meta['earned'] = earned
    meta['avail'] = avail
    meta['max_total'] = TOTAL_MAX
    meta['rate'] = rate
    meta['missing'] = [m['label'] for m in mkt if m['score'] is None]
    meta['dropped_indices'] = [n for c, n, _a, _b in INDICES if c not in {i['code'] for i in idx}]
    return {
        'date': date, 'verdict': verdict, 'score': rate,
        'earned': earned, 'avail': avail,
        'summary': _summary(verdict, earned, avail, rate, idx, mkt),
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
    print('[market_bottom] %s  %s  %g/%g 分（%.1f%%）' % (
        d['date'], d['verdict'], d['earned'], d['avail'], d['score']))
    print('  ' + d['summary'])
    for i in d['indices']:
        print('  %-8s 回撤 %5.1f%%  历时 %3d  PE %6.2f (%s%%)  PB %5.2f (%s%%)  ERP %.2fpp (%s%%)  得分 %g/%g' % (
            i['name'], i['dd'], i['days'], i['pe'] or 0, i['pe_pct'],
            i['pb'] or 0, i['pb_pct'], i['erp'] or 0, i['erp_pct'], i['score'], i['max']))
        for it in i['items']:
            print('      %-8s %s%s  得分 %s/%g' % (
                it['label'], it['value'], it['unit'] or '',
                '—' if it['score'] is None else ('%g' % round(it['score'], 1)), it['max']))
    for m in d['market']:
        sc = '—' if m['score'] is None else '%g/%g' % (round(m['score'], 1), m['max'])
        print('  %-8s %-8s %s  得分 %s' % (m['label'], m['value'], m['detail'], sc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
