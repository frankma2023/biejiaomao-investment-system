import sqlite3, json
DB = "D:/hanako/investment-system/data/lixinger.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Load ALL signals
sig_rows = cur.execute("SELECT date, stock_code, signals_json FROM pattern_scan_signals WHERE date >= '2025-12-01' AND date <= '2026-06-05'").fetchall()
sig_cache = {}
for r in sig_rows:
    sig_cache[(r['date'], r['stock_code'])] = r['signals_json']

mw_rows = cur.execute("SELECT * FROM mw_signal_daily").fetchall()
print(f"Total MW signals: {len(mw_rows)}")

updates = []
for r in mw_rows:
    rid, code, b2_date = r['id'], r['stock_code'], r['b2_date']
    
    new_d = r['score_d'] * 3 if r['score_d'] else 0
    
    h_rs250 = r['h_rs250']
    if h_rs250 is not None and h_rs250 >= 90: new_i2 = 15
    elif h_rs250 is not None and h_rs250 >= 85: new_i2 = 10
    elif h_rs250 is not None and h_rs250 >= 80: new_i2 = 5
    else: new_i2 = 0
    
    new_sig = 0
    raw = sig_cache.get((b2_date, code))
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
    
    new_score = (r['score_h'] or 0) + new_d + (r['score_c'] or 0) + (r['score_p'] or 0) + \
                (r['score_i1'] or 0) + new_i2 + new_sig + (r['score_gap'] or 0)
    
    new_conf = '高' if new_score >= 80 else ('中' if new_score >= 55 else '低')
    new_plus = 1 if (new_score >= 80 and new_d == 15 and r['score_i1'] == 15) else 0
    
    updates.append((new_d, new_i2, new_sig, new_score, new_conf, new_plus, rid))

cur.executemany("UPDATE mw_signal_daily SET score_d=?,score_i2=?,score_sig=?,score=?,confidence=?,is_plus=? WHERE id=?", updates)
conn.commit()
print(f"Updated: {cur.rowcount}")

cur.execute("SELECT score_d,COUNT(*) FROM mw_signal_daily GROUP BY score_d ORDER BY score_d DESC")
print("score_d:", cur.fetchall())
cur.execute("SELECT score_i2,COUNT(*) FROM mw_signal_daily GROUP BY score_i2 ORDER BY score_i2 DESC")
print("score_i2:", cur.fetchall())
cur.execute("SELECT confidence,COUNT(*) FROM mw_signal_daily GROUP BY confidence")
print("conf:", cur.fetchall())
cur.execute("SELECT COUNT(*),AVG(score),MAX(score) FROM mw_signal_daily")
print("score:", cur.fetchone())
cur.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE is_plus=1")
print("PLUS:", cur.fetchone()[0])
conn.close()
print("Done.")
