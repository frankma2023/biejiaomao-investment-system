# -*- coding: utf-8 -*-
"""共振信号收益统计：未来 10/20/30/60 交易日涨跌幅
输入：analysis/bb_pp2_confluence_signal_dates.json（扫描产物）
输出：analysis/bb_pp2_confluence_stats.json + 控制台摘要
"""
import sys, os, sqlite3, json
from collections import defaultdict

PROJ = r'D:\hanako\investment-system'
DB = os.path.join(PROJ, 'data', 'lixinger.db')
SIGNAL_START = '2023-08-01'
END_DATE = '2026-08-07'
HORIZONS = (10, 20, 30, 60)

def load_kline_map(codes):
    """加载所有相关股票 K 线（前复权），返回 {code: [(date, adj_close), ...]}"""
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    result = {}
    for code in codes:
        rows = db.execute("""SELECT date, close, change_pct FROM daily_kline
            WHERE stock_code=? AND date>=? AND date<=? ORDER BY date""",
            (code, '2021-01-01', END_DATE)).fetchall()
        if not rows:
            continue
        # 前复权：从后往前推
        n = len(rows)
        adj = [None] * n
        adj[n-1] = rows[n-1]['close']
        for i in range(n-2, -1, -1):
            chg = rows[i+1]['change_pct']
            adj[i] = adj[i+1] / (1 + chg) if chg is not None else adj[i+1]
        result[code] = [(rows[i]['date'], adj[i]) for i in range(n)]
    db.close()
    return result

def main():
    with open(os.path.join(PROJ, 'analysis', 'bb_pp2_confluence_signal_dates.json'), encoding='utf-8') as f:
        data = json.load(f)
    pairs = [(c, d) for c, d in data['confluence']]
    print(f'共振信号: {len(pairs)} 组')

    codes = sorted(set(c for c, _ in pairs))
    klines = load_kline_map(codes)
    print(f'K线已加载: {len(klines)} 只')

    # 日期 → 索引
    stats = {h: [] for h in HORIZONS}
    missing = 0
    for code, sig_date in pairs:
        kl = klines.get(code)
        if not kl:
            missing += 1
            continue
        dates = [d for d, _ in kl]
        if sig_date not in dates:
            missing += 1
            continue
        i0 = dates.index(sig_date)
        base = kl[i0][1]
        if not base:
            missing += 1
            continue
        for h in HORIZONS:
            i1 = i0 + h
            if i1 < len(kl):
                stats[h].append((kl[i1][1] / base - 1) * 100)
            # 不足 h 日的跳过（信号太新）

    print(f'\n{"="*70}')
    print(f'基部突破 + 口袋支点V2 同日共振 → 未来收益统计（{SIGNAL_START} ~ {END_DATE}）')
    print(f'共振样本: {len(pairs)} 组 | 有效统计: {len(stats[10])} 组（信号日太新或停牌跳过 {missing}）')
    print(f'{"="*70}')

    out = {'total': len(pairs), 'valid': len(stats[10]), 'horizons': {}}
    for h in HORIZONS:
        vals = stats[h]
        if not vals:
            print(f'\n{h}日: 无有效样本')
            continue
        vals_sorted = sorted(vals)
        n = len(vals)
        mean = sum(vals) / n
        median = vals_sorted[n // 2]
        win = sum(1 for v in vals if v > 0) / n * 100
        p25 = vals_sorted[int(n * 0.25)]
        p75 = vals_sorted[int(n * 0.75)]
        p10 = vals_sorted[int(n * 0.10)]
        p90 = vals_sorted[int(n * 0.90)]
        out['horizons'][str(h)] = {
            'n': n, 'mean': round(mean, 2), 'median': round(median, 2),
            'win_rate': round(win, 1), 'p10': round(p10, 2), 'p25': round(p25, 2),
            'p75': round(p75, 2), 'p90': round(p90, 2),
        }
        print(f'\n{h}日后 (n={n}):')
        print(f'  均值 {mean:+.2f}% | 中位数 {median:+.2f}% | 胜率 {win:.1f}%')
        print(f'  分布: P10={p10:+.1f}% P25={p25:+.1f}% P75={p75:+.1f}% P90={p90:+.1f}%')

    with open(os.path.join(PROJ, 'analysis', 'bb_pp2_confluence_stats.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'\n已保存 analysis/bb_pp2_confluence_stats.json')

if __name__ == '__main__':
    main()
