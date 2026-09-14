#!/usr/bin/env python3
"""
【CPA #4 前置强势闸门阈值校准】

PRD §3：阶段① 有四个前置条件——
  r_drawdown_min  自 250 日高点回撤 ≥30%
  r_strong_gain   250 日内最大涨幅 ≥40%
  r_high_recency  距 250 日高点 ≤120 交易日
  r_h_rps250      高点时 RPS250 ≥70
后两个是「或」关系：`gain≥40% AND (recent OR rps≥70)`。

PRD §10.2 已记：「①D 闸门提升最小」→ 建议「前置闸门从必要条件降为记录项」。
回测 1 重跑（2026-09-14，v1.4 数据）：①D 相对 ①A 只 +0.56pp 均值 / +0.3pp 胜率。

本脚本把闸门拆开扫参，回答：**任一子条件单独拿出来能不能比闸门整体更有效？**

口径：T+1 复权开盘 / H20 / 扣 0.3% / 超额 = 个股 − 000985 / 同股去重 20 交易日
"""
import sys, os, sqlite3, random, statistics, time, argparse, itertools
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


def stat(v):
    if len(v) < 30:
        return None
    b = sorted(v); n = len(b)
    return n, sum(1 for x in b if x > 0) / n * 100, statistics.mean(b) * 100, b[n // 2] * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=600)
    ap.add_argument('--dedup', type=int, default=20)
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=60000')
    idx_ret = load_index_ret(conn)

    allcodes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM daily_kline_adj WHERE date>='2016-01-01'")]
    random.seed(42)
    codes = random.sample(allcodes, min(args.sample, len(allcodes)))
    print(f'样本 {len(codes)} 只｜去重 {args.dedup} 日\n', flush=True)

    base = []          # ①A 候选日的 (gain, days_since_high, rps_at_high, forward_excess)
    for ci, code in enumerate(codes):
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 320:
            continue
        ind = cpa.compute_indicators(kl)
        rps_map = {}
        for r in conn.execute("SELECT date, rps_250 FROM stock_rs_daily WHERE stock_code=?", (code,)):
            rps_map[r[0]] = r[1]
        last = -10 ** 9
        for i in range(260, len(kl) - 21):
            if i - last < args.dedup:
                continue
            c = ind['closes'][i]
            e20, a20 = ind['ema20'][i], ind['atr20'][i]
            if None in (c, e20, a20) or not a20 or not e20:
                continue
            dd, hi_idx = cpa.drawdown_from_high(kl, i, cpa.CFG['pctile_win'])
            if dd is None or hi_idx is None or dd < cpa.CFG['r_drawdown_min']:
                continue
            nd20 = ind['nd20'][i]
            d20 = c / e20 - 1
            if not ((nd20 is not None and -nd20 >= cpa.CFG['r_nd20_min'])
                    or (d20 <= cpa.CFG['r_d20_max_pct'])):
                continue
            panic = False
            for j in range(max(0, i - 20), i + 1):
                vj, cj = ind['vr'][j], ind['closes'][j]
                cp = ind['closes'][j - 1] if j > 0 else None
                if vj and cj and cp and cj < cp and vj >= cpa.CFG['r_panic_vr']:
                    panic = True
                    break
            if not panic:
                continue
            # 闸门三个分量
            gain = cpa.max_gain_in(kl, max(0, hi_idx - 250), hi_idx)
            rec = i - hi_idx
            rps_h = rps_map.get(kl[hi_idx]['date'])
            # 前瞻
            ent = kl[i + 1]['open_adj']; ext = kl[i + 1 + 20]['adj_close']
            ir = idx_ret(kl[i]['date'], 20)
            if not ent or not ext or ir is None:
                continue
            last = i
            base.append((gain, rec, rps_h, (ext / ent - 1 - COST) - ir))
        if ci % 150 == 0:
            print(f'  ...{ci}/{len(codes)} 候选={len(base)} ({time.time()-t0:.0f}s)', flush=True)

    print(f'\n①A 候选日 {len(base):,} 个')
    s0 = stat([x[3] for x in base])
    print(f'①A 基准（无闸门）: n={s0[0]:,}  胜率 {s0[1]:.0f}%  均值 {s0[2]:+.2f}%  中位 {s0[3]:+.2f}%')

    def show(title, pred, order=None):
        print('\n' + '=' * 96)
        print(title)
        print('=' * 96)
        print(f'{"子条件":<28}{"n":>8}{"保留率":>8}{"胜率":>8}{"均值":>10}{"中位":>10}{"均值Δ":>9}')
        for name in (order or sorted(pred.keys())):
            v = [x[3] for x in base if pred[name](x)]
            s = stat(v)
            if not s:
                print(f'{name:<28}{len(v):>8}  (样本不足)')
                continue
            print(f'{name:<28}{s[0]:>8,}{s[0]/s0[0]*100:>7.0f}%{s[1]:>7.0f}%'
                  f'{s[2]:>+9.2f}%{s[3]:>+9.2f}%{s[2]-s0[2]:>+8.2f}pp')

    # 1) 现行闸门（整体）
    show('1) 现行闸门整体（gain≥40% 且 (rec≤120 或 rps≥70)）', {
        '现行闸门 ①D': lambda x: x[0] is not None and x[0] >= 0.40
                                and (x[1] <= 120 or (x[2] is not None and x[2] >= 70)),
    })

    # 2) 三个分量单独拿出来
    show('2) 三个分量各自单独作闸门', {
        f'仅 涨幅≥40%': lambda x: x[0] is not None and x[0] >= 0.40,
        f'仅 距高点≤120日': lambda x: x[1] <= 120,
        f'仅 RPS250≥70': lambda x: x[2] is not None and x[2] >= 70,
    }, ['仅 涨幅≥40%', '仅 距高点≤120日', '仅 RPS250≥70'])

    # 3) 涨幅阈值扫描
    show('3) 涨幅阈值扫描（仅此一条）', {
        '涨幅 ≥20%': lambda x: x[0] is not None and x[0] >= 0.20,
        '涨幅 ≥30%': lambda x: x[0] is not None and x[0] >= 0.30,
        '涨幅 ≥40%（现行）': lambda x: x[0] is not None and x[0] >= 0.40,
        '涨幅 ≥60%': lambda x: x[0] is not None and x[0] >= 0.60,
        '涨幅 ≥100%': lambda x: x[0] is not None and x[0] >= 1.00,
    }, ['涨幅 ≥20%', '涨幅 ≥30%', '涨幅 ≥40%（现行）', '涨幅 ≥60%', '涨幅 ≥100%'])

    # 4) 新鲜度阈值扫描
    show('4) 距高点天数阈值扫描', {
        '距高点 ≤60 日': lambda x: x[1] <= 60,
        '距高点 ≤120 日（现行）': lambda x: x[1] <= 120,
        '距高点 ≤250 日': lambda x: x[1] <= 250,
    }, ['距高点 ≤60 日', '距高点 ≤120 日（现行）', '距高点 ≤250 日'])

    # 5) RPS 阈值扫描
    show('5) 高点 RPS250 阈值扫描', {
        'RPS250 ≥50': lambda x: x[2] is not None and x[2] >= 50,
        'RPS250 ≥70（现行）': lambda x: x[2] is not None and x[2] >= 70,
        'RPS250 ≥90': lambda x: x[2] is not None and x[2] >= 90,
    }, ['RPS250 ≥50', 'RPS250 ≥70（现行）', 'RPS250 ≥90'])

    # 6) 组合：涨幅 AND RPS（AND 而非 OR）
    show('6) 组合形式对比（涨幅≥40% 为底）', {
        '∧ 距高点≤120（纯 AND）': lambda x: x[0] is not None and x[0] >= 0.40 and x[1] <= 120,
        '∧ RPS≥70（纯 AND）': lambda x: x[0] is not None and x[0] >= 0.40 and x[2] is not None and x[2] >= 70,
        '∧ (rec≤120 或 rps≥70)（现行）': lambda x: x[0] is not None and x[0] >= 0.40
                                               and (x[1] <= 120 or (x[2] is not None and x[2] >= 70)),
        '∧ 距高点≤120 且 RPS≥70': lambda x: x[0] is not None and x[0] >= 0.40
                                       and x[1] <= 120 and x[2] is not None and x[2] >= 70,
    }, ['∧ 距高点≤120（纯 AND）', '∧ RPS≥70（纯 AND）', '∧ (rec≤120 或 rps≥70)（现行）', '∧ 距高点≤120 且 RPS≥70'])

    print(f'\n耗时 {time.time()-t0:.0f}s')
    conn.close()


if __name__ == '__main__':
    main()
