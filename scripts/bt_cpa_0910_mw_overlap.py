#!/usr/bin/env python3
"""
【CPA 回测 #9 + #10】MW 信号 × CPA 阶段的重叠度与区分力

#10：MW B1 与 ② Wedge Pop 的重叠度（背景：早前用 B1 当 Wedge Pop 代理失败，
     实测 B1 日 low 在 EMA10 下方仅 42.3%，怀疑两者重叠度很低 → 现在用真实的 ② 入场事件去量）
#9 ：③ Crossback + B1/B2 重叠 vs 单独

口径（同其它回测）：800 只随机（seed 42）/ T+1 复权开盘 / H10/20/60 / 扣 0.3% / 超额 − 000985 / 同股去重 20 日
样本：cpa_stage_transitions 的 ② 与 ③ 入场事件；MW 信号取自 mw_signal_daily
重叠定义：同股同日；另给 ±2 个交易日（宽松）版本
"""
import sys, os, sqlite3, random, statistics, time, argparse
from collections import defaultdict
from datetime import datetime, timedelta
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=800)
    ap.add_argument('--dedup', type=int, default=20)
    ap.add_argument('--win', type=int, default=2, help='宽松重叠的交易日窗口')
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=60000')
    idx_ret = load_index_ret(conn)

    allc = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM cpa_stage_daily WHERE date>='2016-01-01'")]
    random.seed(42)
    codes = set(random.sample(allc, min(args.sample, len(allc))))
    print(f'样本 {len(codes)} 只｜宽松窗口 ±{args.win} 交易日\n', flush=True)

    # MW 信号日期集合
    mw = defaultdict(lambda: {'b1': set(), 'b2': set()})
    for r in conn.execute("SELECT stock_code, b1_date, b2_date FROM mw_signal_daily"):
        if r['stock_code'] not in codes:
            continue
        if r['b1_date']:
            mw[r['stock_code']]['b1'].add(r['b1_date'])
        if r['b2_date']:
            mw[r['stock_code']]['b2'].add(r['b2_date'])

    # CPA 事件
    ev = conn.execute("""SELECT stock_code, transition_date, to_stage FROM cpa_stage_transitions
        WHERE to_stage IN ('②','③') AND transition_date>='2016-01-01'
        ORDER BY stock_code, transition_date""").fetchall()

    cache = {}
    near = defaultdict(list)      # 归类 -> 超额
    stat = defaultdict(int)
    for e in ev:
        code = e['stock_code']
        if code not in codes:
            continue
        st = e['to_stage'].replace('T', '')
        if code not in cache:
            kl = cpa.load_klines(conn, code, '2014-01-01')
            cache[code] = ({k['date']: j for j, k in enumerate(kl)}, kl)
        pos, kl = cache[code]
        j = pos.get(e['transition_date'])
        if j is None or j + 1 >= len(kl):
            continue
        d0 = datetime.strptime(e['transition_date'], '%Y-%m-%d')
        lo = (d0 - timedelta(days=int(args.win * 1.45))).strftime('%Y-%m-%d')
        hi = (d0 + timedelta(days=int(args.win * 1.45))).strftime('%Y-%m-%d')

        def _hit(kind):
            if e['transition_date'] in mw[code][kind]:
                return 'exact'
            if any(lo <= x <= hi for x in mw[code][kind]):
                return 'near'
            return None
        b1, b2 = _hit('b1'), _hit('b2')
        if b1 == 'exact':
            stat[f'{st}+B1同日'] += 1; key = f'{st}+B1同日'
        elif b1 == 'near':
            stat[f'{st}+B1近邻'] += 1; key = f'{st}+B1近邻'
        elif b2 == 'exact':
            stat[f'{st}+B2同日'] += 1; key = f'{st}+B2同日'
        elif b2 == 'near':
            stat[f'{st}+B2近邻'] += 1; key = f'{st}+B2近邻'
        else:
            stat[f'{st}单独'] += 1; key = f'{st}单独'
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
            near[key].append((H, (ext / ent - 1 - COST) - ir))

    def st_(a):
        if not a:
            return None
        b = sorted(a); n = len(b)
        return n, sum(1 for v in b if v > 0) / n * 100, statistics.mean(b) * 100, b[n // 2] * 100

    print('=' * 90)
    print('【#10】MW B1 与 ② Wedge Pop 的重叠度')
    print('=' * 90)
    b1 = stat['②+B1同日'] + stat['②+B1近邻']
    b2 = stat['②+B2同日'] + stat['②+B2近邻']
    solo = stat['②单独']
    tot = b1 + b2 + solo
    print(f'  ② 入场事件 {tot:,}：')
    print(f'    与 B1 重叠（同日 {stat["②+B1同日"]:,} / ±{args.win}日 {stat["②+B1近邻"]:,}）'
          f' = {b1:,} ({b1 / tot * 100:.1f}%)')
    print(f'    与 B2 重叠 = {b2:,} ({b2 / tot * 100:.1f}%)')
    print(f'    完全单独   = {solo:,} ({solo / tot * 100:.1f}%)')

    print('\n【#9】③ Crossback 与 B1/B2 的重叠')
    b1c = stat['③+B1同日'] + stat['③+B1近邻']
    b2c = stat['③+B2同日'] + stat['③+B2近邻']
    solc = stat['③单独']
    totc = b1c + b2c + solc
    print(f'  ③ 入场事件 {totc:,}：')
    print(f'    与 B1 重叠 = {b1c:,} ({b1c / totc * 100:.1f}%)｜与 B2 重叠 = {b2c:,}'
          f' ({b2c / totc * 100:.1f}%)｜单独 = {solc:,} ({solc / totc * 100:.1f}%)')

    print('\n--- 前瞻超额（H20）：重叠 vs 单独 ---')
    print(f'{"组":<14}{"n":>7}{"胜率":>7}{"均值":>9}{"中位":>9}')
    for k in sorted(near, key=lambda x: -len(near[x])):
        v = [x for h, x in near[k] if h == 20]
        s = st_(v)
        if s:
            print(f'{k:<14}{s[0]:>7,}{s[1]:>6.0f}%{s[2]:>+8.2f}%{s[3]:>+8.2f}%')
    print(f'\n耗时 {time.time() - t0:.0f}s')
    conn.close()


if __name__ == '__main__':
    main()
