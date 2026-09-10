# -*- coding: utf-8 -*-
"""
【回测 2.5/23】① vs ② 深化对比：多周期 × -7% 止损
════════════════════════════════════════════════════════════
信号组：
  ①-收阳   = ①a（深回撤30%+N_D20≥3+恐慌量）+ close>前日close
  ①-反转日 = ①a + 反转日（low<前日low 且 close>前日high）
  ②-修正版 = 首次站上EMA10+20 + 放量 + 停顿区（3-40日收缩/量缩）

维度：
  周期 H10 / H20 / H30 / H60（T+1 复权开盘入场，扣 0.3%）
  模式 无止损 / -7% 止损（持有期内收盘跌破 entry×0.93 → 以该收盘价出场）
口径：超额 = 个股收益 - 000985 同期；信号去重 20 交易日
"""
import sys, os, io, time, random, sqlite3, statistics
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
import scanners.cpa_stage as cpa

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'lixinger.db')
COST = 0.003
STOP = 0.07
PERIODS = [10, 20, 30, 60]
F = cpa.CFG


def load_index(conn):
    rows = conn.execute("SELECT date, close FROM index_daily_kline WHERE stock_code='000985' ORDER BY date").fetchall()
    ds = [r[0] for r in rows]; cs = {r[0]: r[1] for r in rows}
    idx = {d: k for k, d in enumerate(ds)}
    def ret(d, off):
        k0 = idx.get(d)
        if k0 is None or k0 + off >= len(ds):
            return None
        return cs[ds[k0 + off]] / cs[ds[k0]] - 1
    return ret


def rev_base(ind, kl, i):
    """①a 基础条件"""
    c, e20, a20 = ind['closes'][i], ind['ema20'][i], ind['atr20'][i]
    if None in (c, e20, a20) or not a20:
        return False
    dd, _ = cpa.drawdown_from_high(kl, i, F['pctile_win'])
    if dd is None or dd < F['r_drawdown_min']:
        return False
    nd20 = ind['nd20'][i]
    d20 = c / e20 - 1
    if not ((nd20 is not None and -nd20 >= F['r_nd20_min']) or d20 <= F['r_d20_max_pct']):
        return False
    for j in range(max(0, i - 20), i + 1):
        vj, cj = ind['vr'][j], ind['closes'][j]
        cp = ind['closes'][j - 1] if j > 0 else None
        if vj and cj and cp and cj < cp and vj >= F['r_panic_vr']:
            return True
    return False


def wp_base(ind, kl, i):
    """② 首次站上 + 放量 + 停顿区"""
    c, v = ind['closes'][i], ind['vr'][i]
    e10, e20 = ind['ema10'][i], ind['ema20'][i]
    if None in (c, v, e10, e20) or not (c > e10 and c > e20 and v >= F['w_breakout_vr']):
        return False
    below = sum(1 for j in range(max(0, i - F['w_first_lookback']), i)
                if ind['closes'][j] and ind['ema20'][j] and ind['closes'][j] < ind['ema20'][j])
    if below < F['w_first_lookback'] * 0.6:
        return False
    p = cpa.find_pause_zone(ind, i, F['w_win_min'], F['w_win_max'], F['w_amp_tol'], F['w_vol_dry'], kl=kl)
    return bool(p and c > p['high'] * F['w_breakout_buf'])


def main():
    t0 = time.time()
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    idx_ret = load_index(conn)
    allcodes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM daily_kline WHERE date>='2016-01-01' AND stock_code NOT LIKE '%sentinel%'")]
    random.seed(42)
    codes = random.sample(allcodes, 800)
    print('样本 %d 只 |  周期 %s | 止损 -7%%（收盘触发）\n' % (len(codes), PERIODS), flush=True)

    # sig[组] = [(entry, [ret1..ret60], {周期: 指数收益}).]
    sig = {'①收阳': [], '①反转日': [], '②修正版': []}
    for ci, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 340:
            continue
        ind = cpa.compute_indicators(kl)
        last = {'①收阳': -99, '①反转日': -99, '②修正版': -99}
        for i in range(280, len(kl) - 61):
            hits = []
            if rev_base(ind, kl, i):
                c = ind['closes'][i]
                if i > 0 and ind['closes'][i - 1] and c > ind['closes'][i - 1]:
                    hits.append('①收阳')
                if i > 0:
                    pl, ph = kl[i - 1].get('low_adj'), kl[i - 1].get('high_adj')
                    l = kl[i].get('low_adj')
                    if pl and ph and l and l < pl and c > ph:
                        hits.append('①反转日')
            if wp_base(ind, kl, i):
                hits.append('②修正版')
            if not hits:
                continue
            entry = kl[i + 1]['open_adj']
            if not entry:
                continue
            seq = [(kl[i + 1 + k]['adj_close'] / entry - 1) if (i + 1 + k < len(kl) and kl[i + 1 + k]['adj_close']) else None
                   for k in range(1, 61)]
            if any(x is None for x in seq):
                continue
            ir = {}
            ok = True
            for p in PERIODS:
                r = idx_ret(kl[i]['date'], p)
                if r is None:
                    ok = False; break
                ir[p] = r
            if not ok:
                continue
            for g in hits:
                if i - last[g] >= 20:
                    last[g] = i
                    sig[g].append((entry, seq, ir))
        if ci % 200 == 0:
            print('  ...%d/%d (%.0fs)' % (ci, len(codes), time.time() - t0), flush=True)

    def stat(vals):
        a = sorted(vals); n = len(a)
        return n, sum(1 for v in a if v > 0) / n * 100, statistics.mean(a) * 100, a[n // 2] * 100

    print('\n' + '=' * 92)
    print('【回测 2.5】① vs ② 多周期 × -7% 止损（超额收益）')
    for g in ('①收阳', '①反转日', '②修正版'):
        rows = sig[g]
        if not rows:
            print('\n%s: n=0' % g); continue
        n0 = len(rows)
        print('\n### %s（n=%d）' % (g, n0))
        print('  %-6s | %-32s | %-32s' % ('周期', '无止损（胜率/均值/中位）', '-7% 止损（胜率/均值/中位｜触发率）'))
        for p in PERIODS:
            nosl, sl = [], []
            nstop = 0
            for (entry, seq, ir) in rows:
                # 无止损
                nosl.append(seq[p - 1] - COST - ir[p])
                # 有止损：持有期 1..p 内首次 close ≤ -7%
                hit = None
                for k in range(p):
                    if seq[k] <= -STOP:
                        hit = k
                        break
                if hit is not None:
                    sl.append(seq[hit] - COST - ir[p])
                    nstop += 1
                else:
                    sl.append(seq[p - 1] - COST - ir[p])
            n1, w1, m1, md1 = stat(nosl)
            n2, w2, m2, md2 = stat(sl)
            print('  %-6s | %3.0f%% %+6.2f%% %+6.2f%%        | %3.0f%% %+6.2f%% %+6.2f%% ｜ %2.0f%%' % (
                'H%d' % p, w1, m1, md1, w2, m2, md2, nstop / n1 * 100))

    print('\n' + '=' * 92)
    print('【组间对比】各周期均值（无止损 → -7%止损）')
    print('  %-6s | %-24s | %-24s | %-24s' % ('周期', '①收阳', '①反转日', '②修正版'))
    for p in PERIODS:
        line = []
        for g in ('①收阳', '①反转日', '②修正版'):
            rows = sig[g]
            if not rows:
                line.append('n=0'); continue
            nosl = [seq[p - 1] - COST - ir[p] for (_, seq, ir) in rows]
            sl = []
            for (_, seq, ir) in rows:
                hit = next((k for k in range(p) if seq[k] <= -STOP), None)
                sl.append((seq[hit] if hit is not None else seq[p - 1]) - COST - ir[p])
            line.append('%+5.2f%% → %+5.2f%%' % (statistics.mean(nosl) * 100, statistics.mean(sl) * 100))
        print('  %-6s | %-24s | %-24s | %-24s' % ('H%d' % p, line[0], line[1], line[2]))
    conn.close()
    print('\n耗时 %.0fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
