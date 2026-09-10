# -*- coding: utf-8 -*-
"""
CPA × FTD 普查（校准版）
════════════════════════════════════════════════════════════
FTD（Follow-Through Day，欧奈尔追盘日）标准口径：
  1. 低点 L = 近 120 交易日【最低收盘】（唯一，且自 250 日高点回撤 ≥25%）
  2. 反转窗口 = L 之后第 4~7 个交易日（欧奈尔标准）
  3. FTD 日 = 窗口内首个满足：涨幅 ≥+2% 且 VR ≥1.3 且 收盘 > EMA20
  4. 去重：每 60 交易日最多 1 个 FTD（同一段反弹只算一次）

硬约束：非对称规则改造后，FTD 场景的 ② 确认延迟必须 ≤2 自然日。

用法：python scripts/audit_cpa_ftd.py --sample 150
"""
import sys, os, io, time, random, sqlite3, argparse, statistics
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
import scanners.cpa_stage as cpa
from datetime import datetime

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'lixinger.db')


def find_ftds(kl, ind):
    """返回 [(low_idx, low_date, ftd_idx, ftd_date), ...]"""
    out = []
    n = len(kl)
    last_ftd = -999
    for L in range(260, n - 30):
        # 低点：近 120 日最低收盘，且 250 日回撤 ≥25%
        s = max(0, L + 1 - 120)
        seg = [kl[j]['adj_close'] for j in range(s, L + 1) if kl[j]['adj_close']]
        if not seg or kl[L]['adj_close'] != min(seg):
            continue
        dd, hi = cpa.drawdown_from_high(kl, L, 250)
        if dd is None or dd < 0.25:
            continue
        # 反转窗口 4~7
        ftd = None
        for j in range(L + 4, min(L + 8, n)):
            cj, cj1, vr, e20 = kl[j]['adj_close'], kl[j - 1]['adj_close'], ind['vr'][j], ind['ema20'][j]
            if None in (cj, cj1, vr, e20) or not cj1:
                continue
            if cj / cj1 - 1 >= 0.02 and vr >= 1.3 and cj > e20:
                ftd = j
                break
        if ftd is None:
            continue
        if ftd - last_ftd < 60:
            continue
        last_ftd = ftd
        out.append((L, kl[L]['date'], ftd, kl[ftd]['date']))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=150)
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    allcodes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM daily_kline WHERE date>='2016-01-01' AND stock_code NOT LIKE '%sentinel%'")]
    random.seed(args.seed)
    codes = random.sample(allcodes, min(args.sample, len(allcodes)))
    print('FTD 普查样本: %d 只（随机）\n' % len(codes), flush=True)

    delays = []          # 确认延迟（自然日）
    stage_at_ftd = {}    # FTD 当日状态分布
    cases = []
    n_ftd = 0
    for idx, code in enumerate(codes):
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
        ftds = find_ftds(kl, ind)
        n_ftd += len(ftds)
        state_by_date = {r[1]: r[2] for r in daily}
        for (L, ld, F, fd) in ftds:
            st = state_by_date.get(fd, '?')
            stage_at_ftd[st] = stage_at_ftd.get(st, 0) + 1
            # 首次进入 ②/③（≥FTD 日）的日期
            ent = None
            for r in daily:
                if r[1] >= fd and r[2] in ('②', '③'):
                    ent = r[1]
                    break
            if ent:
                d = (datetime.strptime(ent, '%Y-%m-%d') - datetime.strptime(fd, '%Y-%m-%d')).days
                delays.append(d)
                if d > 2:
                    cases.append((code, ld, fd, ent, d, st))
        if idx % 50 == 0:
            print('  ...%d/%d (%.0fs)' % (idx, len(codes), time.time() - t0), flush=True)

    print('\n' + '=' * 60)
    print('【FTD 普查（校准口径）】')
    print('  FTD 案例: %d 个（%d 只样本, 每只年均 %.1f 个）' % (
        n_ftd, len(codes), n_ftd / len(codes) / (10.0)))
    print('  FTD 当日状态机所处阶段:', dict(sorted(stage_at_ftd.items(), key=lambda x: -x[1])))
    if delays:
        d = sorted(delays)
        n = len(d)
        print('\n  ② 确认延迟（自然日）: 中位=%d 均值=%.1f | 分位 25%%=%d 75%%=%d 90%%=%d 95%%=%d' % (
            d[n // 2], statistics.mean(d), d[n // 4], d[int(n * .75)], d[int(n * .9)], d[int(n * .95)]))
        print('  ≤2 天占比: %.0f%%（硬约束线）' % (sum(1 for x in d if x <= 2) / n * 100))
        print('  延迟 >10 天的案例: %d 个 (%.0f%%)' % (
            sum(1 for x in d if x > 10), sum(1 for x in d if x > 10) / n * 100))
        if cases:
            print('\n  延迟超标案例（最多 8 例）:')
            for c in cases[:8]:
                print('    %s 低点%s FTD%s → %s进入② 延迟%d天 (FTD当日状态=%s)' % c)
    conn.close()
    print('\n耗时 %.0fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
