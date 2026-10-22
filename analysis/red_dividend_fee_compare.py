#!/usr/bin/env python
"""规则 E 在不同渠道下的手续费对比（场内 ETF vs 场外 A/C 类）。

核心问题：规则 E 十年 9 次卖出 + 7 次回补，卖出后的回补段持有期从几个月到几年不等。
场外基金的赎回费按「每份份额的持有期」收，所以这批持有期决定了场外成本。

做法：从 analysis/red_dividend_1m_strategy.py 的真实成交记录重建 FIFO 份额批次，
逐笔算出每个被卖出的批次持有了多少天，再套不同费率表算总成本。

费率假设（都是「典型值」，各券商/各基金不同，须自查）：
  场内 ETF   佣金 0.01% 双边（万1、免最低5元）+ 买卖价差 0.05% 单边
  场外 A 类  申购 0.10%（1.0% 的第三方 1 折）；赎回费两种常见表
  场外 C 类  申购 0；销售服务费 0.40%/年（按持有自然日计提）；赎回 <7日 1.5% / ≥7日 0

用法：
    python analysis/red_dividend_fee_compare.py
"""
import importlib.util
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    'r1m', os.path.join(ROOT, 'analysis', 'red_dividend_1m_strategy.py'))
R1M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R1M)

DEEP = (0.0, 0.0, 1.0, 1.0)
CAP = R1M.CAPITAL


def days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def build_lots():
    """重建 FIFO 份额批次，返回 (事件列表, 期末份额)。"""
    rows = R1M.load_data()
    rec = R1M.backtest(rows, sell_frac=(0, 0, 0.50, 1.00), buyback_frac=DEEP)
    events = [(rows[0]['date'], CAP, rows[0]['price'], '开仓')]
    for d, what, amt, _h, _t, p, _r in rec['log']:
        events.append((d, abs(amt), p, what))
    return events, rec, rows


def ladder_old(dd):      # 老式阶梯：7天~1年 0.5%（返回小数，不是百分数）
    return 0.015 if dd < 7 else 0.005 if dd < 365 else 0.0025 if dd < 730 else 0.0


def ladder_new(dd):      # 新式（如博时中证红利ETF联接）：7天以上免
    return 0.015 if dd < 7 else 0.0


def simulate(sell_fee=ladder_new, buy_fee=0.0, spread=0.0, commission=0.0, svc_rate=0.0):
    """sell_fee(held_days) -> 赎回费率；buy_fee 单边买入费率；spread 单边价差；
    commission 单边佣金（买入卖出都收）；svc_rate 年化销售服务费（按持仓天数累计）。"""
    events, rec, rows = build_lots()
    lots = []          # [[shares, buy_date]]
    total_fee = 0.0
    fee_buy = fee_sell = fee_svc = 0.0
    hold_buckets = {'<7天': 0.0, '7天~1年': 0.0, '1~2年': 0.0, '≥2年': 0.0}
    n_buy = n_sell = 0

    for d, amt, p, kind in events:
        shares = amt / p
        is_buy = kind == '开仓' or kind.startswith('回补')
        if is_buy:
            n_buy += 1
            lots.append([shares, d])
            f = amt * (buy_fee + spread + commission)   # 买入侧：申购费/价差/佣金
            total_fee += f
            fee_buy += f
        else:                                            # 卖出
            n_sell += 1
            left = shares
            while left > 1e-9 and lots:
                lot = lots[0]
                take = min(lot[0], left)
                dd = days(lot[1], d)
                proceeds = take * p
                f = proceeds * (sell_fee(dd) + spread + commission)  # 卖出侧：赎回费/价差/佣金
                total_fee += f
                fee_sell += f
                if svc_rate:
                    f2 = proceeds * svc_rate * dd / 365.0
                    total_fee += f2
                    fee_svc += f2
                k = ('<7天' if dd < 7 else '7天~1年' if dd < 365
                     else '1~2年' if dd < 730 else '≥2年')
                hold_buckets[k] += take
                lot[0] -= take
                left -= take
                if lot[0] <= 1e-9:
                    lots.pop(0)
    return {'fee': total_fee, 'buy': fee_buy, 'sell': fee_sell, 'svc': fee_svc,
            'final': rec['final'], 'net': rec['final'] - total_fee,
            'buckets': hold_buckets, 'n_sell': n_sell, 'n_buy': n_buy}


def main():
    events, rec, rows = build_lots()
    print(f'规则 E · 100 万起点 · {rows[0]["date"]} ~ {rows[-1]["date"]}')
    print(f'期末总资产（未扣费） {rec["final"]:,.0f} 元')
    _p = simulate()
    print(f'成交：{_p["n_buy"]} 次买入 + {_p["n_sell"]} 次卖出')

    print('\n── 被卖出份额的持有期分布（FIFO，自然日）')
    probe = simulate(ladder_old)
    tot = sum(probe['buckets'].values())
    for k, v in probe['buckets'].items():
        print(f'   {k:<10} {v:>10,.0f} 份   占卖出份额 {v / tot * 100:5.1f}%')

    cases = [
        ('场内 ETF（佣金万1 + 价差0.05%）', dict(commission=0.0001, spread=0.0005)),
        ('场外 A 类 · 老式阶梯赎回费', dict(buy_fee=0.0010, sell_fee=ladder_old)),
        ('场外 A 类 · 7天以上免赎回费', dict(buy_fee=0.0010, sell_fee=ladder_new)),
        ('场外 C 类（0.40%/年销售服务费）',
         dict(sell_fee=ladder_new, svc_rate=0.0040)),
        ('场外 A 类（申购不打折 1.0%）', dict(buy_fee=0.0100, sell_fee=ladder_new)),
    ]
    print('\n── 十年总费用对比')
    print(f'   {"渠道":<30}{"总费用":>12}{"占期末资产":>12}{"扣费后期末":>14}{"年化拖累":>10}')
    print('   ' + '-' * 76)
    for name, kw in cases:
        r = simulate(**kw)
        drag = (r['net'] / r['final']) ** 0.1 - 1
        print(f'   {name:<30}{r["fee"]:>12,.0f}{r["fee"] / r["final"] * 100:>11.2f}%'
              f'{r["net"]:>14,.0f}{drag * 100:>9.2f}%')

    print('\n── 明细（场外 A 类老式阶梯 vs 场内）')
    a_old = simulate(buy_fee=0.0010, sell_fee=ladder_old)
    etf = simulate(commission=0.0001, spread=0.0005)
    print(f'   场外A 老式：申购 {a_old["buy"]:>9,.0f} + 赎回 {a_old["sell"]:>9,.0f}')
    print(f'   场外C      ：销售服务费 {simulate(sell_fee=ladder_new, svc_rate=0.004)["svc"]:>9,.0f}')
    print(f'   场内ETF    ：佣金+价差 {etf["buy"]:>9,.0f}')

    print('\n── 场内 ETF 对买卖价差很敏感（价差是大头，佣金几乎可忽略）')
    for sp in (0.0002, 0.0005, 0.0010, 0.0020):
        r = simulate(commission=0.0001, spread=sp)
        print(f'   价差 {sp * 100:.2f}% 单边 → 十年总费用 {r["fee"]:>8,.0f} 元'
              f'（占期末 {r["fee"] / r["final"] * 100:.2f}%，年化拖累 '
              f'{((r["net"] / r["final"]) ** 0.1 - 1) * 100:.2f}%）')

    # ── 「7 天内赎回」在结构上有没有可能：算 RSI20 超卖→超买的间隔下限 ──
    print('\n── 「7 天内赎回」在结构上可能吗')
    _e, _r, rows = build_lots()
    rsi = [x['rsi'][20] for x in rows]
    dts = [x['date'] for x in rows]
    gaps = []
    for i, v in enumerate(rsi):
        if v is None or v >= 35:
            continue
        j = next((k for k in range(i + 1, len(rsi))
                  if rsi[k] is not None and rsi[k] > 73), None)
        if j is not None:
            gaps.append((j - i, dts[i], dts[j]))
    gaps.sort()
    g0 = gaps[0]
    print(f'   RSI20 从 <35 到 >73 的间隔：样本 {len(gaps)} 次')
    print(f'   最短 {g0[0]} 个交易日 = {days(g0[1], g0[2])} 个自然日'
          f'（{g0[1]} → {g0[2]}）')
    print(f'   中位 {gaps[len(gaps) // 2][0]} 个交易日，最长 {gaps[-1][0]} 个交易日')
    cal = sorted(days(a, b) for _n, a, b in gaps)
    print(f'   按自然日看：最短 {cal[0]} 天，中位 {cal[len(cal) // 2]} 天')
    print('   → 7 天惩罚档（1.5%）结构上碰不到；但注意最短那次正好是 2024 年'
          '「9·24」那种 V 型行情，离 7 天线只有 3 天余量。')


if __name__ == '__main__':
    main()
