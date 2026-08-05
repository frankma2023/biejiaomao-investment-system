"""
T2 v3: 增加信号后每10日累计涨跌幅（基准=第2日收盘价）
观察点: d2之后第 10/20/30/40/50/60/70/80/90 个交易日收盘 vs d2收盘
"""
import sqlite3
import pandas as pd
import numpy as np
import json
from volume_reversal import load_all_klines, detect_all_levels

DB = 'D:/hanako/investment-system/data/lixinger.db'
WINDOWS = [20, 60, 90]
DECADE_POINTS = [10, 20, 30, 40, 50, 60, 70, 80, 90]  # 每10日观察点


def load_series():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT stock_code, date, open, close, high, low FROM daily_kline
        WHERE date >= '2023-08-01' ORDER BY stock_code, date
    """).fetchall()
    conn.close()
    df = pd.DataFrame([dict(r) for r in rows])
    series = {}
    for code, sub in df.groupby('stock_code'):
        series[code] = (
            sub['date'].values,
            sub['open'].values.astype(float),
            sub['close'].values.astype(float),
            sub['high'].values.astype(float),
            sub['low'].values.astype(float),
        )
    return series


def compute_event_stats(series, events):
    stats_list = []
    for ev in events:
        code = ev['stock_code']
        if code not in series:
            continue
        dates, opens, closes, highs, lows = series[code]

        # 第3日开盘买入（原逻辑保留）
        buy_idx = np.searchsorted(dates, ev['buy_date'])
        if buy_idx >= len(dates) or dates[buy_idx] != ev['buy_date']:
            continue
        entry = opens[buy_idx]
        if not entry or entry <= 0:
            continue

        # 第2日收盘（基准）
        d2_idx = np.searchsorted(dates, ev['d2_date'])
        if d2_idx >= len(dates) or dates[d2_idx] != ev['d2_date']:
            continue
        d2_close = closes[d2_idx]

        row = {
            'stock_code': code, 'd1_date': ev['d1_date'],
            'd2_date': ev['d2_date'], 'buy_date': ev['buy_date'],
            'entry': round(entry, 2), 'd2_close': round(d2_close, 2),
        }

        # 原窗口收益（第3日开盘买入）
        end = min(len(closes), buy_idx + 1 + 90)
        fwd_closes = closes[buy_idx+1:end]
        n_fwd = len(fwd_closes)
        for w in WINDOWS:
            if n_fwd >= w:
                row[f'ret_{w}'] = round((fwd_closes[w-1] / entry - 1) * 100, 2)
            else:
                row[f'ret_{w}'] = None
        if n_fwd > 0:
            peak = max(entry, fwd_closes.max())
            row['max_dd'] = round((peak - fwd_closes.min()) / peak * 100, 1) if peak > 0 else 0
            row['max_up'] = round((fwd_closes.max() / entry - 1) * 100, 1) if entry > 0 else None
        else:
            row['max_dd'] = None
            row['max_up'] = None

        # 新增: 每10日累计涨跌幅（基准=第2日收盘，观察点=第2日之后第N个交易日）
        for n in DECADE_POINTS:
            obs_idx = d2_idx + n
            if obs_idx < len(closes):
                row[f'd10_{n}'] = round((closes[obs_idx] / d2_close - 1) * 100, 2)
            else:
                row[f'd10_{n}'] = None

        stats_list.append(row)
    return stats_list


def main():
    df = load_all_klines()
    events_by_level = detect_all_levels(df)
    series = load_series()
    print("序列加载完成")

    all_stats = {}
    for label, events in events_by_level.items():
        stats = compute_event_stats(series, events)
        all_stats[label] = stats
        n90 = sum(1 for s in stats if s.get('ret_90') is not None)
        print(f"{label}: {len(events)} 事件, {n90} 有90日数据")

    with open('D:/hanako/investment-system/analysis/volume_reversal_stats.json', 'w', encoding='utf-8') as f:
        json.dump({'all_stats': all_stats}, f, ensure_ascii=False, default=str)
    print("已保存（含每10日统计）")


if __name__ == '__main__':
    main()
