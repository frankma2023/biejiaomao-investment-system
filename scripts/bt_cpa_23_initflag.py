#!/usr/bin/env python3
"""
【#23 init_flag 初值影响评估】
PRD §9.6：回测时排除初值来自倒推的样本，避免初值噪声污染。

做法：比较 init_flag=true / false 两组的
  1) 样本量占比（是否值得排除）
  2) 阶段分布（初值是否把股票堆在某个阶段）
  3) H20 超额（初值组的收益是否与事件驱动组不同 → 若不同说明确实有噪声）
口径：T+1 复权开盘 / H20 / 扣 0.3% / 超额 = 个股 − 000985 / 同股去重 20 日
"""
import sys, os, sqlite3, json, random, statistics, time
from collections import defaultdict, Counter
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

COST = 0.003
N_SAMPLE = 600


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
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=60000')
    idx_ret = load_index_ret(conn)

    allc = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM cpa_stage_daily WHERE date>='2016-01-01'")]
    random.seed(42)
    codes = sorted(random.sample(allc, min(N_SAMPLE, len(allc))))
    print(f'样本 {len(codes)} 只\n', flush=True)

    stage_all = Counter(); stage_init = Counter()
    grp = defaultdict(list)
    n_row = n_init = 0
    for ci, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 320:
            continue
        pos = {k['date']: j for j, k in enumerate(kl)}
        rows = conn.execute("SELECT date, stage, metrics_json FROM cpa_stage_daily "
                            "WHERE stock_code=? AND date>='2016-01-01' ORDER BY date",
                            (code,)).fetchall()
        last = -10 ** 9
        for r in rows:
            try:
                m = json.loads(r['metrics_json'] or '{}')
            except Exception:
                m = {}
            is_init = bool(m.get('init_flag'))
            st = r['stage'] or ''
            n_row += 1
            stage_all[st] += 1
            if is_init:
                n_init += 1
                stage_init[st] += 1
            j = pos.get(r['date'])
            if j is None or j + 1 + 20 >= len(kl):
                continue
            if j - last < 20:
                continue
            last = j
            ent = kl[j + 1]['open_adj']
            ext = kl[j + 1 + 20]['adj_close']
            ir = idx_ret(r['date'], 20)
            if not ent or not ext or ir is None:
                continue
            v = (ext / ent - 1 - COST) - ir
            grp['init' if is_init else 'event'].append(v)
        if ci % 200 == 0:
            print(f'  ...{ci}/{len(codes)} ({time.time()-t0:.0f}s)', flush=True)

    print(f'\n总行数 {n_row:,}｜init_flag=true {n_init:,} ({n_init/max(n_row,1)*100:.2f}%)')
    print()
    print('=== 阶段分布：init 组 vs 全体 ===')
    print(f'{"阶段":<8}{"全体占比":>10}{"init占比":>10}{"init/全体":>12}')
    for st, c in stage_all.most_common():
        a = c / n_row * 100
        b = stage_init.get(st, 0) / max(n_init, 1) * 100
        print(f'{st:<8}{a:>9.2f}%{b:>9.2f}%{b/max(a,1e-9):>11.2f}x')
    print()
    print('=== H20 超额：init 组 vs 事件组 ===')
    print(f'{"组":<10}{"n":>8}{"胜率":>8}{"均值":>10}{"中位":>10}')
    for k in ('event', 'init'):
        v = grp.get(k) or []
        if len(v) < 20:
            print(f'{k:<10}{len(v):>8}  (样本不足)')
            continue
        b = sorted(v); n = len(b)
        print(f'{k:<10}{n:>8,}{sum(1 for x in b if x>0)/n*100:>7.0f}%'
              f'{statistics.mean(b)*100:>+9.2f}%{b[n//2]*100:>+9.2f}%')
    print(f'\n耗时 {time.time()-t0:.0f}s')
    conn.close()


if __name__ == '__main__':
    main()
