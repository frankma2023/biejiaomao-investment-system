"""
v4.3 各因子 IC/ICIR 分析 + 逐月稳定性
"""
import sqlite3, json, numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
WIDE = 'D:/hanako/investment-system/config/strategy/mw_backtest_wide.json'

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
t0 = datetime.now()

# ── 加载 ──
with open(WIDE, 'r') as f: wide = json.load(f)
wm = {}
for r in wide:
    if r.get('ret_b1_10d') is not None and r.get('ret_b1_20d') is not None:
        wm[(r['stock_code'], r['b1_date'])] = r

signals = conn.execute("""
    SELECT stock_code, b1_date, h_date, h_rs250, decline_pct, ind_rs20, tech_score_v4_3, tech_score_detail_v4_3
    FROM mw_signal_daily WHERE b1_date >= '2016-01-01' AND b1_date != '_sentinel_'
""").fetchall()

# 计算外部因子
needed = defaultdict(list)
for s in signals: needed[s['stock_code']].append(s['b1_date'])
klines_all = {}
for code, b1_dates in needed.items():
    min_d = (datetime.strptime(min(b1_dates), '%Y-%m-%d') - timedelta(days=80)).strftime('%Y-%m-%d')
    rows = conn.execute("SELECT date, open, high, low, close, volume FROM daily_kline WHERE stock_code=? AND date>=? AND date<=? ORDER BY date", (code, min_d, max(b1_dates))).fetchall()
    if rows:
        klines_all[code] = [(r['date'], float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['volume'])) for r in rows]

def ext_factors(code, b1, kt):
    try: idx = next(i for i, k in enumerate(kt) if k[0] == b1)
    except StopIteration: return {}
    if idx < 40: return {}
    closes = np.array([k[4] for k in kt[:idx+1]])
    f = {}
    if len(closes) >= 20:
        net = closes[-1] - closes[-20]
        path = np.sum(np.abs(np.diff(closes[-20:])))
        f['teh'] = net / path if path > 0 else 0
        ma20 = np.mean(closes[-20:]); std20 = np.std(closes[-20:])
        f['ub'] = (closes[-1] - (ma20 + 2*std20)) / (ma20 + 2*std20) if (ma20 + 2*std20) > 0 else 0
        f['dev'] = (closes[-1] - ma20) / ma20 * 100 if ma20 > 0 else 0
    return f

# 收集因子值 + 收益
from datetime import date
data = []
for s in signals:
    w = wm.get((s['stock_code'], s['b1_date']))
    if not w: continue
    kt = klines_all.get(s['stock_code'])
    ef = ext_factors(s['stock_code'], s['b1_date'], kt) if kt else {}
    
    # 距H天数
    dh = 0
    if s['h_date'] and s['h_date'] > '2000-01-01':
        dh = (date.fromisoformat(s['b1_date']) - date.fromisoformat(s['h_date'])).days
    
    detail = {}
    det_raw = s['tech_score_detail_v4_3']
    if det_raw:
        try: detail = json.loads(det_raw)
        except: pass
    
    data.append({
        'date': s['b1_date'],
        'month': s['b1_date'][:7],
        'ub': ef.get('ub', 0) * 100,  # 百分比
        'teh': ef.get('teh', 0),
        'ind_rs20': s['ind_rs20'] or 0,
        'decline': s['decline_pct'] or 0,
        'dev': ef.get('dev', 0),
        'dh': dh,
        'rs250': s['h_rs250'] or 0,
        'score': s['tech_score_v4_3'] or 0,
        'ret10': w['ret_b1_10d'],
        'ret20': w['ret_b1_20d'],
        # 子项得分
        'sc_ub': detail.get('upper_band', 0),
        'sc_teh': detail.get('trend_eff', 0),
        'sc_ind': detail.get('ind_rs20', 0),
        'sc_dec': detail.get('decline', 0),
        'sc_dev': detail.get('deviation', 0),
        'sc_dh': detail.get('days_since_h', 0),
        'sc_rs': detail.get('h_rs250', 0),
    })

print(f"有效: {len(data):,} 条")

# ── 1. IC/ICIR（Spearman rank correlation）──
from scipy.stats import spearmanr

print("\n" + "=" * 70)
print("v4.3 各因子 IC/ICIR 分析")
print("=" * 70)

factors = [
    ('上轨突破%', 'ub', 20),
    ('趋势效率', 'teh', 20),
    ('行业RS_20', 'ind_rs20', 20),
    ('回调深度', 'decline', 10),
    ('乖离率MA20', 'dev', 10),
    ('距H天数', 'dh', 10),
    ('h_rs250', 'rs250', 10),
    ('v4.3总分', 'score', 100),
]

print(f"  {'因子':<16} {'权重':>5} {'IC(10d)':>9} {'ICIR(10d)':>9} {'IC(20d)':>9} {'ICIR(20d)':>9} {'贡献':>7}")
print(f"  {'-'*65}")

# 计算每个因子的逐月IC
ic_data = {}
for name, key, wt in factors:
    vals = np.array([float(d.get(key, 0) or 0) for d in data])
    mask = np.isfinite(vals)
    
    # Overall IC
    ic10, _ = spearmanr(vals[mask], [d['ret10'] for d in data]) if mask.sum() > 10 else (0, 0)
    ic20, _ = spearmanr(vals[mask], [d['ret20'] for d in data]) if mask.sum() > 10 else (0, 0)
    
    # 逐月 IC（计算 ICIR）
    monthly_ic10 = []
    monthly_ic20 = []
    for month in sorted(set(d['month'] for d in data)):
        md = [d for d in data if d['month'] == month]
        if len(md) < 30: continue
        mv = np.array([d[key] for d in md])
        mm = np.isfinite(mv)
        if mm.sum() < 10: continue
        ic_m10, _ = spearmanr(mv[mm], [d['ret10'] for d in md])
        ic_m20, _ = spearmanr(mv[mm], [d['ret20'] for d in md])
        if not np.isnan(ic_m10): monthly_ic10.append(ic_m10)
        if not np.isnan(ic_m20): monthly_ic20.append(ic_m20)
    
    icir10 = np.mean(monthly_ic10) / np.std(monthly_ic10) if monthly_ic10 and np.std(monthly_ic10) > 0 else 0
    icir20 = np.mean(monthly_ic20) / np.std(monthly_ic20) if monthly_ic20 and np.std(monthly_ic20) > 0 else 0
    
    # 贡献度 = |IC| * 权重 / 100（归一化）
    contrib = abs(ic10) * wt  # 权重×|IC| = 贡献度指标
    
    star = '★' if abs(ic10) > 0.02 else '☆' if abs(ic10) > 0.01 else ''
    print(f"  {name:<16} {wt:>5} {ic10:>+8.4f} {star} {icir10:>+8.3f} {ic20:>+8.4f} {icir20:>+8.3f} {contrib:>6.3f}")
    
    ic_data[name] = {'ic10': ic10, 'icir10': icir10, 'ic20': ic20, 'icir20': icir20, 'contrib': contrib}

# ── 2. 得分贡献 vs IC 贡献对比 ──
print("\n" + "=" * 70)
print("权重 vs 实际 IC 贡献对比")
print("=" * 70)
print(f"  {'因子':<16} {'设定权重':>8} {'实际IC贡献':>10} {'合理性'}")
print(f"  {'-'*50}")
for name, _, wt in factors[:-1]:  # skip 总分
    d = ic_data.get(name, {})
    actual = d.get('contrib', 0)
    ratio = actual * 100 / wt if wt > 0 else 0
    verdict = '✅' if ratio > 0.05 else ('⚠️ 偏高' if wt > 15 and actual < 0.5 else '⚠️ 偏低')
    print(f"  {name:<16} {wt:>6}分 {actual*100:>7.1f}% {verdict}")

conn.close()
print(f"\n总耗时: {(datetime.now()-t0).total_seconds():.0f}s")
