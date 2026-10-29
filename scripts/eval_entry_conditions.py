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
def _kline(con, code, n=260):
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
    # fundamental_indicator 是长表（metric_code/value），不是宽表
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
def eval_rule(rule, m, canslim):
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
    if t == 'turnover':
        v = m.get('amount_yi')
        return cmp(v), f'成交额 {label(v,"亿")} {op} {th}亿', rule
    return None, f'未知规则 {t}', rule


def get_canslim(code):
    """从 canslim_scorecard 表或直接调评分函数取总分（失败返回 None）。"""
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
    out, ok_n = [], 0
    for r in rules:
        ok, txt, raw = eval_rule(r, m, canslim)
        if ok:
            ok_n += 1
        out.append({'rule': r, 'ok': ok, 'desc': txt})
    return {'stock_code': code, 'stock_name': name or code,
            'satisfied': ok_n, 'total': len(rules),
            'triggered': ok_n == len(rules), 'detail': out,
            'canslim': canslim, 'close': m['close']}


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

    # 评估
    if a.code:
        rows = con.execute("SELECT * FROM stock_entry_conditions WHERE stock_code=?",
                           (a.code,)).fetchall()
    else:
        rows = con.execute(
            "SELECT c.* FROM stock_entry_conditions c "
            "JOIN watchlist w ON w.stock_code=c.stock_code "
            "WHERE c.enabled=1").fetchall() if _has(con, 'watchlist') else \
            con.execute("SELECT * FROM stock_entry_conditions WHERE enabled=1").fetchall()

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
        print(f"    {res['stock_code']} {res['stock_name']:<8} {flag}")
        for d in res['detail']:
            mark = '✅' if d['ok'] else ('❌' if d['ok'] is False else '➖')
            print(f"       {mark} {d['desc']}")
    con.close()


def _has(con, table):
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


if __name__ == '__main__':
    main()
