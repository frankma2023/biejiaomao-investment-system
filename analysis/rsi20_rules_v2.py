#!/usr/bin/env python
"""RSI20 信号驱动的两套加减仓规则回测（000922 中证红利，全收益口径）。

规则1（回补式减仓）：
    - 2016-09-28 买入底仓 10000 元；
    - 强卖点卖出当前持仓市值的 30%，极强卖点卖出 60%，卖出所得留作现金；
    - 强买点：若此前有卖出未回补，则用留存现金回补「累计卖出额」并清零；
      若没有可回补的，则新增 5000 元；
    - 极强买点：新增 10000 元（主口径不回补；另给「先回补再追加」变体）。

规则2（信号择时的定额定投）：
    - 从 2016-09-28 起每满 12 个月为一个额度年，每年额度 10000 元，从不卖出；
    - 强买点买入 5000 元，极强买点买入 10000 元；
    - 同一额度年内信号用款超过剩余额度则直接放弃该次（不部分成交）。

共同口径：
    - RSI20 与页面图表同源（src/detectors/divergence.compute_rsi），价格取 index_daily_kline
      的 kline_type='normal'，收益取 index_full_return_daily（全收益，含分红再投）。
    - 信号为「段口径」：每段极大连续超卖/超买行情内每档只触发一次（与图上标注一致）；
      另给「档位口径」（重新进入即触发）对照。
    - 一律信号次日收盘成交，不用事后极值点（避免未来函数）。
    - 收益率 = 期末总资产 / 累计外部投入 - 1；另给 XIRR（年化内部收益率）做同口径比较。

用法：
    python analysis/rsi20_rules_v2.py
"""
import datetime
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    'rsi20_base', os.path.join(ROOT, 'analysis', 'rsi20_tier_dca.py'))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

START, END = '2016-09-28', '2026-09-28'
BASE_POS = 10000.0


def xirr(flows):
    if not flows:
        return None
    d0 = datetime.date.fromisoformat(flows[0][0])
    pts = [((datetime.date.fromisoformat(d) - d0).days / 365.0, a) for d, a in flows]

    def npv(r):
        return sum(a / (1.0 + r) ** t for t, a in pts)

    lo, hi = -0.9999, 10.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if flo * fm <= 0:
            hi = mid
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


# ── 规则1 ────────────────────────────────────────────────────────────────
def rule1(series, events, exec_lag=1, strong_sell=0.30, extreme_sell=0.60,
          extreme_also_buyback=False):
    prices = [r[2] for r in series]
    units = BASE_POS / prices[0]
    injected = BASE_POS
    cash = 0.0
    pending = 0.0          # 待回补的累计卖出额
    sold_total = 0.0
    buyback_total = 0.0
    flows = [(series[0][0], -BASE_POS)]
    n = {'强卖': 0, '极强卖': 0, '回补': 0, '强买新增': 0, '极强买新增': 0}
    log = []
    shortfall = 0.0

    for i, kind, t in events:
        j = i + exec_lag
        if j >= len(series):
            continue
        d, _v, p = series[j]
        if kind == 'S':
            frac = {1: 0.0, 2: strong_sell, 3: extreme_sell}.get(t, 0.0)
            if frac <= 0:
                continue
            amt = units * p * frac
            if amt <= 1e-9:
                continue
            units -= amt / p
            cash += amt
            pending += amt
            sold_total += amt
            n['极强卖' if t == 3 else '强卖'] += 1
            log.append((d, f'{"极强" if t == 3 else "强"}卖 {frac:.0%}', -amt, units, cash, p))
        else:
            if t == 3 and extreme_also_buyback and pending > 1e-9:
                amt = min(pending, cash)
                units += amt / p
                cash -= amt
                buyback_total += amt
                pending -= amt
                n['回补'] += 1
                log.append((d, '极强买·回补', amt, units, cash, p))
            if t == 2:
                if pending > 1e-9:
                    if cash + 1e-6 < pending:
                        shortfall = max(shortfall, pending - cash)
                    amt = min(pending, cash)
                    units += amt / p
                    cash -= amt
                    buyback_total += amt
                    pending -= amt
                    n['回补'] += 1
                    log.append((d, '强买·回补', amt, units, cash, p))
                else:
                    units += 5000.0 / p
                    injected += 5000.0
                    flows.append((d, -5000.0))
                    n['强买新增'] += 1
                    log.append((d, '强买·新增', 5000.0, units, cash, p))
            elif t == 3:
                units += 10000.0 / p
                injected += 10000.0
                flows.append((d, -10000.0))
                n['极强买新增'] += 1
                log.append((d, '极强买·新增', 10000.0, units, cash, p))

    hold = units * prices[-1]
    total = hold + cash
    return {
        'hold': hold, 'cash': cash, 'total': total, 'injected': injected,
        'sold': sold_total, 'buyback': buyback_total, 'n': n,
        'ret': total / injected - 1.0,
        'irr': xirr(flows + [(series[-1][0], total)]),
        'pending_left': pending, 'log': log, 'shortfall': shortfall,
    }


# ── 规则2 ────────────────────────────────────────────────────────────────
def budget_year(d):
    """从 2016-09-28 起每满 12 个月一个额度年，返回 0..9。"""
    y, m, day = int(d[:4]), int(d[5:7]), int(d[8:10])
    k = y - 2016 - (1 if (m, day) < (9, 28) else 0)
    return max(0, min(k, 9))


def rule2(series, events, exec_lag=1, annual=10000.0):
    prices = [r[2] for r in series]
    units, injected = 0.0, 0.0
    spent, skipped, taken = {}, 0, []
    flows = []

    for i, kind, t in events:
        if kind != 'B':
            continue
        want = {2: 5000.0, 3: 10000.0}.get(t, 0.0)
        if want <= 0:
            continue
        j = i + exec_lag
        if j >= len(series):
            continue
        d, _v, p = series[j]
        b = budget_year(d)
        used = spent.get(b, 0.0)
        if used + want > annual + 1e-9:
            skipped += 1
            taken.append((d, t, want, 'skip', b, used))
            continue
        spent[b] = used + want
        units += want / p
        injected += want
        flows.append((d, -want))
        taken.append((d, t, want, 'buy', b, spent[b]))

    hold = units * prices[-1]
    committed = annual * 10
    return {
        'hold': hold, 'total': hold, 'injected': injected, 'committed': committed,
        'ret': hold / injected - 1.0 if injected else None,
        'ret_committed': (hold + (committed - injected)) / committed - 1.0,
        'irr': xirr(flows + [(series[-1][0], hold)]) if flows else None,
        'skipped': skipped, 'spent': spent, 'log': taken,
    }


def dca_benchmark(series, annual=10000.0, n_years=10):
    """基准：不看信号，每年在周年日固定投入 annual，共 n_years 次。"""
    prices = {r[0]: r[2] for r in series}
    dates = [r[0] for r in series]
    units, injected = 0.0, 0.0
    flows = []
    for k in range(n_years):
        target = f'{2016 + k}-09-28'
        d = next((x for x in dates if x >= target), None)
        if d is None:
            continue
        p = prices[d]
        units += annual / p
        injected += annual
        flows.append((d, -annual))
    hold = units * prices[dates[-1]]
    return {'hold': hold, 'injected': injected, 'ret': hold / injected - 1.0,
            'irr': xirr(flows + [(dates[-1], hold)])}


def show(title, r, extra=''):
    irr = f'{r["irr"] * 100:+.2f}%' if r.get('irr') is not None else 'n/a'
    print(f'\n-- {title}')
    print(f'   累计投入 {r["injected"]:>10,.0f}   期末持仓 {r["hold"]:>10,.0f}'
          + (f'   期末现金 {r["cash"]:>9,.0f}' if 'cash' in r else ''))
    if 'cash' in r:
        print(f'   累计卖出 {r["sold"]:>10,.0f}   已回补 {r["buyback"]:>9,.0f}'
              f'   待回补余额 {r["pending_left"]:,.0f}')
    if 'total' in r:
        print(f'   期末总资产 {r["total"]:>9,.0f}   总收益率 {r["ret"] * 100:+.1f}%   XIRR {irr}')
    if extra:
        print(f'   {extra}')


def robustness(codes):
    """同一套规则换标的跑，检验结论是否只来自 000922 这一个样本。"""
    print('\n' + '=' * 108)
    print('稳健性检验：同一套规则换红利指数（窗口 2016-09-28 ~ 2026-09-28）')
    hdr = (f'{"指数":<9}{"区间涨幅":>9}{"买入持有XIRR":>13}'
           f'{"规则1收益":>10}{"规则1 XIRR":>12}{"规则2收益":>10}{"规则2 XIRR":>12}{"定投XIRR":>10}')
    print(hdr)
    print('-' * 106)
    for c in codes:
        base.CODE = c
        try:
            series, _a, _b = base.load_series(START, END)
        except Exception as e:  # noqa: BLE001 - 缺数据的指数跳过即可
            print(f'{c:<9}  读取失败 {type(e).__name__}')
            continue
        if len(series) < 500:
            continue
        p0, p1 = series[0][2], series[-1][2]
        bh = p1 / p0 - 1.0
        y = len(series) / 244.0
        bh_ann = (1 + bh) ** (1 / y) - 1
        ev = base.events_by_episode(series)
        r1 = rule1(series, ev)
        r2 = rule2(series, ev)
        d = dca_benchmark(series)
        print(f'{c:<9}{bh * 100:>8.1f}%{bh_ann * 100:>12.2f}%'
              f'{r1["ret"] * 100:>9.1f}%{r1["irr"] * 100:>11.2f}%'
              f'{r2["ret"] * 100:>9.1f}%{r2["irr"] * 100:>11.2f}%{d["irr"] * 100:>9.2f}%')
    print('   规则1收益＝期末总资产/累计外部投入-1；规则2收益＝期末持仓/累计投入-1（两者投入额不同，跨行别比收益，比 XIRR）')


def main():
    series, _a, _b = base.load_series(START, END)
    p0, p1 = series[0][2], series[-1][2]
    bh = p1 / p0 - 1.0
    years = len(series) / 244.0
    bh_ann = (1 + bh) ** (1 / years) - 1
    print(f'中证红利 000922 全收益 · 窗口 {series[0][0]} ~ {series[-1][0]}'
          f'（约 {years:.1f} 年）')
    print(f'基准·1万起点买入持有：{bh * 100:+.1f}%   年化 {bh_ann * 100:+.2f}%')

    ev_ep = base.events_by_episode(series)
    ev_ti = base.events_by_tier(series)

    print('\n' + '=' * 92)
    print('规则1  回补式减仓')
    r1 = rule1(series, ev_ep)
    show('主口径（极强买点不回补）· 段口径信号', r1)
    print(f'   次数 强卖 {r1["n"]["强卖"]}  极强卖 {r1["n"]["极强卖"]}  '
          f'回补 {r1["n"]["回补"]}  强买新增 {r1["n"]["强买新增"]}  极强买新增 {r1["n"]["极强买新增"]}')
    r1b = rule1(series, ev_ep, extreme_also_buyback=True)
    show('变体（极强买点先回补再追加1万）', r1b)
    r1c = rule1(series, ev_ti)
    show('变体（档位口径信号：重新进入即触发）', r1c)
    r1d = rule1(series, ev_ep, exec_lag=0)
    show('变体（信号当日收盘成交）', r1d)

    print('\n-- 规则1 主口径逐笔明细')
    print(f'   {"日期":<12}{"动作":<14}{"金额":>11}{"后持仓市值":>13}{"后现金":>11}{"成交价":>10}')
    for d, what, amt, u, c, p in r1['log']:
        print(f'   {d:<12}{what:<14}{amt:>+11,.0f}{u * p:>13,.0f}{c:>11,.0f}{p:>10.3f}')
    print(f'   回补现金缺口（应为 0）: {r1["shortfall"]:,.2f}')

    print('\n' + '=' * 92)
    print('规则2  信号择时的定额定投（每年额度 1 万，从不卖出）')
    r2 = rule2(series, ev_ep)
    show('段口径信号', r2)
    by_year = '  '.join(f'{2016 + k}:{r2["spent"].get(k, 0):,.0f}' for k in range(10))
    print(f'   各额度年实际投入 {by_year}')
    print(f'   因超额度放弃的信号 {r2["skipped"]} 次')
    r2b = rule2(series, ev_ti)
    show('变体（档位口径信号）', r2b)
    print(f'   因超额度放弃的信号 {r2b["skipped"]} 次')

    print(f'   注：年度额度合计 {r2["committed"]:,.0f} 元，实际只投出 {r2["injected"]:,.0f} 元；'
          f'若把未投出的 {r2["committed"] - r2["injected"]:,.0f} 元也按本金计，'
          f'总收益率为 {r2["ret_committed"] * 100:+.1f}%')

    b = dca_benchmark(series)
    show('基准：不看信号，每年周年日固定投 1 万（共 10 次）', b)
    print(f'   对比：规则2 段口径 XIRR {r2["irr"] * 100:+.2f}%  vs  无脑定投 {b["irr"] * 100:+.2f}%'
          f'  →  {(r2["irr"] - b["irr"]) * 100:+.2f} 个百分点')
    lr, la = (100000.0 * (1 + bh)), bh_ann
    print(f'\n-- 基准：10 万在 2016-09-28 一次性投入')
    print(f'   期末 {lr:,.0f}   总收益率 {bh * 100:+.1f}%   XIRR {la * 100:+.2f}%')

    robustness(['000922', '000015', '930839', '930914', '930955',
                '931468', '931848', '980081', '980092', 'H30269'])


if __name__ == '__main__':
    main()
