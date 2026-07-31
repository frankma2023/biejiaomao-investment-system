"""
MW 外部因子回测 · 量价相关/OBV斜率/趋势效率/上轨突破/均线斜率
"""
import json, sqlite3, numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
WIDE = 'D:/hanako/investment-system/config/strategy/mw_backtest_wide.json'

print("=" * 60)
print("MW 外部因子回测")
print("=" * 60)

# ── 1. 加载信号 ──
with open(WIDE, 'r') as f: wide = json.load(f)
signals = [(r['stock_code'], r['b1_date'], r['tech_score'], r.get('ret_b1_10d'), r.get('ret_b1_20d'))
           for r in wide if r.get('ret_b1_10d') is not None]
print(f"[1] 信号: {len(signals):,} 条")

# ── 2. 加载K线(只取 B1日前 50 天 + B1日当天) ──
print("[2] 加载K线...", end=' ', flush=True)
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

needed = defaultdict(list)
for code, b1, _, _, _ in signals:
    needed[code].append(b1)

klines_all = {}
n = 0
for code, b1_dates in needed.items():
    min_d = (datetime.strptime(min(b1_dates), '%Y-%m-%d') - timedelta(days=80)).strftime('%Y-%m-%d')
    max_d = max(b1_dates)
    rows = conn.execute("""
        SELECT date, open, high, low, close, volume FROM daily_kline
        WHERE stock_code=? AND date >= ? AND date <= ? ORDER BY date
    """, (code, min_d, max_d)).fetchall()
    if rows:
        klines_all[code] = [(r['date'], float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['volume'])) for r in rows]
        n += 1
print(f"{n} 只")

# ── 3. 计算因子 ──
print("[3] 计算因子...", end=' ', flush=True)

def compute_factors(code, b1_date, klines):
    """在 B1 日当天计算各因子值"""
    # 找到 B1 日索引
    try:
        b1_idx = next(i for i, k in enumerate(klines) if k[0] == b1_date)
    except StopIteration:
        return None
    
    if b1_idx < 40:  # 需要至少 40 天历史
        return None
    
    closes = np.array([k[4] for k in klines[:b1_idx+1]])
    volumes = np.array([k[5] for k in klines[:b1_idx+1]])
    highs = np.array([k[2] for k in klines[:b1_idx+1]])
    lows = np.array([k[3] for k in klines[:b1_idx+1]])
    
    factors = {}
    
    # ── 量价相关 (10日) ──
    if len(closes) >= 10:
        rets = np.diff(closes[-11:]) / closes[-11:-1]
        vols = volumes[-11:]
        if len(rets) >= 10 and np.std(rets) > 0 and np.std(vols) > 0:
            factors['pv_corr_10d'] = np.corrcoef(rets[-10:], vols[-10:])[0,1]
    
    # ── 量价相关 (20日) ──
    if len(closes) >= 20:
        rets = np.diff(closes[-21:]) / closes[-21:-1]
        vols = volumes[-21:]
        if len(rets) >= 10 and np.std(rets) > 0 and np.std(vols) > 0:
            factors['pv_corr_20d'] = np.corrcoef(rets[-10:], vols[-10:])[0,1]
    
    # ── OBV 斜率 (20日) ──
    if len(closes) >= 20:
        obv = np.zeros(21)
        for i in range(1, 21):
            idx = b1_idx - 21 + i
            obv[i] = obv[i-1] + volumes[idx] * (1 if closes[idx] > closes[idx-1] else (-1 if closes[idx] < closes[idx-1] else 0))
        x = np.arange(20)
        slope = np.polyfit(x, obv[1:], 1)[0]
        factors['obv_slope'] = slope / np.mean(volumes[-20:]) if np.mean(volumes[-20:]) > 0 else 0
    
    # ── 趋势效率 (20日) ──
    if len(closes) >= 20:
        net_change = closes[-1] - closes[-20]
        path_length = np.sum(np.abs(np.diff(closes[-20:])))
        factors['trend_efficiency'] = net_change / path_length if path_length > 0 else 0
    
    # ── 上轨突破 ──
    if len(closes) >= 20:
        ma20 = np.mean(closes[-20:])
        std20 = np.std(closes[-20:])
        upper = ma20 + 2 * std20
        factors['upper_band_pct'] = (closes[-1] - upper) / upper if upper > 0 else 0  # 正值=突破上轨
    
    # ── 均线斜率 (MA20) ──
    if len(closes) >= 20:
        ma20_vals = np.array([np.mean(closes[i-19:i+1]) for i in range(19, len(closes))])
        if len(ma20_vals) >= 10:
            x = np.arange(min(10, len(ma20_vals)))
            slope = np.polyfit(x, ma20_vals[-10:], 1)[0]
            factors['ma20_slope'] = slope / np.mean(closes[-20:]) if np.mean(closes[-20:]) > 0 else 0
    
    return factors

# 批量计算
results = []
for i, (code, b1_date, score, ret10, ret20) in enumerate(signals):
    if i % 10000 == 0: print(f'{i//1000}k...', end=' ', flush=True)
    kt = klines_all.get(code)
    if not kt: continue
    factors = compute_factors(code, b1_date, kt)
    if factors:
        factors['ret10'] = ret10
        factors['ret20'] = ret20
        factors['score'] = score
        results.append(factors)

# 过滤掉 ret20=None 的
results = [r for r in results if r.get('ret20') is not None]
print(f"\n  有效: {len(results):,} 条")

# ── 4. 相关性分析 ──
print("\n" + "=" * 65)
print("因子与 10/20 日收益的相关系数")
print("=" * 65)
factor_names = [
    ('pv_corr_10d', '量价相关(10日)', '负值=背离, 越低越好'),
    ('pv_corr_20d', '量价相关(20日)', '负值=背离'),
    ('obv_slope', 'OBV斜率(20日)', '正值=资金流入'),
    ('trend_efficiency', '趋势效率(20日)', '越高=趋势越顺畅'),
    ('upper_band_pct', '上轨突破%', '正值=突破布林上轨'),
    ('ma20_slope', 'MA20斜率', '正值=均线上行'),
]
for key, name, note in factor_names:
    vals = np.array([r.get(key, 0) or 0 for r in results])
    finite = np.isfinite(vals)
    r10 = np.corrcoef(vals[finite], [r['ret10'] for r in results])[0,1] if finite.sum() > 10 else 0
    r20 = np.corrcoef(vals[finite], [r['ret20'] for r in results])[0,1] if finite.sum() > 10 else 0
    star10 = '★' if abs(r10) > 0.02 else ''
    star20 = '★' if abs(r20) > 0.03 else ''
    print(f"  {name:<22} r10={r10:+.4f} {star10}  r20={r20:+.4f} {star20}  ({note})")

# ── 5. 五分位 ──
print("\n" + "=" * 65)
print("五分位单调性（10日胜率+中位）")
print("=" * 65)

def quintile(data, key, name, reverse=True):
    valid = [r for r in data if r.get(key) is not None and np.isfinite(r.get(key, 0))]
    vals = sorted(valid, key=lambda r: r.get(key, 0), reverse=reverse)
    n = max(1, len(vals) // 5)
    print(f"\n{name}")
    print(f"  {'分位':<6} {'N':>6} {'10d胜率':>8} {'10d中位':>9} {'20d胜率':>8}")
    print(f"  {'-'*40}")
    for i in range(5):
        s, e = i*n, (i+1)*n if i<4 else len(vals)
        chunk = vals[s:e]
        if not chunk: continue
        r10 = np.array([r['ret10'] for r in chunk])
        r20 = np.array([r['ret20'] for r in chunk])
        print(f"  Q{i+1:<5} {len(chunk):>6,} {(r10>0).mean()*100:>7.1f}% {np.median(r10)*100:>8.2f}% {(r20>0).mean()*100:>7.1f}%")

quintile(results, 'trend_efficiency', '趋势效率(20日)', reverse=True)
quintile(results, 'obv_slope', 'OBV斜率(20日)', reverse=True)
quintile(results, 'pv_corr_10d', '量价相关(10日)', reverse=False)  # 负值好→低到高
quintile(results, 'upper_band_pct', '上轨突破%', reverse=True)
quintile(results, 'ma20_slope', 'MA20斜率', reverse=True)

# ── 6. 多因子组合 vs 单因子 ──
print("\n" + "=" * 65)
print("增量贡献：控制关注分后，因子剩余区分力")
print("=" * 65)
# 按关注分中位数分组
med_score = np.median([r['score'] for r in results])
high_score = [r for r in results if (r['score'] or 0) >= med_score]
low_score = [r for r in results if (r['score'] or 0) < med_score]
print(f"  高关注分(≥{med_score:.0f}): {len(high_score):,} 条")
print(f"  低关注分(<{med_score:.0f}): {len(low_score):,} 条")

for key, name, _ in factor_names:
    for label, subset in [('高分', high_score), ('低分', low_score)]:
        valid = [r for r in subset if r.get(key) is not None and np.isfinite(r.get(key, 0))]
        if len(valid) < 100: continue
        vals = sorted(valid, key=lambda r: r.get(key, 0))
        top_n = len(vals) // 3
        top = vals[-top_n:]
        bot = vals[:top_n]
        r10_top = np.array([r['ret10'] for r in top])
        r10_bot = np.array([r['ret10'] for r in bot])
        if len(r10_top) and len(r10_bot):
            diff = (r10_top > 0).mean() - (r10_bot > 0).mean()
            if abs(diff) > 0.02:
                print(f"  [{label}] {name}: top1/3胜率={(r10_top>0).mean()*100:.1f}% vs bot1/3={(r10_bot>0).mean()*100:.1f}% (差{diff*100:+.1f}pp)")

conn.close()
print("\n完成")
