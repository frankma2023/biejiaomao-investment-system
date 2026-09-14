#!/usr/bin/env python3
"""
【CPA 回测 #16】⑤ 的延伸极值判据：ATR 分母用 ATR20 还是 ATR60？

⑤ 的硬条件（PRD §7）：`N_D10 ≥4（ATR 归一） 或 D10 ≥16%（百分比）`
两个子句测同一件事（价格偏离 EMA10 多远），所以可以互为参照：
  「漏判」= 百分比子句判为极端、而归一化子句没跟上的比例。
哪个 ATR 让漏判率更低，哪个就是更贴合这个判据的分母。

口径：800 只随机（seed 42）/ T+1 复权开盘 / H10/20/60 / 扣 0.3% / 超额 − 000985 / 同股去重 20 日
样本：全历史交易日（只在有 ATR60 的日子上比较）
"""
import sys, os, sqlite3, random, statistics, time, argparse
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

COST = 0.003
TH_N = 4.0        # N_D10 门槛
TH_D = 0.16       # D10 百分比门槛


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
    print(f'样本 {len(codes)} 只｜门槛 N_D10≥{TH_N} / D10≥{TH_D:.0%}\n', flush=True)

    cnt = defaultdict(int)
    # 四组：命中情形（百分比为参照）；看归一化子句是否跟上
    cross = defaultdict(int)
    fwd = defaultdict(lambda: defaultdict(list))
    n_tot = 0
    for ci, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 320:
            continue
        ind = cpa.compute_indicators(kl)
        highs = [k['high_adj'] for k in kl]
        lows = [k['low_adj'] for k in kl]
        atr60 = cpa.atr_series(highs, lows, 60)
        last = defaultdict(lambda: -10 ** 9)
        for j in range(80, len(kl) - 1):
            c, e10 = ind['closes'][j], ind['ema10'][j]
            a20, a60 = ind['atr20'][j], atr60[j]
            if None in (c, e10, a20, a60) or not e10 or not a20 or not a60:
                continue
            d10 = c / e10 - 1                      # 百分比偏离
            n20 = (c - e10) / a20                  # ATR20 归一
            n60 = (c - e10) / a60                  # ATR60 归一
            f_d = d10 >= TH_D
            f_20 = n20 >= TH_N
            f_60 = n60 >= TH_N
            n_tot += 1
            cnt['all'] += 1
            cnt['d'] += f_d
            cnt['n20'] += f_20
            cnt['n60'] += f_60
            if f_d:
                cnt['d&n20'] += f_20
                cnt['d&n60'] += f_60
            if f_20:
                cnt['n20&d'] += f_d
            if f_60:
                cnt['n60&d'] += f_d
            # 分组做前瞻收益
            grp = ('D10达标' if f_d else '') + ('/N20达标' if f_20 else '') + ('/N60达标' if f_60 else '')
            grp = grp or '三不达标'
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
                ir = idx_ret(kl[j]['date'], H)
                if not ext or ir is None:
                    continue
                fwd[grp][H].append((ext / ent - 1 - COST) - ir)
        if ci % 200 == 0:
            print(f'  ...{ci}/{len(codes)} ({time.time() - t0:.0f}s)', flush=True)

    print('\n' + '=' * 92)
    print('【回测 #16】ATR20 vs ATR60 作归一化分母 —— 对百分比子句的漏判率')
    print('=' * 92)
    print(f'  总样本日 {n_tot:,}')
    print(f'  D10 ≥16%            {cnt["d"]:>10,}  ({cnt["d"] / n_tot * 100:.3f}%)')
    print(f'  N_D10 ≥4（ATR20）   {cnt["n20"]:>10,}  ({cnt["n20"] / n_tot * 100:.3f}%)')
    print(f'  N_D10 ≥4（ATR60）   {cnt["n60"]:>10,}  ({cnt["n60"] / n_tot * 100:.3f}%)')
    print()
    print('  漏判率（百分比说极端、归一化没跟上）：')
    print(f'    ATR20：{1 - cnt["d&n20"] / cnt["d"]:.1%}   而 ATR60：{1 - cnt["d&n60"] / cnt["d"]:.1%}')
    print('  反向漏判（归一化说极端、百分比没跟上）：')
    print(f'    ATR20：{1 - cnt["n20&d"] / cnt["n20"]:.1%}   而 ATR60：{1 - cnt["n60&d"] / cnt["n60"]:.1%}')
    print()
    print('  口径一致性（两子句同向的比例）：')
    print(f'    ATR20 ↔ 百分比：{(cnt["d&n20"]) / max(cnt["d"], cnt["n20"]) * 100:.1f}%')
    print(f'    ATR60 ↔ 百分比：{(cnt["d&n60"]) / max(cnt["d"], cnt["n60"]) * 100:.1f}%')

    print('\n--- 各组前瞻超额（看哪个分母分出的组更有区分度）---')

    def st(a):
        if not a:
            return None
        b = sorted(a); n = len(b)
        return n, sum(1 for v in b if v > 0) / n * 100, statistics.mean(b) * 100, b[n // 2] * 100

    keys = sorted(fwd, key=lambda k: -len(fwd[k].get(20, [])))
    print(f'{"组":<26}{"H20 n":>9}{"胜率":>7}{"均值":>9}{"中位":>9}')
    for k in keys:
        s = st(fwd[k].get(20))
        if s:
            print(f'{k:<26}{s[0]:>9,}{s[1]:>6.0f}%{s[2]:>+8.2f}%{s[3]:>+8.2f}%')
    print(f'\n耗时 {time.time() - t0:.0f}s')
    conn.close()


if __name__ == '__main__':
    main()
