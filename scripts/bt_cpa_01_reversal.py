# -*- coding: utf-8 -*-
"""
【回测 1/23】阶段① 四组对照
════════════════════════════════════════════════════════════
①A = ①a 基础（深回撤 ≥30% + N_D20≥3 + 恐慌量 VR≥1.8，无闸门/无衰竭迹象）
①B = ①A + close > 前日 close            （用户方案）
①C = ①A + 反转日（low<前日low 且 close>前日high）（严格版）
①D = ①A + 强势闸门（250日最大涨幅≥40% 且 (距高点≤120日 或 RPS250≥70)）

口径：T+1 复权开盘买入 → H20 复权收盘卖出 → 扣 0.3% → 超额 = 收益 - 000985 同期
样本：800 只随机（seed 42）；信号去重：同股 20 交易日内只取首个
"""
import sys, os, io, time, random, sqlite3, statistics, argparse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
import scanners.cpa_stage as cpa

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'lixinger.db')
COST = 0.003


def load_index_ret(conn):
    rows = conn.execute("SELECT date, close FROM index_daily_kline WHERE stock_code='000985' ORDER BY date").fetchall()
    ds = [r[0] for r in rows]
    cs = {r[0]: r[1] for r in rows}
    idx = {d: k for k, d in enumerate(ds)}
    cache = {}

    def ret(d, off=20):
        k0 = idx.get(d)
        if k0 is None:
            return None
        k1 = k0 + off
        if k1 >= len(ds):
            return None
        return cs[ds[k1]] / cs[ds[k0]] - 1
    return ret


def scan_group(ind, kl, i, rps_map):
    """返回命中的组集合（A 为基准，B/C/D 是 A 的子集）"""
    c = ind['closes'][i]
    e20, a20 = ind['ema20'][i], ind['atr20'][i]
    if None in (c, e20, a20) or not a20 or not e20:
        return set()
    dd, hi_idx = cpa.drawdown_from_high(kl, i, cpa.CFG['pctile_win'])
    if dd is None or hi_idx is None or dd < cpa.CFG['r_drawdown_min']:
        return set()
    nd20 = ind['nd20'][i]
    d20 = c / e20 - 1
    ext = (nd20 is not None and -nd20 >= cpa.CFG['r_nd20_min']) or (d20 <= cpa.CFG['r_d20_max_pct'])
    if not ext:
        return set()
    panic = False
    for j in range(max(0, i - 20), i + 1):
        vj, cj = ind['vr'][j], ind['closes'][j]
        cp = ind['closes'][j - 1] if j > 0 else None
        if vj and cj and cp and cj < cp and vj >= cpa.CFG['r_panic_vr']:
            panic = True
            break
    if not panic:
        return set()
    out = {'A'}                                    # ①a 基础
    # B：收阳（> 前日 close）
    if i > 0 and ind['closes'][i - 1] and c > ind['closes'][i - 1]:
        out.add('B')
    # C：反转日
    if i > 0:
        pl, ph = kl[i - 1].get('low_adj'), kl[i - 1].get('high_adj')
        l = kl[i].get('low_adj')
        if pl and ph and l and l < pl and c > ph:
            out.add('C')
    # D：强势闸门
    gain = cpa.max_gain_in(kl, max(0, hi_idx - 250), hi_idx)
    recent = (i - hi_idx) <= cpa.CFG['r_high_recency']
    rps_h = rps_map.get(kl[hi_idx]['date'])
    if gain is not None and gain >= cpa.CFG['r_strong_gain'] and (recent or (rps_h is not None and rps_h >= cpa.CFG['r_h_rps250'])):
        out.add('D')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=800)
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    idx_ret = load_index_ret(conn)
    allcodes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM daily_kline WHERE date>='2016-01-01' AND stock_code NOT LIKE '%sentinel%'")]
    random.seed(42)
    codes = random.sample(allcodes, min(args.sample, len(allcodes)))
    print('样本 %d 只 | 信号去重窗口 20 交易日\n' % len(codes), flush=True)

    ex = {'A': [], 'B': [], 'C': [], 'D': []}
    for ci, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 320:
            continue
        ind = cpa.compute_indicators(kl)
        rps_map = {}
        for r in conn.execute("SELECT date, rps_250 FROM stock_rs_daily WHERE stock_code=?", (code,)):
            if r[1] is not None:
                rps_map[r[0]] = r[1]
        last_sig = {'A': -99, 'B': -99, 'C': -99, 'D': -99}
        for i in range(280, len(kl) - 21):
            groups = scan_group(ind, kl, i, rps_map)
            if not groups:
                continue
            r = idx_ret(kl[i]['date'])
            if r is None:
                continue
            for g in groups:
                if i - last_sig[g] < 20:      # 去重
                    continue
                last_sig[g] = i
                entry = kl[i + 1]['open_adj']
                exit_ = kl[i + 21]['adj_close'] if i + 21 < len(kl) else None
                if not entry or not exit_:
                    continue
                ret = exit_ / entry - 1 - COST
                ex[g].append(ret - r)
        if ci % 200 == 0:
            print('  ...%d/%d (%.0fs)' % (ci, len(codes), time.time() - t0), flush=True)

    print('\n' + '=' * 68)
    print('【回测 1/23】阶段① 四组对照（超额收益，H20，T+1开盘）')
    for g, label in [('A', '①A 基础(超跌+恐慌量)'), ('B', '①B +收阳(用户方案)'),
                     ('C', '①C +反转日(严格)'), ('D', '①D +强势闸门')]:
        arr = ex[g]
        if not arr:
            print('  %-24s n=0' % label); continue
        a = sorted(arr)
        n = len(a)
        print('  %-24s n=%5d | 胜率 %3.0f%% | 平均 %+6.2f%% | 中位 %+6.2f%%' % (
            label, n, sum(1 for v in a if v > 0) / n * 100, statistics.mean(a) * 100, a[n // 2] * 100))
    # 关键对比
    print('\n相对 ①A 的增量：')
    for g in ('B', 'C', 'D'):
        if ex[g] and ex['A']:
            d = (statistics.mean(ex[g]) - statistics.mean(ex['A'])) * 100
            ds = (sum(1 for v in ex[g] if v > 0) / len(ex[g]) - sum(1 for v in ex['A'] if v > 0) / len(ex['A'])) * 100
            print('  ①%s: 均值 %+.2fpp | 胜率 %+.1fpp' % (g, d, ds))
    conn.close()
    print('\n耗时 %.0fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
