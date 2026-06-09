"""重算 D15 + I2新阶梯 + Sig10 后的所有MW信号得分"""
import sqlite3, json

DB = "D:/hanako/investment-system/data/lixinger.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 1. 从 pattern_scan_signals 加载所有B2日信号共振数据
print("Loading pattern_scan_signals...")
sig_rows = cur.execute("""
    SELECT date, stock_code, signals_json FROM pattern_scan_signals
    WHERE date >= '2026-01-01' AND date <= '2026-06-05'
""").fetchall()
sig_cache = {}
for r in sig_rows:
    key = (r['date'], r['stock_code'])
    sig_cache[key] = r['signals_json']
print(f"  {len(sig_cache)} signal records loaded")

# 2. 读取所有MW信号
print("Loading MW signals...")
mw_rows = cur.execute("""
    SELECT * FROM mw_signal_daily 
    WHERE b2_date >= '2026-01-01' AND b2_date <= '2026-06-05'
""").fetchall()
print(f"  {len(mw_rows)} MW signals")

# 3. 逐行重算
updates = []
new_sig_dist = {}
for r in mw_rows:
    rid = r['id']
    code = r['stock_code']
    b2_date = r['b2_date']
    
    # score_d: 0→0, 5→15 (multiply by 3)
    new_d = r['score_d'] * 3 if r['score_d'] else 0
    
    # score_i2: new tiers based on h_rs250
    h_rs250 = r['h_rs250']
    if h_rs250 is not None and h_rs250 >= 90:
        new_i2 = 15
    elif h_rs250 is not None and h_rs250 >= 85:
        new_i2 = 10
    elif h_rs250 is not None and h_rs250 >= 80:
        new_i2 = 5
    else:
        new_i2 = 0
    
    # score_sig: recompute from pattern_scan_signals
    new_sig = 0
    key = (b2_date, code)
    raw = sig_cache.get(key)
    if raw:
        try:
            sigs = json.loads(raw) if isinstance(raw, str) else raw
            sources_seen = set()
            for s in (sigs if isinstance(sigs, list) else []):
                src = s.get('source', '')
                if src in sources_seen:
                    continue
                sources_seen.add(src)
                if src in ('base_breakout', 'pocket_pivot'):
                    new_sig += 6
                elif src in ('cdl', 'talib'):
                    new_sig += 1
            new_sig = min(new_sig, 10)
        except:
            pass
    
    new_sig_dist[new_sig] = new_sig_dist.get(new_sig, 0) + 1
    
    # score = sum of all
    new_score = (r['score_h'] or 0) + new_d + (r['score_c'] or 0) + (r['score_p'] or 0) + \
                (r['score_i1'] or 0) + new_i2 + new_sig + (r['score_gap'] or 0)
    
    # confidence
    if new_score >= 80:
        new_conf = '高'
    elif new_score >= 55:
        new_conf = '中'
    else:
        new_conf = '低'
    
    # is_plus
    new_plus = 1 if (new_score >= 80 and new_d == 15 and r['score_i1'] == 15) else 0
    
    updates.append((new_d, new_i2, new_sig, new_score, new_conf, new_plus, rid))

# 4. 批量更新
print("Updating database...")
cur.executemany("""
    UPDATE mw_signal_daily SET 
        score_d=?, score_i2=?, score_sig=?, score=?, confidence=?, is_plus=?
    WHERE id=?
""", updates)
conn.commit()
print(f"  {cur.rowcount} rows updated")

# 5. 验证
print("\n=== Sig分布变化 ===")
print("New score_sig distribution:", dict(sorted(new_sig_dist.items())))

cur.execute("SELECT score_i2, COUNT(*) FROM mw_signal_daily GROUP BY score_i2 ORDER BY score_i2 DESC")
print("score_i2:", cur.fetchall())

cur.execute("SELECT score_d, COUNT(*) FROM mw_signal_daily GROUP BY score_d ORDER BY score_d DESC")
print("score_d:", cur.fetchall())

cur.execute("SELECT confidence, COUNT(*) FROM mw_signal_daily GROUP BY confidence")
print("confidence:", cur.fetchall())

cur.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE is_plus=1")
print(f"PLUS: {cur.fetchone()[0]}")

# 抽样
cur.execute("""SELECT stock_code, b2_date, score_h, score_d, score_c, score_p, score_i1, score_i2, score_sig, score_gap, score, confidence
    FROM mw_signal_daily WHERE is_plus=1 ORDER BY score DESC LIMIT 5""")
print("\nPLUS samples:")
for r in cur.fetchall():
    s = dict(r)
    print(f"  {s['stock_code']} {s['b2_date']} H={s['score_h']} D={s['score_d']} C={s['score_c']} P={s['score_p']} I1={s['score_i1']} I2={s['score_i2']} Sig={s['score_sig']} Gap={s['score_gap']} → {s['score']} {s['confidence']}")

conn.close()
print("\nDone.")
