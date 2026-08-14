# -*- coding: utf-8 -*-
"""
红利指数 回撤口径对比回测：250日滚动 vs 年度
==========================================
数据：H00922 中证红利全收益（2018-01 起）+ 000922 中证红利价格（2018-01 起，与全收益对齐）
方法（与 dividend_stats.py 一致）：
  - 触发：回撤 ≥ 阈值（首次穿越，20日去重）
  - 买入：次日开盘（无开盘用收盘）
  - 持有：20/60 日收益
  - 对比：① 250日滚动回撤（滚动最高点）② 年度回撤（当年内最高点，年初清零）
  - 另统计：每年回撤分布、触发次数、年度回撤的"每年必有一次"验证
"""
import sqlite3
import akshare as ak
import pandas as pd

DB = 'D:/hanako/investment-system/data/lixinger.db'
THRESHOLDS = [0.08, 0.10, 0.12, 0.15, 0.20]
COOLDOWN = 20


def load_csindex(symbol, start='20180101', end='20260813'):
    df = ak.stock_zh_index_hist_csindex(symbol=symbol, start_date=start, end_date=end)
    df['date'] = pd.to_datetime(df['日期'])
    df = df.sort_values('date').reset_index(drop=True)
    # 用收盘价（该接口无开盘列，实际为 NaN）
    df['close'] = df['收盘'].astype(float)
    return df[['date', 'close']]


def dd_250_series(closes):
    """250日滚动回撤序列"""
    dds = []
    for i in range(len(closes)):
        w = closes[max(0, i-249):i+1]
        hi = max(w)
        dds.append((hi - closes[i]) / hi)
    return dds


def dd_annual_series(dates, closes):
    """年度回撤序列：当年内滚动最高点（年初清零）"""
    dds = []
    year_hi = None
    prev_year = None
    for i in range(len(closes)):
        y = pd.Timestamp(dates[i]).year
        if prev_year is None or y != prev_year:
            year_hi = closes[i]  # 年初清零：以当年首个收盘为起点
            prev_year = y
        if closes[i] > year_hi:
            year_hi = closes[i]
        dds.append((year_hi - closes[i]) / year_hi)
    return dds


def detect_events(dds, threshold):
    """回撤≥阈值触发（20日去重）"""
    events = []
    last = -999
    for i, dd in enumerate(dds):
        if dd >= threshold and i - last >= COOLDOWN:
            events.append(i)
            last = i
    return events


def fwd_returns(closes, events, windows=(20, 60)):
    """次日买入（无开盘用收盘），20/60日收益"""
    res = {w: [] for w in windows}
    for i in events:
        if i + 1 >= len(closes):
            continue
        entry = closes[i + 1]
        if not entry or entry <= 0:
            continue
        for w in windows:
            wi = i + 1 + w
            if wi < len(closes):
                res[w].append((closes[wi] / entry - 1) * 100)
    return res


def stat(returns):
    if not returns:
        return None
    n = len(returns)
    wins = sum(1 for r in returns if r > 0)
    s = sorted(returns)
    med = s[n//2] if n % 2 else (s[n//2-1]+s[n//2])/2
    return {'n': n, 'win': round(wins/n*100, 1), 'avg': round(sum(returns)/n, 2), 'med': round(med, 2)}


def random_base(closes, n=2000):
    import random
    random.seed(42)
    valid = list(range(260, len(closes)-60))
    samples = random.sample(valid, min(n, len(valid)))
    res = {20: [], 60: []}
    for i in samples:
        entry = closes[i+1]
        if not entry or entry <= 0:
            continue
        for w in (20, 60):
            wi = i + 1 + w
            if wi < len(closes):
                res[w].append((closes[wi]/entry - 1) * 100)
    return res


def main():
    # 拉数据（各拉一次，防止重复请求）
    h = load_csindex('H00922')
    p = load_csindex('000922')
    print(f'H00922: {len(h)} 条 ({h.date.min().date()} ~ {h.date.max().date()})')
    print(f'000922: {len(p)} 条')

    for tag, df in [('全收益H00922', h), ('价格000922', p)]:
        dates, closes = df['date'].values, df['close'].values
        dd250 = dd_250_series(closes)
        ddan = dd_annual_series(dates, closes)

        print(f'\n{"="*70}\n【{tag}】\n{"="*70}')

        # 年度回撤分布（每年最大回撤）
        years = sorted(set(pd.Timestamp(d).year for d in dates))
        print('\n年度最大回撤（每年）:')
        for y in years:
            idx = [i for i, d in enumerate(dates) if pd.Timestamp(d).year == y]
            if not idx:
                continue
            m = max(ddan[i] for i in idx)
            days_ge10 = sum(1 for i in idx if ddan[i] >= 0.10)
            print(f'  {y}: 最大回撤 {m*100:5.1f}% | 回撤≥10% 天数 {days_ge10:>3}')

        # 随机基准
        base = random_base(closes)
        print(f'\n随机基准: 20日 n={len(base[20])} 胜率={stat(base[20])["win"]}% 中位={stat(base[20])["med"]}% | '
              f'60日 胜率={stat(base[60])["win"]}% 中位={stat(base[60])["med"]}%')

        # 两种口径对比
        print('\n口径 × 阈值 矩阵（20日/60日）:')
        print(f'{"口径":<12}{"阈值":<8}{"触发":<6}{"20日胜率":<10}{"20日中位":<10}{"60日胜率":<10}{"60日中位":<10}')
        for dd_name, dds in [('250日滚动', dd250), ('年度', ddan)]:
            for th in THRESHOLDS:
                ev = detect_events(dds, th)
                r = fwd_returns(closes, ev)
                s20, s60 = stat(r[20]), stat(r[60])
                if not s20:
                    continue
                print(f'{dd_name:<12}{int(th*100):<8}{len(ev):<6}'
                      f'{str(s20["win"])+"%":<10}{str(s20["med"])+"%":<10}'
                      f'{str(s60["win"])+"%":<10}{str(s60["med"])+"%":<10}')
        print()


if __name__ == '__main__':
    main()
