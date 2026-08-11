# -*- coding: utf-8 -*-
"""对比分析：共振 vs 单独引擎信号 + 年份分布 + 大盘基准"""
import sys, os, sqlite3, json
from collections import defaultdict

PROJ = r'D:\hanako\investment-system'
DB = os.path.join(PROJ, 'data', 'lixinger.db')
END_DATE = '2026-08-07'
HORIZONS = (10, 20, 30, 60)

def load_klines(codes):
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    result = {}
    for code in codes:
        rows = db.execute("""SELECT date, close, change_pct FROM daily_kline
            WHERE stock_code=? AND date>=? AND date<=? ORDER BY date""",
            (code, '2021-01-01', END_DATE)).fetchall()
        if not rows: continue
        n = len(rows)
        adj = [None] * n
        adj[n-1] = rows[n-1]['close']
        for i in range(n-2, -1, -1):
            chg = rows[i+1]['change_pct']
            adj[i] = adj[i+1] / (1 + chg) if chg is not None else adj[i+1]
        result[code] = [(rows[i]['date'], adj[i]) for i in range(n)]
    db.close()
    return result

def calc_returns(pairs, klines):
    """返回 {h: [returns...]} + 年份分解 {year: {h: [returns]}}"""
    rets = {h: [] for h in HORIZONS}
    by_year = defaultdict(lambda: {h: [] for h in HORIZONS})
    for code, sig_date in pairs:
        kl = klines.get(code)
        if not kl: continue
        dates = [d for d, _ in kl]
        if sig_date not in dates: continue
        i0 = dates.index(sig_date)
        base = kl[i0][1]
        if not base: continue
        year = sig_date[:4]
        for h in HORIZONS:
            i1 = i0 + h
            if i1 < len(kl):
                r = (kl[i1][1] / base - 1) * 100
                rets[h].append(r)
                by_year[year][h].append(r)
    return rets, by_year

def summarize(vals):
    if not vals: return None
    vs = sorted(vals)
    n = len(vs)
    return {
        'n': n,
        'mean': round(sum(vs)/n, 2),
        'median': round(vs[n//2], 2),
        'win': round(sum(1 for v in vs if v > 0)/n*100, 1),
    }

def main():
    # 加载共振 + 全量引擎信号（重新扫描得到全量日期，与共振同源）
    # 为省时：直接读共振文件 + 单引擎信号日期文件（若不存在则提示）
    base_dir = os.path.join(PROJ, 'analysis')
    with open(os.path.join(base_dir, 'bb_pp2_confluence_signal_dates.json'), encoding='utf-8') as f:
        conflu = [(c, d) for c, d in json.load(f)['confluence']]

    # 单独引擎信号：重跑会太久，从之前的全量扫描结果重建——需要重新扫描单引擎
    # 这里用快捷方式：扫描脚本已跑过，但没有保存单引擎日期。重新跑一次单引擎快速版？
    # 决策：为对比公平，重新扫描只保存日期的轻量版（快，~4分钟）
    print('需要单引擎信号日期，轻量重扫中...')
    import subprocess
    # 直接用 scan 脚本的逻辑但保存全量——直接跑一个变体
    from scan_bb_pp2_confluence import scan_one, load_kline  # noqa

    # 单引擎扫描（只跑 base_breakout 和 pocket_pivot_v2 的日期集合）
    import sqlite3 as sq
    db = sq.connect(DB)
    codes = [r[0] for r in db.execute(
        "SELECT DISTINCT stock_code FROM daily_kline WHERE date >= '2023-08-01' AND date <= ?",
        (END_DATE,)).fetchall()]
    db.close()
    codes = [c for c in codes if not c.startswith(('8', '4', '9'))]

    from multiprocessing import Pool
    bb_all, pp2_all = set(), set()
    with Pool(8) as pool:
        for i, (code, bb, pp2) in enumerate(pool.imap_unordered(scan_one, codes, chunksize=50)):
            for d in bb: bb_all.add((code, d))
            for d in pp2: pp2_all.add((code, d))
    print(f'bb={len(bb_all)} pp2={len(pp2_all)} conflu={len(conflu)}')

    klines = load_klines(set(c for c, _ in bb_all) | set(c for c, _ in pp2_all))
    print(f'K线: {len(klines)} 只')

    # 三组收益
    groups = {
        '共振(BB+PP2同日)': conflu,
        '仅基部突破(BB)': sorted(bb_all - set(conflu)),
        '仅口袋支点V2(PP2)': sorted(pp2_all - set(conflu)),
    }
    # 大盘基准：上证综指同期
    db = sq.connect(DB)
    db.row_factory = sq.Row
    sh = db.execute("""SELECT date, close FROM index_daily_kline
        WHERE stock_code='000001' AND date>='2023-08-01' AND date<=? ORDER BY date""",
        (END_DATE,)).fetchall()
    db.close()
    sh_dates = [r['date'] for r in sh]
    sh_closes = [r['close'] for r in sh]
    sh_map = dict(zip(sh_dates, sh_closes))

    print(f'\n{"="*78}')
    print(f'三组信号未来收益对比（2023-08-01 ~ 2026-08-07）')
    print(f'{"="*78}')
    all_rets = {}
    for gname, pairs in groups.items():
        rets, by_year = calc_returns(pairs, klines)
        all_rets[gname] = rets
        print(f'\n◆ {gname} (n={len(pairs)})')
        for h in HORIZONS:
            s = summarize(rets[h])
            if s:
                print(f'  {h}日: 均值{s["mean"]:+.2f}% 中位{s["median"]:+.2f}% 胜率{s["win"]:.1f}% (n={s["n"]})')

    # 年份分解（只做共振）
    print(f'\n{"="*78}')
    print(f'共振信号按年份分解（60日收益）')
    print(f'{"="*78}')
    _, conflu_year = calc_returns(conflu, klines)
    for year in sorted(conflu_year.keys()):
        s = summarize(conflu_year[year][60])
        if s:
            print(f'  {year}: n={s["n"]:5d} 均值{s["mean"]:+.2f}% 中位{s["median"]:+.2f}% 胜率{s["win"]:.1f}%')

    # 大盘基准（上证综指 60 日滚动）
    sh_rets = []
    for i in range(60, len(sh_closes)):
        sh_rets.append((sh_closes[i] / sh_closes[i-60] - 1) * 100)
    s = summarize(sh_rets)
    if s:
        print(f'\n基准: 上证综指 60日滚动收益 n={s["n"]} 均值{s["mean"]:+.2f}% 中位{s["median"]:+.2f}% 胜率{s["win"]:.1f}%')

    out = {}
    for gname, rets in all_rets.items():
        out[gname] = {str(h): summarize(rets[h]) for h in HORIZONS}
    out['conflu_by_year'] = {y: {str(h): summarize(v[h]) for h in HORIZONS} for y, v in conflu_year.items()}
    with open(os.path.join(base_dir, 'bb_pp2_confluence_compare.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'\n已保存 analysis/bb_pp2_confluence_compare.json')

if __name__ == '__main__':
    main()
