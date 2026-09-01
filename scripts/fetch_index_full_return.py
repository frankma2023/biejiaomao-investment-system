# -*- coding: utf-8 -*-
"""
scripts/fetch_index_full_return.py — 红利指数全收益（total_return）拉取
=====================================================================
数据源：理杏仁 /index/candlestick type=total_return（全收益率点位，2016 前无数据）
表：index_full_return_daily（stock_code=原指数代码，与价格指数 index_daily_kline 分表）

用法：
  python scripts/fetch_index_full_return.py            # 增量（各指数表内 MAX(date) 起）
  python scripts/fetch_index_full_return.py --full     # 全量（2016-01-01 起）
"""
import sys, os, io, sqlite3, argparse
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from common import api_post

DB_PATH = os.path.join(os.path.dirname(SCRIPT_DIR), 'data', 'lixinger.db')

# 全收益指数池（理杏仁 total_return 支持的指数）
INDICES = [
    ('000922', '中证红利'),
    ('H30269', '红利低波'),
    ('930955', '红利低波100'),
    ('931468', '红利质量'),
    ('930914', '港股通高股息'),
    ('930839', '港股通高股息精选'),
    ('000015', '红利指数'),
    ('931848', '800红利低波'),
]

FULL_START = '2016-01-01'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--full', action='store_true', help='全量（2016 起），默认增量')
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS index_full_return_daily (
        stock_code TEXT NOT NULL, date TEXT NOT NULL, close REAL,
        change_pct REAL, pe_ttm REAL,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (stock_code, date))""")

    for code, name in INDICES:
        if args.full:
            start = FULL_START
        else:
            r = conn.execute("SELECT MAX(date) FROM index_full_return_daily WHERE stock_code=?", (code,)).fetchone()
            start = r[0] if r and r[0] else FULL_START
        try:
            data = api_post('/index/candlestick', {
                'stockCode': code, 'type': 'total_return',
                'startDate': start, 'endDate': datetime.now().strftime('%Y-%m-%d'),
            })
            if not data:
                print(f'⚠️ {code} {name}: 无数据')
                continue
            rows = [(code, d['date'][:10], d['close'], d.get('change'), None) for d in data]
            conn.executemany(
                "INSERT OR REPLACE INTO index_full_return_daily (stock_code, date, close, change_pct, pe_ttm) VALUES (?,?,?,?,?)",
                rows)
            conn.commit()
            print(f'✅ {code} {name}: +{len(rows)} 条 ({rows[-1][1]} ~ {rows[0][1]})')
        except Exception as e:
            print(f'❌ {code} {name}: {str(e)[:70]}')

    conn.close()
    print('完成')


if __name__ == '__main__':
    main()
