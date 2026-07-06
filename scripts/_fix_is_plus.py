import sqlite3, time

for attempt in range(10):
    try:
        db = sqlite3.connect('data/lixinger.db', timeout=30)
        db.execute("PRAGMA busy_timeout=30000")
        cur = db.cursor()

        # 统计符合条件的
        cur.execute("""
            SELECT COUNT(*) FROM mw_signal_daily 
            WHERE b2_date IS NOT NULL 
              AND score >= 80 
              AND score_d = 15 
              AND score_i1 >= 10 
              AND stock_code != '_sentinel_'
        """)
        qualifying = cur.fetchone()[0]
        print(f'符合条件的: {qualifying}')

        # 执行 UPDATE
        cur.execute("""
            UPDATE mw_signal_daily 
            SET is_plus = 1 
            WHERE b2_date IS NOT NULL 
              AND score >= 80 
              AND score_d = 15 
              AND score_i1 >= 10 
              AND stock_code != '_sentinel_'
        """)
        db.commit()
        print(f'已更新行数: {cur.rowcount}')

        # 验证
        cur.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE is_plus = 1")
        print(f'验证 is_plus=1: {cur.fetchone()[0]}')

        # 按年分布
        cur.execute("""
            SELECT substr(b2_date,1,4), COUNT(*) 
            FROM mw_signal_daily 
            WHERE is_plus = 1 
            GROUP BY 1 ORDER BY 1
        """)
        print('按年分布:')
        for r in cur.fetchall():
            print(f'  {r[0]}: {r[1]}')

        db.close()
        break
    except sqlite3.OperationalError as e:
        print(f'  尝试 {attempt+1}/10: {e}')
        if attempt < 9:
            time.sleep(3 + attempt * 2)
        else:
            print('重试耗尽，数据库仍被锁')
            raise
