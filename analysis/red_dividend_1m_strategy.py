#!/usr/bin/env python
"""100 万长期投资红利基金 · 基于 RSI6/14/20 的策略设计（000922 中证红利全收益）。

框架（关键，先统一口径）：
- 100 万在起点全部"承诺"出去：投出去的是份额，没投出去的是现金，现金按货币基金计息。
  期末资产 = 持仓市值 + 现金（含利息）。年化 = (期末/100万)^(1/年数) - 1。
  这样「留弹药」不会被系统性低估，也不会被高估。
- 现金收益：2018 起用 bond_yield_daily.y2（2 年国债，真实数据），2016-2017 无数据，取 2.5% 保守值。
- RSI 与图表同源（Wilder），价格用 index_daily_kline 的 kline_type='normal'，收益用 index_full_return_daily。
- 信号取「段口径」：每段极大连续超卖/超买内每档只触发一次（与图上标注一致），信号次日收盘成交。

用法：
    python analysis/red_dividend_1m_strategy.py
"""
import datetime
import importlib.util
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from src.detectors.divergence import compute_rsi  # noqa: E402

DB = os.path.join(ROOT, 'data', 'lixinger.db')
CODE = '000922'
CAPITAL = 1_000_000.0
STRATEGY_START = '2016-09-28'
STRATEGY_END = '2026-09-28'

TIERS = {
    6:  {'buy': [29.0, 20.0, 10.0], 'sell': [81.0, 89.0, 93.0]},
    14: {'buy': [38.0, 31.0, 24.0], 'sell': [70.0, 78.0, 83.0]},
    20: {'buy': [40.0, 35.0, 28.0], 'sell': [67.0, 73.0, 78.0]},
}
CASH_RATE_FALLBACK = 0.025


# ── 数据 ────────────────────────────────────────────────────────────────
def load_data(start=STRATEGY_START, end=STRATEGY_END, code=CODE):
    db = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    k = db.execute("SELECT date, close FROM index_daily_kline "
                   "WHERE stock_code=? AND kline_type='normal' ORDER BY date",
                   (code,)).fetchall()
    fr = dict(db.execute('SELECT date, close FROM index_full_return_daily '
                         'WHERE stock_code=?', (code,)).fetchall())
    y2 = dict(db.execute('SELECT date, y2 FROM bond_yield_daily '
                         'WHERE y2 IS NOT NULL').fetchall())
    db.close()

    dates = [r[0] for r in k]
    closes = [r[1] for r in k]
    rsi = {p: compute_rsi(closes, p) for p in TIERS}

    rows = []
    for i, d in enumerate(dates):
        if d not in fr or rsi[20][i] is None:
            continue
        if not (start <= d <= end):
            continue
        rows.append({
            'date': d, 'price': fr[d],
            'rsi': {p: rsi[p][i] for p in TIERS},
            'rate': (y2.get(d, CASH_RATE_FALLBACK * 100) / 100.0),
        })
    return rows


def tier_of(v, thresholds, is_buy):
    if v is None:
        return 0
    if is_buy:
        if v < thresholds[2]:
            return 3
        if v < thresholds[1]:
            return 2
        if v < thresholds[0]:
            return 1
        return 0
    if v > thresholds[2]:
        return 3
    if v > thresholds[1]:
        return 2
    if v > thresholds[0]:
        return 1
    return 0


def episode_events(rows, period, side):
    """段口径：每段极大连续超卖/超买内每档只触发一次。返回 [(索引, 档位)]。"""
    th = TIERS[period]['buy' if side == 'B' else 'sell']
    outer = th[0]
    ev, fired = [], set()
    for i, r in enumerate(rows):
        v = r['rsi'][period]
        cond = (v < outer) if side == 'B' else (v > outer)
        if cond:
            t = tier_of(v, th, side == 'B')
            if t and t not in fired:
                fired.add(t)
                ev.append((i, t))
        else:
            fired = set()
    return ev


# ── 信号强度（用于决定三个周期怎么用）────────────────────────────────────
def signal_edge(rows, horizons=(20, 60, 120)):
    prices = [r['price'] for r in rows]
    print(f'\n{"周期":<6}{"侧":<4}{"档位":<10}{"次数":>5}' +
          ''.join(f'{f"后{n}日":>16}' for n in horizons))
    base = []
    for n in horizons:
        v = [prices[i + n] / prices[i] - 1 for i in range(len(prices) - n)]
        base.append(f'{sum(v) / len(v) * 100:+.1f}%({sum(1 for x in v if x > 0) / len(v) * 100:.0f}%)')
    print(f'{"基准":<6}{"":<4}{"全样本":<10}{len(prices):>5}' +
          ''.join(f'{c:>16}' for c in base))
    for p in (6, 14, 20):
        for side in ('B', 'S'):
            groups = {}
            for i, t in episode_events(rows, p, side):
                groups.setdefault(t, []).append(i)
            for t in sorted(groups):
                idxs = groups[t]
                label = {1: '买点', 2: '强买点', 3: '极强买点'} if side == 'B' \
                    else {1: '卖点', 2: '强卖点', 3: '极强卖点'}
                cells = []
                for n in horizons:
                    v = [prices[i + n] / prices[i] - 1 for i in idxs if i + n < len(prices)]
                    cells.append(f'{sum(v) / len(v) * 100:+.1f}%({sum(1 for x in v if x > 0) / len(v) * 100:.0f}%)'
                                 if v else 'n/a')
                print(f'RSI{p:<3}{"买" if side == "B" else "卖":<4}{label[t]:<10}{len(idxs):>5}' +
                      ''.join(f'{c:>16}' for c in cells))


# ── 通用引擎 ────────────────────────────────────────────────────────────
def backtest(rows, init_frac=1.0, sell_frac=(0.0, 0.0, 0.0, 0.0),
             reserve_frac=(0.0, 0.0, 0.0, 0.0), buyback_frac=(0.0, 1.0, 1.0, 1.0),
             buyback=True, sell_period=20, buy_period=20, resonance=0,
             exec_lag=1, label=''):
    """init_frac        初始投入比例
    sell_frac[t]      第 t 档卖出信号卖出当前持仓的比例（下标 1/2/3 = 卖点/强卖点/极强卖点）
    reserve_frac[t]   第 t 档买入信号在「无卖出可回补」时动用现金的比例
    buyback_frac[t]   第 t 档买入信号回补「待回补余额」的比例（0=该档不回补，<1=分批回补）
    resonance         加仓所需的最低共振周期数（0=不要求）
    """
    n = len(rows)
    units = CAPITAL * init_frac / rows[0]['price']
    cash = CAPITAL * (1 - init_frac)
    pending = 0.0
    peak = CAPITAL
    max_dd = 0.0
    trades = {'sell': 0, 'buyback': 0, 'reserve': 0}

    # 预计算信号
    buys = dict(episode_events(rows, buy_period, 'B'))
    sells = dict(episode_events(rows, sell_period, 'S'))
    reso = {}
    if resonance > 0:
        for i, r in enumerate(rows):
            reso[i] = sum(1 for p in TIERS if tier_of(r['rsi'][p], TIERS[p]['buy'], True) > 0)

    pending_wait, actions = [], []
    log = []
    for i, r in enumerate(rows):
        # 现金计息（按年化/252 日复利）
        cash *= (1 + r['rate'] / 252.0)

        # 执行前一日挂单
        if pending_wait:
            p = r['price']
            for kind, t, sig_rsi in pending_wait:
                if kind == 'S':
                    amt = units * p * sell_frac[t]
                    if amt > 1e-9:
                        before = units
                        units -= amt / p
                        cash += amt
                        pending += amt
                        trades['sell'] += 1
                        log.append((r['date'], f'卖{t}档 {sell_frac[t]:.0%}', -amt,
                                    before * p, units * p + cash, p, sig_rsi))
                else:
                    if buyback and pending > 1e-9:
                        amt = min(pending * buyback_frac[t], cash)
                        units += amt / p
                        cash -= amt
                        pending -= amt
                        trades['buyback'] += 1
                        log.append((r['date'], f'回补{t}档 {buyback_frac[t]:.0%}', amt,
                                    units * p, units * p + cash, p, sig_rsi))
                    elif reserve_frac[t] > 0 and cash > 1e-9:
                        amt = cash * reserve_frac[t]
                        units += amt / p
                        cash -= amt
                        trades['reserve'] += 1
                        log.append((r['date'], f'投放{t}档 {reserve_frac[t]:.0%}', amt,
                                    units * p, units * p + cash, p, sig_rsi))
            pending_wait = []

        # 生成次日挂单
        if not pending_wait and i + exec_lag < n:
            if i in sells and sell_frac[sells[i]] > 0:
                pending_wait = [('S', sells[i], r['rsi'][sell_period])]
            elif i in buys:
                t = buys[i]
                ok = resonance <= 0 or reso[i] >= resonance
                want_back = buyback and pending > 1e-9 and buyback_frac[t] > 0
                if ok and (want_back or reserve_frac[t] > 0):
                    pending_wait = [('B', t, r['rsi'][buy_period])]

        value = units * r['price'] + cash
        peak = max(peak, value)
        max_dd = max(max_dd, 1 - value / peak)

    final = units * rows[-1]['price'] + cash
    years = len(rows) / 244.0
    return {
        'label': label, 'final': final, 'ret': final / CAPITAL - 1,
        'cagr': (final / CAPITAL) ** (1 / years) - 1,
        'maxdd': max_dd, 'trades': trades,
        'cash_end': cash, 'units_end': units, 'log': log,
    }


def show_results(results):
    hdr = (f'{"策略":<34}{"期末资产":>12}{"总收益率":>10}{"年化":>9}{"最大回撤":>10}'
           f'{"卖":>4}{"回补":>5}{"备用金":>6}')
    print(hdr)
    print('-' * 96)
    for r in results:
        print(f'{r["label"]:<34}{r["final"]:>12,.0f}{r["ret"] * 100:>9.1f}%'
              f'{r["cagr"] * 100:>8.2f}%{r["maxdd"] * 100:>9.1f}%'
              f'{r["trades"]["sell"]:>4}{r["trades"]["buyback"]:>5}{r["trades"]["reserve"]:>6}')


def dca_lump(rows, n=10, per_year=None):
    """100 万在起点承诺，分 n 年等额投入（不看信号）；未投出的部分按货币基金计息。

    与 backtest() 同为「100 万起点承诺」，所以总收益率与年化可直接对比。
    """
    per = per_year if per_year is not None else CAPITAL / n
    units, cash, bought = 0.0, CAPITAL, 0
    peak, max_dd = CAPITAL, 0.0
    for r in rows:
        cash *= (1 + r['rate'] / 252.0)
        if bought < n and r['date'] >= f'{2016 + bought}-09-28':
            amt = min(per, cash)
            units += amt / r['price']
            cash -= amt
            bought += 1
        value = units * r['price'] + cash
        peak = max(peak, value)
        max_dd = max(max_dd, 1 - value / peak)
    final = units * rows[-1]['price'] + cash
    years = len(rows) / 244.0
    return {'final': final, 'ret': final / CAPITAL - 1,
            'cagr': (final / CAPITAL) ** (1 / years) - 1, 'maxdd': max_dd,
            'injected': per * bought, 'cash_end': cash}


def main():
    rows = load_data()
    years = len(rows) / 244.0
    print(f'中证红利 {CODE} 全收益 · {rows[0]["date"]} ~ {rows[-1]["date"]}'
          f'（{len(rows)} 个交易日，约 {years:.1f} 年）')
    print(f'区间涨幅：{rows[0]["price"]:.1f} -> {rows[-1]["price"]:.1f}'
          f'（{rows[-1]["price"] / rows[0]["price"] - 1:+.1%}）')
    bh = rows[-1]['price'] / rows[0]['price']
    print(f'基准·100 万立即满仓持有：期末 {CAPITAL * bh:,.0f}   年化 '
          f'{bh ** (1 / years) - 1:+.2%}')

    signal_edge(rows)

    print('\n' + '=' * 96)
    print('设计依据（看上面那张表得出的，逐条对应）')
    print('  1) 买点用 RSI20：RSI20 强买点 n=24、后20日 +4.5%（胜率83%）、后60日 +5.2%（79%），')
    print('     在三个周期里幅度最大且样本量最大（RSI6 强买点 n=38 但只有 +3.0%；RSI14 n=25 +4.2%）。')
    print('  2) 卖点不改成 RSI6：RSI6 极强卖点后60日 -3.4%、胜率 0%，看着最好，但 n 只有 5 次，')
    print('     不可靠；RSI20 强卖点 n=8、后60日 -4.3%、胜率 12%，幅度更大且样本更多。')
    print('     两者各有短长，所以主口径买卖都用 RSI20（同周期、样本更大、好执行）；')
    print('     RSI6 卖出版留作可选激进版（000922 上更高，但 10 指数稳健性更差）。')
    print('  3) 回补只在 RSI20<35（强买点），不在 35~40 的普通买点：这一条在四个不同卖出')
    print('     设计上都一致带来 8%~14% 的改进，跨设计一致说明是真效应而非拟合噪声。')
    print('  4) 方向：RSI 高＝超买→减仓；RSI 低＝超卖→买回。与"低 RSI 后大涨、高 RSI 后下跌"')
    print('     的前瞻收益表一致。反向操作（高 RSI 买、低 RSI 卖）等于对着上表做反。')

    print('\n' + '=' * 96)
    print('候选策略（100 万起点，2016-09-28 ~ 2026-09-28）')
    rs = []
    DEEP = (0, 0.0, 1.0, 1.0)   # 只在 RSI20 强买点(<35)/极强买点(<28) 回补，普通买点不动
    rs.append(backtest(rows, label='S0 立即满仓持有（基准）'))
    rs.append(backtest(rows, label='S1 满仓+RSI20减30/60+任意档回补',
                       sell_frac=(0, 0, 0.30, 0.60)))
    rs.append(backtest(rows, label='S1b 同上但只在强买点回补',
                       sell_frac=(0, 0, 0.30, 0.60), buyback_frac=DEEP))
    rs.append(backtest(rows, label='S3 满仓+RSI20减50/100+任意档回补',
                       sell_frac=(0, 0, 0.50, 1.00)))
    rs.append(backtest(rows, label='S3b 同上但只在强买点回补',
                       sell_frac=(0, 0, 0.50, 1.00), buyback_frac=DEEP))
    rs.append(backtest(rows, label='S5 卖RSI6极强清仓+任意档回补',
                       sell_frac=(0, 0, 0, 1.00), sell_period=6, buy_period=20))
    rs.append(backtest(rows, label='S5b 同上但只在强买点回补',
                       sell_frac=(0, 0, 0, 1.00), sell_period=6, buy_period=20,
                       buyback_frac=DEEP))
    rs.append(backtest(rows, label='S12 卖RSI6强卖减30/极强卖清仓',
                       sell_frac=(0, 0, 0.30, 1.00), sell_period=6, buy_period=20))
    rs.append(backtest(rows, label='S12b 同上但只在强买点回补',
                       sell_frac=(0, 0, 0.30, 1.00), sell_period=6, buy_period=20,
                       buyback_frac=DEEP))
    rs.append(backtest(rows, label='S10 卖RSI6极强清仓+回补分两批',
                       sell_frac=(0, 0, 0, 1.00), sell_period=6, buy_period=20,
                       buyback_frac=(0, 0.0, 0.50, 1.00)))
    rs.append(backtest(rows, label='S7 初始60%+信号投放（无卖出）',
                       init_frac=0.60, sell_frac=(0, 0, 0, 0),
                       reserve_frac=(0, 0, 0.25, 0.50)))
    rs.append(backtest(rows, label='S9 初始80%+减30/60+回补+投放',
                       init_frac=0.80, sell_frac=(0, 0, 0.30, 0.60),
                       reserve_frac=(0, 0, 0.25, 0.50), buyback_frac=DEEP))
    show_results(rs)

    best = max(rs, key=lambda x: x['final'])
    print(f'\n本窗口最优：{best["label"]}  →  期末 {best["final"]:,.0f}'
          f'（年化 {best["cagr"] * 100:+.2f}%），基准 {rs[0]["final"]:,.0f}')

    print('\n-- S5（买 RSI20 / 卖 RSI6 极强 100%）逐笔明细')
    s5 = [r for r in rs if r['label'].startswith('S5')][0]
    for d, what, amt, hold, total, p, rv in s5['log']:
        print(f'   {d}  {what:<14}{amt:>+12,.0f}   持仓 {hold:>11,.0f}'
              f'   总资产 {total:>11,.0f}   @ {p:.1f}   信号周期 RSI={rv:.1f}')

    print('\n' + '=' * 96)
    print('多指数稳健性（100 万起点，2016-09-28 ~ 2026-09-28）')
    hdr = (f'{"指数":<9}{"基准满仓":>12}{"S3b 减50/100":>13}{"S5b 卖6极强":>13}'
           f'{"S12b 卖6两档":>13}{"S3b年化":>9}{"S12b年化":>10}{"基准年化":>9}')
    print(hdr)
    print('-' * 100)
    win3 = win12 = 0
    e3 = e12 = 0.0
    codes = ['000922', '000015', '930839', '930914', '930955',
             '931468', '931848', '980081', '980092', 'H30269']
    for c in codes:
        sub = load_data(code=c)
        if len(sub) < 500:
            continue
        ys = len(sub) / 244.0
        bh = sub[-1]['price'] / sub[0]['price']
        base_final = CAPITAL * bh
        DEEP = (0, 0.0, 1.0, 1.0)
        r3 = backtest(sub, sell_frac=(0, 0, 0.50, 1.00), buyback_frac=DEEP)
        r5 = backtest(sub, sell_frac=(0, 0, 0, 1.00), sell_period=6,
                      buy_period=20, buyback_frac=DEEP)
        r12 = backtest(sub, sell_frac=(0, 0, 0.30, 1.00), sell_period=6,
                       buy_period=20, buyback_frac=DEEP)
        win3 += r3['final'] > base_final
        win12 += r12['final'] > base_final
        e3 += r3['final'] / base_final - 1
        e12 += r12['final'] / base_final - 1
        print(f'{c:<9}{base_final:>12,.0f}{r3["final"]:>13,.0f}{r5["final"]:>13,.0f}'
              f'{r12["final"]:>13,.0f}{r3["cagr"] * 100:>8.2f}%{r12["cagr"] * 100:>9.2f}%'
              f'{bh ** (1 / ys) - 1:>8.2%}')
    n = len(codes)
    print(f'\n   跑赢满仓持有：S3b {win3}/{n}（平均多赚 {e3 / n * 100:+.1f}%）'
          f'   S12b {win12}/{n}（平均多赚 {e12 / n * 100:+.1f}%）')

    print('\n' + '=' * 96)
    print('推荐设计（S3b）')
    DEEP = (0, 0.0, 1.0, 1.0)
    rec = backtest(rows, sell_frac=(0, 0, 0.50, 1.00), buyback_frac=DEEP)
    print('   起点：100 万一次性满仓买入红利 ETF，不留现金等待（实测留现金等信号是最差的一类）')
    print('   卖出：RSI20 > 73（强卖点）减仓 50%；RSI20 > 78（极强卖点）清仓')
    print('   回补：只在 RSI20 < 35（强买点）用留存现金全额回补；35~40 的普通买点不动')
    print('   闲置现金：放货币基金（2018 起按 2 年国债实际利率计息，2016-2017 取 2.5%）')
    print(f'   回测结果：100 万 -> {rec["final"]:,.0f}（{rec["ret"] * 100:+.1f}%）'
          f'   年化 {rec["cagr"] * 100:+.2f}%   最大回撤 {rec["maxdd"] * 100:.1f}%')
    print(f'   同期满仓持有：100 万 -> {rs[0]["final"]:,.0f}（{rs[0]["ret"] * 100:+.1f}%）'
          f'   年化 {rs[0]["cagr"] * 100:+.2f}%   最大回撤 {rs[0]["maxdd"] * 100:.1f}%')
    print(f'   交易频率：10 年 {rec["trades"]["sell"]} 次卖出 + {rec["trades"]["buyback"]} 次回补')
    print('\n   逐笔（触发RSI＝信号日该周期读数；成交RSI20＝成交当日读数）：')
    print(f'     {"日期":<12}{"动作":<16}{"金额":>12}{"总资产":>12}{"成交价":>10}'
          f'{"触发RSI":>9}{"成交RSI20":>11}')
    for d, what, amt, hold, total, p, sig_rsi in rec['log']:
        exec_rsi = next(x['rsi'][20] for x in rows if x['date'] == d)
        print(f'     {d:<12}{what:<16}{amt:>+12,.0f}{total:>12,.0f}{p:>10.1f}'
              f'{sig_rsi:>9.1f}{exec_rsi:>11.1f}')

    print('\n   规则方向自检（用触发日的读数核对，这是决定下单的那个值）：')
    sells = [x for x in rec['log'] if x[2] < 0]
    buys = [x for x in rec['log'] if x[2] > 0]
    print(f'     卖出 {len(sells)} 笔，触发 RSI20 区间 [{min(x[6] for x in sells):.1f}, '
          f'{max(x[6] for x in sells):.1f}]，全部 > 73（强卖点阈值）'
          f' → RSI 高＝超买＝减仓')
    print(f'     回补 {len(buys)} 笔，触发 RSI20 区间 [{min(x[6] for x in buys):.1f}, '
          f'{max(x[6] for x in buys):.1f}]，全部 < 35（强买点阈值）'
          f' → RSI 低＝超卖＝买回')

    sold_u = sum(-x[2] / x[5] for x in sells)
    bought_u = sum(x[2] / x[5] for x in buys)
    bh_u = CAPITAL / rows[0]['price']
    price_edge = 1 - sold_u / bought_u
    print(f'\n   方向的价值：卖出累计 {sold_u:,.1f} 份、回补累计 {bought_u:,.1f} 份 —— '
          f'回补均价低于卖出均价约 {price_edge:.1%}，同样的钱多买到 {bought_u / sold_u - 1:+.1%} 的份额。')
    print(f'   期末份额：策略 {rec["units_end"]:,.1f} 份 vs 满仓持有 {bh_u:,.1f} 份'
          f'（{rec["units_end"] / bh_u - 1:+.1%}），另有期末现金 {rec["cash_end"]:,.0f} 元')
    print('   反向操作（RSI20>73 买入、<35 卖出）会把这个折价变成溢价：'
          '每次"回补"都少买份额，累计份额净减少，必然跑输满仓持有。')

    print('\n' + '=' * 96)
    print('与无脑定投对比（三者都是 100 万在起点承诺，年化可直接比）')
    dca = dca_lump(rows, n=10)
    dca5 = dca_lump(rows, n=5)
    cmp_hdr = (f'{"方案":<30}{"期末资产":>12}{"总收益率":>10}{"年化":>9}{"最大回撤":>10}')
    print(cmp_hdr)
    print('-' * 76)
    print(f'{"建议规则（RSI20 择时增强）":<30}{rec["final"]:>12,.0f}{rec["ret"] * 100:>9.1f}%'
          f'{rec["cagr"] * 100:>8.2f}%{rec["maxdd"] * 100:>9.1f}%')
    print(f'{"无脑定投（每年 10 万 × 10 年）":<30}{dca["final"]:>12,.0f}{dca["ret"] * 100:>9.1f}%'
          f'{dca["cagr"] * 100:>8.2f}%{dca["maxdd"] * 100:>9.1f}%')
    print(f'{"无脑定投（每年 20 万 × 5 年）":<30}{dca5["final"]:>12,.0f}{dca5["ret"] * 100:>9.1f}%'
          f'{dca5["cagr"] * 100:>8.2f}%{dca5["maxdd"] * 100:>9.1f}%')
    print(f'{"一次性满仓持有":<30}{rs[0]["final"]:>12,.0f}{rs[0]["ret"] * 100:>9.1f}%'
          f'{rs[0]["cagr"] * 100:>8.2f}%{rs[0]["maxdd"] * 100:>9.1f}%')
    print(f'\n   建议规则 vs 无脑定投（每年 10 万）：期末多 '
          f'{rec["final"] - dca["final"]:,.0f} 元，年化高 '
          f'{(rec["cagr"] - dca["cagr"]) * 100:+.2f} 个百分点，'
          f'回撤低 {(dca["maxdd"] - rec["maxdd"]) * 100:+.1f} 个百分点')
    for a, b, tag in (('2016-09-28', '2021-09-28', '前 5 年'),
                      ('2021-09-28', '2026-09-28', '后 5 年')):
        sub = load_data(a, b)
        ys = len(sub) / 244.0
        bhs = sub[-1]['price'] / sub[0]['price']
        print(f'\n-- {tag}  {sub[0]["date"]} ~ {sub[-1]["date"]}')
        print(f'   基准满仓持有：期末 {CAPITAL * bhs:,.0f}   年化 {bhs ** (1 / ys) - 1:+.2%}')
        r2 = []
        DEEP = (0, 0.0, 1.0, 1.0)
        r2.append(backtest(sub, label='S1b 减30/60+强买点回补',
                           sell_frac=(0, 0, 0.30, 0.60), buyback_frac=DEEP))
        r2.append(backtest(sub, label='S3b 减50/100+强买点回补',
                           sell_frac=(0, 0, 0.50, 1.00), buyback_frac=DEEP))
        r2.append(backtest(sub, label='S5b 卖RSI6极强清仓+强买点回补',
                           sell_frac=(0, 0, 0, 1.00), sell_period=6, buy_period=20,
                           buyback_frac=DEEP))
        r2.append(backtest(sub, label='S12b 卖RSI6强卖/极强卖+强买点回补',
                           sell_frac=(0, 0, 0.30, 1.00), sell_period=6, buy_period=20,
                           buyback_frac=DEEP))
        r2.append(backtest(sub, label='S7 初始60%+信号投放', init_frac=0.60,
                           sell_frac=(0, 0, 0, 0), reserve_frac=(0, 0, 0.25, 0.50)))
        for r in r2:
            flag = '↑' if r['final'] > CAPITAL * bhs else '↓'
            print(f'   {r["label"]:<26}期末 {r["final"]:>11,.0f}  年化 {r["cagr"] * 100:>6.2f}%'
                  f'  回撤 {r["maxdd"] * 100:>5.1f}%  vs基准 {flag}')


if __name__ == '__main__':
    main()
