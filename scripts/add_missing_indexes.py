"""
为频繁查询的表添加缺失的索引
"""
import sqlite3, sys, time

DB = r'D:\hanako\investment-system\data\lixinger.db'
db = sqlite3.connect(DB, timeout=60)
db.execute("PRAGMA journal_mode=WAL")

indexes = [
    # ─── 高频表：日期单独查询 ───

    ("idx_irs_date", "index_rs_daily", "date",
     "指数RS按日期查询（MAX(date)、WHERE date=?），1M行"),

    ("idx_icd_date", "index_crowding_daily", "date",
     "指数拥挤度按日期查询（MAX(date)、WHERE date=?），794K行"),

    ("idx_idk_date", "index_daily_kline", "date",
     "指数K线按日期查询，1.4M行"),

    # ─── 大表：缺 stock_code 索引 ───

    ("idx_chanlun_stock_date", "chanlun_scan_daily", "stock_code, scan_date",
     "缠论扫描按个股+日期查询，5.2M行，当前只有scan_date索引"),

    # ─── 信号表：缺常用查询索引 ───

    ("idx_pp_stock_date", "pocket_pivot_daily", "stock_code, date",
     "口袋支点按个股+日期查询，11.9K行，频繁与mw_signal_daily JOIN"),

    ("idx_pp_date", "pocket_pivot_daily", "date",
     "口袋支点按日期查询（最新信号、回测过滤）"),
]

added = 0
skipped = 0
for idx_name, table, columns, reason in indexes:
    # 检查是否已存在
    exists = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (idx_name,)
    ).fetchone()
    if exists:
        print(f"  ⏭  {idx_name:30s} → 已存在，跳过")
        skipped += 1
        continue

    t0 = time.time()
    try:
        db.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({columns})")
        db.commit()
        elapsed = time.time() - t0
        print(f"  ✅ {idx_name:30s} → {table}({columns})  ({elapsed:.1f}s)")
        print(f"     原因: {reason}")
        added += 1
    except Exception as e:
        print(f"  ❌ {idx_name:30s} → 失败: {e}")

print(f"\n完成: 新增 {added} 个, 跳过 {skipped} 个")

# 验证
print("\n=== 验证 ===")
cur = db.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%' ORDER BY name")
for r in cur.fetchall():
    print(f"  {r[0]:30s} → {r[1]}")
db.close()
