# -*- coding: utf-8 -*-
"""
FTD 分场景审计（补丁三 + 补丁四）
════════════════════════════════════════════════════════════
补丁三：①b/⑥b 场景的 FTD 是否劣质（诱多中继）→ 决定路径B 是否加前置状态约束
补丁四：第 4-7 日封顶 vs 8-15 日 → 决定封顶线

口径：FTD 当日状态机所处阶段 × FTD 后 20 日收益
用法：python scripts/audit_cpa_ftd_by_state.py --sample 200
"""
import sys, os, io, time, random, sqlite3, argparse, statistics
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
import scanners.cpa_stage as cpa

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'lixinger.db')


def find_ftds(kl, ind, win_lo, win_hi, min_dd=0.25, gap=60):
    """返回 [(L, low_date, ftd_idx, ftd_date)]，反转窗口 [win_lo, win_hi]"""
    out = []
    n = len(kl)
    last = -999
    for L in range(260, n - 30):
        s = max(0, L + 1 - 120)
        seg = [kl[j]['adj_close'] for j in range(s, L + 1) if kl[j]['adj_close']]
        if not seg or kl[L]['adj_close'] != min(seg):
            continue
        dd, _ = cpa.drawdown_from_high(kl, L, 250)
        if dd is None or dd < min_dd:
            continue
        ftd = None
        for j in range(L + win_lo, min(L + win_hi + 1, n)):
            cj, cj1 = kl[j]['adj_close'], kl[j - 1]['adj_close']
            vr, e20 = ind['vr'][j], ind['ema20'][j]
            if None in (cj, cj1, vr, e20) or not cj1:
                continue
            if cj / cj1 - 1 >= 0.02 and vr >= 1.3 and cj > e20:
                ftd = j
                break
        if ftd is None or ftd - last < gap:
            continue
        last = ftd
        out.append((L, kl[L]['date'], ftd, kl[ftd]['date']))
    return out


def h20(kl, i):
    if i + 20 >= len(kl) or not kl[i]['adj_close']:
        return None
    return kl[i + 20]['adj_close'] / kl[i]['adj_close'] - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=200)
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    allcodes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM daily_kline WHERE date>='2016-01-01' AND stock_code NOT LIKE '%sentinel%'")]
    random.seed(args.seed)
    codes = random.sample(allcodes, min(args.sample, len(allcodes)))
    print('样本 %d 只\n' % len(codes), flush=True)

    # group[窗口][状态] = [H20 收益...]
    groups = {'4-7日': {}, '8-15日': {}}
    for idx, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 340:
            continue
        ind = cpa.compute_indicators(kl)
        tops = cpa.load_bi_tops(conn, code)
        try:
            daily, _ = cpa.run_state_machine(conn, code, kl, ind, tops)
        except Exception:
            continue
        if not daily:
            continue
        st = {r[1]: r[2] for r in daily}
        for label, (lo, hi) in [('4-7日', (4, 7)), ('8-15日', (8, 15))]:
            for (L, ld, F, fd) in find_ftds(kl, ind, lo, hi):
                r = h20(kl, F)
                if r is None:
                    continue
                s = st.get(fd, '?')
                groups[label].setdefault(s, []).append(r)
        if idx % 50 == 0:
            print('  ...%d/%d (%.0fs)' % (idx, len(codes), time.time() - t0), flush=True)

    print('\n' + '=' * 66)
    for label in ('4-7日', '8-15日'):
        g = groups[label]
        print('\n【窗口 %s】FTD 后 20 日收益（按 FTD 当日状态分组）' % label)
        tot = []
        for s, arr in sorted(g.items(), key=lambda x: -len(x[1])):
            tot += arr
            a = sorted(arr)
            n = len(a)
            print('  %-4s n=%4d | 均值 %+6.2f%% | 中位 %+6.2f%% | 胜率 %3.0f%%' % (
                s, n, statistics.mean(a) * 100, a[n // 2] * 100, sum(1 for v in a if v > 0) / n * 100))
        if tot:
            a = sorted(tot)
            n = len(a)
            print('  ── 全体 n=%d | 均值 %+.2f%% | 中位 %+.2f%% | 胜率 %.0f%%' % (
                n, statistics.mean(a) * 100, a[n // 2] * 100, sum(1 for v in a if v > 0) / n * 100))
        # 关键对比：①b/⑥b（危险批） vs ⑥c（主战场）
        danger = g.get('①b', []) + g.get('⑥b', [])
        main_ = g.get('⑥c', [])
        if danger and main_:
            d1, d2 = sorted(danger), sorted(main_)
            print('  ★ 危险批(①b+⑥b) n=%d 均值 %+.2f%% 胜率 %.0f%% | ⑥c n=%d 均值 %+.2f%% 胜率 %.0f%% | 差 %+.2f%%' % (
                len(d1), statistics.mean(d1) * 100, sum(1 for v in d1 if v > 0) / len(d1) * 100,
                len(d2), statistics.mean(d2) * 100, sum(1 for v in d2 if v > 0) / len(d2) * 100,
                (statistics.mean(d1) - statistics.mean(d2)) * 100))
    conn.close()
    print('\n耗时 %.0fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
