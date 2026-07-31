"""
MW 信号回测 · 第二层：单信号胜率与收益
进场方式: B1次日开盘 / B1收盘 / B2次日开盘 / B2收盘
持有窗口: 5/10/20/60 天
"""
import sqlite3, json, os, sys
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np

DB = 'D:/hanako/investment-system/data/lixinger.db'
OUT = 'D:/hanako/investment-system/config/strategy/mw_signal_L2_returns.json'

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# ── 加载信号 ──
print("加载信号...", end=' ', flush=True)
signals = conn.execute("""
    SELECT stock_code, b1_date, b2_date, tech_score, decline_pct, h_rs250,
           is_plus, score, b1_return_pct, b2_return_pct
    FROM mw_signal_daily
    WHERE b1_date >= '2016-01-01' AND b1_date != '_sentinel_'
    ORDER BY b1_date
""").fetchall()
signals = [dict(r) for r in signals]
print(f"{len(signals)} 条")

# ── 批量加载 K 线（只取需要的：信号日后 60 天）──
print("加载K线(分批)...", end=' ', flush=True)

# 收集所有需要的 (code, date_from) 对
needed = defaultdict(set)  # code -> set of dates
for sig in signals:
    needed[sig['stock_code']].add(sig['b1_date'])
    if sig.get('b2_date') and sig['b2_date'] != '':
        needed[sig['stock_code']].add(sig['b2_date'])

# 每个股票只取最早和最晚需要日期之间的 K 线
klines_all = {}
n_loaded = 0
for code, dates_set in needed.items():
    dlist = sorted(dates_set)
    min_d = dlist[0]
    max_d = (datetime.strptime(dlist[-1], '%Y-%m-%d') + timedelta(days=65)).strftime('%Y-%m-%d')  # +65 天确保持有窗口
    rows = conn.execute(
        "SELECT date, open, high, low, close, volume FROM daily_kline WHERE stock_code=? AND date >= ? AND date <= ? ORDER BY date",
        (code, min_d, max_d)
    ).fetchall()
    if rows:
        klines_all[code] = [dict(r) for r in rows]
        n_loaded += 1
    if n_loaded % 1000 == 0:
        print(f'{n_loaded}...', end=' ', flush=True)
print(f"{n_loaded} 只")

# ── 回测核心函数 ──
def find_klines_after(code, date_str, min_days=60):
    """找到 date_str 之后（含）的 K 线，至少需要 min_days 条"""
    all_k = klines_all.get(code, [])
    # 二分查找起始位置
    dates = [k['date'] for k in all_k]
    try:
        idx = dates.index(date_str)
    except ValueError:
        # 找最近的
        for i, d in enumerate(dates):
            if d >= date_str:
                idx = i
                break
        else:
            return None, None
    remaining = all_k[idx:]
    if len(remaining) < min_days:
        return None, None
    return idx, remaining

def compute_forward_return(klines_from_entry, hold_days):
    """从入场日开始算持有期收益。入场价用次日开盘（可执行）。"""
    if len(klines_from_entry) < hold_days + 1:
        return None, 0
    entry_price = klines_from_entry[0]['open']  # 入场日开盘
    exit_price = klines_from_entry[hold_days - 1]['close']  # 持有 hold_days 后收盘
    if entry_price <= 0:
        return None, 0
    ret = (exit_price - entry_price) / entry_price
    return ret, 1

# ── 批量回测 ──
HOLD_PERIODS = [5, 10, 20, 60]
# 存储: results[entry_type][hold_days] = [returns_list]
entry_types = ['B1_next_open', 'B1_close', 'B2_next_open', 'B2_close']

results = {et: {h: [] for h in HOLD_PERIODS} for et in entry_types}
stats = {et: {h: {'win_rate': 0, 'median': 0, 'mean': 0, 'n': 0, 'max_dd': 0, 'sharpe': 0} for h in HOLD_PERIODS} for et in entry_types}

n = len(signals)
t0 = datetime.now()
for i, sig in enumerate(signals):
    if i % 5000 == 0:
        elapsed = (datetime.now() - t0).total_seconds()
        eta = elapsed / (i+1) * (n-i-1) if i > 0 else 0
        print(f'  进度: {i}/{n} ({elapsed:.0f}s, ETA {eta:.0f}s)', flush=True)
    
    code = sig['stock_code']
    
    # ── B1 入场 ──
    b1_date = sig['b1_date']
    b1_idx, b1_klines = find_klines_after(code, b1_date)
    if b1_klines:
        # B1 次日开盘
        next_open_klines = b1_klines[1:]  # 跳过 B1 日，从次日开始
        if len(next_open_klines) >= 60:
            for hd in HOLD_PERIODS:
                ret_tuple = compute_forward_return(next_open_klines, hd)
                if ret_tuple[0] is not None:
                    results['B1_next_open'][hd].append(ret_tuple[0])
        
        # B1 收盘（不可执行，仅对比用）
        b1_close = b1_klines[0]['close']
        if b1_close > 0:
            for hd in HOLD_PERIODS:
                if len(b1_klines) >= hd:
                    exit_close = b1_klines[hd - 1]['close']
                    ret = (exit_close - b1_close) / b1_close
                    results['B1_close'][hd].append(ret)
    
    # ── B2 入场 ──
    b2_date = sig.get('b2_date')
    if b2_date and b2_date != '':
        b2_idx, b2_klines = find_klines_after(code, b2_date)
        if b2_klines:
            # B2 次日开盘
            next_open_klines = b2_klines[1:]
            if len(next_open_klines) >= 60:
                for hd in HOLD_PERIODS:
                    ret_tuple = compute_forward_return(next_open_klines, hd)
                    if ret_tuple[0] is not None:
                        results['B2_next_open'][hd].append(ret_tuple[0])
            
            # B2 收盘
            b2_close = b2_klines[0]['close']
            if b2_close > 0:
                for hd in HOLD_PERIODS:
                    if len(b2_klines) >= hd:
                        exit_close = b2_klines[hd - 1]['close']
                        ret = (exit_close - b2_close) / b2_close
                        results['B2_close'][hd].append(ret)

# ── 计算统计 ──
for et in entry_types:
    for hd in HOLD_PERIODS:
        rets = results[et][hd]
        if not rets:
            continue
        arr = np.array(rets)
        win_rate = (arr > 0).mean() * 100
        median = np.median(arr) * 100
        mean = arr.mean() * 100
        n = len(arr)
        # 最大亏损
        max_dd = arr.min() * 100
        # 简化夏普（年化）
        if arr.std() > 0:
            sharpe = arr.mean() / arr.std() * np.sqrt(252 / hd) if hd > 0 else 0
        else:
            sharpe = 0
        stats[et][hd] = {
            'win_rate': round(win_rate, 1),
            'median': round(median, 2),
            'mean': round(mean, 2),
            'n': n,
            'max_dd': round(max_dd, 2),
            'sharpe': round(sharpe, 2),
        }

# ── 输出 ──
print("\n" + "=" * 90)
print("MW 单信号收益回测 · 四种入场方式")
print("=" * 90)

entry_labels = {
    'B1_close': 'B1 收盘(不可执行)',
    'B1_next_open': 'B1 次日开盘 ✅',
    'B2_close': 'B2 收盘(不可执行)',
    'B2_next_open': 'B2 次日开盘 ✅',
}

for et in entry_types:
    print(f"\n── {entry_labels[et]} ──")
    print(f"{'持有':>6} {'N':>8} {'胜率':>8} {'中位':>8} {'均值':>8} {'最大亏损':>8} {'夏普':>6}")
    print("-" * 56)
    for hd in HOLD_PERIODS:
        s = stats[et][hd]
        if s['n'] == 0:
            continue
        print(f"{hd:>4}天 {s['n']:>8,} {s['win_rate']:>7.1f}% {s['median']:>7.2f}% {s['mean']:>7.2f}% {s['max_dd']:>7.2f}% {s['sharpe']:>6.2f}")

# ── 保存 ──
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump({'stats': stats, 'hold_periods': HOLD_PERIODS}, f, ensure_ascii=False, indent=2)
print(f"\nJSON → {OUT}")

conn.close()
print(f"\n总耗时: {(datetime.now()-t0).total_seconds():.0f}s")
