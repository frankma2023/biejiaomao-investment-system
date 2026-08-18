# -*- coding: utf-8 -*-
"""
src/scanners/red_dividend_metrics.py — 红利指数温度计引擎（实时计算）
=====================================================================
三维指标：拥挤度 / 恐慌贪婪 / 股债性价比息差 → 温度计合成（v1.1 标定权重）

① 拥挤度（0-100）：交易热度 50%（成交额占比120日分位×0.5 + 换手率120日分位×0.5）
                    + 估值水位 50%（PE×0.3 + PB×0.3 + dyr_pct取反×0.4）
   —— 红利语义：拥挤 = 避险资金涌入压低股息率（dyr_pct 低 → 拥挤高）
② 恐慌贪婪（0=恐慌/100=贪婪）：ATR(20)252日百分位 + 250日回撤252日百分位 + 5日动量倒数
③ 股债息差：dyr×100 − 10年国债收益率（bond_yield_daily，中债估值）
④ 温度计 v1.1 = 50 + (拥挤度−50)×0.40 + (100−恐慌贪婪−50)×0.20 + (100−息差250日分位−50)×0.40
   权重由实验标定（2026-08-17，analysis/red_temp_calibrate.py）：冷≤30=买入增强、热≥65=回避区

口径：全部实时计算（支持任意 date 回看），不存表。
"""
import os
import sys
import sqlite3
import math

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')


def _pct_rank(values, v):
    """v 在 values 中的百分位（0~1）"""
    s = sorted(x for x in values if x is not None)
    if not s:
        return 0.5
    return sum(1 for x in s if x <= v) / len(s)


def _get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def _load_kline(db, code, target_date, limit=600):
    rows = db.execute("""
        SELECT date, open, high, low, close, volume, amount FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date<=?
        ORDER BY date DESC LIMIT ?
    """, (code, target_date, limit)).fetchall()
    return list(reversed([dict(r) for r in rows]))


def _load_fund(db, code, target_date, limit=600):
    rows = db.execute("""
        SELECT date, to_r, pe_ttm_pct, pb_pct, dyr, dyr_pct FROM index_fundamental_daily
        WHERE stock_code=? AND date<=?
        ORDER BY date DESC LIMIT ?
    """, (code, target_date, limit)).fetchall()
    return list(reversed([dict(r) for r in rows]))


def _load_market_amount(db, target_date, limit=600):
    """中证全指 000985 成交额（全市场口径）"""
    rows = db.execute("""
        SELECT date, amount FROM index_daily_kline
        WHERE stock_code='000985' AND kline_type='normal' AND date<=?
        ORDER BY date DESC LIMIT ?
    """, (target_date, limit)).fetchall()
    return list(reversed([dict(r) for r in rows]))


def _load_bond_y10(db, target_date):
    """≤date 的最近 10 年国债收益率"""
    r = db.execute("""
        SELECT date, y10 FROM bond_yield_daily
        WHERE date<=? AND y10 IS NOT NULL ORDER BY date DESC LIMIT 1
    """, (target_date,)).fetchone()
    return (r['date'], r['y10']) if r else (None, None)


# ───────────────────────────────
# ① 拥挤度
# ───────────────────────────────
def compute_crowding(db, code, target_date):
    k = _load_kline(db, code, target_date, 300)
    mkt = _load_market_amount(db, target_date, 300)
    f = _load_fund(db, code, target_date, 300)

    if len(k) < 40 or len(f) < 40 or not mkt:
        return None

    # 成交额占比序列（指数amount/全市场amount，120日窗口）
    mkt_map = {r['date']: r['amount'] for r in mkt if r['amount']}
    amt_ratio = []
    for r in k[-120:]:
        ma = mkt_map.get(r['date'])
        if ma and r['amount']:
            amt_ratio.append(r['amount'] / ma)
    heat_amt = _pct_rank(amt_ratio, amt_ratio[-1]) * 100 if amt_ratio else 50

    # 换手率分位
    to_rs = [r['to_r'] for r in f if r['to_r'] is not None]
    heat_to = _pct_rank(to_rs[-120:], to_rs[-1]) * 100 if to_rs else 50

    heat = heat_amt * 0.5 + heat_to * 0.5  # 交易热度 50%

    # 估值水位 50%：PE×0.3 + PB×0.3 + dyr_pct取反×0.4
    pe_pct = (f[-1]['pe_ttm_pct'] or 0) * 100 if f[-1]['pe_ttm_pct'] is not None else 50
    pb_pct = (f[-1]['pb_pct'] or 0) * 100 if f[-1]['pb_pct'] is not None else 50
    dyr_pct_inv = 100 - (f[-1]['dyr_pct'] or 0) * 100 if f[-1]['dyr_pct'] is not None else 50
    valuation = pe_pct * 0.3 + pb_pct * 0.3 + dyr_pct_inv * 0.4

    score = heat * 0.5 + valuation * 0.5
    # O8：数据质量标记（heat/valuation 内部缺失时已 fallback 50）
    missing = []
    if not amt_ratio:
        missing.append('成交额占比')
    if not to_rs:
        missing.append('换手率')
    if f[-1]['pe_ttm_pct'] is None:
        missing.append('PE分位')
    if f[-1]['pb_pct'] is None:
        missing.append('PB分位')
    if f[-1]['dyr_pct'] is None:
        missing.append('股息率分位')
    data_quality = 'partial(' + ','.join(missing) + ')' if missing else 'full'
    if score < 30:
        level = '低拥挤'
    elif score < 60:
        level = '正常'
    elif score < 80:
        level = '偏高'
    else:
        level = '高拥挤'
    return {'score': round(score, 1), 'level': level,
            'heat_score': round(heat, 1), 'valuation_score': round(valuation, 1),
            'dyr_pct_inv': round(dyr_pct_inv, 1), 'data_quality': data_quality}


# ───────────────────────────────
# ② 恐慌贪婪
# ───────────────────────────────
def compute_fear_greed(db, code, target_date):
    k = _load_kline(db, code, target_date, 600)  # 600条：252日回撤分位需要 252+250 样本（B1 修复）
    if len(k) < 300:
        return None
    closes = [r['close'] for r in k]

    # 子指标1：ATR(20)/close 的 252 日百分位（W1 修复：分位基准用滚动 ATR20 序列）
    ranges = []
    for r in k[-252:]:
        if r['high'] and r['low'] and r['close']:
            ranges.append((r['high'] - r['low']) / r['close'] * 100)
    atr_series = []
    for i in range(19, len(ranges)):
        atr_series.append(sum(ranges[i - 19:i + 1]) / 20)
    if not atr_series:
        return None
    atr20 = atr_series[-1]
    vol_pct = _pct_rank(atr_series, atr20) * 100

    # 子指标2：250日回撤深度 252日百分位
    seg = closes[-250:]
    high250 = max(seg)
    dd = (high250 - closes[-1]) / high250 * 100
    dd_hist = []
    for i in range(252, len(closes)):
        w = closes[i - 249:i + 1]
        dd_hist.append((max(w) - w[-1]) / max(w) * 100)
    dd_pct = _pct_rank(dd_hist, dd) * 100 if dd_hist else 50

    # 子指标3：5日动量倒数
    ret_5d = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 and closes[-6] else 0
    momentum_pct = max(0, -ret_5d) / 10 * 100

    fear_raw = (vol_pct + dd_pct + momentum_pct) / 3
    composite = round(max(0.0, min(100.0, 100 - fear_raw)), 1)  # W2：clamp 防极端动量击穿 0-100
    if composite >= 80:
        level = '贪婪区'
    elif composite <= 20:
        level = '恐慌区'
    else:
        level = '正常'
    return {'score': composite, 'level': level,
            'vol_pct': round(vol_pct, 1), 'dd_pct': round(dd_pct, 1),
            'momentum_pct': round(momentum_pct, 1), 'dd_now': round(dd, 1)}


# ───────────────────────────────
# ③ 股债息差 + 序列
# ───────────────────────────────
def compute_spread(db, code, target_date):
    f = _load_fund(db, code, target_date, 2600)
    if len(f) < 40:
        return None
    bond_date, y10 = _load_bond_y10(db, target_date)

    # 序列：股息率 × 国债收益率（日期并集）
    bond_rows = db.execute("""
        SELECT date, y10 FROM bond_yield_daily
        WHERE date<=? AND y10 IS NOT NULL ORDER BY date
    """, (target_date,)).fetchall()
    bond_map = {r['date']: r['y10'] for r in bond_rows}
    f_map = {r['date']: (r['dyr'] or 0) * 100 for r in f if r['dyr'] is not None}

    dates_all = sorted(set(list(f_map.keys()) + list(bond_map.keys())))
    series = []
    for d in dates_all:
        if d < '2018-01-01':
            continue
        dy = f_map.get(d)
        by = bond_map.get(d)
        if dy is not None and by is not None:
            series.append({'date': d, 'dyr': round(dy, 2), 'bond': round(by, 4), 'spread': round(dy - by, 2)})

    if not series:
        return None
    cur = series[-1]
    spreads = [s['spread'] for s in series]
    pct_all = _pct_rank(spreads, cur['spread']) * 100
    spreads_250 = spreads[-250:]  # O7：最近 250 个有效样本点（交易日交集序列，非严格自然日；国债缺口时窗口微漂移，量级可忽略）
    pct_250 = _pct_rank(spreads_250, cur['spread']) * 100

    return {'value': cur['spread'], 'dyr': cur['dyr'], 'bond_yield': cur['bond'],
            'bond_date': cur['date'], 'pct_250': round(pct_250, 1), 'pct_all': round(pct_all, 1),
            'series': series}  # W2 修复：全量输出（约2100点）


# ───────────────────────────────
# ④ 温度计合成（v1.1 标定：pct_250 + 权重 0.40/0.20/0.40）
# ───────────────────────────────
def compose_temperature(crowd, fg, spread):
    if not crowd or not fg or not spread:
        return None
    # v1.1：息差分位用 250 日滚动（pct_250），权重 0.40/0.20/0.40（实验标定，analysis/red_temp_calibrate.py）
    t = 50 + (crowd['score'] - 50) * 0.40 \
          + (100 - fg['score'] - 50) * 0.20 \
          + (100 - spread['pct_250'] - 50) * 0.40
    t = round(max(0, min(100, t)), 1)
    if t >= 65:  # W4：阈值对齐实验口径（≥65=回避区，60日胜率43%/20日36%）
        label = '偏热区（回避/减仓区）'
    elif t >= 55:
        label = '微热区（定投不追高）'
    elif t <= 30:
        label = '偏冷区（买入增强区）'
    else:
        label = '中性区'
    return {'value': t, 'label': label}


def compute_all(code, target_date):
    """主入口：返回三维指标 + 温度计 + 息差序列"""
    db = _get_db()
    try:
        crowd = compute_crowding(db, code, target_date)
        fg = compute_fear_greed(db, code, target_date)
        spread = compute_spread(db, code, target_date)
        temp = compose_temperature(crowd, fg, spread)
        return {
            'code': code, 'date': target_date,
            'crowding': crowd, 'fear_greed': fg, 'spread': spread,
            'temperature': temp,
            'data_note': '口径：拥挤度=交易热度50%(成交额占比+换手率,120日分位)+估值水位50%(PE0.3+PB0.3+股息率分位取反0.4)；恐慌贪婪=ATR252日百分位+250日回撤252日百分位+5日动量(等权)；息差=股息率−10年国债(中债估值,akshare,序列2018起),含250日/全历史分位；温度计v1.1权重(拥挤0.40/恐慌0.20/息差0.40,息差用250日滚动分位,实验标定2026-08-17,2018-07起回测)：冷≤30=买入增强区(60日胜率64%)、热≥65=回避区(60日胜率43%,20日36%)，非独立买卖信号',
        }
    finally:
        db.close()


if __name__ == '__main__':
    import json
    code = sys.argv[1] if len(sys.argv) > 1 else '000922'
    d = sys.argv[2] if len(sys.argv) > 2 else '2026-08-17'
    print(json.dumps(compute_all(code, d), ensure_ascii=False, indent=1))
