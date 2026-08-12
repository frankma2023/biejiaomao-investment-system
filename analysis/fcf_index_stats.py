# -*- coding: utf-8 -*-
"""
980092 国证自由现金流指数 · 历史回撤规律研究
==========================================
目的：搞清楚 250日回撤 ≥ 不同阈值后的买入胜率/收益规律（对标红利指数的 dd250_15 胜率65.8%）

方法（与 dividend_stats.py 一致）：
  - 250日回撤 = (250日滚动最高收盘 - 当前收盘) / 250日滚动最高
  - 触发日：回撤首次 ≥ 阈值（20日去重：触发后 20 日内不再重复触发）
  - 买入：次日开盘
  - 持有：5/20/60 日收益（开盘口径，与红利回测一致）
  - 对照：随机基准（随机选交易日买入的收益分布）
"""
import sqlite3
import random
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
CODE = '980092'
THRESHOLDS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35]
WINDOWS = [5, 20, 60]
COOLDOWN = 20  # 触发后冷却天数（去重）


def load_kline(conn):
    rows = conn.execute("""
        SELECT date, open, close FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' ORDER BY date
    """, (CODE,)).fetchall()
    return [dict(r) for r in rows]


def detect_drawdown_events(klines, threshold):
    """250日回撤 ≥ 阈值的触发点（20日去重）"""
    closes = [k['close'] for k in klines]
    events = []
    last_trigger = -999
    for i in range(250, len(klines)):
        window = closes[i-250:i+1]
        hi = max(window)
        dd = (hi - closes[i]) / hi
        if dd >= threshold and i - last_trigger >= COOLDOWN:
            events.append(i)
            last_trigger = i
    return events


def fwd_returns(klines, trigger_idxs):
    """次日开盘买入，5/20/60 日收益"""
    results = {w: [] for w in WINDOWS}
    for ti in trigger_idxs:
        if ti + 1 >= len(klines):
            continue
        entry = klines[ti + 1]['open']
        if not entry or entry <= 0:
            continue
        for w in WINDOWS:
            wi = ti + 1 + w
            if wi < len(klines) and klines[wi]['open']:
                results[w].append((klines[wi]['open'] / entry - 1) * 100)
    return results


def random_baseline(klines, n_samples=5000):
    """随机基准：随机交易日买入的收益分布"""
    valid = range(250, len(klines) - 60 - 1)
    samples = random.sample(list(valid), min(n_samples, len(valid)))
    results = {w: [] for w in WINDOWS}
    for i in samples:
        entry = klines[i]['open']
        if not entry or entry <= 0:
            continue
        for w in WINDOWS:
            wi = i + w
            if wi < len(klines) and klines[wi]['open']:
                results[w].append((klines[wi]['open'] / entry - 1) * 100)
    return results


def stat(returns):
    if not returns:
        return {'n': 0}
    n = len(returns)
    wins = sum(1 for r in returns if r > 0)
    sorted_r = sorted(returns)
    median = sorted_r[n // 2] if n % 2 else (sorted_r[n//2-1] + sorted_r[n//2]) / 2
    return {
        'n': n, 'win_rate': round(wins / n * 100, 1),
        'avg': round(sum(returns) / n, 2), 'median': round(median, 2),
        'p25': round(sorted_r[n // 4], 2), 'p75': round(sorted_r[3 * n // 4], 2),
    }


def main():
    random.seed(42)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    klines = load_kline(conn)
    conn.close()
    print(f"980092 数据: {len(klines)} 条 ({klines[0]['date']} ~ {klines[-1]['date']})")

    # 随机基准
    base = random_baseline(klines)
    print("\n=== 随机基准（任意交易日买入）===")
    for w in WINDOWS:
        print(f"  {w}日: {stat(base[w])}")

    # 各阈值
    print("\n=== 250日回撤阈值矩阵 ===")
    for th in THRESHOLDS:
        events = detect_drawdown_events(klines, th)
        returns = fwd_returns(klines, events)
        print(f"\n【回撤≥{int(th*100)}%】触发 {len(events)} 次")
        for w in WINDOWS:
            print(f"  {w}日: {stat(returns[w])}")
        # 触发日期（最近10个）
        if events:
            recent = [klines[i]['date'] for i in events[-10:]]
            print(f"  最近触发: {recent}")

    # 回撤分布概览
    closes = [k['close'] for k in klines]
    dds = []
    for i in range(250, len(klines)):
        hi = max(closes[i-250:i+1])
        dds.append((klines[i]['date'], (hi - closes[i]) / hi * 100))
    deep = [d for d in dds if d[1] >= 15]
    print(f"\n=== 回撤分布 ===")
    print(f"历史最大回撤: {max(d[1] for d in dds):.1f}% ({[d[0] for d in dds if d[1]==max(x[1] for x in dds)]})")
    print(f"回撤≥10% 天数: {len([d for d in dds if d[1]>=10])} / {len(dds)}")
    print(f"回撤≥15% 天数: {len(deep)} / {len(dds)}")
    print(f"回撤≥20% 天数: {len([d for d in dds if d[1]>=20])} / {len(dds)}")
    print(f"回撤≥25% 天数: {len([d for d in dds if d[1]>=25])} / {len(dds)}")
    print(f"回撤≥30% 天数: {len([d for d in dds if d[1]>=30])} / {len(dds)}")
    # 回撤≥15% 的连续区间（近似段数）
    seg = 0
    in_seg = False
    for d in dds:
        if d[1] >= 15 and not in_seg:
            seg += 1
            in_seg = True
        elif d[1] < 15:
            in_seg = False
    print(f"回撤≥15% 的独立区间段数: {seg}")


if __name__ == '__main__':
    main()
