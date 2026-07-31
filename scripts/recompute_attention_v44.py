"""
重算全量 MW 信号关注分 · v4.4 IC校准权重
用法:
  python scripts/recompute_attention_v44.py
  python scripts/recompute_attention_v44.py --start 2016-01-01 --end 2016-12-31
"""
import sqlite3, json, os, argparse, numpy as np
from datetime import date, datetime, timedelta
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout=30000")
t0 = datetime.now()

parser = argparse.ArgumentParser()
parser.add_argument('--start', type=str, default='2016-01-01')
parser.add_argument('--end', type=str, default='2026-07-24')
args = parser.parse_args()

print("=" * 60)
print(f"重算关注分 v4.4 · {args.start} ~ {args.end}")
print("=" * 60)

# ── 1. 加载信号 ──
signals = conn.execute("""
    SELECT id, stock_code, b1_date, h_date, h_rs250, decline_pct, ind_rs20
    FROM mw_signal_daily
    WHERE b1_date >= ? AND b1_date <= ? AND b1_date != '_sentinel_'
    ORDER BY id
""", (args.start, args.end)).fetchall()
print(f"[1] 信号: {len(signals):,} 条")

# ── 2. 批量加载K线（只取B1日前20天）──
print("[2] 加载K线...", end=' ', flush=True)
needed = defaultdict(list)
for s in signals:
    needed[s['stock_code']].append(s['b1_date'])

klines_cache = {}
n = 0
for code, b1_dates in needed.items():
    min_d = (datetime.strptime(min(b1_dates), '%Y-%m-%d') - timedelta(days=30)).strftime('%Y-%m-%d')
    max_d = max(b1_dates)
    rows = conn.execute(
        "SELECT date, close FROM daily_kline WHERE stock_code=? AND date>=? AND date<=? ORDER BY date",
        (code, min_d, max_d)
    ).fetchall()
    if rows:
        klines_cache[code] = [(r['date'], float(r['close'])) for r in rows]
        n += 1
print(f"{n} 只")

# ── 3. 计算 ──
print("[3] 计算...", end=' ', flush=True)

def score_tier(val, tiers):
    for t, sc in tiers:
        if val >= t: return sc
    return 0

def score_rev(val, tiers):
    for t, sc in tiers:
        if val <= t: return sc
    return 0

batch = []
updated = 0
for i, s in enumerate(signals):
    if i % 10000 == 0: print(f'{i//1000}k...', end=' ', flush=True)
    
    code, b1, h = s['stock_code'], s['b1_date'], s['h_date']
    rs = s['h_rs250'] or 0
    dec = s['decline_pct'] or 0
    ind = s['ind_rs20'] or 0
    
    # 外部因子
    kt = klines_cache.get(code)
    ub_pct, teh, dev_ma20 = 0, 0, 0
    if kt:
        # 找到 B1 日索引
        b1_closes = [float(c) for d, c in kt if d <= b1]
        if len(b1_closes) >= 20:
            closes = np.array(b1_closes[-20:])
            ma20 = np.mean(closes)
            std20 = np.std(closes)
            upper = ma20 + 2 * std20
            if upper > 0:
                ub_pct = (closes[-1] - upper) / upper * 100
            net = closes[-1] - closes[0]
            path = np.sum(np.abs(np.diff(closes)))
            if path > 0:
                teh = net / path
            if ma20 > 0:
                dev_ma20 = (closes[-1] - ma20) / ma20 * 100
    
    # 距H天数
    dh = 0
    if h and h > '2000-01-01' and b1:
        dh = (date.fromisoformat(b1) - date.fromisoformat(h)).days
    
    sc = 0; d = {}
    
    # 1. 上轨突破 25 (反向)
    v = score_rev(ub_pct, [(-5,25),(-2,18),(0,12),(5,6)])
    sc += v; d['upper_band'] = v
    # 2. 乖离率 20 (反向)
    v = score_rev(dev_ma20, [(0,20),(5,15),(10,10),(20,5)])
    sc += v; d['deviation'] = v
    # 3. 趋势效率 20 (反向)
    v = score_rev(teh, [(-0.5,20),(-0.2,15),(0,10),(0.3,5)])
    sc += v; d['trend_eff'] = v
    # 4. 距H天数 15 (正向)
    if 40 <= dh <= 60: v = 15
    elif 30 <= dh < 40: v = 12
    elif (20 <= dh < 30) or (60 < dh <= 80): v = 8
    elif dh > 80: v = 5
    else: v = 0
    sc += v; d['days_since_h'] = v
    # 5. 回调深度 10 (正向)
    v = score_tier(dec, [(35,10),(25,8),(20,5),(15,3)])
    sc += v; d['decline'] = v
    # 6. 行业RS 5 (正向)
    v = score_tier(ind, [(90,5),(80,4),(70,3),(60,2)])
    sc += v; d['ind_rs20'] = v
    # 7. h_rs250 5 (正向)
    v = score_tier(rs, [(90,5),(80,3),(70,2)])
    sc += v; d['h_rs250'] = v
    
    batch.append((sc, json.dumps(d, ensure_ascii=False), s['id']))
    
    if len(batch) >= 5000:
        conn.executemany("UPDATE mw_signal_daily SET tech_score=?, tech_score_detail=? WHERE id=?", batch)
        conn.commit()
        updated += len(batch)
        batch = []

if batch:
    conn.executemany("UPDATE mw_signal_daily SET tech_score=?, tech_score_detail=? WHERE id=?", batch)
    conn.commit()
    updated += len(batch)

print(f"\n  更新: {updated:,} 条 ({(datetime.now()-t0).total_seconds():.0f}s)")
conn.close()
