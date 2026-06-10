import sqlite3
c=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
queries={
    "daily_kline":"SELECT MIN(date),MAX(date),COUNT(*) FROM daily_kline",
    "stock_rs_daily":"SELECT MIN(date),MAX(date),COUNT(*) FROM stock_rs_daily",
    "index_daily_kline":"SELECT MIN(date),MAX(date),COUNT(*) FROM index_daily_kline",
    "mw_signal_daily":"SELECT MIN(b2_date),MAX(b2_date),COUNT(*) FROM mw_signal_daily",
    "chanlun_scan_daily":"SELECT MIN(scan_date),MAX(scan_date),COUNT(*) FROM chanlun_scan_daily",
    "stock_basic":"SELECT COUNT(*) FROM stock_basic WHERE listing_status='normally_listed'",
}
for tbl,q in queries.items():
    r=c.execute(q).fetchone()
    print(f"{tbl:25s}: {r[0]} ~ {r[1]} ({r[2]:,} rows)")
c.close()
