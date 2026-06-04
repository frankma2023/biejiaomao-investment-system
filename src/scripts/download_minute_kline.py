"""下载观察池+精选池的分钟K线数据（60分+15分）

用途：为缠论区间套分析（日→60分→15分三级级联）缓存分钟数据
优化：一次登录批量拉取，避免逐只登录登出

使用：
    python src/scripts/download_minute_kline.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import sqlite3
import baostock as bs
import pandas as pd
from datetime import datetime
from data.lixr_api.api_stock_minute import fetch_minute_kline, TABLE_MAP, _ensure_table

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')


def main():
    conn = sqlite3.connect(DB_PATH)
    codes = set()

    # 来源1：最新观察池
    try:
        for r in conn.execute("""
            SELECT DISTINCT stock_code FROM discipline_observation_pool
            WHERE date = (SELECT MAX(date) FROM discipline_observation_pool)
        """).fetchall():
            codes.add(r[0])
    except Exception as e:
        print(f'观察池读取失败: {e}')

    # 来源2：最新精选池
    try:
        for r in conn.execute("""
            SELECT DISTINCT stock_code FROM discipline_screening_daily
            WHERE date = (SELECT MAX(date) FROM discipline_screening_daily)
        """).fetchall():
            codes.add(r[0])
    except Exception as e:
        print(f'精选池读取失败: {e}')

    if not codes:
        print('无关注股票')
        conn.close()
        return

    total = len(codes)
    print(f'共 {total} 只股票，一次登录批量拉取，预计 {total * 2 // 60} 分钟...\n')

    # 确保表存在
    _ensure_table(conn, TABLE_MAP['60'])
    _ensure_table(conn, TABLE_MAP['15'])
    conn.execute("PRAGMA journal_mode=WAL")

    # 一次登录
    bs.login()
    print('Baostock 已连接\n')

    end_date = datetime.now().strftime('%Y-%m-%d')
    ok = 0
    fail = 0
    total_inserted = 0

    for i, code in enumerate(sorted(codes)):
        try:
            for freq in ['60', '15']:
                table = TABLE_MAP[freq]
                # 增量：仅拉取缺失数据
                row = conn.execute(f"SELECT MAX(date) FROM {table} WHERE stock_code=?", (code,)).fetchone()
                if row and row[0]:
                    start = row[0]
                else:
                    start = (datetime.now().replace(day=1) - pd.DateOffset(months=4)).strftime('%Y-%m-%d')

                if start >= end_date:
                    continue

                df = fetch_minute_kline(code, freq, start, end_date, auto_session=False)
                if df.empty:
                    continue

                for _, rd in df.iterrows():
                    conn.execute(
                        f"INSERT OR IGNORE INTO {table} (stock_code, date, time, open, high, low, close, volume, amount) "
                        f"VALUES (?,?,?,?,?,?,?,?,?)",
                        (code, str(rd['date']), str(rd['time']),
                         float(rd['open']), float(rd['high']), float(rd['low']), float(rd['close']),
                         float(rd['volume']), float(rd['amount'])))
                conn.commit()
            ok += 1
        except Exception as e:
            print(f'  [{i+1}/{total}] {code} 失败: {e}')
            fail += 1
            try:
                bs.login()  # 断线重连
            except:
                pass
            continue

        if (i + 1) % 50 == 0:
            print(f'  进度: {i+1}/{total} (成功 {ok}, 失败 {fail})')

    bs.logout()
    conn.close()

    print(f'\n完成: 成功 {ok} 只, 失败 {fail} 只')


if __name__ == '__main__':
    main()
