"""
B1 入场后的真实路径分析：轻仓→等 B2→决定加仓或退出
"""
import sqlite3, numpy as np
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# 加载 B1 信号 + B2 状态
print("加载...", end=' ', flush=True)
rows = conn.execute("""
    SELECT stock_code, b1_date, b2_date, tech_score, decline_pct, 
           h_rs250, is_plus, b1_return_pct, b1_vol_ratio, ind_rs20
    FROM mw_signal_daily
    WHERE b1_date >= '2016-01-01' AND b1_date != '_sentinel_'
""").fetchall()
signals = [dict(r) for r in rows]
print(f"{len(signals)} 条")

# ── 构建临时 K 线表 ──
conn.execute("DROP TABLE IF EXISTS _tmp_k")
conn.execute("""
    CREATE TEMP TABLE _tmp_k AS
    SELECT stock_code, date, open, close,
           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date) - 1 as rn
    FROM daily_kline WHERE date >= '2016-01-01' AND date <= '2026-09-30'
""")
conn.execute("CREATE INDEX IF NOT EXISTS _tmp_k_idx ON _tmp_k(stock_code, rn)")

# ── JOIN 获取每个 B1 的 forward returns ──
data = conn.execute("""
    SELECT s.tech_score, s.decline_pct, s.h_rs250, s.b1_return_pct, s.b1_vol_ratio,
           s.ind_rs20,
           CASE WHEN s.b2_date IS NOT NULL AND s.b2_date != '' THEN 1 ELSE 0 END as has_b2,
           k10.close / k1.open - 1 as ret_10d,
           k20.close / k1.open - 1 as ret_20d,
           k30.close / k1.open - 1 as ret_30d
    FROM mw_signal_daily s
    JOIN _tmp_k k0 ON k0.stock_code = s.stock_code AND k0.date = s.b1_date
    JOIN _tmp_k k1 ON k1.stock_code = s.stock_code AND k1.rn = k0.rn + 1
    LEFT JOIN _tmp_k k10 ON k10.stock_code = s.stock_code AND k10.rn = k0.rn + 1 + 10
    LEFT JOIN _tmp_k k20 ON k20.stock_code = s.stock_code AND k20.rn = k0.rn + 1 + 20
    LEFT JOIN _tmp_k k30 ON k30.stock_code = s.stock_code AND k30.rn = k0.rn + 1 + 30
    WHERE s.b1_date >= '2016-01-01' AND s.b1_date != '_sentinel_' AND k1.open > 0
""").fetchall()
data = [dict(r) for r in data]
conn.execute("DROP TABLE IF EXISTS _tmp_k")

def s(arr):
    arr = np.array([x for x in arr if x is not None])
    if len(arr) == 0: return {'n':0,'wr':0,'med':0,'mean':0}
    return {'n':len(arr), 'wr':round((arr>0).mean()*100,1), 
            'med':round(np.median(arr)*100,2), 'mean':round(arr.mean()*100,2)}

# ═══════════════════════════════════════
# 核心分析：站在 B1 当天，你能知道什么？
# ═══════════════════════════════════════

print("\n" + "=" * 80)
print("B1 入场后的真实路径")
print("=" * 80)

# 1. B1 信号中，B2 出现的概率
b2_rate = sum(1 for r in data if r['has_b2']) / len(data) * 100
print(f"\nB1 信号总数: {len(data):,}")
print(f"后来出现 B2: {b2_rate:.0f}%")
print(f"从未出现 B2: {100-b2_rate:.0f}%")

# 2. 如果买入所有 B1 → 真实收益
print("\n" + "-" * 60)
print("策略A: 每个 B1 都买（无法预知 B2）")
b1_all_10 = s([r['ret_10d'] for r in data])
b1_all_20 = s([r['ret_20d'] for r in data])
print(f"  10日: 胜率 {b1_all_10['wr']}%  中位 {b1_all_10['med']}%  均值 {b1_all_10['mean']}%")
print(f"  20日: 胜率 {b1_all_20['wr']}%  中位 {b1_all_20['med']}%  均值 {b1_all_20['mean']}%")

# 3. 事后看：B1 的两种命运
print("\n" + "-" * 60)
print("事后拆分: B1 → 后来有B2  vs  B1 → 后来无B2")
b1_to_b2 = [r for r in data if r['has_b2']]
b1_no_b2 = [r for r in data if not r['has_b2']]
for label, subset in [('→有B2', b1_to_b2), ('→无B2', b1_no_b2)]:
    st = s([r['ret_10d'] for r in subset])
    print(f"  {label}: N={st['n']:,}  胜率 {st['wr']}%  中位 {st['med']}%  均值 {st['mean']}%")

# 4. 关键问题：B1 当天能否预测 B2？
print("\n" + "-" * 60)
print("B1 当天可观测因子 vs B2 出现概率")
factors = [
    ('B1涨幅 ≥5%', lambda r: (r['b1_return_pct'] or 0) >= 5),
    ('B1涨幅 <3%', lambda r: (r['b1_return_pct'] or 0) < 3),
    ('B1量比 ≥2.0', lambda r: (r['b1_vol_ratio'] or 0) >= 2.0),
    ('B1量比 <1.5', lambda r: (r['b1_vol_ratio'] or 0) < 1.5),
    ('极高关注 ≥80', lambda r: (r['tech_score'] or 0) >= 80),
    ('低关注 <35', lambda r: (r['tech_score'] or 0) < 35),
    ('深调 ≥35%', lambda r: (r['decline_pct'] or 0) >= 35),
    ('浅调 <20%', lambda r: (r['decline_pct'] or 0) < 20),
    ('行业rs20≥90', lambda r: (r['ind_rs20'] or 0) >= 90),
    ('h_rs250≥90', lambda r: (r['h_rs250'] or 0) >= 90),
]
print(f"{'B1条件':<20} {'N':>6} {'→B2率':>8} {'10d胜率':>8} {'20d胜率':>8}")
print("-" * 56)
for label, fn in factors:
    subset = [r for r in data if fn(r)]
    if not subset: continue
    has_b2_rate = sum(1 for r in subset if r['has_b2']) / len(subset) * 100
    st10 = s([r['ret_10d'] for r in subset])
    st20 = s([r['ret_20d'] for r in subset])
    print(f"{label:<20} {len(subset):>6,} {has_b2_rate:>7.0f}% {st10['wr']:>7.1f}% {st20['wr']:>7.1f}%")

# 5. 渐进式仓位策略
print("\n" + "=" * 80)
print("渐进式仓位策略: B1轻仓 → 等B2加仓 → 无B2退出")
print("=" * 80)

# 策略B: B1轻仓(50%) → 30天内B2出现则加满(100%) → 无B2则在30天退出
b1_b2_30d = [r for r in data if r['has_b2']]
b1_no_b2_30d = [r for r in data if not r['has_b2']]

# 有B2: 50%仓位持有B1→B2全程 + 50%仓位B2日加仓
ret_with_b2_20d = []
ret_with_b2_30d = []
for r in b1_b2_30d:
    if r['ret_20d'] is not None:
        # 假设B2在15天左右出现 → 简化: 50%仓位持20天 + 50%仓位持10天(从B2起)
        ret_with_b2_20d.append(r['ret_20d'] * 0.5 + (r['ret_10d'] or 0) * 0.5)
    if r['ret_30d'] is not None:
        ret_with_b2_30d.append(r['ret_30d'] * 0.5 + (r['ret_10d'] or 0) * 0.5)

# 无B2: 50%仓位持有20天后退出
ret_no_b2_20d = [r['ret_20d'] * 0.5 for r in b1_no_b2_30d if r['ret_20d'] is not None]

all_returns = ret_with_b2_20d + ret_no_b2_20d
st_all = s(all_returns)
print(f"\n策略: B1买入50%仓位 → 有B2则加满 → 20日退出")
print(f"  N={st_all['n']:,}  胜率 {st_all['wr']}%  中位 {st_all['med']}%  均值 {st_all['mean']}%")

# 6. 筛选B1条件后的小样本效果
print("\n" + "-" * 60)
print("叠加 B1 筛选（B1涨幅≥5% + 极高关注）后效果")
b1_filtered = [r for r in data if (r['b1_return_pct'] or 0) >= 5 and (r['tech_score'] or 0) >= 80]
st_f = s([r['ret_10d'] for r in b1_filtered])
b2_in_filtered = sum(1 for r in b1_filtered if r['has_b2']) / len(b1_filtered) * 100 if b1_filtered else 0
print(f"  B1强+极高: N={len(b1_filtered):,}  →B2率={b2_in_filtered:.0f}%  10d胜率={st_f['wr']}%  中位={st_f['med']}%")

conn.close()
