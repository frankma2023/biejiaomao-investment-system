#!/usr/bin/env python3
"""
【CPA 回测 #18】⑤ 的前置峰值涨幅阈值校准（当前 30%）

⑤ 的判据前置：`自 ② 以来的区间最大涨幅 ≥30%（峰值涨幅）`（PRD §7）。
本回测把这个门槛拆成桶，看"涨得越多是否后续越差"，从而判断 30% 是否合理。

口径（同其它回测）：800 只随机（seed 42）/ T+1 复权开盘 / H10/20/60 / 扣 0.3% / 超额 − 000985 / 同股去重 20 日
样本：所有 →⑤ 的入场迁移（cpa_stage_transitions），peak_gain 取自 trigger_detail_json
"""
import sys, os, sqlite3, random, statistics, time, json, argparse
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

COST = 0.003
BUCKETS = [(0, 0.20, '<20%'), (0.20, 0.30, '20~30%'), (0.30, 0.40, '30~40%'),
           (0.40, 0.50, '40~50%'), (0.50, 0.75, '50~75%'), (0.75, 99, '≥75%')]


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

    codes = set(random.sample(
        [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM cpa_stage_daily WHERE date>='2016-01-01'")],
        args.sample)) if args.sample else None
    random.seed(42)
    allc = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM cpa_stage_daily WHERE date>='2016-01-01'")]
    random.seed(42)
    codes = set(random.sample(allc, min(args.sample, len(allc))))

    ev = conn.execute("""SELECT stock_code, transition_date, trigger_detail_json
        FROM cpa_stage_transitions WHERE to_stage='⑤' AND transition_date>='2016-01-01'
        ORDER BY stock_code, transition_date""").fetchall()
    print(f'样本 {len(codes)} 只｜→⑤ 入场事件（全市场）{len(ev):,}\n', flush=True)

    grp = defaultdict(lambda: defaultdict(list))
    n_ev = defaultdict(int)
    cache = {}
    for e in ev:
        code = e['stock_code']
        if code not in codes:
            continue
        try:
            d = json.loads(e['trigger_detail_json'] or '{}')
        except Exception:
            continue
        pg = d.get('peak_gain')
        if pg is None:
            continue
        lab = next((lb for lo, hi, lb in BUCKETS if lo <= pg < hi), None)
        if lab is None:
            continue
        if code not in cache:
            kl = cpa.load_klines(conn, code, '2014-01-01')
            cache[code] = ({k['date']: j for j, k in enumerate(kl)}, kl)
        pos, kl = cache[code]
        j = pos.get(e['transition_date'])
        if j is None or j + 1 >= len(kl):
            continue
        n_ev[lab] += 1
        ent = kl[j + 1]['open_adj']
        if not ent:
            continue
        for H in (10, 20, 60):
            if j + 1 + H >= len(kl):
                continue
            ext = kl[j + 1 + H]['adj_close']
            ir = idx_ret(e['transition_date'], H)
            if not ext or ir is None:
                continue
            grp[lab][H].append((ext / ent - 1 - COST) - ir)

    def st(a):
        if not a:
            return None
        b = sorted(a); n = len(b)
        return n, sum(1 for v in b if v > 0) / n * 100, statistics.mean(b) * 100, b[n // 2] * 100

    print('=' * 92)
    print('【回测 #18】⑤ 前置峰值涨幅分桶（前置门槛现为 ≥30%）')
    print('=' * 92)
    print(f'{"峰值涨幅":<10}{"事件":>8}{"H20 n":>8}{"胜率":>7}{"均值":>9}{"中位":>9}')
    for _, _, lb in BUCKETS:
        s = st(grp[lb].get(20))
        if not s:
            print(f'{lb:<10}{n_ev[lb]:>8}{"":>8}')
            continue
        cur = '  ← 现行门槛' if lb == '30~40%' else ''
        print(f'{lb:<10}{n_ev[lb]:>8}{s[0]:>8,}{s[1]:>6.0f}%{s[2]:>+8.2f}%{s[3]:>+8.2f}%{cur}')
    print('\n--- 按现行门槛二分（≥30% vs <30%）---')
    for side, labels in (('≥30%（现行认定）', ['30~40%', '40~50%', '50~75%', '≥75%']),
                         ('<30%（现行排除）', ['<20%', '20~30%'])):
        for H in (10, 20, 60):
            a = []
            for lb in labels:
                a += grp[lb].get(H, [])
            s = st(a)
            if s:
                print(f'  {side:<18} H{H:<3} n={s[0]:>6,}｜胜率 {s[1]:>3.0f}%｜均值 {s[2]:>+6.2f}%｜中位 {s[3]:>+6.2f}%')
        print()
    print(f'耗时 {time.time() - t0:.0f}s')
    conn.close()


if __name__ == '__main__':
    main()
