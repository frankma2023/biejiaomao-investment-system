#!/usr/bin/env python3
"""
【CPA #31 ⑥b/⑥c 分界（回撤 30%）校准】

PRD §8.6-8.7：⑥b = 破位后延续（自高点回撤 <30% 且未复活）；⑥c = 破位后深跌（≥30%）。
问题（2026-09-13 以 002648 为例提出）：30% 过粗——
  · 段2（05-08~06-23，32 个交易日持续下跌）回撤只有 −26.9%，差 3.1pp 卡在 ⑥b；
  · 段1（04-21~04-28，6 日反弹 +10.4%）也在 ⑥b —— 同一状态内部方向可以相反。
候选方案：① 提高显示层粒度（拆「破位后·反弹」/「破位后·续跌」）；
          ② 回撤阈值改双条件（幅度 + 持续天数）。

本脚本不预设答案，只做两件事：
  A. 把 ⑥ 区间内的每一天按「回撤幅度 × 距高点天数」二维分桶，看 H20 超额在哪里转坏
     → 找出数据上的自然分界，而不是沿用自设的 30%
  B. 检验「方向」（近 5 日涨跌）在 ⑥b 内部是否真的两极分化

口径：T+1 复权开盘 / H10/20/60 / 扣 0.3% / 超额 = 个股 − 000985 / 同股去重 20 交易日
用法：python scripts/bt_cpa_31_drop_split.py [--sample 800]
"""
import sys, os, sqlite3, random, statistics, time, argparse
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


def stat(vals):
    if len(vals) < 30:
        return None
    b = sorted(vals); n = len(b)
    return (n, sum(1 for x in b if x > 0) / n * 100,
            statistics.mean(b) * 100, b[n // 2] * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=800)
    ap.add_argument('--dedup', type=int, default=20)
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=60000')
    idx_ret = load_index_ret(conn)

    allc = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM cpa_stage_daily WHERE date>='2016-01-01'")]
    random.seed(42)
    codes = sorted(random.sample(allc, min(args.sample, len(allc))))
    print(f'样本 {len(codes)} 只｜去重 {args.dedup} 日\n', flush=True)

    grp_dd = defaultdict(list)        # 回撤分桶
    grp_dir = defaultdict(list)       # 方向（近5日涨跌）
    grp_dd_dir = defaultdict(list)    # 二维
    grp_days = defaultdict(list)      # 距高点天数
    n_row = 0
    for ci, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 320:
            continue
        pos = {k['date']: j for j, k in enumerate(kl)}
        highs = [k['high_adj'] if k.get('high_adj') else 0 for k in kl]
        closes = [k['adj_close'] for k in kl]
        # 滚动 250 日最高
        roll_hi = [None] * len(kl)
        for j in range(len(kl)):
            w = highs[max(0, j - 249):j + 1]
            roll_hi[j] = max(w) if w else None
        # 距高点天数（上一次刷新 250 日新高的日子）
        last_hi = -1
        days_since = [None] * len(kl)
        for j in range(len(kl)):
            if highs[j] and roll_hi[j] and abs(highs[j] - roll_hi[j]) < 1e-9:
                last_hi = j
            days_since[j] = (j - last_hi) if last_hi >= 0 else None

        rows = conn.execute("""SELECT date, stage FROM cpa_stage_daily
            WHERE stock_code=? AND date>='2016-01-01'
              AND (stage LIKE '⑥%') ORDER BY date""", (code,)).fetchall()
        last = -10 ** 9
        for r in rows:
            j = pos.get(r['date'])
            if j is None or j + 1 + 20 >= len(kl):
                continue
            if j - last < args.dedup:
                continue
            last = j
            c = closes[j]
            hi = roll_hi[j]
            if not c or not hi or hi <= 0:
                continue
            dd = c / hi - 1
            if dd > 0:
                continue
            # 方向：近 5 日涨跌
            j5 = closes[j - 5] if j >= 5 else None
            d5 = (c / j5 - 1) if (j5 and j5 > 0) else None
            ent = kl[j + 1]['open_adj']
            ext = kl[j + 1 + 20]['adj_close']
            ir = idx_ret(r['date'], 20)
            if not ent or not ext or ir is None:
                continue
            v = (ext / ent - 1 - COST) - ir
            n_row += 1
            b = ('0~-10%' if dd > -0.10 else '-10~-20%' if dd > -0.20 else
                 '-20~-30%' if dd > -0.30 else '-30~-40%' if dd > -0.40 else
                 '-40~-50%' if dd > -0.50 else '<-50%')
            grp_dd[b].append(v)
            if d5 is not None:
                db = ('反弹>5%' if d5 > 0.05 else '反弹0~5%' if d5 > 0 else
                      '下跌0~5%' if d5 > -0.05 else '下跌>5%')
                grp_dir[db].append(v)
                grp_dd_dir[(b, db)].append(v)
            ds = days_since[j]
            if ds is not None:
                gb = ('≤5日' if ds <= 5 else '6~15日' if ds <= 15 else
                      '16~30日' if ds <= 30 else '31~60日' if ds <= 60 else '>60日')
                grp_days[gb].append(v)
        if ci % 200 == 0:
            print(f'  ...{ci}/{len(codes)} ({time.time()-t0:.0f}s)', flush=True)

    def show(title, d, order=None):
        print('\n' + '=' * 92)
        print(title)
        print('=' * 92)
        print(f'{"分组":<26}{"n":>8}{"胜率":>8}{"均值":>10}{"中位":>10}')
        for k in (order or sorted(d.keys(), key=str)):
            if k not in d:
                continue
            s = stat(d[k])
            label = (' × '.join(str(x) for x in k) if isinstance(k, tuple) else str(k))
            if not s:
                print(f'{label:<26}{len(d[k]):>8}  (样本不足)')
                continue
            print(f'{label:<26}{s[0]:>8,}{s[1]:>7.0f}%{s[2]:>+9.2f}%{s[3]:>+9.2f}%')

    print(f'\n⑥ 区间下跌日样本 {n_row:,} 个')
    show('A. 按自 250 日高点回撤分桶（现行分界 30%）', grp_dd,
         ['0~-10%', '-10~-20%', '-20~-30%', '-30~-40%', '-40~-50%', '<-50%'])
    show('B. 按距高点天数分桶', grp_days, ['≤5日', '6~15日', '16~30日', '31~60日', '>60日'])
    show('C. 按近 5 日方向分桶（检验"同一状态方向相反"）', grp_dir,
         ['反弹>5%', '反弹0~5%', '下跌0~5%', '下跌>5%'])
    show('D. 回撤 × 方向 二维（只看 n≥100 的格子）', grp_dd_dir)
    print(f'\n耗时 {time.time()-t0:.0f}s')
    conn.close()


if __name__ == '__main__':
    main()
