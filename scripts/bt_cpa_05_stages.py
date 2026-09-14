# -*- coding: utf-8 -*-
"""
【回测 5/26】CPA 状态机整体回测 —— 各阶段 H20 超额收益分布

回答的核心问题：**③ 是否真的优于 ②**（Kell 体系里 ③ 是"确认"、② 只是"起点"；
而回测 1/2 发现 A 股 ① 信号强度 >> ②，这个排序在完整状态机下是否成立）

口径（同回测 1/2，保证可比）：800 只随机（seed 42）/ T+1 复权开盘入场 /
H20 复权收盘出场 / 扣 0.3% / 超额 = 个股 - 000985 同期 / 同股同阶段去重 20 交易日

两个视角：
  A. 期间视角 —— 处于该阶段的每一天都算一个样本（去重后）。刻画"待在该阶段值不值"
  B. 入场视角 —— 只取每个阶段区间的**首日**。刻画"刚进这个阶段时买进去行不行"
     （B 更接近可交易信号：A 的样本高度自相关，且包含大量"已经涨完"的日子）

数据源：cpa_stage_daily（已按 #27 修复后的引擎重算，无未来函数）
"""
import sys, os, sqlite3, statistics, random, time, argparse
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
import scanners.cpa_stage as cpa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'data', 'lixinger.db')
COST = 0.003
DEDUP = 20          # 信号去重窗口（交易日）

STAGES = ['①a', '①b', '②', '②T', '③', '④', '⑤', '⑥a', '⑥b', '⑥bT', '⑥c', '⑥cT', '⑥w']
LABEL = {
    '①a': '①a 超跌(未反转)', '①b': '①b 超跌+反转迹象', '②': '② Wedge Pop(起点)',
    '②T': '②T 过渡态', '③': '③ 确认/后整理', '④': '④ 停顿/箱体',
    '⑤': '⑤ 衰竭延长', '⑥a': '⑥a 派发初现', '⑥b': '⑥b 下跌中继',
    '⑥bT': '⑥bT 过渡态', '⑥c': '⑥c 深度下跌', '⑥cT': '⑥cT 过渡态', '⑥w': '⑥w 预警',
}


def load_index_ret(conn):
    rows = conn.execute("SELECT date, close FROM index_daily_kline "
                        "WHERE stock_code='000985' ORDER BY date").fetchall()
    ds = [r[0] for r in rows]
    cs = {r[0]: r[1] for r in rows}
    idx = {d: k for k, d in enumerate(ds)}

    def ret(d, off=20):
        k0 = idx.get(d)
        if k0 is None or k0 + off >= len(ds):
            return None
        return cs[ds[k0 + off]] / cs[ds[k0]] - 1
    return ret


def stats(arr):
    if not arr:
        return None
    a = sorted(arr)
    n = len(a)
    return {'n': n, 'win': sum(1 for v in a if v > 0) / n * 100,
            'mean': statistics.mean(a) * 100, 'med': a[n // 2] * 100}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=800)
    ap.add_argument('--h', type=int, default=20, help='持有期（交易日）')
    ap.add_argument('--dedup', type=int, default=DEDUP)
    args = ap.parse_args()
    H = args.h
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=60000')
    idx_ret = load_index_ret(conn)

    allcodes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM cpa_stage_daily "
        "WHERE date >= '2016-01-01' ORDER BY stock_code")]
    random.seed(42)
    codes = random.sample(allcodes, min(args.sample, len(allcodes)))
    print(f'样本 {len(codes)} 只｜持有期 H{H}｜去重 {args.dedup} 交易日｜'
          f'数据源 cpa_stage_daily（#27 修复后）\n', flush=True)

    daily = defaultdict(list)     # 期间视角
    entry = defaultdict(list)     # 入场视角（阶段首日）
    n_stock_ok = 0
    for ci, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 300:
            continue
        pos = {k['date']: j for j, k in enumerate(kl)}
        rows = conn.execute("SELECT date, stage FROM cpa_stage_daily "
                            "WHERE stock_code=? ORDER BY date", (code,)).fetchall()
        if not rows:
            continue
        n_stock_ok += 1
        last = defaultdict(lambda: -10 ** 9)
        prev_stage = None
        for r in rows:
            d, st = r['date'], r['stage']
            j = pos.get(d)
            if j is None or j + 1 + H >= len(kl):
                prev_stage = st
                continue
            ir = idx_ret(d, H)
            if ir is None:
                prev_stage = st
                continue
            ent = kl[j + 1]['open_adj']
            ex = kl[j + 1 + H]['adj_close']
            if not ent or not ex:
                prev_stage = st
                continue
            x = (ex / ent - 1 - COST) - ir
            is_new = (st != prev_stage)          # 该阶段区间的首日
            prev_stage = st
            if st not in STAGES:
                continue
            if j - last[st] >= args.dedup:
                last[st] = j
                daily[st].append(x)
            if is_new:
                entry[st].append(x)
        if ci % 200 == 0:
            print(f'  ...{ci}/{len(codes)} ({time.time() - t0:.0f}s)', flush=True)

    print('\n' + '=' * 92)
    print(f'【回测 5/26】CPA 各阶段 H{H} 超额收益（{n_stock_ok} 只，超额 = 个股 - 000985，已扣 0.3%）')
    print('=' * 92)
    print(f'{"阶段":<18}{"期间视角 n":>10}{"胜率":>7}{"均值":>8}{"中位":>8}   |'
          f'{"入场视角 n":>10}{"胜率":>7}{"均值":>8}{"中位":>8}')
    print('-' * 92)
    order = ['①a', '①b', '②', '②T', '③', '④', '⑤', '⑥a', '⑥b', '⑥bT', '⑥c', '⑥cT', '⑥w']
    for st in order:
        a, b = stats(daily.get(st)), stats(entry.get(st))
        sa = (f'{a["n"]:>10,}{a["win"]:>6.0f}%{a["mean"]:>+8.2f}{a["med"]:>+8.2f}'
              if a else f'{"-":>10}{"-":>7}{"-":>8}{"-":>8}')
        sb = (f'{b["n"]:>10,}{b["win"]:>6.0f}%{b["mean"]:>+8.2f}{b["med"]:>+8.2f}'
              if b else f'{"-":>10}{"-":>7}{"-":>8}{"-":>8}')
        print(f'  {LABEL[st]:<16}{sa}   |{sb}')

    print('\n' + '=' * 92)
    print('关键结论对照')
    print('=' * 92)

    def cmp(a, b, na, nb):
        x, y = stats(entry.get(a)), stats(entry.get(b))
        if not x or not y:
            print(f'  {na} vs {nb}: 样本不足'); return
        print(f'  {na} vs {nb}：均值 {x["mean"]:+.2f}% vs {y["mean"]:+.2f}% '
              f'({x["mean"] - y["mean"]:+.2f}pp)｜胜率 {x["win"]:.0f}% vs {y["win"]:.0f}% '
              f'({x["win"] - y["win"]:+.1f}pp)')

    print('  【核心】③ 是否优于 ②：')
    cmp('③', '②', '③ 确认/后整理', '② Wedge Pop  ')
    cmp('④', '②', '④ 停顿/箱体 ', '② Wedge Pop  ')
    print('  【对照】① 家族 vs ②（回测 1/2 在独立口径下的发现）：')
    cmp('①b', '②', '①b 反转迹象 ', '② Wedge Pop  ')
    print('  【风险端】⑥ 家族是否该回避：')
    for st in ('⑥a', '⑥b', '⑥c', '⑥w'):
        cmp(st, '①a', LABEL[st], '①a 超跌     ')
    print(f'\n耗时 {time.time() - t0:.0f}s')
    conn.close()


if __name__ == '__main__':
    main()
