"""
MW 信号回测 · 第四层：多因子交叉 + 持仓模拟
"""
import sqlite3, json, os, numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
OUT = 'D:/hanako/investment-system/config/strategy/mw_signal_L4_portfolio.json'

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
t0 = datetime.now()

# ── SQL 一步算出 forward returns ──
print("计算forward returns...", end=' ', flush=True)
conn.execute("DROP TABLE IF EXISTS _tmp_klines_rn")
conn.execute("""
    CREATE TEMP TABLE _tmp_klines_rn AS
    SELECT stock_code, date, open, close,
           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date) - 1 as rn
    FROM daily_kline WHERE date >= '2016-01-01' AND date <= '2026-09-30'
""")
conn.execute("CREATE INDEX IF NOT EXISTS _tmp_rn_idx ON _tmp_klines_rn(stock_code, rn)")

rows = conn.execute("""
    SELECT s.stock_code, s.b1_date, s.tech_score, s.decline_pct, s.h_rs250, s.is_plus,
           s.ind_rs20, s.ind_rs250,
           CASE WHEN s.b2_date IS NOT NULL AND s.b2_date != '' THEN 1 ELSE 0 END as has_b2,
           k10.close / k1.open - 1 as ret_10d,
           k20.close / k1.open - 1 as ret_20d,
           k5.close / k1.open - 1 as ret_5d
    FROM mw_signal_daily s
    JOIN _tmp_klines_rn k0 ON k0.stock_code = s.stock_code AND k0.date = s.b1_date
    JOIN _tmp_klines_rn k1 ON k1.stock_code = s.stock_code AND k1.rn = k0.rn + 1
    LEFT JOIN _tmp_klines_rn k5 ON k5.stock_code = s.stock_code AND k5.rn = k0.rn + 1 + 5
    LEFT JOIN _tmp_klines_rn k10 ON k10.stock_code = s.stock_code AND k10.rn = k0.rn + 1 + 10
    LEFT JOIN _tmp_klines_rn k20 ON k20.stock_code = s.stock_code AND k20.rn = k0.rn + 1 + 20
    WHERE s.b1_date >= '2016-01-01' AND s.b1_date != '_sentinel_' AND k1.open > 0
""").fetchall()
data = [dict(r) for r in rows]
print(f"{len(data)} 条 ({(datetime.now()-t0).total_seconds():.0f}s)")

def stats(arr):
    arr = np.array([x for x in arr if x is not None])
    if len(arr) == 0:
        return {'n':0,'win_rate':0,'median':0,'mean':0,'sharpe':0}
    return {
        'n': len(arr),
        'win_rate': round((arr>0).mean()*100,1),
        'median': round(np.median(arr)*100,2),
        'mean': round(arr.mean()*100,2),
        'sharpe': round(arr.mean()/arr.std()*np.sqrt(252/10),2) if arr.std()>0 else 0
    }

# ── 1. 多因子矩阵（B2 + 关注分 + 回调 + RS + 行业RS） ──
print("\n" + "=" * 90)
print("多因子交叉矩阵 · 10日持有 · B1次日开盘")
print("=" * 90)

combos = [
    # B2 + 关注分
    ("有B2", lambda r: r['has_b2']==1),
    ("有B2 + 极高", lambda r: r['has_b2']==1 and (r['tech_score'] or 0)>=80),
    ("有B2 + 高", lambda r: r['has_b2']==1 and 65<=(r['tech_score'] or 0)<=79),
    ("有B2 + 关注", lambda r: r['has_b2']==1 and 50<=(r['tech_score'] or 0)<=64),
    # B2 + 回调深度
    ("有B2 + 深调>35%", lambda r: r['has_b2']==1 and (r['decline_pct'] or 0)>=35),
    ("有B2 + 中调25~35%", lambda r: r['has_b2']==1 and 25<=(r['decline_pct'] or 0)<35),
    ("有B2 + 浅调<20%", lambda r: r['has_b2']==1 and (r['decline_pct'] or 0)<20),
    # B2 + RS
    ("有B2 + h_rs250≥90", lambda r: r['has_b2']==1 and (r['h_rs250'] or 0)>=90),
    ("有B2 + h_rs250 80~89", lambda r: r['has_b2']==1 and 80<=(r['h_rs250'] or 0)<=89),
    ("有B2 + h_rs250<70", lambda r: r['has_b2']==1 and (r['h_rs250'] or 0)<70),
    # B2 + 行业RS
    ("有B2 + 行业rs20≥90", lambda r: r['has_b2']==1 and (r['ind_rs20'] or 0)>=90),
    ("有B2 + 行业rs20 80~89", lambda r: r['has_b2']==1 and 80<=(r['ind_rs20'] or 0)<=89),
    # 最强组合
    ("B2+极高+深调+高RS", lambda r: r['has_b2']==1 and (r['tech_score'] or 0)>=80 and (r['decline_pct'] or 0)>=25 and (r['h_rs250'] or 0)>=85),
    ("PLUS", lambda r: r['is_plus']==1),
]

print(f"{'筛选条件':<28} {'N':>6} {'胜率':>7} {'中位':>7} {'均值':>7} {'夏普':>6}")
print("-" * 65)
results_combos = {}
for label, fn in combos:
    subset = [r for r in data if fn(r)]
    rets = [r['ret_10d'] for r in subset if r['ret_10d'] is not None]
    s = stats(rets)
    results_combos[label] = s
    print(f"{label:<28} {s['n']:>6,} {s['win_rate']:>6.1f}% {s['median']:>6.2f}% {s['mean']:>6.2f}% {s['sharpe']:>6.2f}")

# ── 2. 持仓模拟 ──
print("\n" + "=" * 90)
print("持仓模拟 · 固定持有10日 · 等权重 · 滚动10年")
print("=" * 90)

# 获取所有 B1 日期
dates_all = sorted(set(r['b1_date'] for r in data))
print(f"交易日数: {len(dates_all)} (B1信号日)")

def simulate_portfolio(data, date_list, filter_fn, top_n=5, hold_days=10, capital=1e6):
    """日频持仓模拟 · 等权重 · 每日合并所有活跃持仓收益"""
    # 索引：按日历日组织信号
    from collections import defaultdict
    
    # 构建 (entry_date, stock_code, ret) 列表
    trades = []
    for d in date_list:
        day_signals = [r for r in data if r['b1_date'] == d and filter_fn(r)]
        if not day_signals:
            continue
        day_signals.sort(key=lambda r: r['tech_score'] or 0, reverse=True)
        for sig in day_signals[:top_n]:
            if sig['ret_10d'] is not None:
                trades.append((d, sig['stock_code'], sig['ret_10d']))
    
    if not trades:
        return {'n_positions': 0, 'total_return': 0, 'annual_return': 0, 'max_drawdown': 0, 'sharpe': 0}
    
    # 按日期组织：entry_date -> [(ret, exit_date)]
    # 持有期 10 天 → exit_date = entry_date + 10 trading days
    # 简化：用日历日 offset
    all_dates = sorted(set(d for d, _, _ in trades))
    
    # 获取完整日历日序列
    conn2 = sqlite3.connect(DB)
    cal_dates = [r[0] for r in conn2.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date >= ? AND date <= '2026-07-31' ORDER BY date",
        (all_dates[0],)
    ).fetchall()]
    conn2.close()
    
    # 日期索引映射
    date2idx = {d: i for i, d in enumerate(cal_dates)}
    
    # 按 entry 日期分组的持仓：每个 entry_date -> [(return, exit_idx)]
    positions_by_entry = defaultdict(list)
    for entry_d, code, ret in trades:
        entry_idx = date2idx.get(entry_d)
        if entry_idx is None:
            continue
        exit_idx = entry_idx + hold_days
        if exit_idx >= len(cal_dates):
            continue
        positions_by_entry[entry_idx].append((ret, exit_idx))
    
    # 日频模拟
    daily_equity = [1.0]
    active_positions = defaultdict(list)  # exit_idx -> [ret, ret, ...]
    daily_rets = []
    
    for day_idx in range(len(cal_dates)):
        # 新开仓
        if day_idx in positions_by_entry:
            for ret, exit_idx in positions_by_entry[day_idx]:
                active_positions[exit_idx].append(ret / top_n)
        
        # 计算当日收益 = 所有活跃持仓的收益（线性摊销到每天）
        day_ret = 0.0
        active_count = 0
        positions_to_remove = []
        for exit_idx, rets in active_positions.items():
            if day_idx >= exit_idx:
                positions_to_remove.append(exit_idx)
            else:
                # 每天摊销 1/hold_days 的收益
                for ret in rets:
                    day_ret += ret / hold_days
                    active_count += 1
        
        for exit_idx in positions_to_remove:
            del active_positions[exit_idx]
        
        if active_count > 0:
            daily_equity.append(daily_equity[-1] * (1 + day_ret))
            daily_rets.append(day_ret)
        else:
            daily_equity.append(daily_equity[-1])
            daily_rets.append(0.0)
    
    equity = np.array(daily_equity)
    total_ret = (equity[-1] / equity[0] - 1) * 100
    years = len(date_list) / 252
    annual_ret = ((equity[-1] / equity[0]) ** (1/years) - 1) * 100 if years > 0 else 0
    
    # 最大回撤
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak * 100
    max_dd = dd.min()
    
    # 夏普
    daily_rets = np.diff(equity) / equity[:-1]
    sharpe = np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252) if np.std(daily_rets) > 0 else 0
    
    return {
        'n_positions': len(trades),
        'total_return': round(total_ret, 1),
        'annual_return': round(annual_ret, 1),
        'max_drawdown': round(max_dd, 1),
        'sharpe': round(sharpe, 2),
    }

# ── 2. 逐年收益分解 ──
print("\n" + "=" * 90)
print("持仓模拟 · 逐年收益 · 有B2+top5 · 10日持有")
print("=" * 90)

print(f"{'年份':<8} {'信号':>6} {'胜率':>7} {'中位':>7} {'均值':>7} {'累计':>9}")
print("-" * 50)

# 按年统计有B2信号的收益
b2_data = [r for r in data if r['has_b2'] == 1]
cumulative = 1.0
yearly_stats = []
for year in range(2016, 2027):
    yr_data = [r for r in b2_data if r['b1_date'] >= f'{year}-01-01' and r['b1_date'] < f'{year+1}-01-01']
    if not yr_data:
        continue
    # 每天取 top 5
    yr_by_day = defaultdict(list)
    for r in yr_data:
        yr_by_day[r['b1_date']].append(r['ret_10d'])
    
    daily_rets = []
    for d, rets in yr_by_day.items():
        valid = [r for r in rets if r is not None]
        if not valid:
            continue
        valid.sort(reverse=True)
        top = rets[:5]
        valid = [r for r in top if r is not None]
        if valid:
            daily_rets.append(sum(valid) / 5)  # 等权
    
    if not daily_rets:
        continue
    
    arr = np.array(daily_rets)
    wr = (arr > 0).mean() * 100
    med = np.median(arr) * 100
    mn = arr.mean() * 100
    n = len(daily_rets)
    
    yr_avg = np.mean(daily_rets) * 100  # 年均每笔收益
    
    yearly_stats.append({'year':year, 'n':n, 'win_rate':wr, 'median':med, 'mean':mn, 'avg_ret':yr_avg})
    print(f"{year:<8} {n:>6} {wr:>6.1f}% {med:>6.2f}% {mn:>6.2f}% {yr_avg:>8.1f}%")

print("-" * 50)
print(f"{'年均':<8} {'':>6} {'':>7} {'':>7} {'':>7} {np.mean([s['avg_ret'] for s in yearly_stats]):>8.1f}%")

# ── 保存 ──
out = {
    'combo_matrix': results_combos,
    'yearly_portfolio': yearly_stats,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"\nJSON → {OUT}")

conn.execute("DROP TABLE IF EXISTS _tmp_klines_rn")
conn.close()
print(f"总耗时: {(datetime.now()-t0).total_seconds():.0f}s")
