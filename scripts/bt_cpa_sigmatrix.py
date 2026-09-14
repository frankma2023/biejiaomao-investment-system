#!/usr/bin/env python3
"""
【CPA 信号交叉回测 · 通用框架】任意信号 × CPA 阶段 → 重叠度 + 前瞻超额

数据源：signal_events（统一信号库，mask 位映射）
  1 = MW_B1 ｜ 2 = MW_B2 ｜ 8 = PP_V1 ｜ 16 = PP_V2 ｜ 32 = BO_V2

回答两个问题：
  1. 该信号的日期落在哪些 CPA 阶段？（信号与阶段是否正交）
  2. 「信号 ∩ 阶段」的前瞻收益，相比「阶段单独」有没有提升？

口径（同其它回测）：800 只随机（seed 42）/ T+1 复权开盘 / H10/20/60 / 扣 0.3% / 超额 − 000985 / 同股去重 20 日
用法：python scripts/bt_cpa_sigmatrix.py --mask 8 --name PP_V1
"""
import sys, os, sqlite3, random, statistics, time, argparse
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

COST = 0.003
MASK_NAME = {1: 'MW_B1', 2: 'MW_B2', 8: 'PP_V1', 16: 'PP_V2', 32: 'BO_V2'}


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


def base_stage(s):
    s = (s or '')
    if s == '⑥w':
        return '⑥w'
    return s[:-1] if s.endswith('T') else s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mask', type=int, default=0, help='信号位（0=不限，只看信号数）')
    ap.add_argument('--all-of', type=int, default=0, help='要求同时具备这些位（与运算，如 9=MW_B1且PP_V1）')
    ap.add_argument('--name', default=None)
    ap.add_argument('--min-count', type=int, default=1, help='信号数下限，>1 即只看共振日')
    ap.add_argument('--sample', type=int, default=800)
    ap.add_argument('--dedup', type=int, default=20)
    args = ap.parse_args()
    nm = args.name or (MASK_NAME.get(args.mask, f'mask{args.mask}') if args.mask else '全部信号')
    if args.all_of:
        nm += f'·同时{args.all_of}'
    
    if args.min_count > 1:
        nm += f'·共振≥{args.min_count}'
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=60000')
    idx_ret = load_index_ret(conn)

    allc = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM cpa_stage_daily WHERE date>='2016-01-01'")]
    random.seed(42)
    codes = set(random.sample(allc, min(args.sample, len(allc))))

    _q = """SELECT stock_code, date FROM signal_events WHERE date >= '2016-01-01'"""
    _p = []
    if args.mask:
        _q += " AND (signal_mask & ?) != 0"
        _p.append(args.mask)
    if args.all_of:
        _q += " AND (signal_mask & ?) = ?"
        _p += [args.all_of, args.all_of]
    if args.min_count > 1:
        _q += " AND signal_count >= ?"
        _p.append(args.min_count)
    _q += " ORDER BY stock_code, date"
    ev = conn.execute(_q, _p).fetchall()
    print(f'信号 {nm}｜全市场事件 {len(ev):,}｜样本 {len(codes)} 只\n', flush=True)

    cache = {}
    res = defaultdict(lambda: defaultdict(list))     # (stage, 有信号?) -> H -> [...]
    cnt = defaultdict(int)
    for e in ev:
        code = e['stock_code']
        if code not in codes:
            continue
        if code not in cache:
            kl = cpa.load_klines(conn, code, '2014-01-01')
            st = {r['date']: base_stage(r['stage']) for r in conn.execute(
                "SELECT date, stage FROM cpa_stage_daily WHERE stock_code=?", (code,))}
            cache[code] = ({k['date']: j for j, k in enumerate(kl)}, kl, st)
        pos, kl, stmap = cache[code]
        j = pos.get(e['date'])
        if j is None or j + 1 >= len(kl):
            continue
        s = stmap.get(e['date'])
        if not s:
            continue
        ent = kl[j + 1]['open_adj']
        if not ent:
            continue
        for H in (10, 20, 60):
            if j + 1 + H >= len(kl):
                continue
            ext = kl[j + 1 + H]['adj_close']
            ir = idx_ret(e['date'], H)
            if not ext or ir is None:
                continue
            res[s]['sig'].append((H, (ext / ent - 1 - COST) - ir))
            res[s]['all'].append((H, (ext / ent - 1 - COST) - ir))
        cnt[s] += 1

    # 阶段基准（该阶段全部交易日，做对照）
    for code in list(codes)[:args.sample]:
        if code in cache:
            pos, kl, stmap = cache[code]
        else:
            kl = cpa.load_klines(conn, code, '2014-01-01')
            st = {r['date']: base_stage(r['stage']) for r in conn.execute(
                "SELECT date, stage FROM cpa_stage_daily WHERE stock_code=?", (code,))}
            cache[code] = ({k['date']: j for j, k in enumerate(kl)}, kl, st)
            pos, kl, stmap = cache[code]
        last = defaultdict(lambda: -10 ** 9)
        for d, s in stmap.items():
            j = pos.get(d)
            if j is None or j + 1 >= len(kl) or j - last[s] < args.dedup:
                continue
            last[s] = j
            ent = kl[j + 1]['open_adj']
            if not ent:
                continue
            for H in (10, 20, 60):
                if j + 1 + H >= len(kl):
                    continue
                ext = kl[j + 1 + H]['adj_close']
                ir = idx_ret(d, H)
                if not ext or ir is None:
                    continue
                res[s]['stage'].append((H, (ext / ent - 1 - COST) - ir))

    def st_(a, H):
        v = [x for h, x in a if h == H]
        if not v:
            return None
        b = sorted(v); n = len(b)
        return n, sum(1 for x in b if x > 0) / n * 100, statistics.mean(b) * 100, b[n // 2] * 100

    print('=' * 96)
    print(f'【信号 {nm} × CPA 阶段】H20 超额（T+1 开盘 → 20 日收盘，含 0.3% 成本）')
    print('=' * 96)
    print(f'{"阶段":<8}{"信号日数":>9}   {"信号∩阶段  n/胜率/均值/中位":>34}   {"阶段单独  n/胜率/均值/中位":>34}')
    for s in sorted(res, key=lambda x: -cnt.get(x, 0)):
        a, b = st_(res[s]['sig'], 20), st_(res[s]['stage'], 20)
        fa = f'{a[0]:>6,} {a[1]:>3.0f}% {a[2]:>+6.2f}% {a[3]:>+6.2f}%' if a else '—'
        fb = f'{b[0]:>6,} {b[1]:>3.0f}% {b[2]:>+6.2f}% {b[3]:>+6.2f}%' if b else '—'
        print(f'{s:<8}{cnt.get(s, 0):>9,}   {fa:>34}   {fb:>34}')

    print('\n--- 汇总：该信号整体 vs 各阶段基准 ---')
    allsig = [x for s in res for x in res[s]['sig']]
    allstg = [x for s in res for x in res[s]['stage']]
    for H in (10, 20, 60):
        a, b = st_(allsig, H), st_(allstg, H)
        if a and b:
            print(f'  H{H:<3} 信号整体 n={a[0]:>7,} 胜率 {a[1]:>3.0f}% 均值 {a[2]:>+6.2f}% 中位 {a[3]:>+6.2f}%'
                  f'   ｜阶段整体 n={b[0]:>7,} 均值 {b[2]:>+6.2f}%   → 信号增量 {a[2] - b[2]:>+6.2f}pp')
    print(f'\n耗时 {time.time() - t0:.0f}s')
    conn.close()


if __name__ == '__main__':
    main()
