import sqlite3, json
DB = "D:/hanako/investment-system/data/lixinger.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 1. 修复 score_d：从 decline_pct 重新计算（不受之前的倍增影响）
cur.execute("""
    UPDATE mw_signal_daily SET score_d = 
        CASE WHEN decline_pct >= 15 AND decline_pct <= 35 THEN 15 ELSE 0 END
""")
print(f"score_d reset: {cur.rowcount}")

# 2. 重置 score_i2（新阶梯）
cur.execute("""
    UPDATE mw_signal_daily SET score_i2 = 
        CASE WHEN h_rs250 >= 90 THEN 15 WHEN h_rs250 >= 85 THEN 10 
             WHEN h_rs250 >= 80 THEN 5 ELSE 0 END
""")
print(f"score_i2 reset: {cur.rowcount}")

# 3. 重算 score_sig
sig_rows = cur.execute("""
    SELECT date, stock_code, signals_json FROM pattern_scan_signals
    WHERE date >= '2025-12-01' AND date <= '2026-06-05'
""").fetchall()
sig_cache = {}
for r in sig_rows:
    sig_cache[(r['date'], r['stock_code'])] = r['signals_json']

mw_rows = cur.execute("SELECT id, stock_code, b2_date FROM mw_signal_daily").fetchall()
print(f"Recalculating sig for {len(mw_rows)} signals...")

updates_sig = []
for r in mw_rows:
    new_sig = 0
    raw = sig_cache.get((r['b2_date'], r['stock_code']))
    if raw:
        try:
            sigs = json.loads(raw) if isinstance(raw, str) else raw
            sources_seen = set()
            for s in (sigs if isinstance(sigs, list) else []):
                src = s.get('source', '')
                if src in sources_seen: continue
                sources_seen.add(src)
                if src in ('base_breakout', 'pocket_pivot'): new_sig += 6
                elif src in ('cdl', 'talib'): new_sig += 1
            new_sig = min(new_sig, 10)
        except: pass
    updates_sig.append((new_sig, r['id']))

cur.executemany("UPDATE mw_signal_daily SET score_sig=? WHERE id=?", updates_sig)
print(f"score_sig: {cur.rowcount}")

# 4. 重算 score = sum of all
cur.execute("""
    UPDATE mw_signal_daily SET score = 
        COALESCE(score_h,0) + COALESCE(score_d,0) + COALESCE(score_c,0) + 
        COALESCE(score_p,0) + COALESCE(score_i1,0) + COALESCE(score_i2,0) + 
        COALESCE(score_sig,0) + COALESCE(score_gap,0)
""")
print(f"score: {cur.rowcount}")

# 5. confidence
cur.execute("""
    UPDATE mw_signal_daily SET confidence = 
        CASE WHEN score >= 80 THEN '高' WHEN score >= 55 THEN '中' ELSE '低' END
""")

# 6. is_plus
cur.execute("""
    UPDATE mw_signal_daily SET is_plus = 
        CASE WHEN score >= 80 AND score_d = 15 AND score_i1 = 15 THEN 1 ELSE 0 END
""")
conn.commit()

# Verify
for q,label in [
    ("SELECT score_d,COUNT(*) FROM mw_signal_daily GROUP BY score_d ORDER BY score_d DESC", "score_d"),
    ("SELECT score_i2,COUNT(*) FROM mw_signal_daily GROUP BY score_i2 ORDER BY score_i2 DESC", "score_i2"),
    ("SELECT score_sig,COUNT(*) FROM mw_signal_daily GROUP BY score_sig ORDER BY score_sig DESC", "score_sig"),
    ("SELECT confidence,COUNT(*) FROM mw_signal_daily GROUP BY confidence ORDER BY confidence", "conf"),
    ("SELECT COUNT(*),ROUND(AVG(score),1),MAX(score),MIN(score) FROM mw_signal_daily", "score"),
    ("SELECT COUNT(*) FROM mw_signal_daily WHERE is_plus=1", "PLUS"),
]:
    cur.execute(q)
    print(f"{label}: {cur.fetchall()}")

conn.close()
print("Done.")
