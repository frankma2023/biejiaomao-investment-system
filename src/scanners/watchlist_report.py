# -*- coding: utf-8 -*-
"""
自选池日报 · 扫描主脚本（daily_update 步骤 32 调用）

双轨：
- 股票轨：K线(前复权) → 指标 → 19 引擎 → 信号 + MW 表 + 缠论标注 → 规则引擎 → 5 档
- 指数轨：回撤/估值/网格档位 → 简化建议映射（复用已验证策略结论）

输出：generate_report() 返回 report dict（供 HTML 生成 + DB 落盘 + 回放验证）
"""
import os
import sys
import sqlite3
import yaml
from datetime import datetime, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))

from src.scanners.report_rules import (evaluate, load_weights, normalize_engine_signal,
                                       normalize_mw_rows, detect_missed, LEVEL_CN)

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')

# ETF 代码前缀（51/15/56/58 = 场内 ETF；9 开头 6 位 = 指数）
ETF_PREFIX = ('51', '15', '56', '58')
INDEX_PREFIX = ('0009', '9', '930', '950', 'H', 'h')  # 9xxxxx 指数段

_KLINE_CACHE = {}


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


# ─────────────────────────────────────────────
# 标的分类
# ─────────────────────────────────────────────
def classify(code, db):
    """返回 'stock' | 'etf' | 'index' | 'unknown'"""
    # 1. 股票主表命中 → stock（000338 潍柴动力在 stock_basic）
    r = db.execute("SELECT 1 FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
    if r:
        return 'stock'
    # 2. ETF 前缀 → etf
    if code.startswith(ETF_PREFIX):
        return 'etf'
    # 3. 指数表命中 → index
    r = db.execute("SELECT 1 FROM index_daily_kline WHERE stock_code=? LIMIT 1", (code,)).fetchone()
    if r:
        return 'index'
    return 'unknown'


def load_watchlist(db):
    rows = db.execute(
        "SELECT stock_code, stock_name FROM watchlist WHERE removed_at IS NULL ORDER BY added_at").fetchall()
    return [{'code': r['stock_code'], 'name': r['stock_name'] or r['stock_code']} for r in rows]


def load_holdings(db):
    """未平仓持仓 {code: {cost, stop_loss, qty}}"""
    rows = db.execute(
        "SELECT stock_code, buy_price, stop_loss_price, buy_qty FROM discipline_trades WHERE sell_date IS NULL").fetchall()
    out = {}
    for r in rows:
        c = r['stock_code']
        if c not in out:
            out[c] = {'cost': 0.0, 'qty': 0, 'stop_loss': r['stop_loss_price']}
        out[c]['cost'] += r['buy_price'] * (r['buy_qty'] or 0)
        out[c]['qty'] += r['buy_qty'] or 0
    for c in out:
        if out[c]['qty'] > 0:
            out[c]['cost'] /= out[c]['qty']
    return out


# ─────────────────────────────────────────────
# K 线 / 上下文（股票轨）
# ─────────────────────────────────────────────
def _load_klines(db, code, scan_date, days=750):
    rows = db.execute(f"""
        SELECT date, COALESCE(adj_open, open) as open, COALESCE(adj_high, high) as high,
               COALESCE(adj_low, low) as low, COALESCE(adj_close, close) as close,
               volume, amount, change_pct
        FROM daily_kline WHERE stock_code=? AND date<=?
        ORDER BY date DESC LIMIT {days}""", (code, scan_date)).fetchall()
    klines = list(reversed([dict(r) for r in rows]))
    if not klines:
        return []
    # 前复权兜底（complex_factor 大量 NULL）
    from src.server import _ensure_adj_prices
    _ensure_adj_prices(klines)
    return klines


def _sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def build_context(klines):
    """close / pos_250 / ma50 / ma50_slope / gain_from_low / fib_levels"""
    if not klines:
        return {}
    closes = [k['close'] for k in klines]
    close = closes[-1]
    seg = closes[-250:]
    lo, hi = min(seg), max(seg)
    pos = (close - lo) / (hi - lo) * 100 if hi > lo else 50
    ma50 = _sma(closes, 50)
    ma50_prev = _sma(closes[:-5], 50)
    slope = (ma50 - ma50_prev) / ma50_prev * 100 if ma50 and ma50_prev else 0
    gain_from_low = (close / lo - 1) * 100 if lo else 0
    # 斐波那契回调位：从 250 日波段 hi→lo 的 38.2/50/61.8% 回撤价格
    fib = [hi - (hi - lo) * r for r in (0.382, 0.5, 0.618)] if hi > lo else []
    return {
        'close': close,
        'pos_250': round(pos),
        'ma50': ma50,
        'ma50_slope': slope,
        'low_250': lo,
        'high_250': hi,
        'gain_from_low': gain_from_low,
        'fib_levels': [round(f, 2) for f in fib],
        'change_pct': klines[-1].get('change_pct'),
    }


def _rs_latest(code, db):
    """最近一期 RS（discipline_observation_pool）"""
    r = db.execute("""SELECT rps_20, rps_60, rps_120, rps_250 FROM discipline_observation_pool
        WHERE stock_code=? ORDER BY date DESC LIMIT 1""", (code,)).fetchone()
    if r:
        return {k: r[k] for k in ('rps_20', 'rps_60', 'rps_120', 'rps_250') if r[k] is not None}
    return {}


def _chanlun_note(code, db, scan_date):
    """缠论最新状态（P0 仅标注）"""
    r = db.execute("""SELECT latest_trade_side, latest_trade_type, latest_div_type, scan_date
        FROM chanlun_scan_daily WHERE stock_code=? AND scan_date<=?
        ORDER BY scan_date DESC LIMIT 1""", (code, scan_date)).fetchone()
    if not r or (r['latest_trade_side'] is None and r['latest_div_type'] is None):
        return None
    parts = []
    if r['latest_trade_side']:
        parts.append(f"缠论{r['latest_trade_type'] or r['latest_trade_side']}")
    if r['latest_div_type']:
        parts.append(r['latest_div_type'])
    return {'text': ' · '.join(parts), 'side': r['latest_trade_side'], 'date': r['scan_date']}


def scan_stock(code, name, db, scan_date, weights=None):
    """股票轨：引擎扫描 + 规则引擎 → 卡片"""
    klines = _load_klines(db, code, scan_date)
    if not klines:
        return {'code': code, 'name': name, 'kind': 'stock', 'error': '无K线数据', 'level': 'hold'}

    ctx = build_context(klines)
    ctx['rps'] = _rs_latest(code, db)

    # 引擎扫描（复用 pattern-scan 管线）
    from src.server import _compute_indicators
    for k in klines:
        k['stock_code'] = code
    indicators = _compute_indicators(klines)
    from src.engine_registry import run_all_engines
    eng_signals = run_all_engines(klines=klines, indicators=indicators, silent=True)

    # MW 历史表
    mw_rows = db.execute("""SELECT * FROM mw_signal_daily WHERE stock_code=?
        AND COALESCE(b1_date, b2_date) >= date(?, '-120 days')
        ORDER BY scan_date DESC LIMIT 10""", (code, scan_date)).fetchall()
    mw_signals = normalize_mw_rows([dict(r) for r in mw_rows])

    # 归一化引擎信号（排除 talib/cdl 参考类）
    norm = [n for n in (normalize_engine_signal(s, weights=weights) for s in eng_signals) if n]
    # 先窗口过滤（60日内）再同源去重（取最早=首次确认）：避免全历史去重把近期信号挤掉
    from src.scanners.report_rules import _dedup_by_source, _days_between
    win = int((weights or load_weights())['rules'].get('new_signal_window', 60))
    pool = [s for s in norm + mw_signals if s.get('date') and _days_between(s['date'], scan_date) <= win]
    norm = _dedup_by_source(pool)

    # 缠论标注
    chan = _chanlun_note(code, db, scan_date)

    # 规则引擎（norm 已含 mw_signals 并去重）
    res = evaluate(norm, ctx, weights=weights, scan_date=scan_date)
    # 错过检测（有 last_view 时）
    missed = detect_missed(norm, None, scan_date, ctx, weights=weights)

    return {
        'code': code, 'name': name, 'kind': 'stock',
        'close': ctx['close'], 'change_pct': ctx.get('change_pct'),
        'ctx': {k: ctx[k] for k in ('pos_250', 'ma50', 'ma50_slope', 'gain_from_low', 'fib_levels', 'low_250', 'high_250')},
        'rps': ctx.get('rps', {}),
        'chanlun': chan,
        'signals': res['signals_used'],
        'eval': {k: res[k] for k in ('level', 'level_cn', 'net', 'buy_score', 'sell_score', 'reasons', 'tips', 'callback', 'resonance')},
        'missed': missed,
    }


# ─────────────────────────────────────────────
# 指数轨
# ─────────────────────────────────────────────
def _index_metrics(db, code, scan_date):
    """指数基础指标：回撤/位置/估值"""
    from src.server import _dd_from_full_return, FULL_RETURN_MAP
    ddinfo = _dd_from_full_return(db, code, scan_date)
    out = {'dd_250': None, 'pos_250': None, 'close': None}
    if ddinfo:
        out['dd_250'] = round(ddinfo['dd_250'], 1)
        out['close'] = ddinfo['current']
    r = db.execute("""SELECT pe_ttm, pe_ttm_pct, dyr, dyr_pct FROM index_fundamental_daily
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1""", (code, scan_date)).fetchone()
    if r:
        out['pe_pct'] = round(r['pe_ttm_pct'] * 100) if r['pe_ttm_pct'] is not None else None
        out['dyr_pct'] = round(r['dyr_pct'] * 100) if r['dyr_pct'] is not None else None
        out['pe'] = round(r['pe_ttm'], 1) if r['pe_ttm'] else None
        out['dyr'] = round(r['dyr'] * 100, 2) if r['dyr'] else None
    return out


def scan_index(code, name, db, scan_date):
    """指数轨：策略结论映射（红利回撤买点 / 券商网格 / 煤炭网格）"""
    m = _index_metrics(db, code, scan_date)
    level = 'hold'
    reasons = []
    tips = []
    callback = None

    if m.get('dd_250') is None and m.get('close') is None:
        return {'code': code, 'name': name, 'kind': 'index', 'error': '无指数数据', 'level': 'hold'}

    dd = m.get('dd_250')
    pos = m.get('pos_250')
    pe_pct = m.get('pe_pct')
    dyr_pct = m.get('dyr_pct')

    # 红利系（回撤买点 ≥10%，回测 74% 胜率）
    if code in ('000922', 'H30269', '931468', '000015', '931848'):
        if dd is not None and dd >= 10:
            level = 'buy'
            reasons.append(f"🟢 红利策略：250日回撤 {dd}% ≥10%（回测 32次/20日胜率75%）")
        elif pe_pct is not None and pe_pct > 80:
            level = 'wait'
            reasons.append(f"⏳ 估值警示：PE 分位 {pe_pct}% >80%（10年口径）——不追高")
        elif dyr_pct is not None and dyr_pct > 90:
            level = 'buy'
            reasons.append(f"🟢 红利策略：股息率分位 {dyr_pct}% >90%——高息买点")
        else:
            reasons.append(f"⚪ 回撤 {dd}% 未到 10% 买点；PE 分位 {pe_pct}%")

    # 券商 ETF（512000 网格，5% 间距）
    elif code == '512000':
        lo = db.execute("""SELECT MIN(close) m FROM hk_etf_daily WHERE stock_code='512000'""").fetchone()['m']
        if lo:
            base = lo * 0.95
            step = base * 0.05
            lvl = int((m['close'] - base) / step) if m['close'] else 0
            reasons.append(f"⛳ 券商网格：当前档{lvl}（5%间距）· 250日位置 {pos}% · 回撤 {dd}%")
            if pos is not None and pos < 30:
                level = 'buy'
                reasons.append("🟢 券商策略：历史低位区（PE分位0%），网格首仓窗口")
            else:
                level = 'wait'
                reasons.append("⏳ 券商网格：非首仓窗口，按档位表逐档执行")

    # 煤炭（399998）
    elif code in ('399998', '931238'):
        reasons.append(f"⛏ 煤炭网格：回撤 {dd}% · 位置 {pos}%（10%间距适配性研究，见 coal-advice）")
        if dd is not None and dd >= 25:
            level = 'buy'
            reasons.append("🟢 煤炭策略：深度回撤，网格下沿买入区")
        elif pos is not None and pos > 85:
            level = 'wait'
            reasons.append("⏳ 煤炭位置偏高，网格上沿——不追")

    # 自由现金流（980092/932365）
    elif code in ('980092', '932365'):
        if dd is not None and dd >= 20:
            level = 'buy'
            reasons.append(f"🟢 自由现金流策略：深回撤 {dd}% ≥20%（回测67%）")
        elif pe_pct is not None and pe_pct < 33:
            level = 'buy'
            reasons.append(f"🟢 估值双低窗口：PE 分位 {pe_pct}% <33%")
        else:
            reasons.append(f"⚪ 自由现金流：回撤 {dd}% 未到 20% 深回撤买点")

    # 通用指数
    else:
        reasons.append(f"📊 通用指数：250日回撤 {dd}% · 位置 {pos}%")
        if dd is not None and dd >= 25:
            level = 'buy'
            reasons.append("🟢 深度回撤 ≥25%，配置窗口")

    return {
        'code': code, 'name': name, 'kind': 'index',
        'close': m.get('close'), 'metrics': m,
        'eval': {'level': level, 'level_cn': LEVEL_CN[level], 'reasons': reasons, 'tips': tips, 'callback': callback},
        'signals': [], 'missed': [],
    }


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────
def generate_report(scan_date=None, weights=None):
    """
    生成日报数据 dict。
    scan_date: None=最新交易日
    返回: {date, generated_at, cards: [...], summary: {...}, last_view}
    """
    db = get_db()
    db.execute("CREATE TABLE IF NOT EXISTS watchlist_review_state (key TEXT PRIMARY KEY, value TEXT)")
    db.commit()
    scan_date = scan_date or db.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
    weights = weights or load_weights()
    wl = load_watchlist(db)
    holdings = load_holdings(db)
    last_view = db.execute("SELECT value FROM watchlist_review_state WHERE key='last_view'").fetchone()
    last_view = last_view['value'] if last_view else None

    cards = []
    for item in wl:
        code, name = item['code'], item['name']
        kind = classify(code, db)
        try:
            if kind == 'stock':
                card = scan_stock(code, name, db, scan_date, weights)
            elif kind in ('etf', 'index'):
                card = scan_index(code, name, db, scan_date)
            else:
                card = {'code': code, 'name': name, 'kind': 'unknown', 'error': '无法识别标的类型', 'level': 'hold'}
        except Exception as e:
            card = {'code': code, 'name': name, 'kind': kind, 'error': f'{type(e).__name__}: {e}', 'level': 'hold'}

        # 持仓联动（止损/浮盈）
        hold = holdings.get(code)
        if hold:
            close = card.get('close')
            if close:
                pnl = (close / hold['cost'] - 1) * 100
                card['holding'] = {'cost': round(hold['cost'], 2), 'pnl_pct': round(pnl, 1),
                                   'stop_loss': hold['stop_loss']}
                if card.get('eval'):
                    card['eval']['tips'] = (card['eval'].get('tips') or []) + [f"💼 持仓成本 {hold['cost']} · 浮盈 {round(pnl,1)}% · 止损位 {hold['stop_loss']}"]
            else:
                card['holding'] = {'cost': round(hold['cost'], 2), 'pnl_pct': None, 'stop_loss': hold['stop_loss']}
        cards.append(card)
    db.close()

    # 排序：avoid > 回调触发 > buy > miss > hold
    order = {'avoid': 0, 'wait': 1, 'buy': 2, 'buy_strong': 2, 'hold': 4}
    def key(c):
        base = order.get((c.get('eval') or {}).get('level', 'hold'), 5)
        gain = abs(c.get('close') or 0) and 1
        return (base, -((c.get('eval') or {}).get('net', 0)))
    cards.sort(key=key)

    # 汇总
    summary = {'avoid': 0, 'buy': 0, 'buy_strong': 0, 'wait': 0, 'hold': 0, 'error': 0}
    for c in cards:
        lv = (c.get('eval') or {}).get('level', 'hold')
        if c.get('error'):
            summary['error'] += 1
        summary[lv] = summary.get(lv, 0) + 1

    return {'date': scan_date, 'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'cards': cards, 'summary': summary, 'last_view': last_view,
            'total': len(cards)}


if __name__ == '__main__':
    # 快速试跑
    import json
    rep = generate_report()
    print('date:', rep['date'], '| cards:', rep['total'], '| summary:', rep['summary'])
    for c in rep['cards']:
        ev = c.get('eval') or {}
        print(f"  {c['code']} {c['name']:<6} {c.get('kind'):<6} {ev.get('level','?'):<10} 净分{ev.get('net','-'):<8} {c.get('error','')[:30]}")
