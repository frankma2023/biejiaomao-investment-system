#!/usr/bin/env python
"""RSI20 分档加减仓回测（000922 中证红利，全收益口径）。

规则（用户指定）：
    初始投入 1 万元建底仓；强买点买入 5000 元，极强买点买入 10000 元；
    强卖点卖出 5000 元，极强卖点卖出 10000 元。

口径（先说清楚，避免误读）：
- RSI20 用 Wilder 平滑，与页面图表同源（src/detectors/divergence.compute_rsi），
  取 index_daily_kline 的 kline_type='normal' 收盘价；收益取 index_full_return_daily。
- 档位区间互不重叠：买点 k 档 = [deeper, threshold)，卖点 k 档 = (threshold, deeper]。
  RSI20 <28 极强买点、[28,35) 强买点、[35,40) 买点；>78 极强卖点、(73,78] 强卖点、(67,73] 卖点。
- 两种触发口径：
  「档位」＝ 当日重新进入某档就触发（RSI 在档位间反复进出会反复触发）；
  「段」  ＝ 每段极大连续超卖/超买行情内，每档只触发一次（与图中标注的"每段一次"一致）。
- 一律不取事后极值点成交（极值只有事后才知道，用它成交就是未来函数）。
- 买入用外部新增资金；卖出所得留存为现金。XIRR 按「留存」与「提走」两种口径都算。

用法：
    python analysis/rsi20_tier_dca.py
"""
import datetime
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from src.detectors.divergence import compute_rsi  # noqa: E402

DB = os.path.join(ROOT, 'data', 'lixinger.db')
CODE = '000922'
NAME = '中证红利'
START = '2016-09-28'
END = '2026-09-28'

BUY_TIERS = [40.0, 35.0, 28.0]    # 买点 / 强买点 / 极强买点
SELL_TIERS = [67.0, 73.0, 78.0]   # 卖点 / 强卖点 / 极强卖点
BUY_SIZE = {1: 0.0, 2: 5000.0, 3: 10000.0}
SELL_SIZE = {1: 0.0, 2: 5000.0, 3: 10000.0}
SELL_SIZE_ALL = {1: 5000.0, 2: 5000.0, 3: 10000.0}
BASE = 10000.0
PERIOD = 20


def load_series(start=START, end=END):
    db = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    k = db.execute(
        "SELECT date, close FROM index_daily_kline "
        "WHERE stock_code=? AND kline_type='normal' ORDER BY date", (CODE,)
    ).fetchall()
    fr = dict(db.execute(
        'SELECT date, close FROM index_full_return_daily WHERE stock_code=?', (CODE,)
    ).fetchall())
    db.close()
    dates = [r[0] for r in k]
    rsi = compute_rsi([r[1] for r in k], PERIOD)
    out = [(d, rsi[i], fr[d]) for i, d in enumerate(dates)
           if rsi[i] is not None and d in fr and start <= d <= end]
    return out, dates[0], dates[-1]


def tier_buy(v, t=BUY_TIERS):
    if v is None:
        return 0
    if v < t[2]:
        return 3
    if v < t[1]:
        return 2
    if v < t[0]:
        return 1
    return 0


def tier_sell(v, t=SELL_TIERS):
    if v is None:
        return 0
    if v > t[2]:
        return 3
    if v > t[1]:
        return 2
    if v > t[0]:
        return 1
    return 0


def events_by_tier(series, buy_tiers=BUY_TIERS, sell_tiers=SELL_TIERS):
    """口径一：当日重新进入某档即触发。"""
    ev, pb, ps = [], 0, 0
    for i, (_d, v, _p) in enumerate(series):
        b, s = tier_buy(v, buy_tiers), tier_sell(v, sell_tiers)
        if b > pb:
            ev.append((i, 'B', b))
        if s > ps:
            ev.append((i, 'S', s))
        pb, ps = b, s
    return ev


def events_by_episode(series, buy_tiers=BUY_TIERS, sell_tiers=SELL_TIERS):
    """口径二：每段极大连续超卖（RSI<买点外沿）/超买（RSI>卖点外沿）内每档只触发一次。"""
    ev = []
    fired_b, fired_s = set(), set()
    for i, (_d, v, _p) in enumerate(series):
        if v < buy_tiers[0]:
            b = tier_buy(v, buy_tiers)
            if b and b not in fired_b:
                fired_b.add(b)
                ev.append((i, 'B', b))
        else:
            fired_b = set()
        if v > sell_tiers[0]:
            s = tier_sell(v, sell_tiers)
            if s and s not in fired_s:
                fired_s.add(s)
                ev.append((i, 'S', s))
        else:
            fired_s = set()
    return ev


def xirr(flows):
    """flows = [(date_str, amount)]，出金为负。二分法求年化内部收益率。"""
    if not flows:
        return None
    base = datetime.date.fromisoformat(flows[0][0])
    pts = [((datetime.date.fromisoformat(d) - base).days / 365.0, a) for d, a in flows]

    def npv(rate):
        return sum(a / (1.0 + rate) ** t for t, a in pts)

    lo, hi = -0.9999, 10.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fmid = npv(mid)
        if flo * fmid <= 0:
            hi, fhi = mid, fmid
        else:
            lo, flo = mid, fmid
    return (lo + hi) / 2


def simulate(series, events, exec_lag=1, buy_size=None, sell_size=None):
    buy_size = BUY_SIZE if buy_size is None else buy_size
    sell_size = SELL_SIZE if sell_size is None else sell_size
    prices = [r[2] for r in series]

    units = BASE / prices[0]
    injected, cash, sold = BASE, 0.0, 0.0
    inject_flows = [(series[0][0], -BASE)]      # 留存口径：只记外部投入
    withdraw_flows = [(series[0][0], -BASE)]    # 提走口径：卖出计为流入
    n_buy, n_sell = {}, {}

    for i, kind, t in events:
        j = i + exec_lag
        if j >= len(series):
            continue
        d, _v, p = series[j]
        if kind == 'B':
            amt = buy_size.get(t, 0.0)
            if amt <= 0:
                continue
            units += amt / p
            injected += amt
            inject_flows.append((d, -amt))
            withdraw_flows.append((d, -amt))
            n_buy[t] = n_buy.get(t, 0) + 1
        else:
            amt = sell_size.get(t, 0.0)
            if amt <= 0:
                continue
            amt = min(amt, units * p)
            if amt <= 0:
                continue
            units -= amt / p
            cash += amt
            sold += amt
            withdraw_flows.append((d, amt))
            n_sell[t] = n_sell.get(t, 0) + 1

    hold = units * prices[-1]
    total = hold + cash
    return {
        'hold': hold, 'cash': cash, 'total': total,
        'injected': injected, 'sold': sold,
        'ret': total / injected - 1.0,
        'irr_keep': xirr(inject_flows + [(series[-1][0], total)]),
        'irr_take': xirr(withdraw_flows + [(series[-1][0], hold)]),
        'n_buy': n_buy, 'n_sell': n_sell,
    }


def fwd_stats(series, events, horizons=(20, 60, 120)):
    """各档事件后的前瞻全收益（事件当日收盘价起算），并给全样本基准。"""
    prices = [r[2] for r in series]
    groups = {}
    for i, kind, t in events:
        groups.setdefault((kind, t), []).append(i)

    def ret_from(i, n):
        j = i + n
        return (prices[j] / prices[i] - 1.0) if j < len(prices) else None

    print('\n-- 事件后前瞻收益（全收益口径，事件当日收盘起算）')
    head = f'{"事件":<14}{"次数":>5}' + ''.join(f'{f"后{n}日":>16}' for n in horizons)
    print(head)
    for (kind, t) in sorted(groups, key=lambda x: (x[0], x[1])):
        idxs = groups[(kind, t)]
        name = ('买' if kind == 'B' else '卖') + {1: '点', 2: '·强', 3: '·极强'}[t]
        cells = []
        for n in horizons:
            vals = [v for v in (ret_from(i, n) for i in idxs) if v is not None]
            cells.append(f'{sum(vals) / len(vals) * 100:+.1f}%({sum(1 for v in vals if v > 0) / len(vals) * 100:.0f}%)'
                         if vals else 'n/a')
        print(f'{name:<14}{len(idxs):>5}' + ''.join(f'{c:>16}' for c in cells))

    base = []
    for n in horizons:
        vals = [ret_from(i, n) for i in range(len(prices) - n)]
        base.append(f'{sum(vals) / len(vals) * 100:+.1f}%({sum(1 for v in vals if v > 0) / len(vals) * 100:.0f}%)')
    print(f'{"全样本基准":<14}{len(prices):>5}' + ''.join(f'{c:>16}' for c in base))
    print('   括号内为上涨概率')


def run_window(start, end, detail=False):
    series, _kmin, _kmax = load_series(start, end)
    if len(series) < 250:
        print(f'\n窗口 {start} ~ {end} 样本不足，跳过')
        return
    p0, p1 = series[0][2], series[-1][2]
    bh = p1 / p0 - 1.0
    years = len(series) / 244.0
    bh_ann = (1 + bh) ** (1 / years) - 1
    print(f'\n{"=" * 96}')
    print(f'{NAME} {CODE}  ·  窗口 {series[0][0]} ~ {series[-1][0]}'
          f'（{len(series)} 个交易日，约 {years:.1f} 年）')
    print(f'基准·1万起点买入持有：{bh * 100:+.1f}%   年化 {bh_ann * 100:+.2f}%')

    variants = [
        ('档位口径  T+1', events_by_tier(series), SELL_SIZE),
        ('段口径    T+1', events_by_episode(series), SELL_SIZE),
        ('段口径    卖点起卖', events_by_episode(series), SELL_SIZE_ALL),
        ('段口径    只买不卖', events_by_episode(series), {1: 0.0, 2: 0.0, 3: 0.0}),
    ]

    hdr = (f'{"口径":<18}{"累计投入":>10}{"累计卖出":>10}{"期末总资产":>12}'
           f'{"总收益率":>10}{"XIRR留存":>10}{"XIRR提走":>10}')
    print(hdr)
    print('-' * 90)
    rows = {}
    for label, ev, ss in variants:
        r = simulate(series, ev, exec_lag=1, sell_size=ss)
        rows[label] = r
        k = f'{r["irr_keep"] * 100:+.2f}%' if r['irr_keep'] is not None else 'n/a'
        t = f'{r["irr_take"] * 100:+.2f}%' if r['irr_take'] is not None else 'n/a'
        print(f'{label:<18}{r["injected"]:>10,.0f}{r["sold"]:>10,.0f}{r["total"]:>12,.0f}'
              f'{r["ret"] * 100:>9.1f}%{k:>10}{t:>10}')

    tot = rows['段口径    T+1']['injected']
    print(f'{"基准·买入持有":<18}{BASE:>10,.0f}{0:>10,.0f}{BASE * (1 + bh):>12,.0f}'
          f'{bh * 100:>9.1f}%{bh_ann * 100:>9.2f}%{bh_ann * 100:>9.2f}%')
    print(f'{"基准·同额起点投入":<18}{tot:>10,.0f}{0:>10,.0f}{tot * (1 + bh):>12,.0f}'
          f'{bh * 100:>9.1f}%{bh_ann * 100:>9.2f}%{bh_ann * 100:>9.2f}%')

    r = rows['段口径    卖点起卖']
    print(f'   触发次数（段口径/卖点起卖）：强买 {r["n_buy"].get(2, 0)}  极强买 {r["n_buy"].get(3, 0)}  '
          f'卖点 {r["n_sell"].get(1, 0)}  强卖 {r["n_sell"].get(2, 0)}  极强卖 {r["n_sell"].get(3, 0)}')
    r = rows['段口径    T+1']
    print(f'   触发次数（段口径/卖点不动）：强买 {r["n_buy"].get(2, 0)}  极强买 {r["n_buy"].get(3, 0)}  '
          f'强卖 {r["n_sell"].get(2, 0)}  极强卖 {r["n_sell"].get(3, 0)}')
    if detail:
        fwd_stats(series, events_by_episode(series))


def main():
    run_window('2016-09-28', '2026-09-28', detail=True)
    run_window('2021-09-28', '2026-09-28')


if __name__ == '__main__':
    main()
