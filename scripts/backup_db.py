"""
SQLite 简易备份 v2 — 全量+增量混合
首次全量，后续只拷 WAL（几 MB），定期 checkpoint 合并
"""
import sqlite3, os, shutil, time
from datetime import datetime

SRC = 'D:/hanako/investment-system/data/lixinger.db'
DST_DIR = 'Z:/databak'
os.makedirs(DST_DIR, exist_ok=True)

LATEST_DB = os.path.join(DST_DIR, 'lixinger.db')
LATEST_WAL = os.path.join(DST_DIR, 'lixinger.db-wal')

# 检查是否已存在基准副本
if not os.path.exists(LATEST_DB):
    print('首次备份：全量复制...')
    conn = sqlite3.connect(SRC, timeout=30)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    t0 = time.time()
    shutil.copy2(SRC, LATEST_DB)
    print(f'全量完成: {os.path.getsize(LATEST_DB)/1024**3:.1f}GB, {time.time()-t0:.0f}s')
else:
    # 增量：拷贝 WAL 文件
    # WAL + 主文件 = 完整数据库（SQLite 自动从 WAL 恢复）
    src_wal = SRC + '-wal'
    if os.path.exists(src_wal):
        wal_size = os.path.getsize(src_wal)
        if wal_size > 1024 * 1024:  # > 1MB 才值得拷
            shutil.copy2(src_wal, LATEST_WAL)
            print(f'WAL 增量: {wal_size/1024**2:.1f}MB')
        else:
            print('WAL 太小，跳过增量')
    else:
        print('无 WAL 文件')

    # 每 7 天做一次全量 checkpoint + 重拷（控制副本体积）
    if os.path.getmtime(LATEST_DB) < time.time() - 7 * 86400:
        print('周度全量合并...')
        conn = sqlite3.connect(SRC, timeout=30)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        shutil.copy2(SRC, LATEST_DB)
        if os.path.exists(LATEST_WAL):
            os.remove(LATEST_WAL)
        print('全量完成')
