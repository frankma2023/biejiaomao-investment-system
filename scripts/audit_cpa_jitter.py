# -*- coding: utf-8 -*-
"""
CPA 状态机审计（PRD §11 前置）—— 抖动/迁移频次/FTD 普查/过渡态收益
════════════════════════════════════════════════════════════
顺序纪律：标签可信度达标前不上 23 项回测（坏数据比无数据误导）

审计四件事：
  1. 抖动持续时间分布（九成抖动若只 1~2 天 → 非对称阈值可杀大半）
  2. 补分母：正常年份全状态迁移频次基准（每年几次？占比多少？）
  3. FTD 普查：V 型反转案例，现有判定延迟几天（硬约束：非对称后 ≤2 天）
  4. 过渡态收益特征预览（验证"垃圾时间"猜想）

用法：python scripts/audit_cpa_jitter.py --sample 200 [--seed 42]
"""
import sys, os, io, time, random, sqlite3, argparse, statistics
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
import scanners.cpa_stage as cpa

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'lixinger.db')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=200)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--start', default='2016-01-01')
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # 样本：随机 N 只（含活跃与历史退市，覆盖弱市）
    allcodes = [r[0] for r in conn.execute(
        """SELECT DISTINCT stock_code FROM daily_kline
           WHERE date>='2016-01-01' AND stock_code NOT LIKE '%sentinel%'""")]
    random.seed(args.seed)
    codes = random.sample(allcodes, min(args.sample, len(allcodes)))
    print('审计样本: %d 只（随机, seed=%d）, DB 共 %d 只' % (len(codes), args.seed, len(allcodes)), flush=True)

    # ── 累计统计 ──
    trans_gaps = []          # 迁移间隔（自然日）
    jitter_durations = []    # 抖动持续段长度（连续 <5 日间隔的次数）
    trans_per_year = []      # 每股每年迁移次数
    stage_days = {}          # 阶段 → 总天数
    total_days = 0
    trans_total = 0
    # 过渡态预览：②/⑥b 边界区（EMA20 ±0.5ATR）内的日收益 → H20
    trans_zone_ret, normal_ret = [], []
    ftd_cases = []           # FTD 普查

    for idx, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 320:
            continue
        ind = cpa.compute_indicators(kl)
        tops = cpa.load_bi_tops(conn, code)
        try:
            daily, trans = cpa.run_state_machine(conn, code, kl, ind, tops)
        except Exception as e:
            print('  ! %s 状态机异常: %s' % (code, str(e)[:60]))
            continue
        if not daily:
            continue
        # 迁移间隔
        dates = [t[1] for t in trans]
        for k in range(1, len(dates)):
            d0 = dates[k - 1].replace('-', '')
            d1 = dates[k].replace('-', '')
            from datetime import datetime as _dt
            gap = (_dt.strptime(d1, '%Y%m%d') - _dt.strptime(d0, '%Y%m%d')).days
            trans_gaps.append(gap)
        trans_total += len(trans)
        # 抖动段：连续间隔 <5 日的迁移串
        run = 0
        for gap in [((__import__('datetime').datetime.strptime(dates[k].replace('-', ''), '%Y%m%d')
                      - __import__('datetime').datetime.strptime(dates[k-1].replace('-', ''), '%Y%m%d')).days)
                    for k in range(1, len(dates))]:
            if gap < 5:
                run += 1
            else:
                if run > 0:
                    jitter_durations.append(run)
                run = 0
        if run > 0:
            jitter_durations.append(run)
        # 每股每年迁移
        yrs = max(1, len(daily) / 243)
        trans_per_year.append(len(trans) / yrs)
        # 阶段天数
        for r in daily:
            stage_days[r[2]] = stage_days.get(r[2], 0) + 1
            total_days += 1
        # 过渡态预览：在 EMA20 ±0.5ATR 带内
        idx_by_date = {k['date']: i for i, k in enumerate(kl)}
        for r in daily:
            i = idx_by_date.get(r[1])
            if i is None or i + 20 >= len(kl):
                continue
            c, e20, a20 = ind['closes'][i], ind['ema20'][i], ind['atr20'][i]
            if None in (c, e20, a20) or not a20:
                continue
            in_zone = abs(c - e20) <= 0.5 * a20
            ret20 = kl[i + 20]['adj_close'] / kl[i]['adj_close'] - 1 if kl[i]['adj_close'] else None
            if ret20 is None:
                continue
            (trans_zone_ret if in_zone else normal_ret).append(ret20)
        # FTD 普查：深回撤低点后第 4-7 日的放量 +2% 日
        for i in range(280, len(kl) - 25):
            dd, hi = cpa.drawdown_from_high(kl, i, 250)
            if dd is None or dd < 0.25:
                continue
            nxt = kl[i + 1]['date']
            # 是否为局部低点（后 5 日未创新低）
            lows5 = [kl[j]['adj_close'] for j in range(i + 1, min(i + 6, len(kl)))]
            if not lows5 or min(lows5) < kl[i]['adj_close']:
                continue
            # 低点后第 4-7 日 FTD 检测（放量 +2% 且站上 EMA20）
            ftd_idx, delay = None, None
            for j in range(i + 4, min(i + 8, len(kl))):
                cj, cj1 = kl[j]['adj_close'], kl[j - 1]['adj_close']
                vr = ind['vr'][j]
                e20 = ind['ema20'][j]
                if None in (cj, cj1, vr, e20):
                    continue
                if cj / cj1 - 1 >= 0.02 and vr >= 1.3 and cj > e20:
                    ftd_idx = j
                    break
            if ftd_idx is None:
                continue
            # 状态机在该案例中的 ② 进入日
            ent = None
            for r in daily:
                if r[1] >= kl[ftd_idx]['date'] and r[2] in ('②', '③'):
                    ent = r[1]
                    break
            if ent:
                from datetime import datetime as _dt
                delay = (_dt.strptime(ent, '%Y-%m-%d') - _dt.strptime(kl[ftd_idx]['date'], '%Y-%m-%d')).days
                ftd_cases.append((code, kl[i]['date'], kl[ftd_idx]['date'], delay))
        if idx % 50 == 0:
            print('  ...%d/%d (%.0fs)' % (idx, len(codes), time.time() - t0), flush=True)

    # ═══ 报告 ═══
    print('\n' + '=' * 62)
    print('【审计 1】抖动持续时间分布（迁移间隔 <5 日的连续段）')
    if jitter_durations:
        c = sorted(jitter_durations)
        n = len(c)
        print('  抖动段数: %d | 长度分位: 50%%=%d 75%%=%d 90%%=%d 最长=%d' % (
            n, c[n // 2], c[int(n * .75)], c[int(n * .9)], c[-1]))
        short = sum(1 for x in c if x <= 2)
        print('  ≤2 次连迁的短抖动占: %.0f%%（非对称阈值可杀大半）' % (short / n * 100))
    else:
        print('  无抖动段')

    print('\n【审计 2】迁移频次分母')
    print('  总迁移: %d | 每股年均迁移: %.1f 次' % (trans_total, statistics.mean(trans_per_year) if trans_per_year else 0))
    if trans_gaps:
        g = sorted(trans_gaps)
        n = len(g)
        print('  迁移间隔(自然日)分位: 25%%=%d 50%%=%d 75%%=%d | <5日占比=%.0f%%' % (
            g[n // 4], g[n // 2], g[int(n * .75)], sum(1 for x in g if x < 5) / n * 100))
    print('  阶段时间占比:')
    for s, d in sorted(stage_days.items(), key=lambda x: -x[1]):
        print('    %-4s %6.1f%%' % (s, d / total_days * 100))

    print('\n【审计 3】FTD 普查（硬约束：确认延迟 ≤2 天）')
    if ftd_cases:
        dels = [c[3] for c in ftd_cases]
        dd = sorted(dels)
        n = len(dd)
        print('  FTD 案例: %d 个 | 状态机确认延迟(自然日): 中位=%d 均值=%.1f | ≤2天占比=%.0f%%' % (
            n, dd[n // 2], statistics.mean(dd), sum(1 for x in dd if x <= 2) / n * 100))
        worst = sorted(ftd_cases, key=lambda x: -x[3])[:5]
        print('  延迟最大的 5 例:')
        for c in worst:
            print('    %s 低点%s FTD%s 延迟%d天' % c)
    else:
        print('  未检出 FTD 案例（样本内）')

    print('\n【审计 4】过渡态收益特征预览（EMA20 ±0.5ATR 模糊区）')
    if trans_zone_ret and normal_ret:
        def desc(x):
            x = sorted(x)
            n = len(x)
            return 'n=%d 均值=%+.2f%% 中位=%+.2f%% 胜率=%.0f%%' % (
                n, statistics.mean(x) * 100, x[n // 2] * 100, sum(1 for v in x if v > 0) / n * 100)
        print('  模糊区(过渡态): %s' % desc(trans_zone_ret))
        print('  非模糊区      : %s' % desc(normal_ret))
        print('  → 差值: 均值 %+.2f%%' % ((statistics.mean(trans_zone_ret) - statistics.mean(normal_ret)) * 100))
    conn.close()
    print('\n审计耗时 %.0fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
