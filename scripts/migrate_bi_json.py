"""
chanlun_scan_daily 的 bi_json 拆分为独立表
用法：python scripts/migrate_bi_json.py
"""
import sqlite3, time, os, sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')

def main():
    db = sqlite3.connect(DB, timeout=60)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=NORMAL')
    
    t0 = time.time()

    # ═══ Step 1: 建独立表 ═══
    print('[1/4] 创建 chanlun_bi_json 表...')
    db.execute('''CREATE TABLE IF NOT EXISTS chanlun_bi_json (
        stock_code TEXT NOT NULL, scan_date TEXT NOT NULL,
        bi_json TEXT, PRIMARY KEY(stock_code, scan_date))''')
    db.commit()

    # ═══ Step 2: 分批迁移 bi_json ═══
    total = db.execute("SELECT COUNT(*) FROM chanlun_scan_daily WHERE bi_json IS NOT NULL").fetchone()[0]
    print(f'[2/4] 迁移 {total:,} 行 bi_json...')
    
    batch = 50000
    copied = 0
    for offset in range(0, total, batch):
        rows = db.execute(
            "SELECT stock_code, scan_date, bi_json FROM chanlun_scan_daily WHERE bi_json IS NOT NULL LIMIT ? OFFSET ?",
            (batch, offset)
        ).fetchall()
        for stock_code, scan_date, bi_json in rows:
            db.execute(
                "INSERT OR IGNORE INTO chanlun_bi_json (stock_code, scan_date, bi_json) VALUES (?,?,?)",
                (stock_code, scan_date, bi_json)
            )
        db.commit()
        copied += len(rows)
        pct = copied / total * 100
        elapsed = time.time() - t0
        eta = elapsed / copied * (total - copied) if copied > 0 else 0
        print(f'  {copied:,}/{total:,} ({pct:.0f}%) ETA {eta/60:.0f}min')

    # ═══ Step 3: 重建主表（去掉 bi_json 列）═══
    print('[3/4] 重建主表（移除 bi_json 列）...')
    db.execute('BEGIN')
    db.execute('''CREATE TABLE chanlun_scan_daily_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_date TEXT, stock_code TEXT, stock_name TEXT,
        bi_count INTEGER, zs_count INTEGER, segment_count INTEGER,
        latest_bi_dir TEXT, latest_bi_power REAL,
        divergence_count INTEGER, latest_div_type TEXT,
        trade_signal_count INTEGER, latest_trade_type TEXT,
        latest_trade_side TEXT, latest_trade_price REAL,
        resonance_strength TEXT, created_at TEXT)''')
    
    new_cnt = db.execute(
        "INSERT INTO chanlun_scan_daily_new SELECT id,scan_date,stock_code,stock_name,bi_count,zs_count,segment_count,latest_bi_dir,latest_bi_power,divergence_count,latest_div_type,trade_signal_count,latest_trade_type,latest_trade_side,latest_trade_price,resonance_strength,created_at FROM chanlun_scan_daily"
    ).rowcount
    db.execute('DROP TABLE chanlun_scan_daily')
    db.execute('ALTER TABLE chanlun_scan_daily_new RENAME TO chanlun_scan_daily')
    db.execute('COMMIT')
    print(f'  {new_cnt:,} 行已迁移')

    # ═══ Step 4: 验证 ═══
    print('[4/4] 验证...')
    main_rows = db.execute('SELECT COUNT(*) FROM chanlun_scan_daily').fetchone()[0]
    bi_rows = db.execute('SELECT COUNT(*) FROM chanlun_bi_json').fetchone()[0]
    main_cols = [r[1] for r in db.execute('PRAGMA table_info(chanlun_scan_daily)').fetchall()]
    has_bi = 'bi_json' in main_cols
    
    db.close()
    
    total_elapsed = time.time() - t0
    print()
    print(f'=== 完成 ({total_elapsed/60:.1f}min) ===')
    has_bi_text = '仍存在' if has_bi else '已移除'
    print(f'  chanlun_scan_daily: {main_rows:,} 行 (bi_json 列: {has_bi_text})')
    print(f'  chanlun_bi_json:   {bi_rows:,} 行')
    print(f'  ✅' if not has_bi and main_rows > 0 and bi_rows > 0 else '  ❌ 验证失败')

if __name__ == '__main__':
    main()
