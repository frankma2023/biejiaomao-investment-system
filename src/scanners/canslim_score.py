#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CAN SLIM 评分引擎 v1

用法: python src/scanners/canslim_score.py --stock 600519
"""

import sys, os, yaml, sqlite3, json
import numpy as np
try:
    import talib
except ImportError:
    talib = None
from datetime import datetime, timedelta, date

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")
CONFIG_PATH = os.path.join(PROJECT_DIR, "config", "canslim_scorecard.yaml")


def _compute_indicators(klines):
    """计算技术指标 dict，供 batch 模式引擎使用"""
    if talib is None or len(klines) < 50:
        return {}
    close = np.array([k.get('close') or np.nan for k in klines], dtype=np.float64)
    result = {}
    try:
        for p in [5, 10, 20, 50, 120, 250]:
            sma = talib.SMA(close, p)
            result[f'sma{p}'] = [float(x) if not np.isnan(x) else None for x in sma]
        bb_u, bb_m, bb_l = talib.BBANDS(close, 20, 2, 2, 0)
        result['bb_upper'] = [float(x) if not np.isnan(x) else None for x in bb_u]
        result['bb_middle'] = [float(x) if not np.isnan(x) else None for x in bb_m]
        result['bb_lower'] = [float(x) if not np.isnan(x) else None for x in bb_l]
    except Exception:
        pass
    return result


def load_params():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def get_quarterly(db, stock_code, target_date, n=4):
    rows = db.execute("""
        SELECT * FROM stock_financials_quarterly
        WHERE stock_code=? AND report_date <= ?
        ORDER BY report_date DESC LIMIT ?
    """, (stock_code, target_date, n)).fetchall()
    return [dict(r) for r in rows]


def get_annual(db, stock_code, target_date, n=4):
    rows = db.execute("""
        SELECT * FROM stock_financials_annual
        WHERE stock_code=? AND report_date <= ?
        ORDER BY report_date DESC LIMIT ?
    """, (stock_code, target_date, n)).fetchall()
    return [dict(r) for r in rows]


def score_c(db, stock_code, target_date, p):
    cfg = p.get('c_current_earnings', {})
    qs = get_quarterly(db, stock_code, target_date, 2)
    if not qs:
        return {"score": 0, "detail": "no data", "breakdown": {}}

    latest = qs[0]
    prev_q = qs[1] if len(qs) > 1 else None
    score = 0
    detail = []
    bd = {}

    # EPS YoY
    eps_yoy = latest.get('net_profit_yoy') or 0
    tiers = cfg.get('eps_yoy_tiers', [25, 18, 10])
    scs = cfg.get('eps_yoy_scores', [11, 7, 4])
    eps_sc = 0
    for i, t in enumerate(tiers):
        if eps_yoy >= t:
            eps_sc = scs[i]; break
    score += eps_sc
    bd['eps_yoy'] = {'value': round(eps_yoy, 1), 'score': eps_sc}
    detail.append('EPS YoY {:.1f}%'.format(eps_yoy))

    # EPS accel
    if prev_q and eps_yoy != 0:
        prev_eps = prev_q.get('net_profit_yoy') or 0
        accel = eps_yoy - prev_eps
        if accel >= cfg.get('eps_accel_threshold', 10):
            score += cfg.get('eps_accel_score', 4)
            bd['eps_accel'] = {'value': round(accel, 1), 'score': cfg.get('eps_accel_score', 4)}
        elif accel < -5:
            score += cfg.get('eps_decel_penalty', -2)
            bd['eps_accel'] = {'value': round(accel, 1), 'score': cfg.get('eps_decel_penalty', -2)}

    # Revenue YoY
    rev_yoy = latest.get('revenue_yoy') or 0
    rev_t = cfg.get('revenue_yoy_tiers', [25, 15])
    rev_s = cfg.get('revenue_yoy_scores', [4, 2])
    rev_sc = 0
    for i, t in enumerate(rev_t):
        if rev_yoy >= t:
            rev_sc = rev_s[i]; break
    score += rev_sc
    bd['revenue_yoy'] = {'value': round(rev_yoy, 1), 'score': rev_sc}
    detail.append('Rev {:.1f}%'.format(rev_yoy))

    # Non-recurring ratio
    np_val = latest.get('net_profit_single') or 0
    np_adj = latest.get('net_profit_adj_single') or 0
    if np_val > 0:
        ratio = np_adj / np_val * 100
        if ratio >= cfg.get('nonrecurring_ratio', 90):
            score += cfg.get('nonrecurring_score', 3)
            bd['nonrecurring'] = {'value': round(ratio, 1), 'score': cfg.get('nonrecurring_score', 3)}
        elif ratio < 70:
            score += cfg.get('nonrecurring_warning', -3)
            bd['nonrecurring'] = {'value': round(ratio, 1), 'score': cfg.get('nonrecurring_warning', -3)}

    return {"score": max(0, score), "detail": ", ".join(detail), "breakdown": bd}


def score_a(db, stock_code, target_date, p):
    cfg = p.get('a_annual_earnings', {})
    anns = get_annual(db, stock_code, target_date, 4)
    if len(anns) < 3:
        return {"score": 0, "detail": "need 3+ years", "breakdown": {}}

    score = 0; detail = []; bd = {}

    # 3Y CAGR
    lat = anns[0].get('net_profit') or 0
    old = anns[3].get('net_profit') if len(anns) > 3 else (anns[-1].get('net_profit') or 0)
    cagr = ((lat / old) ** (1.0/3.0) - 1) * 100 if (old > 0 and lat > 0) else 0
    tiers = cfg.get('eps_cagr_3y_tiers', [25, 15, 5])
    scs = cfg.get('eps_cagr_scores', [8, 5, 2])
    cagr_sc = 0
    for i, t in enumerate(tiers):
        if cagr >= t: cagr_sc = scs[i]; break
    score += cagr_sc
    bd['eps_cagr_3y'] = {'value': round(cagr, 1), 'score': cagr_sc}
    detail.append('3Y CAGR {:.0f}%'.format(cagr))

    # Positive years
    pos = sum(1 for i in range(len(anns)-1) if (anns[i].get('net_profit') or 0) > (anns[i+1].get('net_profit') or 0))
    pos_s = cfg.get('positive_years_score', [4, 2])
    if pos >= 3: score += pos_s[0]; bd['pos_years'] = {'value': pos, 'score': pos_s[0]}
    elif pos >= 2: score += pos_s[1]; bd['pos_years'] = {'value': pos, 'score': pos_s[1]}
    else: bd['pos_years'] = {'value': pos, 'score': 0}

    # Stability
    eps_list = [a.get('net_profit') or 0 for a in anns[:3]]
    if len(eps_list) >= 3 and sum(eps_list) > 0:
        mean = sum(eps_list) / len(eps_list)
        std = (sum((e - mean)**2 for e in eps_list) / len(eps_list)) ** 0.5
        cv = std / mean * 100
        if cv < cfg.get('stability_cv_threshold', 30):
            score += cfg.get('stability_score', 3)
            bd['stability'] = {'value': round(cv, 1), 'score': cfg.get('stability_score', 3)}
        elif cv < 50:
            score += 1
            bd['stability'] = {'value': round(cv, 1), 'score': 1}

    return {"score": min(score, 15), "detail": ", ".join(detail), "breakdown": bd}


def score_n(db, stock_code, target_date, p, klines=None, signals=None):
    cfg = p.get('n_new', {})
    if klines is None:
        rows = db.execute("""SELECT date, close FROM daily_kline
            WHERE stock_code=? AND date<=? ORDER BY date""",
            (stock_code, target_date)).fetchall()
        klines = [dict(r) for r in rows]
    if not klines:
        return {"score": 0, "detail": "no kline", "breakdown": {}}

    score = 0; detail = []; bd = {}

    # 52W high
    close = klines[-1]['close']
    highs = [k['close'] for k in klines[-250:] if k.get('close')]
    high52 = max(highs) if highs else close
    if high52 > 0:
        pct = (close - high52) / high52 * 100
        tiers = cfg.get('high52_tiers', [-5, -15])
        scs = cfg.get('high52_scores', [6, 4])
        high_sc = 0
        for i, t in enumerate(tiers):
            if pct > t: high_sc = scs[i]; break
        score += high_sc
        bd['high52'] = {'value': round(pct, 1), 'score': high_sc}
        detail.append('52W {:.0f}%'.format(pct))

    # Form breakout (engines)
    if signals is None:
        try:
            from engine_registry import run_all_engines
            ind = _compute_indicators(klines)
            all_sigs = run_all_engines(klines=klines, indicators=ind, silent=True)
        except:
            all_sigs = []
    else:
        all_sigs = signals

    eng_cfg = cfg.get('engine_scores', {})
    decay = cfg.get('decay_factors', [1.0, 0.8, 0.6, 0.4, 0.4])
    lookback = cfg.get('high_lookback_days', 5)
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    form_sc = 0.0
    cdl_sum = 0
    ta_sum = 0

    for sig in all_sigs:
        try:
            sig_dt = datetime.strptime(sig['date'], '%Y-%m-%d')
        except:
            continue
        days = (target_dt - sig_dt).days
        if days < 0 or days >= lookback: continue
        factor = decay[min(days, len(decay)-1)]
        src = sig.get('source', '')
        tp = sig.get('type', 'bullish')
        if tp != 'bullish': continue

        if src in eng_cfg:
            base = eng_cfg[src]
            form_sc += base * factor
        elif src == 'cdl':
            base = eng_cfg.get('cdl_bullish', 2)
            if cdl_sum < eng_cfg.get('cdl_bullish_max', 4):
                cdl_sum += base * factor
                form_sc += base * factor
        elif src == 'talib':
            base = eng_cfg.get('talib_bullish', 1)
            if ta_sum < eng_cfg.get('talib_bullish_max', 3):
                ta_sum += base * factor
                form_sc += base * factor

    form_sc = min(form_sc, 17)
    score += round(form_sc, 1)
    bd['form_breakout'] = {'value': round(form_sc, 1), 'score': round(form_sc, 1)}

    return {"score": min(score, 17), "detail": ", ".join(detail), "breakdown": bd}


def score_s(db, stock_code, target_date, p):
    cfg = p.get('s_supply_demand', {})
    score = 0; detail = []; bd = {}

    # Volume ratio
    row5 = db.execute("""SELECT AVG(volume) as av FROM (
        SELECT volume FROM daily_kline
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 5)""",
        (stock_code, target_date)).fetchone()
    row50 = db.execute("""SELECT AVG(volume) as av FROM (
        SELECT volume FROM daily_kline
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 50)""",
        (stock_code, target_date)).fetchone()
    if row50 and row50['av'] and row50['av'] > 0 and row5 and row5['av']:
        vr = row5['av'] / row50['av']
        vol_t = cfg.get('vol_ratio_tiers', [2.0, 1.5, 1.2])
        vol_s = cfg.get('vol_ratio_scores', [7, 5, 2])
        vol_sc = 0
        for i, t in enumerate(vol_t):
            if vr >= t: vol_sc = vol_s[i]; break
        score += vol_sc
        bd['vol_ratio'] = {'value': round(vr, 2), 'score': vol_sc}
        detail.append('Vol {:.1f}x'.format(vr))

    return {"score": min(score, 7), "detail": ", ".join(detail), "breakdown": bd}


def score_l(db, stock_code, target_date, p):
    cfg = p.get('l_leader', {})
    score = 0; detail = []; bd = {}

    row = db.execute("""SELECT rps_20, rps_60, rps_120, rps_250 FROM stock_rs_daily
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1""",
        (stock_code, target_date)).fetchone()
    if not row:
        return {"score": 0, "detail": "no RS data", "breakdown": {}}

    rs250 = row['rps_250'] or 0
    tiers = cfg.get('rs250_tiers', [95, 90, 80, 70])
    scs = cfg.get('rs250_scores', [10, 8, 5, 2])
    rs_sc = 0
    for i, t in enumerate(tiers):
        if rs250 >= t: rs_sc = scs[i]; break
    score += rs_sc
    bd['rs_250'] = {'value': rs250, 'score': rs_sc}
    detail.append('RS250={}'.format(rs250))

    # RS momentum
    rs20 = row['rps_20'] or 0
    rs60 = row['rps_60'] or 0
    rs120 = row['rps_120'] or 0
    if rs20 > rs60 > rs120:
        score += cfg.get('rs_momentum_score', 2)
        bd['rs_momentum'] = {'value': 'accel', 'score': cfg.get('rs_momentum_score', 2)}
    elif rs20 < rs120:
        score += cfg.get('rs_momentum_penalty', -2)
        bd['rs_momentum'] = {'value': 'decel', 'score': cfg.get('rs_momentum_penalty', -2)}

    # v3: RS_20 short-term bonus
    rs20_bonus = 0
    rs20_tiers = cfg.get('rs20_bonus_tiers', [95, 90])
    rs20_scores = cfg.get('rs20_bonus_scores', [2, 1])
    for i, t in enumerate(rs20_tiers):
        if rs20 >= t: rs20_bonus = rs20_scores[i]; break
    score += rs20_bonus
    bd['rs_20_bonus'] = {'value': rs20, 'score': rs20_bonus}

    # Industry RS — 取所属全部指数中 RS_20 最高值
    try:
        ind_rs_row = db.execute("""
            SELECT MAX(rs.rs_20) as best_rs
            FROM index_constituents ic
            JOIN index_rs_daily rs ON ic.index_code = rs.stock_code
                AND rs.date = (SELECT MAX(date) FROM index_rs_daily WHERE stock_code=ic.index_code)
            WHERE ic.stock_code=?
        """, (stock_code,)).fetchone()
        if ind_rs_row and ind_rs_row['best_rs'] is not None:
            ind_rs = ind_rs_row['best_rs']
            th = cfg.get('industry_rs_threshold', 80)
            if ind_rs >= th:
                score += cfg.get('industry_rs_score_high', 4)
                bd['industry_rs'] = {'value': ind_rs, 'score': cfg.get('industry_rs_score_high', 4)}
            elif ind_rs >= 70:
                score += cfg.get('industry_rs_score_mid', 2)
                bd['industry_rs'] = {'value': ind_rs, 'score': cfg.get('industry_rs_score_mid', 2)}
            else:
                bd['industry_rs'] = {'value': ind_rs, 'score': 0}
        else:
            bd['industry_rs'] = {'value': '-', 'score': 0, 'note': 'no index data'}
    except sqlite3.OperationalError:
        bd['industry_rs'] = {'value': '-', 'score': 0, 'note': 'no table'}

    # Excess return
    rows = db.execute("""SELECT close FROM daily_kline
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 21""",
        (stock_code, target_date)).fetchall()
    if len(rows) >= 2:
        sr = (rows[0]['close'] - rows[-1]['close']) / rows[-1]['close'] * 100
        idx_rows = db.execute("""SELECT close FROM index_daily_kline
            WHERE stock_code='000985' AND date<=? AND kline_type='normal'
            ORDER BY date DESC LIMIT 21""",
            (target_date,)).fetchall()
        if len(idx_rows) >= 2:
            ir = (idx_rows[0]['close'] - idx_rows[-1]['close']) / idx_rows[-1]['close'] * 100
            excess = sr - ir
            th = cfg.get('excess_return_threshold', 5)
            ex_s = cfg.get('excess_return_scores', [4, 2])
            if excess > th:
                score += ex_s[0]; bd['excess'] = {'value': round(excess, 1), 'score': ex_s[0]}
            elif excess > 0:
                score += ex_s[1]; bd['excess'] = {'value': round(excess, 1), 'score': ex_s[1]}
            else:
                bd['excess'] = {'value': round(excess, 1), 'score': 0}
            detail.append('Excess {:.1f}%'.format(excess))

    return {"score": max(0, min(score, 22)), "detail": ", ".join(detail), "breakdown": bd}


def score_i(db, stock_code, target_date, p):
    cfg = p.get('i_institutional', {})
    score = 0; detail = []; bd = {}

    # Institution holdings (may have no data)
    ir = None
    try:
        ir = db.execute("""SELECT total_inst_proportion, fund_count, top10_inst_count
            FROM stock_institutional_holdings
            WHERE stock_code=?
            ORDER BY date DESC LIMIT 1""", (stock_code,)).fetchone()
    except: pass
    if not ir:
        bd['inst_holding'] = {'value': '-', 'score': 0, 'note': 'run fetch script'}

    if ir and ir['total_inst_proportion']:
        ip = ir['total_inst_proportion'] * 100
        tiers = cfg.get('inst_holding_tiers', [15, 5, 1])
        scs = cfg.get('inst_holding_scores', [5, 3, 1])
        ih_sc = 0
        for i, t in enumerate(tiers):
            if ip >= t: ih_sc = scs[i]; break
        score += ih_sc
        bd['inst_holding'] = {'value': round(ip, 1), 'score': ih_sc}
        detail.append('InstHold {:.1f}%'.format(ip))

    # Institution count change
    try:
        irs = db.execute("""SELECT fund_count, top10_inst_count FROM stock_institutional_holdings
            WHERE stock_code=?
            ORDER BY date DESC LIMIT 2""", (stock_code,)).fetchall()
    except sqlite3.OperationalError:
        irs = []
    if len(irs) >= 2:
        # fallthrough to populate values
        pass
    else:
        bd['inst_change'] = {'value': '-', 'score': 0, 'note': 'no data'}
        irs = []  # dummy
    if len(irs) >= 2:
        cur = irs[0]['fund_count'] + irs[0]['top10_inst_count']
        prev = irs[1]['fund_count'] + irs[1]['top10_inst_count']
        delta = cur - prev
        inc_s = cfg.get('inst_count_score', [5, 2])
        if delta >= cfg.get('inst_count_increase', 10):
            score += inc_s[0]; bd['inst_change'] = {'value': '+{}'.format(delta), 'score': inc_s[0]}
        elif delta > 0:
            score += inc_s[1]; bd['inst_change'] = {'value': '+{}'.format(delta), 'score': inc_s[1]}
        else:
            bd['inst_change'] = {'value': '{}'.format(delta), 'score': 0}

    # Analyst coverage (table may not exist)；主源=sina（新浪研报，实时准确），备源=lx（东财批处理）
    ld = cfg.get('analyst_lookback_days', 90)
    ar = None
    try:
        ar = db.execute("""SELECT org_count, first_coverage, upgrade_count, source
            FROM stock_analyst_reports
            WHERE stock_code=? AND lookback_days=? AND source='sina'
            ORDER BY date DESC LIMIT 1""", (stock_code, ld)).fetchone()
        if not ar:
            ar = db.execute("""SELECT org_count, first_coverage, upgrade_count, source
                FROM stock_analyst_reports
                WHERE stock_code=? AND lookback_days=?
                ORDER BY date DESC LIMIT 1""", (stock_code, ld)).fetchone()
    except sqlite3.OperationalError:
        pass

    if ar:
        oc = ar['org_count'] or 0
        at = cfg.get('analyst_coverage_tiers', [3, 1])
        if oc >= at[0]:
            score += cfg.get('analyst_coverage_score', 3)
            bd['analyst'] = {'value': '{}covers'.format(oc), 'score': cfg.get('analyst_coverage_score', 3)}
        elif oc >= at[1]:
            score += 1
            bd['analyst'] = {'value': '{}covers'.format(oc), 'score': 1}
        else:
            bd['analyst'] = {'value': '{}covers'.format(oc), 'score': 0}
        detail.append('Analyst {}'.format(oc))

        if ar['first_coverage']:
            score += cfg.get('first_coverage_score', 2)
            bd['first_cov'] = {'value': 'yes', 'score': cfg.get('first_coverage_score', 2)}
        else:
            bd['first_cov'] = {'value': 'no', 'score': 0}

        uc = ar['upgrade_count'] or 0
        if uc > 0:
            score += cfg.get('rating_upgrade_score', 1)
            bd['rating_up'] = {'value': '+{}'.format(uc), 'score': cfg.get('rating_upgrade_score', 1)}
        else:
            bd['rating_up'] = {'value': '0', 'score': 0}
    else:
        bd['analyst'] = {'value': '-', 'score': 0, 'note': 'run fetch script'}
        bd['first_cov'] = {'value': '-', 'score': 0}
        bd['rating_up'] = {'value': '-', 'score': 0}

    # v3.2：负债率移出 I 维度（2026-08-27）——语义错置（I 度量机构态度，负债率是公司质量）；
    # 原 2 分拨给首次覆盖(+1)与研报覆盖(+1)。负债率如需风险检查，另设门禁（未启用）
    return {"score": max(0, min(score, 18)), "detail": ", ".join(detail), "breakdown": bd}


def score_market(db, target_date, p):
    cfg = p.get('m_market', {})
    row = db.execute("""SELECT total_score FROM market_health_daily
        WHERE date<=? ORDER BY date DESC LIMIT 1""", (target_date,)).fetchone()
    if not row or not row['total_score']: return {"health_score": None, "position": "N/A"}
    hs = row['total_score']
    if hs >= cfg.get('health_strong', 70): pos = 'FULL'
    elif hs >= cfg.get('health_neutral', 50): pos = '70%'
    elif hs >= cfg.get('health_weak', 30): pos = '50%'
    else: pos = '30%'
    return {"health_score": hs, "position": pos}


def score_stock(stock_code, target_date, params=None, save=False, signals=None):
    if params is None: params = load_params()
    db = sqlite3.connect(DB_PATH); db.row_factory = sqlite3.Row

    rows = db.execute("""SELECT date, open, high, low, close, volume FROM daily_kline
        WHERE stock_code=? AND date<=? ORDER BY date""",
        (stock_code, target_date)).fetchall()
    klines = [dict(r) for r in rows]

    if signals is None and len(klines) >= 50:
        try:
            from engine_registry import run_all_engines
            ind = _compute_indicators(klines)
            signals = run_all_engines(klines=klines, indicators=ind, silent=True)
        except: pass

    c = score_c(db, stock_code, target_date, params)
    a = score_a(db, stock_code, target_date, params)
    n = score_n(db, stock_code, target_date, params, klines, signals)
    s = score_s(db, stock_code, target_date, params)
    l = score_l(db, stock_code, target_date, params)
    i = score_i(db, stock_code, target_date, params)
    m = score_market(db, target_date, params)
    db.close()

    raw = c['score'] + a['score'] + n['score'] + s['score'] + l['score'] + i['score']
    final = min(raw, 100)  # v3: 各维度满分之和=100，无需缩放

    grades = [(85, 'S'), (75, 'A'), (65, 'B'), (55, 'C'), (45, 'D')]
    grade = 'E'
    for th, g in grades:
        if final >= th: grade = g; break

    result = {
        'stock_code': stock_code, 'date': target_date,
        'C': c, 'A': a, 'N': n, 'S': s, 'L': l, 'I': i,
        'raw_total': raw, 'score': final, 'grade': grade, 'M': m,
    }

    if save: _save_score(result)
    return result


def _save_score(result):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS cansim_scores (
        stock_code TEXT, date TEXT, score_c REAL, score_a REAL, score_n REAL,
        score_s REAL, score_l REAL, score_i REAL, raw_total REAL,
        score INTEGER, grade TEXT, detail_json TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (stock_code, date))""")
    conn.execute("""INSERT OR REPLACE INTO cansim_scores
        (stock_code, date, score_c, score_a, score_n, score_s,
         score_l, score_i, raw_total, score, grade, detail_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
        result['stock_code'], result['date'],
        result['C']['score'], result['A']['score'],
        result['N']['score'], result['S']['score'],
        result['L']['score'], result['I']['score'],
        result['raw_total'], result['score'], result['grade'],
        json.dumps(result, ensure_ascii=False)))
    conn.commit(); conn.close()


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--stock', default='600519')
    ap.add_argument('--date', default=date.today().strftime('%Y-%m-%d'))
    ap.add_argument('--save', action='store_true')
    ap.add_argument('--batch', type=str, default=None)
    args = ap.parse_args()

    if args.batch:
        limit = int(args.batch.replace('top', '')) if args.batch.startswith('top') else 100
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""SELECT stock_code FROM stock_rs_daily
            WHERE date=(SELECT MAX(date) FROM stock_rs_daily)
            AND rps_250>=80 ORDER BY rps_250 DESC LIMIT ?""", (limit,)).fetchall()
        conn.close()
        codes = [r[0] for r in rows]
        print('Batch score TOP {}: {} stocks'.format(limit, len(codes)))
        for i, code in enumerate(codes):
            try:
                r = score_stock(code, args.date, save=args.save)
                print('  [{}/{}] {}: {}pts {} C={} A={} N={} S={} L={} I={}'.format(
                    i+1, len(codes), code, r['score'], r['grade'],
                    r['C']['score'], r['A']['score'], r['N']['score'],
                    r['S']['score'], r['L']['score'], r['I']['score']))
            except Exception as e:
                print('  [{}/{}] {}: ERROR {}'.format(i+1, len(codes), code, e))
    else:
        r = score_stock(args.stock, args.date, save=args.save)
        print('{} @ {}'.format(args.stock, args.date))
        print('  C: {}/23  ({})'.format(r['C']['score'], r['C']['detail']))
        print('  A: {}/17  ({})'.format(r['A']['score'], r['A']['detail']))
        print('  N: {}/14  ({})'.format(r['N']['score'], r['N']['detail']))
        print('  S: {}/9   ({})'.format(r['S']['score'], r['S']['detail']))
        print('  L: {}/21  ({})'.format(r['L']['score'], r['L']['detail']))
        print('  I: {}/18  ({})'.format(r['I']['score'], r['I']['detail']))
        print('  {}'.format('-' * 20))
        print('  Score: {}/100 -> {}'.format(r['score'], r['grade']))
        if r['M'].get('health_score'):
            print('  M: Health {} -> {}'.format(r['M']['health_score'], r['M']['position']))