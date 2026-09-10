# -*- coding: utf-8 -*-
"""
【回测 2/23】阶段② Wedge Pop 三组对照
════════════════════════════════════════════════════════════
②A = 严格楔形（停顿区 15-40 日 + 振幅≤12%）+ 突破 + 首次站上 + 放量
②B = 无收缩要求（任意回调后首次站上 EMA10+EMA20 + 放量 VR≥1.5）
②C = 修正版（窗口 3-40 日 + 必须有价格收缩 或 量能干涸 证据；短窗口必须量能）← 当前引擎路径A

口径：T+1 开盘 / H20 / 扣 0.3% / 对照 000985 / 去重 20 交易日
"""
import sys, os, io, time, random, sqlite3, statistics, argparse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
import scanners.cpa_stage as cpa

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'lixinger.db')
COST = 0.003
F = cpa.CFG


def load_index_ret(conn):
    rows = conn.execute("SELECT date, close FROM index_daily_kline WHERE stock_code='000985' ORDER BY date").fetchall()
    ds = [r[0] for r in rows]; cs = {r[0]: r[1] for r in rows}
    idx = {d: k for k, d in enumerate(ds)}
    def ret(d, off=20):
        k0 = idx.get(d)
        if k0 is None or k0 + off >= len(ds):
            return None
        return cs[ds[k0 + off]] / cs[ds[k0]] - 1
    return ret


def common_break(ind, kl, i):
    """首次站上 + 放量突破（②B 的核心）"""
    c, v = ind['closes'][i], ind['vr'][i]
    e10, e20 = ind['ema10'][i], ind['ema20'][i]
    if None in (c, v, e10, e20):
        return False
    if not (c > e10 and c > e20 and v >= F['w_breakout_vr']):
        return False
    below = sum(1 for j in range(max(0, i - F['w_first_lookback']), i)
                if ind['closes'][j] and ind['ema20'][j] and ind['closes'][j] < ind['ema20'][j])
    return below >= F['w_first_lookback'] * 0.6


def pause_A(ind, kl, i):
    """严格楔形：15-40 日 + 振幅≤12%"""
    for w in range(40, 14, -1):
        s = i - w
        if s < 20:
            continue
        hi = [x for x in ind['highs'][s:i] if x]; lo = [x for x in ind['lows'][s:i] if x]
        if len(hi) < w * 0.8 or len(lo) < w * 0.8:
            continue
        amp = (max(hi) - min(lo)) / min(lo)
        if amp <= 0.12:
            return {'high': max(hi), 'low': min(lo), 'win': w}
    return None


def pause_C(ind, kl, i):
    """修正版：3-40 日 + 收缩/量缩证据（短窗口必须量能）"""
    p = cpa.find_pause_zone(ind, i, F['w_win_min'], F['w_win_max'], F['w_amp_tol'], F['w_vol_dry'], kl=kl)
    return p


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--sample', type=int, default=800)
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    idx_ret = load_index_ret(conn)
    allcodes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM daily_kline WHERE date>='2016-01-01' AND stock_code NOT LIKE '%sentinel%'")]
    random.seed(42)
    codes = random.sample(allcodes, min(args.sample, len(allcodes)))
    print('样本 %d 只\n' % len(codes), flush=True)

    ex = {'A': [], 'B': [], 'C': []}
    for ci, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 320:
            continue
        ind = cpa.compute_indicators(kl)
        last = {'A': -99, 'B': -99, 'C': -99}
        for i in range(280, len(kl) - 21):
            if not common_break(ind, kl, i):
                continue
            c = ind['closes'][i]
            hits = []
            # A：严格楔形（15-40 + amp≤12%）+ 突破停顿区高点
            pa = pause_A(ind, kl, i)
            if pa and c > pa['high'] * F['w_breakout_buf']:
                hits.append('A')
            # B：无收缩要求
            hits.append('B')
            # C：修正版停顿区 + 突破
            pc = pause_C(ind, kl, i)
            if pc and c > pc['high'] * F['w_breakout_buf']:
                hits.append('C')
            r = idx_ret(kl[i]['date'])
            if r is None:
                continue
            for g in hits:
                if i - last[g] < 20:
                    continue
                last[g] = i
                entry, exit_ = kl[i + 1]['open_adj'], (kl[i + 21]['adj_close'] if i + 21 < len(kl) else None)
                if not entry or not exit_:
                    continue
                ex[g].append((exit_ / entry - 1 - COST) - r)
        if ci % 200 == 0:
            print('  ...%d/%d (%.0fs)' % (ci, len(codes), time.time() - t0), flush=True)

    print('\n' + '=' * 68)
    print('【回测 2/23】阶段② Wedge Pop 三组对照（超额，H20）')
    base = None
    for g, label in [('A', '②A 严格楔形(15-40日,振幅≤12%)'), ('B', '②B 无收缩要求(仅首次站上+放量)'),
                     ('C', '②C 修正版(3-40日+收缩/量缩证据)')]:
        arr = ex[g]
        if not arr:
            print('  %-32s n=0' % label); continue
        a = sorted(arr); n = len(a)
        print('  %-32s n=%5d | 胜率 %3.0f%% | 平均 %+6.2f%% | 中位 %+6.2f%%' % (
            label, n, sum(1 for v in a if v > 0) / n * 100, statistics.mean(a) * 100, a[n // 2] * 100))
    print('\n对比结论：')
    if ex['B'] and ex['C']:
        d = (statistics.mean(ex['C']) - statistics.mean(ex['B'])) * 100
        ds = (sum(1 for v in ex['C'] if v > 0) / len(ex['C']) - sum(1 for v in ex['B'] if v > 0) / len(ex['B'])) * 100
        print('  ②C vs ②B（收缩前置是否有价值）: 均值 %+.2fpp | 胜率 %+.1fpp' % (d, ds))
        print('  → %s' % ('收缩前置有价值（本系统主张成立）' if d > 0.5 else ('无价值（用户主张成立）' if d > -0.5 else '收缩前置反而有害')))
    if ex['A'] and ex['C']:
        d = (statistics.mean(ex['C']) - statistics.mean(ex['A'])) * 100
        print('  ②C vs ②A（放宽窗口的效果）: 均值 %+.2fpp（②C 样本 %d vs ②A %d）' % (d, len(ex['C']), len(ex['A'])))
    conn.close()
    print('\n耗时 %.0fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
