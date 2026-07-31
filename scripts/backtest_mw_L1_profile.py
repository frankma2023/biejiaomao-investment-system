"""
MW 信号回测 · 第一层：信号基础画像
输出: JSON + 终端表格
"""
import sqlite3, json, os, sys
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
OUT = 'D:/hanako/investment-system/config/strategy/mw_signal_profile.json'

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# ── 总览 ──
total = conn.execute("SELECT COUNT(*) FROM mw_signal_daily").fetchone()[0]
b1_only = conn.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b2_date IS NULL OR b2_date=''").fetchone()[0]
with_b2 = total - b1_only
plus = conn.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE is_plus=1").fetchone()[0]
stocks = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM mw_signal_daily").fetchone()[0]
rs_covered = conn.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE ind_rs20 IS NOT NULL").fetchone()[0]
score_avg = conn.execute("SELECT AVG(tech_score) FROM mw_signal_daily WHERE tech_score>0").fetchone()[0]

print("=" * 70)
print("MW 信号基础画像 · 2016-01 ~ 2026-07")
print("=" * 70)
print(f"总信号: {total:,}  股票数: {stocks:,}")
print(f"含 B2: {with_b2:,} ({with_b2/total*100:.0f}%)   仅 B1: {b1_only:,} ({b1_only/total*100:.0f}%)")
print(f"PLUS: {plus:,} ({plus/total*100:.1f}%)")
print(f"行业RS覆盖: {rs_covered:,} ({rs_covered/total*100:.0f}%)")
print(f"关注分均值: {score_avg:.0f}")

# ── 按年 ──
print("\n" + "-" * 70)
print(f"{'年份':<8} {'信号':>8} {'B2覆盖':>8} {'PLUS':>6} {'关注分':>6} {'decline':>8} {'h_rs250':>8}")
print("-" * 70)
yearly = []
for year in range(2016, 2027):
    r = conn.execute("""
        SELECT COUNT(*) as cnt,
               SUM(CASE WHEN b2_date IS NOT NULL AND b2_date!='' THEN 1 ELSE 0 END) as b2_cnt,
               SUM(CASE WHEN is_plus=1 THEN 1 ELSE 0 END) as plus_cnt,
               AVG(CASE WHEN tech_score>0 THEN tech_score END) as avg_ts,
               AVG(decline_pct) as avg_decline,
               AVG(h_rs250) as avg_rs
        FROM mw_signal_daily
        WHERE b1_date >= ? AND b1_date < ?
    """, (f'{year}-01-01', f'{year+1}-01-01')).fetchone()
    if r['cnt'] == 0:
        continue
    yearly.append(dict(r))
    print(f"{year:<8} {r['cnt']:>8,} {r['b2_cnt']/r['cnt']*100:>7.0f}% {r['plus_cnt']:>6,} {r['avg_ts']:>6.0f} {r['avg_decline']:>7.1f}% {r['avg_rs']:>8.0f}")

# ── 关注分分层 ──
print("\n" + "-" * 70)
print(f"{'关注分':<16} {'信号':>8} {'占比':>8} {'B2覆盖':>8}")
print("-" * 70)
tiers = [
    ('极高 ≥80',  80, 999),
    ('高 65~79',  65, 79),
    ('关注 50~64', 50, 64),
    ('一般 35~49', 35, 49),
    ('低 <35',     0,  34),
]
tier_data = []
for label, lo, hi in tiers:
    r = conn.execute("""
        SELECT COUNT(*) as cnt,
               SUM(CASE WHEN b2_date IS NOT NULL AND b2_date!='' THEN 1 ELSE 0 END) as b2_cnt
        FROM mw_signal_daily
        WHERE tech_score >= ? AND tech_score <= ?
    """, (lo, hi)).fetchone()
    if r['cnt'] == 0:
        continue
    tier_data.append({'label': label, **dict(r)})
    print(f"{label:<16} {r['cnt']:>8,} {r['cnt']/total*100:>7.0f}% {r['b2_cnt']/r['cnt']*100 if r['cnt'] else 0:>7.0f}%")

# ── 回调深度分层 ──
print("\n" + "-" * 70)
print(f"{'回调深度':<16} {'信号':>8} {'占比':>8} {'关注分':>8}")
print("-" * 70)
decline_tiers = [
    ('<15%',   0, 15),
    ('15~20%', 15, 20),
    ('20~25%', 20, 25),
    ('25~35%', 25, 35),
    ('>35%',   35, 999),
]
decline_data = []
for label, lo, hi in decline_tiers:
    r = conn.execute("""
        SELECT COUNT(*) as cnt,
               AVG(tech_score) as avg_ts
        FROM mw_signal_daily
        WHERE decline_pct >= ? AND decline_pct < ?
    """, (lo, hi if hi < 999 else 999)).fetchone()
    if r['cnt'] == 0:
        continue
    decline_data.append({'label': label, **dict(r)})
    print(f"{label:<16} {r['cnt']:>8,} {r['cnt']/total*100:>7.0f}% {r['avg_ts']:>8.0f}")

# ── 行业分布 Top 10 ──
print("\n" + "-" * 70)
print(f"{'行业':<24} {'信号':>8} {'占比':>8} {'关注分':>8}")
print("-" * 70)
ind_rows = conn.execute("""
    SELECT ind_name, COUNT(*) as cnt, AVG(tech_score) as avg_ts
    FROM mw_signal_daily
    WHERE ind_name IS NOT NULL AND ind_name != ''
    GROUP BY ind_name
    ORDER BY cnt DESC
    LIMIT 10
""").fetchall()
ind_data = []
for r in ind_rows:
    ind_data.append(dict(r))
    print(f"{r['ind_name']:<24} {r['cnt']:>8,} {r['cnt']/total*100:>7.0f}% {r['avg_ts']:>8.0f}")

# ── 写入 JSON ──
result = {
    'meta': {
        'date': '2026-07-21',
        'version': 'v5.2',
        'engine': 'mw_signal.py scan_stock',
        'total_signals': total,
        'stock_count': stocks,
        'b2_count': with_b2,
        'b1_only': b1_only,
        'plus_count': plus,
        'rs_coverage': rs_covered,
        'avg_attention_score': round(score_avg, 1),
    },
    'yearly': yearly,
    'attention_tiers': tier_data,
    'decline_tiers': decline_data,
    'top_industries': ind_data,
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"\nJSON → {OUT}")

conn.close()
