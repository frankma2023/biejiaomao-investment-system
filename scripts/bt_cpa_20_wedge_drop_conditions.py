#!/usr/bin/env python3
"""
【CPA 回测 #20】⑥ 的「三条件 AND」是否必要 —— 逐条件拆解

引擎判定（cpa_stage.py:778）：
  hl      = ① 高点不抬高（笔顶序列）
  neg     = ② EMA10 斜率转负
  below_ma= ③a close < EMA20
  below_sup=③b close < 结构支撑
  warn    = hl AND neg                    （PRD §8.4 预警态 ⑥w）
  confirm = warn AND below_ma AND below_sup（→ ⑥a；否则 ⑥b）

要回答的问题：
  **把①②（衰竭证据）去掉、只留③（破坏证据），事后走势会不会不一样？**
  若「只有③」那组的后续同样差 → ①② 可以放宽（AND 过严）；
  若「只有③」那组明显更好 → ①② 正是让它有效的东西（AND 必要）。

口径（同其它回测）：800 只随机 / T+1 复权开盘入场 / H10/20/60 / 扣 0.3% / 超额 − 000985 / 同股去重 20 日
样本：所有 ⑥a/⑥b 的**入场迁移**（cpa_stage_transitions）
"""
import sys, os, sqlite3, random, statistics, time, json, argparse
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

COST = 0.003


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


def label(hl, neg, ma, sup):
    exh = '①②齐' if (hl and neg) else ('①only' if hl else ('②only' if neg else '无衰竭'))
    brk = '③全' if (ma and sup) else ('仅破MA' if ma else ('仅破支撑' if sup else '未破'))
    return f'{exh}+{brk}'


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
    codes = set(random.sample(codes, min(args.sample, len(codes))))
    print(f'样本 {len(codes)} 只｜H10/H20/H60｜去重 {args.dedup} 日\n', flush=True)

    ev = conn.execute("""SELECT stock_code, transition_date, to_stage, trigger_detail_json
        FROM cpa_stage_transitions WHERE to_stage IN ('⑥a','⑥b')
          AND transition_date >= '2016-01-01' ORDER BY stock_code, transition_date""").fetchall()
    print(f'⑥ 入场事件总数（全市场）{len(ev):,}')

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
        hl = d.get('high_lower'); neg = d.get('slope_neg')
        ma = d.get('below_ma'); sup = d.get('below_sup')
        if None in (hl, neg, ma, sup):
            continue
        if code not in cache:
            kl = cpa.load_klines(conn, code, '2014-01-01')
            cache[code] = ({k['date']: j for j, k in enumerate(kl)}, kl)
        pos, kl = cache[code]
        j = pos.get(e['transition_date'])
        if j is None or j + 1 >= len(kl):
            continue
        k = label(hl, neg, ma, sup)
        n_ev[k] += 1
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
            grp[k][H].append((ext / ent - 1 - COST) - ir)

    def st(a):
        if not a:
            return None
        b = sorted(a); n = len(b)
        return n, sum(1 for v in b if v > 0) / n * 100, statistics.mean(b) * 100, b[n // 2] * 100

    print('\n' + '=' * 96)
    print('【回测 #20】⑥ 入场条件拆解（超额收益；对卖出信号而言，越负越“准”）')
    print('=' * 96)
    print(f'{"条件组合":<18}{"事件":>7}{"H20 n":>8}{"胜率":>7}{"均值":>9}{"中位":>9}')
    order = sorted(n_ev, key=lambda k: -n_ev[k])
    for k in order:
        s = st(grp[k].get(20))
        if not s:
            print(f'{k:<18}{n_ev[k]:>7}{"-":>8}')
            continue
        print(f'{k:<18}{n_ev[k]:>7}{s[0]:>8,}{s[1]:>6.0f}%{s[2]:>+8.2f}%{s[3]:>+8.2f}%')

    print('\n--- 核心对比：①②齐+③全（现行 ⑥a） vs 无衰竭+③全（只有破坏）---')
    for k in ('①②齐+③全', '无衰竭+③全'):
        for H in (10, 20, 60):
            s = st(grp[k].get(H))
            if s:
                print(f'  {k:<14} H{H:<3} n={s[0]:>6,}｜胜率 {s[1]:>3.0f}%｜均值 {s[2]:>+6.2f}%｜中位 {s[3]:>+6.2f}%')
    print(f'\n耗时 {time.time() - t0:.0f}s')
    conn.close()


if __name__ == '__main__':
    main()
