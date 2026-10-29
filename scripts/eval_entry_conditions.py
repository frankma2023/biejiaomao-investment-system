#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自选股「入条件」存放与每日评估。

设计：条件存成结构化规则（不是自由文本），这样每天能用本地库算出来、
判断是否满足，并在驾驶舱提醒。

规则类型（rule.type）：
  price_above_ma   {"n": 60}                    收盘价 > MA(n)
  price_below_ma   {"n": 20}                    收盘价 < MA(n)
  vol_ratio        {"op": ">=", "v": 1.5}      5日均量/20日均量
  canslim          {"op": ">=", "v": 65}        CANSLIM 总分
  canslim_dim      {"dim": "N", "op": ">=", "v": 8}
  pe_pct           {"op": "<=", "v": 20}        PE 3 年分位（%）
  rsi              {"op": "<=", "v": 30, "period": 6}
  dist_high        {"op": ">=", "v": -10}       距 250 日高点（%）
  pos_250          {"op": "<=", "v": 30}        250 日区间位置（%）
  turnover          {"op": "<=", "v": 6}        当日成交额（亿）

用法:
    python scripts/eval_entry_conditions.py                # 评估自选股池全部
    python scripts/eval_entry_conditions.py --code 002648  # 单只
    python scripts/eval_entry_conditions.py --seed         # 写入示例条件
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = str(ROOT / 'data' / 'lixinger.db')


# ══════════ 建表 ══════════
def ensure_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS stock_entry_conditions (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT,
            rules TEXT NOT NULL,           -- JSON: [{"type":..., ...}]
            summary TEXT,                  -- 人读的一句话总结
            source TEXT,                   -- 条件来源（哪份报告/哪次分析）
            enabled INTEGER DEFAULT 1,
            created_at TEXT, updated_at TEXT)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS stock_entry_alerts (
            stock_code TEXT NOT NULL REFERENCES stock_entry_conditions(stock_code),
            date TEXT NOT NULL,
            satisfied INTEGER NOT NULL,    -- 满足的规则数
            total INTEGER NOT NULL,        -- 规则总数
            detail TEXT,                   -- JSON: 逐条结果
            triggered INTEGER NOT NULL,    -- 是否全部满足（=提醒条件）
            updated_at TEXT,
            PRIMARY KEY (stock_code, date))
    """)
    con.commit()


# ══════════ 指标计算 ══════════
def is_index_code(code):
    """指数代码判定：93xxxx / 98xxxx / Hxxxxx（国证、中证策略、港股通系列）。"""
    c = str(code)
    return c.startswith(('93', '98', 'H'))


def _kline(con, code, n=260):
    """个股走 daily_kline，指数走 index_daily_kline（两表字段名不同）。"""
    if is_index_code(code):
        return con.execute(
            "SELECT date, close, amount FROM index_daily_kline "
            "WHERE stock_code=? AND kline_type='normal' AND close>0 "
            "ORDER BY date DESC LIMIT ?", (code, n)).fetchall()
    return con.execute(
        "SELECT date, close, amount FROM daily_kline WHERE stock_code=? "
        "AND close>0 ORDER BY date DESC LIMIT ?", (code, n)).fetchall()


def _ma(closes, n):
    seg = closes[:n]
    return sum(seg) / len(seg) if len(seg) >= n else None


def _rsi(closes, period):
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(period):
        d = closes[i] - closes[i + 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - 100 / (1 + rs)


def get_cpa_stage(con, code, date=None):
    """读 CPA 引擎已算好的阶段（个股 cpa_stage_daily / 指数 index_cpa_daily）。

    引擎在 src/scanners/cpa_index.py，全市场每日扫描，不需要重算。
    返回 {stage, name, action, days, support, invalid} 或 None。
    """
    try:
        if is_index_code(code):
            r = con.execute(
                "SELECT stage, stage_name, days_in_stage, since_date, action, close "
                "FROM index_cpa_daily WHERE stock_code=? "
                + ("AND date<=? " if date else "")
                + "ORDER BY date DESC LIMIT 1",
                (code, date) if date else (code,)).fetchone()
            if not r:
                return None
            return {'stage': r['stage'], 'name': r['stage_name'],
                    'days': r['days_in_stage'], 'since': r['since_date'],
                    'action': r['action'], 'close': r['close'],
                    'support': None, 'invalid': None}
        r = con.execute(
            "SELECT stage, prior_stage, days_in_stage, stage_start_date, action, "
            "structure_support, invalid_level, close FROM cpa_stage_daily "
            "WHERE stock_code=? " + ("AND date<=? " if date else "")
            + "ORDER BY date DESC LIMIT 1",
            (code, date) if date else (code,)).fetchone()
        if not r:
            return None
        return {'stage': r['stage'], 'prior': r['prior_stage'],
                'days': r['days_in_stage'], 'since': r['stage_start_date'],
                'action': r['action'], 'support': r['structure_support'],
                'invalid': r['invalid_level'], 'close': r['close']}
    except sqlite3.OperationalError:
        return None


def compute_metrics(con, code):
    rows = _kline(con, code)
    if len(rows) < 60:
        return None
    closes = [r[1] for r in rows]
    amts = [r[2] or 0 for r in rows]
    cur = closes[0]
    m = {'close': cur, 'amount_yi': round(amts[0] / 1e8, 2)}
    for n in (5, 10, 20, 60, 120, 250):
        m[f'ma{n}'] = _ma(closes, n)
    v5 = sum(amts[:5]) / 5 if len(amts) >= 5 else 0
    v20 = sum(amts[:20]) / 20 if len(amts) >= 20 else 0
    m['vol_ratio'] = round(v5 / v20, 2) if v20 else None
    hi = max(closes[:250]) if len(closes) >= 250 else max(closes)
    lo = min(closes[:250]) if len(closes) >= 250 else min(closes)
    m['high250'], m['low250'] = hi, lo
    m['dist_high'] = round((cur / hi - 1) * 100, 1)
    m['pos_250'] = round((cur - lo) / (hi - lo) * 100, 0) if hi > lo else 50
    m['rsi6'] = round(_rsi(closes, 6), 1) if _rsi(closes, 6) else None
    # 估值分位
    # 指数：走 index_fundamental_daily 宽表
    if is_index_code(code):
        try:
            r = con.execute(
                "SELECT pe_ttm, pe_ttm_pct, pb, pb_pct, dyr FROM index_fundamental_daily "
                "WHERE stock_code=? AND pe_ttm IS NOT NULL ORDER BY date DESC LIMIT 1",
                (code,)).fetchone()
            if r:
                m['pe'] = round(r['pe_ttm'], 2) if r['pe_ttm'] is not None else None
                m['pe_pct'] = round((r['pe_ttm_pct'] or 0) * 100, 1)
                m['pb'] = round(r['pb'], 2) if r['pb'] is not None else None
                m['dyr'] = round((r['dyr'] or 0) * 100, 2)
        except sqlite3.OperationalError:
            pass
        return m

    # 个股：fundamental_indicator 是长表（metric_code/value），不是宽表
    try:
        rows2 = con.execute(
            "SELECT metric_code, value FROM fundamental_indicator "
            "WHERE stock_code=? AND metric_code IN ('pe_ttm','pe_ttm_pos','d_pe_ttm','pb','dyr') "
            "ORDER BY date DESC LIMIT 40", (code,)).fetchall()
        last = {}
        for mc, val in rows2:
            if mc not in last and val is not None:
                last[mc] = val
        if 'pe_ttm' in last:
            m['pe'] = round(last['pe_ttm'], 2)
        if 'pb' in last:
            m['pb'] = round(last['pb'], 2)
        if 'pe_ttm_pos' in last:
            m['pe_pct'] = round(last['pe_ttm_pos'] * 100, 1)
    except sqlite3.OperationalError:
        pass
    return m


# ══════════ 规则评估 ══════════
def eval_rule(rule, m, canslim, cpa=None):
    t = rule.get('type')
    op = rule.get('op', '>=')
    th = rule.get('v')

    def cmp(val):
        if val is None:
            return None
        return {'>=': val >= th, '<=': val <= th, '>': val > th, '<': val < th}.get(op)

    def label(val, unit=''):
        return f'{val}{unit}' if val is not None else '无数据'

    if t == 'price_above_ma':
        n = rule.get('n', 60)
        v = m.get(f'ma{n}')
        ok = (m['close'] > v) if v else None
        return ok, f'价 {m["close"]:.2f} > MA{n} {label(round(v,2) if v else None)}', rule
    if t == 'price_below_ma':
        n = rule.get('n', 20)
        v = m.get(f'ma{n}')
        return (m['close'] < v if v else None), f'价 {m["close"]:.2f} < MA{n} {label(round(v,2) if v else None)}', rule
    if t == 'vol_ratio':
        v = m.get('vol_ratio')
        return cmp(v), f'量比 {label(v)} {op} {th}', rule
    if t == 'canslim':
        if canslim is None:
            return None, 'CANSLIM 不适用于指数', rule
        return cmp(canslim), f'CANSLIM {label(canslim)} {op} {th}', rule
    if t == 'canslim_dim':
        d = rule.get('dim')
        return None, f'CANSLIM 分维度 {d}（暂未接入）', rule
    if t == 'pe_pct':
        v = m.get('pe_pct')
        return cmp(v), f'PE 分位 {label(v,"%")} {op} {th}%', rule
    if t == 'rsi':
        p = rule.get('period', 6)
        v = m.get(f'rsi{p}')
        return cmp(v), f'RSI{p} {label(v)} {op} {th}', rule
    if t == 'dist_high':
        v = m.get('dist_high')
        return cmp(v), f'距高点 {label(v,"%")} {op} {th}%', rule
    if t == 'pos_250':
        v = m.get('pos_250')
        return cmp(v), f'250日位置 {label(v,"%")} {op} {th}%', rule
    if t == 'cpa_stage':
        # 期望阶段（in）或排除阶段（not_in）。阶段用 ⓪~⑧ 或数字。
        st = (cpa or {}).get('stage')
        name = (cpa or {}).get('name') or ''
        want_in = rule.get('in')
        want_not = rule.get('not_in')
        txt = f"CPA {st} {name[:22]}" if st is not None else 'CPA 无数据'
        if st is None:
            return None, txt, rule
        s = str(st).strip()
        if want_in:
            ok = s in [str(x).strip() for x in want_in]
            return ok, f"{txt} ∈ {want_in}", rule
        if want_not:
            ok = s not in [str(x).strip() for x in want_not]
            return ok, f"{txt} ∉ {want_not}", rule
        return None, txt + '（规则未指定 in / not_in）', rule
    if t == 'turnover':
        v = m.get('amount_yi')
        return cmp(v), f'成交额 {label(v,"亿")} {op} {th}亿', rule
    return None, f'未知规则 {t}', rule


def get_canslim(code):
    """CANSLIM 只对个股有意义；指数返回 None（规则里的 canslim 会被判为不适用）。"""
    if is_index_code(code):
        return None
    sys.path.insert(0, str(ROOT / 'src'))
    try:
        from scanners import canslim_score as cs
        r = cs.score_stock(code, datetime.now().date().isoformat())
        return r.get('score')
    except Exception:
        return None


def evaluate(con, code, rules, name=None):
    m = compute_metrics(con, code)
    if not m:
        return None
    canslim = get_canslim(code)
    cpa = get_cpa_stage(con, code)
    out, ok_n = [], 0
    for r in rules:
        ok, txt, raw = eval_rule(r, m, canslim, cpa)
        if ok:
            ok_n += 1
        out.append({'rule': r, 'ok': ok, 'desc': txt})
    return {'stock_code': code, 'stock_name': name or code,
            'satisfied': ok_n, 'total': len(rules),
            'triggered': ok_n == len(rules), 'detail': out,
            'canslim': canslim, 'cpa': cpa, 'close': m['close']}


def _sync_enabled(con):
    """把 stock_entry_conditions.enabled 与 watchlist 在场状态对齐。

    watchlist 用 removed_at IS NULL 表示在场；移出的股票条件保留但不展示，
    一旦重新加入自选池（removed_at 置 NULL）条件会自动恢复启用。
    """
    try:
        con.execute("""
            UPDATE stock_entry_conditions
               SET enabled = CASE WHEN stock_code IN (
                     SELECT stock_code FROM watchlist WHERE removed_at IS NULL
                   ) THEN 1 ELSE 0 END
        """)
        con.commit()
    except sqlite3.OperationalError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--code')
    ap.add_argument('--seed', action='store_true')
    ap.add_argument('--add', help='写入/覆盖某只股票的入条件（JSON 数组字符串）')
    ap.add_argument('--name', help='股票名称（配合 --add）')
    ap.add_argument('--summary', help='人读的一句话条件总结（配合 --add）')
    ap.add_argument('--source', help='条件来源，如「2026-10-28 个股体检报告」（配合 --add）')
    a = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    ensure_table(con)

    if a.add:
        code = a.code
        if not code:
            print('  ✗ --add 需同时给 --code')
            return 1
        try:
            rules = json.loads(a.add)
        except Exception as e:
            print(f'  ✗ --add 的 JSON 解析失败: {e}')
            return 1
        if not isinstance(rules, list) or not rules:
            print('  ✗ --add 需为非空 JSON 数组')
            return 1
        now = datetime.now().isoformat(timespec='seconds')
        con.execute(
            "INSERT OR REPLACE INTO stock_entry_conditions "
            "(stock_code, stock_name, rules, summary, source, enabled, created_at, updated_at) "
            "VALUES (?,?,?,?,?,1,?,?)",
            (code, a.name or code, json.dumps(rules, ensure_ascii=False),
             a.summary or '', a.source or '', now, now))
        con.commit()
        print(f'  ✓ 已写入 {code} 的 {len(rules)} 条入条件')
        # 立即评估一次
        res = evaluate(con, code, rules, a.name)
        if res:
            print(f'    当前 {res["satisfied"]}/{res["total"]}'
                  + ('  🔔 全部满足' if res['triggered'] else ''))
            for d in res['detail']:
                mark = '✅' if d['ok'] else ('❌' if d['ok'] is False else '➖')
                print(f'       {mark} {d["desc"]}')
        con.close()
        return 0

    if a.seed:
        seed = {
            '002648': dict(
                name='卫星化学',
                summary='放量站上 MA60(26.40) 且量≥1.5x；CANSLIM≥65',
                source='2026-10-28 个股体检报告',
                rules=[
                    {'type': 'price_above_ma', 'n': 60},
                    {'type': 'vol_ratio', 'op': '>=', 'v': 1.5},
                    {'type': 'canslim', 'op': '>=', 'v': 65},
                ]),
        }
        now = datetime.now().isoformat(timespec='seconds')
        for code, v in seed.items():
            con.execute(
                "INSERT OR REPLACE INTO stock_entry_conditions "
                "(stock_code, stock_name, rules, summary, source, enabled, created_at, updated_at) "
                "VALUES (?,?,?,?,?,1,?,?)",
                (code, v['name'], json.dumps(v['rules'], ensure_ascii=False),
                 v['summary'], v['source'], now, now))
        con.commit()
        print(f'  ✓ 已写入 {len(seed)} 条示例条件')

    # 同步 enabled：以自选池「在场」为准。移出自选池的自动隐藏，
    # 重新加回时自动恢复——不需要手工改 enabled。
    _sync_enabled(con)

    # 评估：只取自选池「在场」的股票。
    # 移出自选池 = 不再关注 → 连评估都不做（CANSLIM 评分要跑全部引擎，很贵）。
    # 这里用 JOIN + removed_at IS NULL 硬过滤，不依赖 enabled 的同步顺序。
    if a.code:
        rows = con.execute(
            "SELECT * FROM stock_entry_conditions WHERE stock_code=? AND enabled=1",
            (a.code,)).fetchall()
    elif _has(con, 'watchlist'):
        rows = con.execute(
            "SELECT c.* FROM stock_entry_conditions c "
            "JOIN watchlist w ON w.stock_code=c.stock_code "
            "WHERE c.enabled=1 AND w.removed_at IS NULL").fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM stock_entry_conditions WHERE enabled=1").fetchall()

    today = datetime.now().date().isoformat()
    now = datetime.now().isoformat(timespec='seconds')
    results = []
    for r in rows:
        rules = json.loads(r['rules'])
        res = evaluate(con, r['stock_code'], rules, r['stock_name'])
        if not res:
            continue
        results.append(res)
        con.execute(
            "INSERT OR REPLACE INTO stock_entry_alerts "
            "(stock_code,date,satisfied,total,detail,triggered,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (r['stock_code'], today, res['satisfied'], res['total'],
             json.dumps(res['detail'], ensure_ascii=False),
             1 if res['triggered'] else 0, now))
    con.commit()

    print(f'  评估 {len(results)} 只（{today}）')
    for res in results:
        flag = '🔔 触发' if res['triggered'] else f"{res['satisfied']}/{res['total']}"
        cp = res.get('cpa') or {}
        cps = (f"  CPA {cp.get('stage')} {str(cp.get('name') or '')[:20]}"
               f"({cp.get('days')}日)") if cp else ''
        print(f"    {res['stock_code']} {res['stock_name']:<8} {flag}{cps}")
        for d in res['detail']:
            mark = '✅' if d['ok'] else ('❌' if d['ok'] is False else '➖')
            print(f"       {mark} {d['desc']}")
    con.close()


def _has(con, table):
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


if __name__ == '__main__':
    main()
