# -*- coding: utf-8 -*-
"""
路径B 效果验证（三项真验证，非定义同义反复）
════════════════════════════════════════════════════════════
验1：② 标签总数变化（FTD 快速通道贡献的新增量）—— 用"禁用 FTD"跑对照组
验2：FTD 入口 ② vs 慢速入口 ② 的 H20 收益分布（决定 entry_path 分层是否必需）
验3：FTD 延迟 sanity check（不当结论）

用法：python scripts/verify_cpa_ftd_path.py --sample 200
"""
import sys, os, io, time, random, sqlite3, argparse, statistics, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
import scanners.cpa_stage as cpa

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'lixinger.db')


def run(codes, conn, disable_ftd=False):
    """跑状态机，返回 (②区间列表, ②天数, 总天数, FTD当日状态命中数)"""
    old = cpa.CFG['ftd_min_dd']
    if disable_ftd:
        cpa.CFG['ftd_min_dd'] = 99.0     # 让 FTD 永不触发（对照组）
    zones = []       # (code, start_date, entry_path, H20收益)
    n2_days = 0
    n_days = 0
    for code in codes:
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 340:
            continue
        ind = cpa.compute_indicators(kl)
        tops = cpa.load_bi_tops(conn, code)
        try:
            daily, trans = cpa.run_state_machine(conn, code, kl, ind, tops)
        except Exception:
            continue
        if not daily:
            continue
        idx_by_date = {k['date']: i for i, k in enumerate(kl)}
        cur_start = None
        cur_path = None
        for r in daily:
            n_days += 1
            if r[2] == '②':
                n2_days += 1
                if cur_start is None:
                    cur_start = r[1]
                    try:
                        m = json.loads(r[10]) if r[10] else {}
                    except Exception:
                        m = {}
                    cur_path = m.get('entry_path', 'pause')
            else:
                if cur_start is not None:
                    i = idx_by_date.get(cur_start)
                    h = (kl[i + 20]['adj_close'] / kl[i]['adj_close'] - 1) if (i is not None and i + 20 < len(kl) and kl[i]['adj_close']) else None
                    zones.append((code, cur_start, cur_path, h))
                    cur_start = None
        if cur_start is not None:
            i = idx_by_date.get(cur_start)
            h = (kl[i + 20]['adj_close'] / kl[i]['adj_close'] - 1) if (i is not None and i + 20 < len(kl) and kl[i]['adj_close']) else None
            zones.append((code, cur_start, cur_path, h))
    cpa.CFG['ftd_min_dd'] = old
    return zones, n2_days, n_days


def desc(arr):
    if not arr:
        return 'n=0'
    a = sorted(arr)
    n = len(a)
    return 'n=%4d | 均值 %+6.2f%% | 中位 %+6.2f%% | 胜率 %3.0f%%' % (
        n, statistics.mean(a) * 100, a[n // 2] * 100, sum(1 for v in a if v > 0) / n * 100)


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

    print('[对照组] 禁用 FTD 路径（旧口径）...', flush=True)
    z_old, n2_old, nd_old = run(codes, conn, disable_ftd=True)
    print('  ② 区间数 %d | ② 天数占比 %.1f%%' % (len(z_old), n2_old / max(1, nd_old) * 100), flush=True)

    print('[实验组] 启用 FTD 路径（新口径）...', flush=True)
    z_new, n2_new, nd_new = run(codes, conn, disable_ftd=False)
    print('  ② 区间数 %d | ② 天数占比 %.1f%%' % (len(z_new), n2_new / max(1, nd_new) * 100), flush=True)

    print('\n' + '=' * 70)
    print('【验1】② 标签总数变化')
    print('  旧口径 ② 区间数: %d | 新口径: %d | 新增量: %+d (%+.1f%%)' % (
        len(z_old), len(z_new), len(z_new) - len(z_old),
        (len(z_new) / max(1, len(z_old)) - 1) * 100))
    print('  ② 天数占比: %.1f%% → %.1f%% (%+.1fpp)' % (
        n2_old / max(1, nd_old) * 100, n2_new / max(1, nd_new) * 100,
        (n2_new / max(1, nd_new) - n2_old / max(1, nd_old)) * 100))

    print('\n【验2】FTD 入口 ② vs 慢速入口 ② 的 H20 收益分布')
    by_path = {}
    for (code, sd, path, h) in z_new:
        if h is None:
            continue
        by_path.setdefault(path, []).append(h)
    for p, arr in sorted(by_path.items(), key=lambda x: -len(x[1])):
        print('  %-10s %s' % (p, desc(arr)))
    pa = by_path.get('pause', [])
    ft = by_path.get('ftd_4_7', []) + by_path.get('ftd_8_15', [])
    if pa and ft:
        d = (statistics.mean(ft) - statistics.mean(pa)) * 100
        print('  ★ FTD入口 vs 慢速入口 均值差: %+.2fpp %s' % (
            d, '→ 差异大，entry_path 分层必需' if abs(d) > 1.0 else '→ 差异小，语义统一的假设成立'))

    print('\n【验3】FTD 延迟 sanity check')
    tot_ftd = sum(1 for z in z_new if z[2].startswith('ftd'))
    print('  FTD 入口 ② 区间: %d 个（应≈0 延迟，因为 FTD 当天即进 ②）' % tot_ftd)
    conn.close()
    print('\n耗时 %.0fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
