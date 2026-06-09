"""
市值快照表 — 为驾驶舱管道提供高性能市值过滤

问题：fundamental_indicator (2.74亿行) 实时查询市值极慢（>120s超时）
方案：每日盘后预计算市值快照，存入轻量表 market_cap_snapshot

用法：
    python src/cockpit/market_cap_snapshot.py          # 全量刷新
    python src/cockpit/market_cap_snapshot.py --date 2026-06-09  # 指定日期
"""
import os
import sys
import sqlite3
import argparse
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')


def init_table(db):
    """创建市值快照表"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS market_cap_snapshot (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT,
            market_cap REAL,          -- 市值（亿元）
            close_price REAL,         -- 最新收盘价
            update_date TEXT           -- 更新日期
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_mcap_cap ON market_cap_snapshot(market_cap)")
    db.commit()


def build_snapshot(db, target_date=None):
    """构建市值快照 — 仅存最新收盘价，市值待回填"""
    run_date = target_date or datetime.now().strftime('%Y-%m-%d')

    # 取最新日期的观察池股票（只取一天，避免 DISTINCT 全表扫描）
    latest_date = db.execute(
        "SELECT MAX(date) FROM discipline_observation_pool"
    ).fetchone()
    if not latest_date or not latest_date[0]:
        print("观察池无数据")
        return

    obs_rows = db.execute(
        "SELECT stock_code, stock_name FROM discipline_observation_pool WHERE date=?",
        (latest_date[0],)
    ).fetchall()

    stock_codes = [r['stock_code'] for r in obs_rows]
    name_map = {r['stock_code']: r['stock_name'] for r in obs_rows}
    print(f"观察池股票: {len(stock_codes)} 只 (日期: {latest_date[0]})")

    # 批量查收盘价（小批次，利用 idx_daily_kline_stock 索引）
    updated = 0
    batch_size = 30

    for i in range(0, len(stock_codes), batch_size):
        batch = stock_codes[i:i+batch_size]
        bp = ','.join(['?'] * len(batch))

        prices = db.execute(f"""
            SELECT stock_code, MAX(close) as close
            FROM daily_kline
            WHERE stock_code IN ({bp})
            GROUP BY stock_code
        """, batch).fetchall()

        for r in prices:
            code = r['stock_code']
            db.execute("""
                INSERT OR REPLACE INTO market_cap_snapshot
                (stock_code, stock_name, market_cap, close_price, update_date)
                VALUES (?, ?, NULL, ?, ?)
            """, (code, name_map.get(code, ''), r['close'], run_date))
            updated += 1

    db.commit()
    print(f"市值快照更新完成: {updated} 只")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='市值快照表构建')
    parser.add_argument('--date', type=str, default=None)
    args = parser.parse_args()

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    init_table(db)
    build_snapshot(db, args.date)
    db.close()
