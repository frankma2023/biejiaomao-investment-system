#!/usr/bin/env python3
"""
【CPA 回测 #21】⑥ 期间的「缩量下跌 vs 放量下跌」后续走势是否不同

背景：引擎在 ⑥ 的判定里给每天打了个 vr_class（CFG d_vr_score=1.3）：
  放量 = VR ≥ 1.3 ｜ 缩量 = VR < 1.3
注释写的是「VR 评分分档（非门槛）」——也就是说它只做标注、不参与判定。
本回测检验这个标注**是否携带信息**：
  若放量下跌后续继续跌、缩量下跌后续止跌 → 分档有效；
  若两组后续无差异 → 这个分档是装饰。

口径（同其它回测）：800 只随机（seed 42）/ T+1 复权开盘入场 /
H10/H20/H60 复权收盘出场 / 扣 0.3% / 超额 = 个股 − 000985 同期 / 同股同组去重 20 交易日
样本：处于 ⑥a/⑥b/⑥c（含 T 变体）的**下跌日**（收盘 < 前收盘）
"""
import sys, os, sqlite3, random, statistics, time, argparse
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

COST = 0.003
GATE = cpa.CFG['d_vr_score']          # 1.3


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
    ap.add_argument('--sample', type=int, default=800)
    ap.add_argument('--dedup', type=int, default=20)
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=60000')
    idx_ret = load_index_ret(conn)

    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM cpa_stage_daily WHERE date>='2016-01-01'")]
    random.seed(42)
    codes = random.sample(codes, min(args.sample, len(codes)))
    print(f'样本 {len(codes)} 只｜放量门槛 VR ≥ {GATE}｜H10/H20/H60｜去重 {args.dedup} 日\n', flush=True)

    ex = defaultdict(lambda: defaultdict(list))
    raw = defaultdict(lambda: defaultdict(list))     # 原始收益（非超额），看绝对方向
    for ci, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 320:
            continue
        ind = cpa.compute_indicators(kl)
        pos = {k['date']: j for j, k in enumerate(kl)}
        rows = conn.execute("""SELECT date, stage FROM cpa_stage_daily
            WHERE stock_code=? AND date>='2016-01-01' ORDER BY date""", (code,)).fetchall()
        last = {'放量': -10 ** 9, '缩量': -10 ** 9}
        for r in rows:
            st = (r['stage'] or '')
            if st[:1] != '⑥' or st == '⑥w':
                continue
            j = pos.get(r['date'])
            if j is None or j < 1 or j + 1 >= len(kl):
                continue
            c, pc = ind['closes'][j], ind['closes'][j - 1]
            vr = ind['vr'][j]
            if not c or not pc or c >= pc or vr is None:
                continue                     # 只看下跌日
            grp = '放量' if vr >= GATE else '缩量'
            if j - last[grp] < args.dedup:
                continue
            last[grp] = j
            ent = kl[j + 1]['open_adj']
            if not ent:
                continue
            for H in (10, 20, 60):
                if j + 1 + H >= len(kl):
                    continue
                ext = kl[j + 1 + H]['adj_close']
                ir = idx_ret(r['date'], H)
                if not ext or ir is None:
                    continue
                x = ext / ent - 1 - COST
                ex[grp][H].append(x - ir)
                raw[grp][H].append(x)
        if ci % 200 == 0:
            print(f'  ...{ci}/{len(codes)} ({time.time() - t0:.0f}s)', flush=True)

    def st(a):
        if not a:
            return None
        b = sorted(a); n = len(b)
        return n, sum(1 for v in b if v > 0) / n * 100, statistics.mean(b) * 100, b[n // 2] * 100

    print('\n' + '=' * 88)
    print('【回测 #21】⑥ 期间「缩量下跌 vs 放量下跌」的后续走势')
    print('=' * 88)
    for g in ('放量', '缩量'):
        for H in (10, 20, 60):
            s, r0 = st(ex[g].get(H)), st(raw[g].get(H))
            if not s:
                continue
            print(f'{g if H == 20 else "":<5}{H:>4}  n={s[0]:>7,}｜超额 胜率 {s[1]:>3.0f}% 均值 {s[2]:>+6.2f}% 中位 {s[3]:>+6.2f}%'
                  f'   ｜原始收益 均值 {r0[2]:>+6.2f}%')
        print()
    print('--- 两组对比（超额，同持有期）---')
    print(f'{"H":>5}   {"放量下跌":>26}   {"缩量下跌":>26}   差值')
    for H in (10, 20, 60):
        a, b = st(ex['放量'].get(H)), st(ex['缩量'].get(H))
        if not a or not b:
            continue
        print(f'{H:>5}   {a[2]:>+8.2f}% / {a[1]:>3.0f}%            {b[2]:>+8.2f}% / {b[1]:>3.0f}%'
              f'           均值 {a[2] - b[2]:>+6.2f}pp｜胜率 {a[1] - b[1]:>+5.1f}pp')
    print(f'\n耗时 {time.time() - t0:.0f}s')
    conn.close()


if __name__ == '__main__':
    main()
