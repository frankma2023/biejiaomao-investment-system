#!/usr/bin/env python3
"""
【CPA × 信号 · 领先-滞后测试】信号能不能"预告"阶段迁移？

背景：此前的交叉回测用的是「同日/±2日重合」，看不见滞后关系。
但 002648 的实例说明两者之间可能有确认延迟：
  2026-07-20 出现基部突破 + 口袋支点V1
  2026-07-22 才进入 ② 突破后运行（差 2 天——因为 ② 的复活确认需要 4中3）
→ 信号可能是阶段的**领先指标**，只是阶段要等确认。

本脚本测三件事：
  A. 领先命中率：信号后 N 日内出现「向上涨阶段的迁移」的比例（对比无条件基准）
  B. 滞后分布：命中时滞后几天
  C. 收益：从**信号日**入场（不是从迁移日），信号+命中 vs 信号+未命中 vs 全部信号

用法：
  python scripts/bt_cpa_siglead.py --mask 32 --name BO_V2 --lead 15
  python scripts/bt_cpa_siglead.py --mask 8  --name PP_V1 --lead 15
  python scripts/bt_cpa_siglead.py --mask 1  --name MW_B1 --lead 15
"""
import sys, os, sqlite3, random, statistics, time, argparse
from collections import defaultdict
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

COST = 0.003
BULL = ('②', '③', '④')          # 向上涨的阶段
BEAR = ('⑥a', '⑥b', '⑥c')       # 向下跌的阶段


def load_index_ret(conn):
    rows = conn.execute("SELECT date, close FROM index_daily_kline "
                        "WHERE stock_code='000985' ORDER BY date").fetchall()
    ds = [r[0] for r in rows]; cs = {r[0]: r[1] for r in rows}
    idx = {d: k for k, d in enumerate(ds)}

    def ret(d, off):
        k0 = idx.get(d)
        if k0 is None or k0 + off >= len(ds):
            return None
        return cs[ds[k0 + off]] / cs[ds[k0]] - 1
    return ret


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mask', type=int, required=True)
    ap.add_argument('--name', default='')
    ap.add_argument('--lead', type=int, default=15, help='观察天数')
    ap.add_argument('--direction', choices=['bull', 'bear'], default='bull')
    ap.add_argument('--sample', type=int, default=800)
    ap.add_argument('--dedup', type=int, default=20)
    args = ap.parse_args()
    TGT = BULL if args.direction == 'bull' else BEAR
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=60000')
    idx_ret = load_index_ret(conn)

    allc = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM cpa_stage_daily WHERE date>='2016-01-01'")]
    random.seed(42)
    codes = sorted(random.sample(allc, min(args.sample, len(allc))))
    print(f'信号 {args.name}（mask {args.mask}）｜目标迁移方向 {args.direction} {TGT}｜'
          f'观察 {args.lead} 日\n', flush=True)

    ev = conn.execute("""SELECT stock_code, date FROM signal_events
        WHERE (signal_mask & ?) != 0 AND date >= '2016-01-01'
        ORDER BY stock_code, date""", (args.mask,)).fetchall()

    hits, misses = defaultdict(list), defaultdict(list)
    lags = []
    n_sig = n_hit = n_base_all = n_base_tgt = 0
    cache = {}
    for e in ev:
        code = e['stock_code']
        if code not in codes:
            continue
        if code not in cache:
            kl = cpa.load_klines(conn, code, '2014-01-01')
            pos = {k['date']: j for j, k in enumerate(kl)}
            tr = conn.execute("""SELECT transition_date, to_stage FROM cpa_stage_transitions
                WHERE stock_code=? AND transition_date >= '2016-01-01'
                ORDER BY transition_date""", (code,)).fetchall()
            trd = [(r['transition_date'], (r['to_stage'] or '').replace('T', '')) for r in tr]
            cache[code] = (pos, kl, trd)
        pos, kl, trd = cache[code]
        j = pos.get(e['date'])
        if j is None or j + 1 >= len(kl):
            continue
        n_sig += 1
        # 基准统计：该股全部交易日中，N 日内出现目标迁移的比例
        n_base_all += 1
        # 找 N 个交易日内的目标迁移
        lo, hi = j + 1, min(j + args.lead, len(kl) - 1)
        d_lo, d_hi = kl[lo]['date'], kl[hi]['date']
        hit = None
        for td, ts in trd:
            if td > d_hi:
                break
            if td >= d_lo and (ts in TGT or (args.direction == 'bull' and ts in ('②', '③', '④'))):
                hit = td
                break
        ent = kl[j + 1]['open_adj']
        if not ent:
            continue
        bucket = hits if hit else misses
        if hit:
            n_hit += 1
            lags.append((pos[hit] - j) if hit in pos else None)
        for H in (10, 20, 60):
            if j + 1 + H >= len(kl):
                continue
            ext = kl[j + 1 + H]['adj_close']
            ir = idx_ret(e['date'], H)
            if not ext or ir is None:
                continue
            bucket[H].append((ext / ent - 1 - COST) - ir)
            misses[H] if False else None
    # 去重后的“全部信号” = hits + misses 之和
    allsg = {H: hits[H] + misses[H] for H in (10, 20, 60)}

    def st(a):
        if not a:
            return None
        b = sorted(a); n = len(b)
        return n, sum(1 for v in b if v > 0) / n * 100, statistics.mean(b) * 100, b[n // 2] * 100

    print('=' * 88)
    print(f'【{args.name} → CPA {args.direction} 阶段迁移】领先关系')
    print('=' * 88)
    print(f'  信号事件 {n_sig:,}｜其中 {args.lead} 日内出现目标迁移的 {n_hit:,}'
          f' = **{n_hit / max(n_sig,1) * 100:.1f}%**')
    lg = [x for x in lags if x]
    if lg:
        lg.sort()
        print(f'  命中时的滞后（交易日）：中位 {lg[len(lg)//2]}｜P25 {lg[len(lg)//4]}｜P75 {lg[len(lg)*3//4]}')
    print()
    print(f'{"组":<22}{"H":>4}{"n":>9}{"胜率":>7}{"均值":>9}{"中位":>9}')
    for H in (10, 20, 60):
        for lab, arr in ((f'信号+{args.lead}日内迁移', hits[H]), ('信号但无迁移', misses[H]),
                         ('全部信号', allsg[H])):
            s = st(arr)
            if s:
                print(f'{lab:<22}{H:>4}{s[0]:>9,}{s[1]:>6.0f}%{s[2]:>+8.2f}%{s[3]:>+8.2f}%')
        print()
    print(f'耗时 {time.time() - t0:.0f}s')
    conn.close()


if __name__ == '__main__':
    main()
