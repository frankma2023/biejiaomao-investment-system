"""
多套关注分权重 · 计算 + 存储 + 回测
"""
import sqlite3, json, os, numpy as np
from datetime import date, datetime, timedelta
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
WIDE = 'D:/hanako/investment-system/config/strategy/mw_backtest_wide.json'

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout=30000")
t0 = datetime.now()

# ── 1. 添加新列 ──
print("[1] 添加列...", end=' ', flush=True)
for col in ['tech_score_v4', 'tech_score_v4_1', 'tech_score_v4_2', 'tech_score_detail_v4', 'tech_score_detail_v4_1', 'tech_score_detail_v4_2']:
    try: conn.execute(f"ALTER TABLE mw_signal_daily ADD COLUMN {col} TEXT DEFAULT ''")
    except: pass
print("OK")

# ── 2. 加载信号 ──
print("[2] 加载信号 + K线...", end=' ', flush=True)
signals = conn.execute("""
    SELECT id, stock_code, b1_date, h_date, h_rs250, decline_pct, b1_return_pct, c_amount_avg,
           ind_rs20, ind_rs250, tech_score_detail
    FROM mw_signal_daily WHERE b1_date >= '2016-01-01' AND b1_date != '_sentinel_'
""").fetchall()
signals = [dict(r) for r in signals]
print(f"{len(signals):,} 条")

# ── 3. 计算外部因子（上轨突破、趋势效率）──
print("[3] 计算外部因子...", end=' ', flush=True)
needed = defaultdict(list)
for s in signals:
    needed[s['stock_code']].append(s['b1_date'])

klines_all = {}
for code, b1_dates in needed.items():
    min_d = (datetime.strptime(min(b1_dates), '%Y-%m-%d') - timedelta(days=80)).strftime('%Y-%m-%d')
    max_d = max(b1_dates)
    rows = conn.execute("""
        SELECT date, open, high, low, close, volume FROM daily_kline
        WHERE stock_code=? AND date >= ? AND date <= ? ORDER BY date
    """, (code, min_d, max_d)).fetchall()
    if rows:
        klines_all[code] = [(r['date'], float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['volume'])) for r in rows]

def compute_ext_factors(code, b1_date, klines):
    try: b1_idx = next(i for i, k in enumerate(klines) if k[0] == b1_date)
    except StopIteration: return None
    if b1_idx < 40: return None
    closes = np.array([k[4] for k in klines[:b1_idx+1]])
    highs = np.array([k[2] for k in klines[:b1_idx+1]])
    factors = {}
    # 趋势效率(20日)
    if len(closes) >= 20:
        net = closes[-1] - closes[-20]
        path = np.sum(np.abs(np.diff(closes[-20:])))
        factors['trend_eff'] = net / path if path > 0 else 0
    # 上轨突破
    if len(closes) >= 20:
        ma20 = np.mean(closes[-20:]); std20 = np.std(closes[-20:])
        upper = ma20 + 2 * std20
        factors['upper_band'] = (closes[-1] - upper) / upper if upper > 0 else 0
    # 乖离率(从已有数据读取，没有则算)
    if len(closes) >= 20:
        ma20 = np.mean(closes[-20:])
        factors['dev_ma20'] = (closes[-1] - ma20) / ma20 * 100 if ma20 > 0 else 0
    return factors

ext_cache = {}
for s in signals:
    code = s['stock_code']
    if code not in ext_cache:
        kt = klines_all.get(code)
        if kt: ext_cache[code] = {}
    cache = ext_cache.get(code)
    if cache is not None and s['b1_date'] not in cache:
        f = compute_ext_factors(code, s['b1_date'], klines_all[code])
        cache[s['b1_date']] = f or {}
    s['ext'] = ext_cache.get(code, {}).get(s['b1_date'], {})
print(f"OK ({(datetime.now()-t0).total_seconds():.0f}s)")

# ── 4. 四套权重方案 ──
print("[4] 计算四套关注分...", end=' ', flush=True)

def score_tier(val, tiers):
    """tiers: [(threshold, score), ...] 从高到低排列"""
    for threshold, sc in tiers:
        if val >= threshold: return sc
    return 0

def score_tier_reverse(val, tiers):
    """反向：值越低分越高"""
    for threshold, sc in tiers:
        if val <= threshold: return sc
    return 0

def compute_scores(s):
    h = s['h_date']
    b1 = s['b1_date']
    dec = s['decline_pct'] or 0
    rs = s['h_rs250'] or 0
    ind = s.get('ind_rs20') or 0
    ext = s.get('ext', {})
    teh = ext.get('trend_eff', 0)
    ub = ext.get('upper_band', 0)
    dev = ext.get('dev_ma20', 0)
    
    # 距H天数
    dh = 0
    if h and h > '2000-01-01' and b1:
        dh = (date.fromisoformat(b1) - date.fromisoformat(h)).days
    
    # 换手率（从旧detail取）
    to_v = 0
    old_detail = s.get('tech_score_detail', '')
    if old_detail:
        try: to_v = json.loads(old_detail).get('turnover', 0)
        except: pass
    
    # ── v3.5 (当前) ──
    s35 = 0
    d35 = {}
    # h_rs250 (50)
    v = score_tier(rs, [(90,50),(80,40),(70,30),(60,15)])
    s35 += v; d35['h_rs250'] = v
    # 距H (22)
    v = 22 if 40<=dh<=60 else (18 if 30<=dh<40 else (12 if (20<=dh<30)or(60<dh<=80) else (7 if dh>80 else 0)))
    s35 += v; d35['days_since_h'] = v
    # 换手率 (15)
    s35 += to_v; d35['turnover'] = to_v
    # 行业RS (8)
    v = 8 if ind>=80 else 0
    s35 += v; d35['ind_rs20'] = v
    # 回调 (5)
    v = score_tier(dec, [(35,5),(25,4),(20,3),(15,2)])
    s35 += v; d35['decline'] = v
    results_v35 = (s35, d35)
    
    # ── v4.0 (简化: 行业RS+回调+乖离率+距H+h_rs250+换手率) ──
    s40 = 0; d40 = {}
    # 行业RS (30)
    v = score_tier(ind, [(90,30),(80,22),(70,15),(60,8)])
    s40 += v; d40['ind_rs20'] = v
    # 回调 (20)
    v = score_tier(dec, [(35,20),(25,15),(20,10),(15,5)])
    s40 += v; d40['decline'] = v
    # 乖离率 (15) 反向:越低越好
    v = score_tier_reverse(dev, [(0,15),(5,12),(10,8),(20,3)])
    s40 += v; d40['deviation'] = v
    # 距H (15)
    v = 15 if 40<=dh<=60 else (12 if 30<=dh<40 else (8 if (20<=dh<30)or(60<dh<=80) else (5 if dh>80 else 0)))
    s40 += v; d40['days_since_h'] = v
    # h_rs250 (10)
    v = score_tier(rs, [(90,10),(80,7),(70,5)])
    s40 += v; d40['h_rs250'] = v
    # 换手率 (10)
    v = min(to_v, 10)
    s40 += v; d40['turnover'] = v
    results_v40 = (s40, d40)
    
    # ── v4.1 (外部因子: 上轨+趋势效率+行业RS+回调+乖离+距H) ──
    s41 = 0; d41 = {}
    # 上轨突破 (20) 反向:越低越好(远低于上轨=安全)
    v = score_tier_reverse(ub, [(-0.05,20),(-0.02,15),(0,10),(0.05,5)])
    s41 += v; d41['upper_band'] = v
    # 趋势效率 (20) 反向:越低越好(横盘整理=蓄力)
    v = score_tier_reverse(teh, [(-0.5,20),(-0.2,15),(0,10),(0.3,5)])
    s41 += v; d41['trend_eff'] = v
    # 行业RS (20)
    v = score_tier(ind, [(90,20),(80,15),(70,10),(60,5)])
    s41 += v; d41['ind_rs20'] = v
    # 回调 (15)
    v = score_tier(dec, [(35,15),(25,12),(20,8),(15,5)])
    s41 += v; d41['decline'] = v
    # 乖离率 (15) 反向
    v = score_tier_reverse(dev, [(0,15),(5,12),(10,8),(20,3)])
    s41 += v; d41['deviation'] = v
    # 距H (10)
    v = 10 if 40<=dh<=60 else (8 if 30<=dh<40 else (5 if (20<=dh<30)or(60<dh<=80) else (3 if dh>80 else 0)))
    s41 += v; d41['days_since_h'] = v
    results_v41 = (s41, d41)
    
    # ── v4.2 (均衡: 行业RS+上轨+趋势效率+回调+乖离+距H+h_rs250+换手) ──
    s42 = 0; d42 = {}
    # 行业RS (20)
    v = score_tier(ind, [(90,20),(80,15),(70,10),(60,5)])
    s42 += v; d42['ind_rs20'] = v
    # 上轨突破 (15) 反向
    v = score_tier_reverse(ub, [(-0.05,15),(-0.02,12),(0,8),(0.05,4)])
    s42 += v; d42['upper_band'] = v
    # 趋势效率 (15) 反向
    v = score_tier_reverse(teh, [(-0.5,15),(-0.2,12),(0,8),(0.3,4)])
    s42 += v; d42['trend_eff'] = v
    # 回调 (15)
    v = score_tier(dec, [(35,15),(25,12),(20,8),(15,5)])
    s42 += v; d42['decline'] = v
    # 乖离率 (10) 反向
    v = score_tier_reverse(dev, [(0,10),(5,8),(10,5),(20,2)])
    s42 += v; d42['deviation'] = v
    # 距H (10)
    v = 10 if 40<=dh<=60 else (8 if 30<=dh<40 else (5 if (20<=dh<30)or(60<dh<=80) else (3 if dh>80 else 0)))
    s42 += v; d42['days_since_h'] = v
    # h_rs250 (8)
    v = score_tier(rs, [(90,8),(80,5),(70,3)])
    s42 += v; d42['h_rs250'] = v
    # 换手率 (7)
    v = min(to_v, 7)
    s42 += v; d42['turnover'] = v
    results_v42 = (s42, d42)
    
    return results_v35, results_v40, results_v41, results_v42

# ── 批量更新 ──
updated = 0
batch = []
for i, s in enumerate(signals):
    if i % 10000 == 0: print(f'{i//1000}k...', end=' ', flush=True)
    try:
        v35, v40, v41, v42 = compute_scores(s)
    except: continue
    
    batch.append((
        v35[0], json.dumps(v35[1], ensure_ascii=False),
        v40[0], json.dumps(v40[1], ensure_ascii=False),
        v41[0], json.dumps(v41[1], ensure_ascii=False),
        v42[0], json.dumps(v42[1], ensure_ascii=False),
        s['id']
    ))
    if len(batch) >= 5000:
        conn.executemany("""
            UPDATE mw_signal_daily SET 
                tech_score=?, tech_score_detail=?,
                tech_score_v4=?, tech_score_detail_v4=?,
                tech_score_v4_1=?, tech_score_detail_v4_1=?,
                tech_score_v4_2=?, tech_score_detail_v4_2=?
            WHERE id=?
        """, batch)
        conn.commit()
        updated += len(batch)
        batch = []

if batch:
    conn.executemany("""UPDATE mw_signal_daily SET tech_score=?, tech_score_detail=?, tech_score_v4=?, tech_score_detail_v4=?, tech_score_v4_1=?, tech_score_detail_v4_1=?, tech_score_v4_2=?, tech_score_detail_v4_2=? WHERE id=?""", batch)
    conn.commit()
    updated += len(batch)

print(f"\n  更新: {updated:,} 条 ({(datetime.now()-t0).total_seconds():.0f}s)")

# ── 5. 四套方案回测对比 ──
print("\n" + "=" * 65)
print("四套关注分 · 五分位回测对比")
print("=" * 65)

# 加载宽表的前向收益
with open(WIDE, 'r') as f: wide = json.load(f)
wide_map = {}
for r in wide:
    if r.get('ret_b1_10d') is not None and r.get('ret_b1_20d') is not None:
        wide_map[(r['stock_code'], r['b1_date'])] = (r['ret_b1_10d'], r['ret_b1_20d'])

# 读取新关注分
scores = conn.execute("""
    SELECT stock_code, b1_date, tech_score, tech_score_v4, tech_score_v4_1, tech_score_v4_2
    FROM mw_signal_daily WHERE b1_date >= '2016-01-01' AND b1_date != '_sentinel_'
""").fetchall()

schemes = [
    ('v3.5 (当前)', 'tech_score'),
    ('v4.0 (简化)', 'tech_score_v4'),
    ('v4.1 (外部)', 'tech_score_v4_1'),
    ('v4.2 (均衡)', 'tech_score_v4_2'),
]

for scheme_name, col_idx in schemes:
    data = []
    for r in scores:
        w = wide_map.get((r['stock_code'], r['b1_date']))
        if w:
            sc = r[col_idx]
            if sc == '' or sc is None: sc = 0
            data.append({'score': int(sc) if isinstance(sc, str) else (sc or 0), 'ret10': w[0], 'ret20': w[1]})
    
    if len(data) < 100: continue
    data.sort(key=lambda x: -x['score'])
    n = len(data) // 5
    print(f"\n{scheme_name} ({len(data):,} 条)")
    print(f"  {'分位':<6} {'分数区间':>12} {'N':>6} {'10d胜率':>8} {'10d中位':>9} {'20d胜率':>8}")
    print(f"  {'-'*52}")
    
    spreads = []
    for i in range(5):
        s, e = i*n, (i+1)*n if i<4 else len(data)
        chunk = data[s:e]
        r10 = np.array([r['ret10'] for r in chunk])
        r20 = np.array([r['ret20'] for r in chunk])
        lo, hi = chunk[-1]['score'], chunk[0]['score']
        print(f"  Q{i+1:<5} {lo:>6.0f}~{hi:<6.0f} {len(chunk):>6,} {(r10>0).mean()*100:>7.1f}% {np.median(r10)*100:>8.2f}% {(r20>0).mean()*100:>7.1f}%")
        if i == 0: spreads.append((r10>0).mean()*100)
        if i == 4: spreads.append((r10>0).mean()*100)
    
    if len(spreads) == 2:
        diff = spreads[0] - spreads[1]
        print(f"  Q1-Q5胜率差: {diff:+.1f}pp {'✅ 有区分力' if abs(diff)>3 else '❌ 无区分力'}")

conn.close()
print(f"\n总耗时: {(datetime.now()-t0).total_seconds():.0f}s")
