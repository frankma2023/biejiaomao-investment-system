import sqlite3
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db', timeout=60)
db.execute("PRAGMA busy_timeout=60000")
c = db.cursor()

print('=== 刷新 MW 信号 confidence 字段 ===')
print()

# ── B1-only：新规则 v3.4 ──
c.execute("""
    UPDATE mw_signal_daily SET confidence = '高'
    WHERE b2_date IS NULL AND b1_date IS NOT NULL 
      AND stock_code != '_sentinel_' AND score >= 55
""")
print(f'B1-only → 高 (score≥55): {c.rowcount}')

c.execute("""
    UPDATE mw_signal_daily SET confidence = '中'
    WHERE b2_date IS NULL AND b1_date IS NOT NULL 
      AND stock_code != '_sentinel_' AND score >= 40 AND score < 55
""")
print(f'B1-only → 中 (40≤score<55): {c.rowcount}')

c.execute("""
    UPDATE mw_signal_daily SET confidence = '低'
    WHERE b2_date IS NULL AND b1_date IS NOT NULL 
      AND stock_code != '_sentinel_' AND score < 40
""")
print(f'B1-only → 低 (score<40): {c.rowcount}')

# ── B1+B2：维持原规则 ──
c.execute("""
    UPDATE mw_signal_daily SET confidence = '高'
    WHERE b2_date IS NOT NULL AND stock_code != '_sentinel_' AND score >= 80
""")
print(f'\nB1+B2 → 高 (score≥80): {c.rowcount}')

c.execute("""
    UPDATE mw_signal_daily SET confidence = '中'
    WHERE b2_date IS NOT NULL AND stock_code != '_sentinel_' AND score >= 55 AND score < 80
""")
print(f'B1+B2 → 中 (55≤score<80): {c.rowcount}')

c.execute("""
    UPDATE mw_signal_daily SET confidence = '低'
    WHERE b2_date IS NOT NULL AND stock_code != '_sentinel_' AND score < 55
""")
print(f'B1+B2 → 低 (score<55): {c.rowcount}')

db.commit()

# ── 验证 ──
print(f'\n=== 验证 ===')
c.execute("""
    SELECT CASE WHEN b2_date IS NULL THEN 'B1-only' ELSE 'B1+B2' END as type,
           confidence, COUNT(*)
    FROM mw_signal_daily WHERE stock_code != '_sentinel_'
    GROUP BY 1, 2 ORDER BY 1, 2
""")
for r in c.fetchall():
    print(f'  {r[0]:8s} {r[1]:4s}: {r[2]:>6,d}')

c.execute("""
    SELECT confidence, COUNT(*) FROM mw_signal_daily 
    WHERE b1_date >= '2026-06-30' AND b2_date IS NULL AND stock_code != '_sentinel_'
    GROUP BY confidence ORDER BY confidence
""")
print(f'\n今日(2026-06-30) B1-only 分布:')
for r in c.fetchall():
    print(f'  {r[0]}: {r[1]}')

db.close()
print('\n完成。')
