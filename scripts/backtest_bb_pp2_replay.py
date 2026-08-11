#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BB_V1 + PP_V3.2 共振信号全市场回放（防未来数据版）
====================================================
目的：验证 pattern-scan 页面口径（base_breakout V1 + pocket_pivot_v2_engine V3.2）
     的"同日共振"信号，在防未来数据的回放下是否仍然有效。

方法论：
  Phase 1 — V1 base_breakout：一次全量调用产出全历史信号。
            V1 引擎天然防未来：detect() 内每个 t_idx 只用 daily[0:t_idx+1] 数据
            （谷底搜索 search_end = t_idx - min_base，前高/均线/量能均在当日之前）。
  Phase 2 — PP_V3.2 pocket_pivot_v2_engine：逐日回放。
            对每个扫描日：
              1) 设置 chanlun_structure._target_date = scan_date（读当日 bi 快照）
              2) K 线截断到 scan_date（防未来：SMA/结构判断只用当日及之前数据）
              3) 跑 detect，只取 scan_date 当天的信号
            smart 模式：只对 Phase 1 的 BB 信号日回放（共振定义=同日双信号，
                        BB 无信号的日子 PP 单独出现不构成共振，无需计算）
            full 模式：逐日全市场回放（更慢，用于交叉验证）
  Phase 3 — 收益统计：共振 vs 仅BB vs 仅PP，未来 10/20/30/60 交易日涨跌幅。

用法：
  python scripts/backtest_bb_pp2_replay.py                    # smart 模式（推荐）
  python scripts/backtest_bb_pp2_replay.py --full             # full 逐日全市场
  python scripts/backtest_bb_pp2_replay.py --start 2023-08-01 --end 2026-08-07 --workers 8
  python scripts/backtest_bb_pp2_replay.py --skip-ph1 --skip-ph2   # 断点续跑

输出（analysis/ 目录）：
  bb_v1_signals.json      Phase 1 全市场 BB_V1 信号
  pp_v32_replay.json      Phase 2 PP_V3.2 回放结果（逐日，含共振标记）
  bb_pp2_replay_stats.json  Phase 3 统计
"""

import sys, os, json, time, argparse, sqlite3, traceback
from datetime import datetime
from multiprocessing import Pool

PROJ = r'D:\hanako\investment-system'
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'src'))
os.chdir(PROJ)

DB = os.path.join(PROJ, 'data', 'lixinger.db')
OUT = os.path.join(PROJ, 'analysis')
os.makedirs(OUT, exist_ok=True)

F_BB = os.path.join(OUT, 'bb_v1_signals.json')
F_PP = os.path.join(OUT, 'pp_v32_replay.json')
F_STATS = os.path.join(OUT, 'bb_pp2_replay_stats.json')

HORIZONS = (10, 20, 30, 60)

# ─────────────────────────────────────────────
# Phase 1: V1 base_breakout 全市场一次调用
# ─────────────────────────────────────────────

def _load_full_klines(code, end_date):
    """加载个股全量 K 线（前复权，注入 stock_code），截至 end_date"""
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    rows = db.execute("""SELECT date, COALESCE(adj_open, open) as open,
        COALESCE(adj_high, high) as high, COALESCE(adj_low, low) as low,
        COALESCE(adj_close, close) as close, volume, amount, change_pct
        FROM daily_kline WHERE stock_code=? AND date<=? ORDER BY date""",
        (code, end_date)).fetchall()
    db.close()
    if not rows:
        return []
    kl = [dict(r) for r in rows]
    for k in kl:
        k['stock_code'] = code
    # 前复权：change_pct 逆向推算（复刻 server._ensure_adj_prices）
    n = len(kl)
    kl[n-1]['adj_close'] = kl[n-1]['close']
    for i in range(n-2, -1, -1):
        chg = kl[i+1].get('change_pct')
        kl[i]['adj_close'] = kl[i+1]['adj_close'] / (1 + chg) if chg is not None else kl[i+1]['adj_close']
    for k in kl:
        ratio = k['adj_close'] / k['close'] if k['close'] else 1
        for f in ('open', 'high', 'low'):
            k[f] = k[f] * ratio if k[f] else k[f]
        k['close'] = k['adj_close']
    return kl


def _scan_bb_one(code, end_date):
    """单只股票跑 V1，返回 (code, [signal_date, ...])（worker 调用）"""
    try:
        from scanners.base_breakout import detect as bb_detect, load_params as bb_params
        kl = _load_full_klines(code, end_date)
        if len(kl) < 170:
            return (code, [])
        sigs = bb_detect(kl, bb_params())
        sigs = sigs if isinstance(sigs, list) else []
        dates = [s.get('date') or s.get('signal_date') for s in sigs if (s.get('date') or s.get('signal_date'))]
        return (code, dates)
    except Exception as e:
        return (code, [])


def _scan_bb_worker(args):
    """模块级 worker 包装（可 pickle）：args = (code, end_date) -> (code, dates)（信号按年过滤）"""
    code, end_date, start = args
    code, dates = _scan_bb_one(code, end_date)
    # 只保留 start 之后的信号（减少后续 PP 回放量）
    dates = [d for d in dates if d >= start]
    return (code, dates)


def phase1(start, end, workers):
    if os.path.exists(F_BB):
        with open(F_BB, encoding='utf-8') as f:
            return json.load(f)
    db = sqlite3.connect(DB)
    codes = [r[0] for r in db.execute(
        "SELECT DISTINCT stock_code FROM daily_kline WHERE date>=? AND date<=?",
        (start, end)).fetchall()]
    db.close()
    codes = [c for c in codes if not c.startswith(('8', '4', '9'))]
    print(f'[P1] V1 全市场扫描: {len(codes)} 只, workers={workers}', flush=True)
    t0 = time.time()
    bb_map = {}
    with Pool(workers) as pool:
        for i, (code, dates) in enumerate(pool.imap_unordered(
                _scan_bb_worker, [(c, end, start) for c in codes], chunksize=50)):
            if dates:
                bb_map[code] = dates
            if (i+1) % 1000 == 0:
                print(f'  {i+1}/{len(codes)} ({time.time()-t0:.0f}s)', flush=True)
    with open(F_BB, 'w', encoding='utf-8') as f:
        json.dump(bb_map, f, ensure_ascii=False)
    n_sig = sum(len(v) for v in bb_map.values())
    print(f'[P1] 完成 {time.time()-t0:.0f}s: {len(bb_map)} 只, {n_sig} 信号 → {F_BB}', flush=True)
    return bb_map


# ─────────────────────────────────────────────
# Phase 2: PP_V3.2 逐日回放
# ─────────────────────────────────────────────

def _scan_pp_day(code, scan_date, full_klines):
    """单股单日回放 PP_V3.2：设置 target_date + K线截断。
    full_klines 为该股全量K线（预加载），按 scan_date 截断。"""
    try:
        import scanners.chanlun_structure as cls
        from scanners.pocket_pivot_v2_engine import detect as pp2_detect
        # 找到 scan_date 在 K 线中的位置，截断
        idx = None
        for i, k in enumerate(full_klines):
            if k['date'] == scan_date:
                idx = i
                break
            if k['date'] > scan_date:
                idx = i  # scan_date 非交易日，截断到其前
                break
        if idx is None:
            return (code, scan_date, False)
        kl = full_klines[:idx+1]
        if len(kl) < 100:
            return (code, scan_date, False)
        # 防未来：读当日快照
        cls._target_date = scan_date
        try:
            sigs = pp2_detect(kl)
        finally:
            cls._target_date = None
        sigs = sigs if isinstance(sigs, list) else []
        hit = any(s.get('date') == scan_date for s in sigs)
        return (code, scan_date, hit)
    except Exception:
        return (code, scan_date, False)


def _pp_worker(args):
    """worker：处理一只股票的所有回放日（预加载K线一次）"""
    code, dates, end = args
    try:
        full_klines = _load_full_klines(code, end)
        if not full_klines:
            return (code, [])
        results = []
        for d in sorted(dates):
            results.append(_scan_pp_day(code, d, full_klines))
        return (code, results)
    except Exception:
        return (code, [])


def phase2(bb_map, start, end, workers, full_mode):
    if os.path.exists(F_PP) and not full_mode:
        with open(F_PP, encoding='utf-8') as f:
            return json.load(f)

    # 收集回放任务
    tasks = []
    if full_mode:
        # full：全市场 × 逐日（所有交易日）
        db = sqlite3.connect(DB)
        trade_dates = [r[0] for r in db.execute(
            "SELECT DISTINCT date FROM daily_kline WHERE date>=? AND date<=? ORDER BY date",
            (start, end)).fetchall()]
        codes = [r[0] for r in db.execute(
            "SELECT DISTINCT stock_code FROM daily_kline WHERE date>=? AND date<=?",
            (start, end)).fetchall()]
        db.close()
        codes = [c for c in codes if not c.startswith(('8', '4', '9'))]
        for code in codes:
            tasks.append((code, trade_dates, end))
        print(f'[P2] FULL 模式: {len(codes)} 只 × {len(trade_dates)} 日', flush=True)
    else:
        # smart：只对 BB 信号日回放
        for code, dates in bb_map.items():
            if dates:
                tasks.append((code, dates, end))
        n_days = sum(len(d) for _, d, _ in tasks)
        print(f'[P2] SMART 模式: {len(tasks)} 只, {n_days} 个 BB 信号日', flush=True)

    t0 = time.time()
    pp_map = {}  # (code, date) -> hit
    with Pool(workers) as pool:
        for i, (code, results) in enumerate(pool.imap_unordered(_pp_worker, tasks, chunksize=10)):
            for code2, d, hit in results:
                pp_map[(code2, d)] = hit
            if (i+1) % 500 == 0:
                print(f'  {i+1}/{len(tasks)} ({time.time()-t0:.0f}s)', flush=True)
    print(f'[P2] 完成 {time.time()-t0:.0f}s, 回放 {len(pp_map)} 股日', flush=True)

    # 保存（key 序列化）
    out = {f'{c}|{d}': h for (c, d), h in pp_map.items()}
    with open(F_PP, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)
    print(f'[P2] → {F_PP}', flush=True)
    return pp_map


# ─────────────────────────────────────────────
# Phase 3: 收益统计
# ─────────────────────────────────────────────

def _load_adj_close_map(codes):
    """加载股票前复权收盘价，{code: [(date, adj_close), ...]}"""
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    result = {}
    for code in codes:
        rows = db.execute("""SELECT date, close, change_pct FROM daily_kline
            WHERE stock_code=? AND date>=? AND date<=? ORDER BY date""",
            (code, '2021-01-01', '2026-08-07')).fetchall()
        if not rows:
            continue
        n = len(rows)
        adj = [None]*n
        adj[n-1] = rows[n-1]['close']
        for i in range(n-2, -1, -1):
            chg = rows[i+1]['change_pct']
            adj[i] = adj[i+1]/(1+chg) if chg is not None else adj[i+1]
        result[code] = [(rows[i]['date'], adj[i]) for i in range(n)]
    db.close()
    return result


def _calc(pairs, klines):
    stats = {h: [] for h in HORIZONS}
    for code, sig_date in pairs:
        kl = klines.get(code)
        if not kl:
            continue
        dates = [d for d, _ in kl]
        if sig_date not in dates:
            continue
        i0 = dates.index(sig_date)
        base = kl[i0][1]
        if not base:
            continue
        for h in HORIZONS:
            i1 = i0 + h
            if i1 < len(kl):
                stats[h].append((kl[i1][1]/base - 1)*100)
    return stats


def _summ(vals):
    if not vals:
        return None
    vs = sorted(vals)
    n = len(vs)
    return {'n': n, 'mean': round(sum(vs)/n, 2), 'median': round(vs[n//2], 2),
            'win': round(sum(1 for v in vs if v > 0)/n*100, 1),
            'p10': round(vs[int(n*.1)], 2), 'p25': round(vs[int(n*.25)], 2),
            'p75': round(vs[int(n*.75)], 2), 'p90': round(vs[int(n*.9)], 2)}


def phase3(bb_map, pp_map, start, end):
    # 三组
    conflu = []   # BB 日 + PP 命中
    bb_only = []  # BB 日 + PP 未命中
    pp_only = []  # 非 BB 日但 PP 命中（仅 full 模式可算）
    for (code, d), hit in pp_map.items():
        in_bb = code in bb_map and d in bb_map[code]
        if in_bb:
            (conflu if hit else bb_only).append((code, d))
        elif hit:
            pp_only.append((code, d))
    print(f'\n[P3] 共振(同日双信号): {len(conflu)} | 仅BB: {len(bb_only)} | 仅PP(full): {len(pp_only)}')

    all_codes = set(c for c, _ in conflu) | set(c for c, _ in bb_only) | set(c for c, _ in pp_only)
    klines = _load_adj_close_map(all_codes)
    print(f'[P3] K线加载 {len(klines)} 只')

    out = {'horizons': {}}
    groups = {'共振(BB+PP2同日)': conflu, '仅BB(V1)': bb_only}
    if pp_only:
        groups['仅PP2(V3.2)'] = pp_only
    for gname, pairs in groups.items():
        stats = _calc(pairs, klines)
        out[gname] = {str(h): _summ(stats[h]) for h in HORIZONS}
        print(f'\n◆ {gname} (n={len(pairs)})')
        for h in HORIZONS:
            s = out[gname][str(h)]
            if s:
                print(f'  {h}日: 均值{s["mean"]:+.2f}% 中位{s["median"]:+.2f}% 胜率{s["win"]:.1f}% (n={s["n"]})')

    with open(F_STATS, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'\n[P3] → {F_STATS}')


# ─────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='BB_V1 + PP_V3.2 防未来回放')
    parser.add_argument('--start', default='2023-08-01')
    parser.add_argument('--end', default='2026-08-07')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--full', action='store_true', help='full 逐日全市场（默认 smart）')
    parser.add_argument('--skip-ph1', action='store_true')
    parser.add_argument('--skip-ph2', action='store_true')
    args = parser.parse_args()

    t_all = time.time()
    print(f'=== BB_V1 + PP_V3.2 防未来回放 ===')
    print(f'区间: {args.start} ~ {args.end} | workers={args.workers} | mode={"FULL" if args.full else "SMART"}')

    bb_map = {} if args.skip_ph1 else phase1(args.start, args.end, args.workers)
    print(f'\n[P1] BB_V1 信号: {len(bb_map)} 只, {sum(len(v) for v in bb_map.values())} 条')

    pp_map = {} if args.skip_ph2 else phase2(bb_map, args.start, args.end, args.workers, args.full)
    if isinstance(pp_map, dict) and pp_map and '|' in list(pp_map.keys())[0]:
        # 断点续跑时从 JSON 恢复
        pp_map = {tuple(k.split('|')): v for k, v in pp_map.items()}
    print(f'\n[P2] PP 回放: {len(pp_map)} 股日')

    phase3(bb_map, pp_map, args.start, args.end)
    print(f'\n=== 全部完成 {time.time()-t_all:.0f}s ===')
