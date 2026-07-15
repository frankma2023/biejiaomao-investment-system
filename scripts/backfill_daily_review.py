#!/usr/bin/env python3
"""批量补跑每日收盘复盘数据（两融/龙虎榜/大宗）"""
import sqlite3, sys, os, subprocess
from datetime import datetime, timedelta

DB = 'D:\\hanako\\investment-system\\data\\lixinger.db'
SCRIPT = 'D:\\hanako\\investment-system\\scripts\\daily_review.py'

def get_trade_dates(start, end):
    """从 daily_kline 获取交易日列表"""
    db = sqlite3.connect(DB)
    dates = [r[0] for r in db.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date>=? AND date<=? ORDER BY date", (start, end)).fetchall()]
    db.close()
    return dates

def get_already_done():
    """检查哪些日期已有两融数据"""
    db = sqlite3.connect(DB)
    done = [r[0] for r in db.execute(
        "SELECT date FROM daily_review_summary WHERE margin_balance IS NOT NULL AND margin_balance != 0").fetchall()]
    db.close()
    return set(done)

if __name__ == '__main__':
    start = sys.argv[1] if len(sys.argv) > 1 else '2026-04-01'
    end = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime('%Y-%m-%d')
    
    dates = get_trade_dates(start, end)
    done = get_already_done()
    todo = [d for d in dates if d not in done]
    
    print(f'📅 {start} ~ {end}: {len(dates)}个交易日, {len(done)}个已有数据, {len(todo)}个待补跑')
    
    for i, d in enumerate(todo):
        print(f'  [{i+1}/{len(todo)}] {d}...', end=' ', flush=True)
        ret = subprocess.run(['python', SCRIPT, d], capture_output=True, text=True, timeout=120)
        if ret.returncode == 0:
            print('✅')
        else:
            print(f'❌ {ret.stderr[:100]}')
