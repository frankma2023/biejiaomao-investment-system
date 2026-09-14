#!/usr/bin/env python3
"""
【CPA #6 基准Y 恒真性验证 / #8 基准X vs 基准Y 区分力】

⚠️ 前提声明（重要）：PRD 只记录了这两条基准的**结论**，没有记录原始公式。
   本脚本按「角色反转（阻力→支撑）」的字面语义 + 早前那次失败尝试留下的三个参数痕迹反推：
     · 早前打印过 (low - EMA10)/ATR20 的中位数
     · 「B1 日 low 在 EMA10 下方仅 42.3%（预期 >80%）」
     · 「基准Y 满足率 78.3%（预期 >95%）」
   若用户手上有原始公式，应以原始公式为准重跑。

反推定义：
  基准Y（角色反转）：low ≤ EMA10 + tol 且 close > EMA10
      —— 当日下探到均线（或略穿）但收盘站回均线上方 = 阻力转支撑的机械定义
  基准X（深度上限，用户初版）：low ≥ ②突破日 low
      —— 回踩不破突破日本身的最低点

回答两个问题：
  #6：基准Y 是不是恒真（满足率是否接近 100% → 无区分力）
  #8：基准X vs 基准Y，谁能更好地区分后续走势

口径：T+1 复权开盘 / H20 / 扣 0.3% / 超额 = 个股 − 000985 / 同股去重 20 交易日
"""
import sys, os, sqlite3, random, statistics, time, argparse, json
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

COST = 0.003
TOL_ATR = 0.3      # 与 CFG['cb_tol_atr'] 一致


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


def stat(v):
    if len(v) < 30:
        return None
    b = sorted(v); n = len(b)
    return n, sum(1 for x in b if x > 0) / n * 100, statistics.mean(b) * 100, b[n // 2] * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=400)
    ap.add_argument('--dedup', type=int, default=20)
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=60000')
    idx_ret = load_index_ret(conn)

    codes = [r[0] for r in conn.execute("SELECT DISTINCT stock_code FROM cpa_stage_daily")]
    random.seed(42)
    codes = sorted(random.sample(codes, min(args.sample, len(codes))))
    print(f'样本 {len(codes)} 只｜去重 {args.dedup} 日\n', flush=True)

    # 基准满足率统计
    hitY = hitX = tot = 0
    grp = defaultdict(list)          # 'Y'/'X'/'both'/'neither'
    both_grp = defaultdict(list)
    # 只看 ②/③ 区间内（基准本是给③用的入场判据）
    grp_w = defaultdict(list)
    hitY_w = hitX_w = tot_w = 0

    for ci, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 320:
            continue
        ind = cpa.compute_indicators(kl)
        pos = {k['date']: j for j, k in enumerate(kl)}
        rows = conn.execute("""SELECT date, stage, metrics_json FROM cpa_stage_daily
            WHERE stock_code=? AND date>='2016-01-01' ORDER BY date""", (code,)).fetchall()
        # ② 突破日 low（最近一次）
        last_w_low = None
        last = -10 ** 9
        for r in rows:
            j = pos.get(r['date'])
            if j is None:
                continue
            try:
                m = json.loads(r['metrics_json'] or '{}')
            except Exception:
                m = {}
            # 记录 ② 的停顿区低点（作为 ②突破日 low 的代理）
            if r['stage'] in ('②', '②T') and (m.get('detail') or {}).get('pause_low'):
                last_w_low = (m['detail'])['pause_low']
            e10 = ind['ema10'][j]
            a20 = ind['atr20'][j]
            low = ind['lows'][j]
            close = ind['closes'][j]
            if None in (e10, a20, low, close):
                continue
            tol = TOL_ATR * a20
            y = (low <= e10 + tol) and (close > e10)
            x = (last_w_low is not None) and (low >= last_w_low)
            tot += 1
            hitY += 1 if y else 0
            hitX += 1 if x else 0
            if j + 1 + 20 >= len(kl):
                continue
            if j - last < args.dedup:
                continue
            last = j
            ent = kl[j + 1]['open_adj']; ext = kl[j + 1 + 20]['adj_close']
            ir = idx_ret(r['date'], 20)
            if not ent or not ext or ir is None:
                continue
            v = (ext / ent - 1 - COST) - ir
            k = ('Y' if y else '-') + ('X' if x else '-')
            both_grp[k].append(v)
            if r['stage'] in ('②', '②T', '③', '③T'):
                tot_w += 1
                hitY_w += 1 if y else 0
                hitX_w += 1 if x else 0
                grp_w[k].append(v)
        if ci % 100 == 0:
            print(f'  ...{ci}/{len(codes)} ({time.time()-t0:.0f}s)', flush=True)

    print(f'\n=== #6 基准Y 恒真性（全样本 {tot:,} 个股票日）===')
    print(f'  基准Y 满足率: {hitY/tot*100:.1f}%   ← 早前预期 >95%（即恒真）')
    print(f'  基准X 满足率: {hitX/tot*100:.1f}%')
    print(f'\n=== ②/③ 区间内（{tot_w:,} 个股票日）===')
    if tot_w:
        print(f'  基准Y 满足率: {hitY_w/tot_w*100:.1f}%')
        print(f'  基准X 满足率: {hitX_w/tot_w*100:.1f}%')

    print('\n' + '=' * 88)
    print('#8 基准X vs 基准Y 的区分力（H20 超额，全样本）')
    print('=' * 88)
    print(f'{"组合":<14}{"n":>8}{"胜率":>8}{"均值":>10}{"中位":>10}')
    order = ['YY', 'Y-', '-X', '--']
    label = {'YY': 'Y且X', 'Y-': '仅Y', '-X': '仅X', '--': '都不满足'}
    for k in order:
        v = both_grp.get(k)
        if not v:
            continue
        s = stat(v)
        if not s:
            print(f'{label[k]:<14}{len(v):>8}  (样本不足)')
            continue
        print(f'{label[k]:<14}{s[0]:>8,}{s[1]:>7.0f}%{s[2]:>+9.2f}%{s[3]:>+9.2f}%')

    print('\n' + '=' * 88)
    print('#8 同上（仅 ②/③ 区间内）')
    print('=' * 88)
    print(f'{"组合":<14}{"n":>8}{"胜率":>8}{"均值":>10}{"中位":>10}')
    for k in order:
        v = grp_w.get(k)
        if not v:
            continue
        s = stat(v)
        if not s:
            print(f'{label[k]:<14}{len(v):>8}  (样本不足)')
            continue
        print(f'{label[k]:<14}{s[0]:>8,}{s[1]:>7.0f}%{s[2]:>+9.2f}%{s[3]:>+9.2f}%')
    print(f'\n耗时 {time.time()-t0:.0f}s')
    conn.close()


if __name__ == '__main__':
    main()
