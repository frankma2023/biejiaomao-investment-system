"""
MW 信号回测 · 第三层：分层（SQL 直接计算，避免大批量加载K线）
"""
import sqlite3, json, os, numpy as np
from datetime import datetime, timedelta

DB = 'D:/hanako/investment-system/data/lixinger.db'
OUT = 'D:/hanako/investment-system/config/strategy/mw_signal_L3_stratify.json'

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# ── 用 SQL 一步算出每个 B1 信号的 forward returns ──
# 策略：先给每个 stock 的 kline 编号，再 JOIN
print("SQL计算forward returns...", end=' ', flush=True)
t0 = datetime.now()

# 创建临时表：给每个 (stock_code, date) 一个 row_number
conn.execute("DROP TABLE IF EXISTS _tmp_klines_rn")
conn.execute("""
    CREATE TEMP TABLE _tmp_klines_rn AS
    SELECT stock_code, date, open, close,
           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date) - 1 as rn
    FROM daily_kline
    WHERE date >= '2016-01-01' AND date <= '2026-09-30'
""")
conn.execute("CREATE INDEX IF NOT EXISTS _tmp_rn_idx ON _tmp_klines_rn(stock_code, rn)")

# 关联信号表，找到每个 B1 日对应的 rn，然后取 rn+1 的 open 作为入场价，rn+1+hold 的 close 作为出场价
print("JOIN...", end=' ', flush=True)

rows = conn.execute("""
    SELECT s.tech_score, s.decline_pct, s.h_rs250, s.is_plus,
           CASE WHEN s.b2_date IS NOT NULL AND s.b2_date != '' THEN 1 ELSE 0 END as has_b2,
           k0.rn as b1_rn,
           -- 5天 forward
           k5.close / k1.open - 1 as ret_5d,
           -- 10天
           k10.close / k1.open - 1 as ret_10d,
           -- 20天
           k20.close / k1.open - 1 as ret_20d,
           -- 60天
           k60.close / k1.open - 1 as ret_60d
    FROM mw_signal_daily s
    JOIN _tmp_klines_rn k0 ON k0.stock_code = s.stock_code AND k0.date = s.b1_date
    JOIN _tmp_klines_rn k1 ON k1.stock_code = s.stock_code AND k1.rn = k0.rn + 1  -- B1次日
    LEFT JOIN _tmp_klines_rn k5 ON k5.stock_code = s.stock_code AND k5.rn = k0.rn + 1 + 5
    LEFT JOIN _tmp_klines_rn k10 ON k10.stock_code = s.stock_code AND k10.rn = k0.rn + 1 + 10
    LEFT JOIN _tmp_klines_rn k20 ON k20.stock_code = s.stock_code AND k20.rn = k0.rn + 1 + 20
    LEFT JOIN _tmp_klines_rn k60 ON k60.stock_code = s.stock_code AND k60.rn = k0.rn + 1 + 60
    WHERE s.b1_date >= '2016-01-01' AND s.b1_date != '_sentinel_'
      AND k1.open > 0
""").fetchall()

data = [dict(r) for r in rows]
print(f"{len(data)} 条 ({ (datetime.now()-t0).total_seconds():.0f}s)")

# ── 分层统计 ──
def stats(arr):
    arr = np.array([x for x in arr if x is not None])
    if len(arr) == 0:
        return {'n': 0, 'win_rate': 0, 'median': 0, 'mean': 0}
    return {
        'n': len(arr),
        'win_rate': round((arr > 0).mean() * 100, 1),
        'median': round(np.median(arr) * 100, 2),
        'mean': round(arr.mean() * 100, 2),
    }

print("\n" + "=" * 85)
print("关注分分层 · B1次日开盘 · 10日持有")
print("-" * 85)

tiers = [
    ('极高 ≥80',   80, 999),
    ('高 65~79',   65, 79),
    ('关注 50~64',  50, 64),
    ('一般 35~49',  35, 49),
    ('低 <35',      0, 34),
]

print(f"{'分层':<14} {'N':>8} {'胜率':>8} {'中位':>8} {'均值':>8}  {'20d胜率':>8}  {'60d胜率':>8}")
print("-" * 85)
for label, lo, hi in tiers:
    subset = [r for r in data if lo <= (r['tech_score'] or 0) <= hi]
    rets_10 = [r['ret_10d'] for r in subset if r['ret_10d'] is not None]
    rets_20 = [r['ret_20d'] for r in subset if r['ret_20d'] is not None]
    rets_60 = [r['ret_60d'] for r in subset if r['ret_60d'] is not None]
    s10 = stats(rets_10)
    s20 = stats(rets_20)
    s60 = stats(rets_60)
    print(f"{label:<14} {s10['n']:>8,} {s10['win_rate']:>7.1f}% {s10['median']:>7.2f}% {s10['mean']:>7.2f}% {s20['win_rate']:>7.1f}% {s60['win_rate']:>7.1f}%")

# PLUS
print("\n" + "-" * 85)
print("PLUS vs 非PLUS · 10日持有")
print("-" * 85)
for label, fn in [('PLUS', lambda r: r['is_plus'] == 1), ('非PLUS', lambda r: r['is_plus'] != 1)]:
    subset = [r for r in data if fn(r)]
    rets = [r['ret_10d'] for r in subset if r['ret_10d'] is not None]
    s = stats(rets)
    print(f"  {label:<8} N={s['n']:>7,}  胜率={s['win_rate']:.1f}%  中位={s['median']:.2f}%  均值={s['mean']:.2f}%")

# B2 有/无
print("\n" + "-" * 85)
print("B2 覆盖 · 10日持有")
print("-" * 85)
for label, fn in [('有B2', lambda r: r['has_b2'] == 1), ('无B2', lambda r: r['has_b2'] == 0)]:
    subset = [r for r in data if fn(r)]
    rets = [r['ret_10d'] for r in subset if r['ret_10d'] is not None]
    s = stats(rets)
    print(f"  {label:<8} N={s['n']:>7,}  胜率={s['win_rate']:.1f}%  中位={s['median']:.2f}%  均值={s['mean']:.2f}%")

# 交叉：极高关注分 + B2 + 深回调
print("\n" + "-" * 85)
print("交叉筛选 · 10日持有")
print("-" * 85)
crosses = [
    ('极高 + B2 + 深调(>25%)',
     lambda r: r['tech_score'] >= 80 and r['has_b2'] == 1 and (r['decline_pct'] or 0) >= 25),
    ('高 + B2 + 深调(>25%)',
     lambda r: 65 <= (r['tech_score'] or 0) <= 79 and r['has_b2'] == 1 and (r['decline_pct'] or 0) >= 25),
    ('极高 + B1only',
     lambda r: r['tech_score'] >= 80 and r['has_b2'] == 0),
    ('极高 + 轻调(<20%)',
     lambda r: r['tech_score'] >= 80 and (r['decline_pct'] or 0) < 20),
]
for label, fn in crosses:
    subset = [r for r in data if fn(r)]
    rets = [r['ret_10d'] for r in subset if r['ret_10d'] is not None]
    s = stats(rets)
    print(f"  {label:<30} N={s['n']:>6,}  胜率={s['win_rate']:.1f}%  中位={s['median']:.2f}%  均值={s['mean']:.2f}%")

conn.execute("DROP TABLE IF EXISTS _tmp_klines_rn")
conn.close()
print(f"\n总耗时: {(datetime.now()-t0).total_seconds():.0f}s")
