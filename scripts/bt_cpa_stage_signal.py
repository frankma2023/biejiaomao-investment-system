#!/usr/bin/env python3
"""
【CPA × 信号 · 阶段内确认度】—— 按用户提出的思路设计

用户的框架（002648 实例归纳）：
  · 阶段 = CPA 自己的规则（以价格结构为主，与成交量关系不大）
  · 自定义信号 = 以价格+成交量的变化为主
  · 二者的关系不是"预告"，而是**信号在阶段内部强化/削弱该阶段的确认度**
    例：在 ② 期间出现口袋支点 → 强化 ② 的确认
        在 ⑥b 期间出现铁轨线看跌 → 强化 ⑥b 的确定性

本脚本量化这个思路：
  对每个 (股票, 交易日 T)，取它当时所处的阶段 S，统计：
    n_bull = 近 6 个交易日内出现的看涨信号数（signal_events 全掩码）
    has_bear = 近 6 个交易日内是否出现看跌形态（pattern_scan_signals 的 bearish）
  再按 (S, 确认度分档) 分组，看 H20 超额 —— 得到可量化的规则表。
  去重 20 交易日；口径同其它回测（T+1 复权开盘 / 扣 0.3% / 超额 − 000985）

用法：python scripts/bt_cpa_stage_signal.py --sample 800
"""
import sys, os, sqlite3, random, statistics, time, argparse, json
from collections import defaultdict, deque
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

COST = 0.003
WIN = 6          # 近 N 个交易日（用户建议的极限窗口）
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
    s = s or ''
    if s == '⑥w':
        return '⑥w'
    return s[:-1] if s.endswith('T') else s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=800)
    ap.add_argument('--dedup', type=int, default=20)
    ap.add_argument('--win', type=int, default=WIN)
    ap.add_argument('--bear-src', default='strong',
                    help='看跌信号源：strong=仅三重顶/头肩顶/铁轨线（推荐）；all=全部家族；none=不看跌')
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=60000')
    idx_ret = load_index_ret(conn)

    # ── 预加载看跌信号（一次性，不进循环）──
    # 用 bearish_signal_events（按信号自身日期，见 scripts/build_bearish_events.py）
    if args.bear_src == 'none':
        bear_by_code = {}
    else:
        if args.bear_src == 'strong':
            # 双重顶每只 232 次，太频繁会饱和；只留稀有而明确的形态
            where = ("WHERE (source='top_pattern' AND pattern IN ('triple_top','head_shoulders')) "
                     "   OR source='railroad_tracks'")
        else:
            where = ''
        bear_by_code = defaultdict(set)
        for r in conn.execute(f"SELECT stock_code, signal_date FROM bearish_signal_events {where}"):
            bear_by_code[r[0]].add(r[1])
        n_b = sum(len(v) for v in bear_by_code.values())
        print(f'看跌信号源={args.bear_src}｜预加载 {n_b:,} 条 / {len(bear_by_code):,} 只', flush=True)

    # ── 预加载看涨信号（signal_events 全部掩码）──
    bull_by_code = defaultdict(set)
    mask_by_code = defaultdict(dict)      # code -> {date: mask}
    for r in conn.execute("SELECT stock_code, date, signal_mask FROM signal_events"):
        bull_by_code[r[0]].add(r[1])
        mask_by_code[r[0]][r[1]] = (mask_by_code[r[0]].get(r[1]) or 0) | (r[2] or 0)

    allc = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM cpa_stage_daily WHERE date>='2016-01-01'")]
    random.seed(42)
    codes = sorted(random.sample(allc, min(args.sample, len(allc))))
    print(f'样本 {len(codes)} 只｜确认窗口 近 {args.win} 交易日｜去重 {args.dedup} 日\n', flush=True)

    grp = defaultdict(list)
    grp_fam = defaultdict(list)      # (stage, mask) -> [(H, excess)]
    base_fam = defaultdict(list)     # stage -> [(H, excess)] 该阶段全部样本日
    n_day = 0
    for ci, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 320:
            continue
        pos = {k['date']: j for j, k in enumerate(kl)}
        rows = conn.execute("""SELECT date, stage FROM cpa_stage_daily
            WHERE stock_code=? AND date>='2016-01-01' ORDER BY date""", (code,)).fetchall()
        bull_days = bull_by_code.get(code, set())
        bear_days = bear_by_code.get(code, set())
        day_mask = mask_by_code.get(code, {})
        # 按交易日序列统计近 N 日
        kl_dates = [k['date'] for k in kl]
        bull_cnt = {}; bear_flag = {}; fam_hit = {}
        for i, d in enumerate(kl_dates):
            lo = max(0, i - args.win + 1)
            win = kl_dates[lo:i + 1]
            bull_cnt[d] = sum(1 for x in win if x in bull_days)
            bear_flag[d] = any(x in bear_days for x in win)
            m = 0
            for x in win:
                m |= day_mask.get(x, 0)
            fam_hit[d] = m
        last = defaultdict(lambda: -10 ** 9)
        for r in rows:
            d = r['date']; s = base_stage(r['stage'])
            j = pos.get(d)
            if j is None or j + 1 >= len(kl):
                continue
            nb = bull_cnt.get(d, 0)
            key = (s, f'看涨{nb}' if nb else '无看涨',
                   '有看跌' if bear_flag.get(d) else '无看跌')
            if j - last[key] < args.dedup:
                continue
            last[key] = j
            ent = kl[j + 1]['open_adj']
            if not ent:
                continue
            n_day += 1
            for H in (10, 20, 60):
                if j + 1 + H >= len(kl):
                    continue
                ext = kl[j + 1 + H]['adj_close']
                ir = idx_ret(d, H)
                if not ext or ir is None:
                    continue
                exc = (ext / ent - 1 - COST) - ir
                grp[key].append((H, exc))
                if H == 20:
                    base_fam[s].append((H, exc))
                    fm = fam_hit.get(d, 0)
                    for bit, nmm in MASK_NAME.items():
                        if fm & bit:
                            grp_fam[(s, bit)].append((H, exc))
        if ci % 200 == 0:
            print(f'  ...{ci}/{len(codes)} ({time.time() - t0:.0f}s)', flush=True)

    def st_(a, H):
        v = [x for h, x in a if h == H]
        if len(v) < 30:
            return None
        b = sorted(v); n = len(b)
        return n, sum(1 for x in b if x > 0) / n * 100, statistics.mean(b) * 100, b[n // 2] * 100

    print('\n' + '=' * 104)
    print(f'【阶段 × 阶段内确认度】H20 超额（看涨=signal_events全掩码；看跌源={args.bear_src}）')
    print('=' * 104)
    print(f'{"阶段":<7}{"近6日看涨":<10}{"近6日看跌":<10}{"H20 n":>9}{"胜率":>7}{"均值":>9}{"中位":>9}')
    stages = ['①a', '①b', '②', '③', '④', '⑤', '⑥a', '⑥b', '⑥c', '⑥w']
    for s in stages:
        keys = [k for k in grp if k[0] == s]
        keys.sort(key=lambda k: (k[1], k[2]))
        for k in keys:
            r20 = st_(grp[k], 20)
            if not r20:
                continue
            print(f'{s:<7}{k[1]:<10}{k[2]:<10}{r20[0]:>9,}{r20[1]:>6.0f}%{r20[2]:>+8.2f}%{r20[3]:>+8.2f}%')
    print(f'\n样本日 {n_day:,}｜耗时 {time.time() - t0:.0f}s')

    # ── 拆掩码：每个家族在哪个阶段有效 ──
    print('\n' + '=' * 104)
    print('【家族 × 阶段】H20 超额（家族触发日 vs 该阶段全部样本日）')
    print('=' * 104)
    print(f'{"阶段":<7}{"家族":<10}{"家族 n":>9}{"家族胜率":>9}{"家族均值":>10}'
          f'{"阶段本底":>10}{"提升":>9}')
    for s in stages:
        b = base_fam.get(s, [])
        if len(b) < 200:
            continue
        bwr = sum(1 for _, x in b if x > 0) / len(b) * 100
        bmn = statistics.mean([x for _, x in b]) * 100
        for bit in (1, 2, 8, 16, 32):
            a = grp_fam.get((s, bit), [])
            if len(a) < 40:
                continue
            v = [x for _, x in a]
            wr = sum(1 for x in v if x > 0) / len(v) * 100
            mn = statistics.mean(v) * 100
            print(f'{s:<7}{MASK_NAME[bit]:<10}{len(v):>9,}{wr:>8.0f}%{mn:>+9.2f}%'
                  f'{bmn:>+9.2f}%{mn - bmn:>+8.2f}pp')
        print()
    conn.close()


if __name__ == '__main__':
    main()
