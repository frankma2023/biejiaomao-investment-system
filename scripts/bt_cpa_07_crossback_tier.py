#!/usr/bin/env python3
"""
【CPA 回测 #7】阶段③ 标准档 vs 深档 —— 检验「浅回踩质量更高」这个未验证假设

背景：③ 的判定里把回踩分成两档（PRD §5）：
  标准档 = low ≤ EMA10 + 0.3×ATR 且 close 守住 EMA10
  深档   = 跌破 EMA10 后 low ≤ EMA20 + 0.3×ATR 且 close 守住 EMA20（但在 EMA10 下方）
PRD §10.4 记：「触 10 vs 触 20：递进（采纳）；"浅回踩质量更高"降级为待验证假设」。
本回测就是去验证它——**如果两档没有差异，那这个两级设计就没有依据**。

口径（同回测 1/2/5，保证可比）：800 只随机（seed 42）/ T+1 复权开盘入场 /
H10 / H20 / H60 复权收盘出场 / 扣 0.3% / 超额 = 个股 − 000985 同期 / 同股去重 20 交易日
样本：cpa_stage_daily 里每个 ③ 区间的**首日**（= 回踩确认日）
"""
import sys, os, sqlite3, random, statistics, time, argparse
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

DB = str(DB_PATH)
COST = 0.003
CFG = cpa.CFG


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


def classify(ind, j):
    """判断第 j 天的 ③ 属于标准档还是深档（复刻 judge_crossback 的档位判定）"""
    l, c = ind['lows'][j], ind['closes'][j]
    e10, e20, a20 = ind['ema10'][j], ind['ema20'][j], ind['atr20'][j]
    if None in (l, c, e10, e20, a20):
        return None
    tol = CFG['cb_tol_atr'] * a20
    if l <= e10 + tol and c > e10:
        return 'standard'
    if l <= e20 + tol and c > e20 and c < e10:
        return 'deep'
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=800)
    ap.add_argument('--dedup', type=int, default=20)
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=60000')
    idx_ret = load_index_ret(conn)

    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM cpa_stage_daily WHERE date>='2016-01-01'")]
    random.seed(42)
    codes = random.sample(codes, min(args.sample, len(codes)))
    print(f'样本 {len(codes)} 只｜H10/H20/H60｜去重 {args.dedup} 日\n', flush=True)

    ex = defaultdict(lambda: defaultdict(list))   # ex[档位][H] = [...]
    n_seg = defaultdict(int)
    for ci, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 320:
            continue
        ind = cpa.compute_indicators(kl)
        pos = {k['date']: j for j, k in enumerate(kl)}
        rows = conn.execute("""SELECT date, stage, stage_start_date FROM cpa_stage_daily
            WHERE stock_code=? AND date>='2016-01-01' ORDER BY date""", (code,)).fetchall()
        last = -10 ** 9
        for r in rows:
            if not (r['stage'] or '').startswith('③'):
                continue
            d = r['date']
            if d != r['stage_start_date']:      # 只取 ③ 区间的首日
                continue
            j = pos.get(d)
            if j is None or j + 1 >= len(kl):
                continue
            if j - last < args.dedup:
                continue
            ph = classify(ind, j)
            if ph is None:
                continue
            last = j
            n_seg[ph] += 1
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
                ex[ph][H].append((ext / ent - 1 - COST) - ir)
        if ci % 200 == 0:
            print(f'  ...{ci}/{len(codes)} ({time.time() - t0:.0f}s)', flush=True)

    print('\n' + '=' * 84)
    print('【回测 #7】③ 标准档 vs 深档 —— "浅回踩质量更高" 是否成立')
    print('=' * 84)
    print(f'{"档位":<10}{"区间数":>8}{"H":>5}{"n":>7}{"胜率":>7}{"均值":>9}{"中位":>9}')

    def st(arr):
        if not arr:
            return None
        a = sorted(arr); n = len(a)
        return n, sum(1 for v in a if v > 0) / n * 100, statistics.mean(a) * 100, a[n // 2] * 100

    for ph, label in (('standard', '标准档(触EMA10)'), ('deep', '深档(触EMA20)')):
        for H in (10, 20, 60):
            s = st(ex[ph].get(H))
            if not s:
                continue
            print(f'{label if H == 20 else "":<10}{n_seg[ph] if H == 20 else "":>8}{H:>5}'
                  f'{s[0]:>7,}{s[1]:>6.0f}%{s[2]:>+8.2f}%{s[3]:>+8.2f}%')

    print('\n--- 两档对比（同一持有期）---')
    print(f'{"H":>5}   {"标准档 均值/胜率":>22}   {"深档 均值/胜率":>22}   差值')
    for H in (10, 20, 60):
        a, b = st(ex['standard'].get(H)), st(ex['deep'].get(H))
        if not a or not b:
            continue
        print(f'{H:>5}   {a[2]:>+9.2f}% / {a[1]:>5.0f}%        {b[2]:>+9.2f}% / {b[1]:>5.0f}%'
              f'      均值 {a[2] - b[2]:>+6.2f}pp｜胜率 {a[1] - b[1]:>+6.1f}pp')
    print(f'\n耗时 {time.time() - t0:.0f}s')
    conn.close()


if __name__ == '__main__':
    main()
