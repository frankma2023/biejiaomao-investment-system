# -*- coding: utf-8 -*-
"""
自选池日报 · 规则回放验证（P0）

对自选池 13 只近 1 年历史信号重放规则引擎，统计各档位的：
- 建议后 20 日正收益比例 / 中位收益
- 各档位样本量
产出：docs/analysis/自选池日报规则回放验证_YYYY-MM.md

数据源：pattern_scan_signals（每日信号总表）+ mw_signal_daily（MW 历史）
不重跑引擎——规则层重放（纯 CPU，秒级）
"""
import sys
import os
import sqlite3
import json
from collections import defaultdict
from datetime import datetime, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))

from src.scanners.report_rules import (evaluate, load_weights, normalize_engine_signal,
                                       normalize_mw_rows, _dedup_by_source, LEVEL_CN)
from src.scanners.watchlist_report import load_watchlist, classify, get_db

WINDOW = 60          # 信号窗口（自然日）
FWD = 20             # 前瞻收益交易日
N_YEAR = 400         # 回看天数（自然日）


def load_kline_map(db, codes):
    """{code: {date: close}} 近 400+250 天"""
    out = {}
    for code in codes:
        rows = db.execute(f"""SELECT date, COALESCE(adj_close, close) as close FROM daily_kline
            WHERE stock_code=? AND date >= date('now', '-{N_YEAR + 260} days') ORDER BY date""", (code,)).fetchall()
        out[code] = {r['date']: r['close'] for r in rows}
    return out


def load_hist_signals(db, codes, start):
    """{code: {date: [signals]}} pattern_scan_signals + mw"""
    out = {c: defaultdict(list) for c in codes}
    for code in codes:
        rows = db.execute("""SELECT date, signals_json FROM pattern_scan_signals
            WHERE stock_code=? AND date>=? ORDER BY date""", (code, start)).fetchall()
        for r in rows:
            for s in json.loads(r['signals_json']):
                ns = normalize_engine_signal(s)
                if ns:
                    out[code][r['date']].append(ns)
        mw = db.execute("""SELECT * FROM mw_signal_daily WHERE stock_code=?
            AND COALESCE(b1_date, b2_date)>=?""", (code, start)).fetchall()
        for m in normalize_mw_rows([dict(x) for x in mw]):
            out[code][m['date']].append(m)
    return out


def daily_context(closes, dates, date):
    """截至 date 的上下文（用历史序列）"""
    idx = [i for i, d in enumerate(dates) if d <= date]
    if not idx:
        return None
    i = idx[-1]
    seg = closes[:i + 1]
    close = closes[i]
    win = seg[-250:]
    lo, hi = min(win), max(win)
    pos = (close - lo) / (hi - lo) * 100 if hi > lo else 50
    ma50 = sum(seg[-50:]) / 50 if len(seg) >= 50 else None
    ma50p = sum(seg[-55:-5]) / 50 if len(seg) >= 55 else None
    slope = (ma50 - ma50p) / ma50p * 100 if ma50 and ma50p else 0
    gain = (close / lo - 1) * 100 if lo else 0
    fib = [hi - (hi - lo) * r for r in (0.382, 0.5, 0.618)] if hi > lo else []
    return {'close': close, 'pos_250': round(pos), 'ma50': ma50, 'ma50_slope': slope,
            'gain_from_low': gain, 'fib_levels': [round(f, 2) for f in fib],
            'low_250': lo, 'high_250': hi}


def replay():
    db = get_db()
    weights = load_weights()
    wl = [w for w in load_watchlist(db) if classify(w['code'], db) == 'stock']
    codes = [w['code'] for w in wl]
    print(f'回放 {len(codes)} 只股票，窗口 {WINDOW} 日，前瞻 {FWD} 日')

    klines = load_kline_map(db, codes)
    start = (datetime.now() - timedelta(days=N_YEAR)).strftime('%Y-%m-%d')
    hist = load_hist_signals(db, codes, start)

    stats = defaultdict(lambda: {'n': 0, 'pos': [], 'med': None})
    by_stock = defaultdict(lambda: defaultdict(int))

    for code in codes:
        dates = sorted(klines[code].keys())
        closes = [klines[code][d] for d in dates]
        sig_dates = sorted(hist[code].keys())
        if not sig_dates:
            continue
        # 逐信号日重放（每 3 日采样一次降噪，避免相邻日重复统计）
        last_eval = ''
        for d in sig_dates:
            if d < start:
                continue
            if d <= last_eval:
                continue
            last_eval = d
            ctx = daily_context(closes, dates, d)
            if not ctx:
                continue
            # 收集 d 前 WINDOW 天内信号
            pool = []
            for sd in sig_dates:
                if sd > d:
                    break
                age = (datetime.strptime(d, '%Y-%m-%d') - datetime.strptime(sd, '%Y-%m-%d')).days
                if age <= WINDOW:
                    pool.extend(hist[code][sd])
            pool = _dedup_by_source(pool)
            res = evaluate(pool, ctx, weights=weights, scan_date=d)
            lv = res['level']
            by_stock[code][lv] += 1
            # 前瞻 20 交易日收益
            di = dates.index(d) if d in dates else None
            if di is None:
                # d 可能不是交易日（信号日=交易日，但保险）
                di = [i for i, x in enumerate(dates) if x <= d][-1] if any(x <= d for x in dates) else None
            if di is not None and di + FWD < len(closes):
                fwd = (closes[di + FWD] / closes[di] - 1) * 100
                stats[lv]['n'] += 1
                stats[lv]['pos'].append(fwd)
                stats[lv]['med'] = None

    # 汇总
    print('\n' + '=' * 70)
    print(f'回放验证结果（{start} ~ 今天，前瞻 {FWD} 交易日，同源去重，每 3 日采样）')
    print('=' * 70)
    lines = []
    for lv in ('buy_strong', 'buy', 'hold', 'wait', 'avoid'):
        s = stats[lv]
        if s['n'] == 0:
            print(f"  {LEVEL_CN[lv]:<8} 样本 0")
            lines.append(f"| {LEVEL_CN[lv]} | 0 | — | — |")
            continue
        pos = sorted(s['pos'])
        med = pos[len(pos) // 2]
        win = sum(1 for x in pos if x > 0) / len(pos) * 100
        avg = sum(pos) / len(pos)
        print(f"  {LEVEL_CN[lv]:<8} 样本 {s['n']:>4} | 20日胜率 {win:.0f}% | 中位 {med:+.1f}% | 平均 {avg:+.1f}%")
        lines.append(f"| {LEVEL_CN[lv]} | {s['n']} | {win:.0f}% | {med:+.1f}% | {avg:+.1f}% |")
    print('\n各档位样本量（按股票）:')
    for code in codes:
        d = dict(by_stock[code])
        if d:
            print(f"  {code}: {d}")

    # 存档
    os.makedirs(os.path.join(PROJECT_DIR, 'docs', 'analysis'), exist_ok=True)
    fn = os.path.join(PROJECT_DIR, 'docs', 'analysis',
                      f'自选池日报规则回放验证_{datetime.now().strftime("%Y-%m")}.md')
    with open(fn, 'w', encoding='utf-8') as f:
        f.write(f"# 自选池日报 · 规则回放验证（{start} ~ 今）\n\n")
        f.write(f"- 标的：{len(codes)} 只自选股票\n- 方法：pattern_scan_signals + mw_signal_daily 历史信号，规则层重放（不重跑引擎），同源去重，每 3 日采样，前瞻 {FWD} 交易日\n\n")
        f.write("| 档位 | 样本 | 20日胜率 | 中位收益 | 平均收益 |\n|---|---|---|---|---|\n")
        f.write('\n'.join(lines) + '\n')
    print('\n已存档:', fn)


if __name__ == '__main__':
    replay()
