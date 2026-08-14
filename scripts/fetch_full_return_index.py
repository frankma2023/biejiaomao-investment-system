# -*- coding: utf-8 -*-
"""
中证红利全收益指数 H00922 数据拉取
==================================
数据源：akshare stock_zh_index_hist_csindex（中证官网）
表：index_full_return_daily（stock_code/date/close/change_pct/pe_ttm/updated_at）

用法:
  python scripts/fetch_full_return_index.py            # 增量（表内 MAX(date) 起）
  python scripts/fetch_full_return_index.py --full     # 全量（2018-01-01 起）
"""
import sys, os, sqlite3, argparse
from datetime import datetime

# Windows 重定向时避免 emoji/中文 stdout 编码崩溃（仓库先例）
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
SYMBOL = 'H00922'
FULL_START = '20180101'
TODAY = datetime.now().strftime('%Y%m%d')

# 数据源（延迟导入，避免 import 时联网）
def fetch_from_akshare(start_date, end_date):
    import akshare as ak
    df = ak.stock_zh_index_hist_csindex(symbol=SYMBOL, start_date=start_date, end_date=end_date)
    return df


def main():
    parser = argparse.ArgumentParser(description='拉取 H00922 中证红利全收益指数')
    parser.add_argument('--full', action='store_true', help='全量拉取（2018-01-01 起），默认增量')
    args = parser.parse_args()

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""CREATE TABLE IF NOT EXISTS index_full_return_daily (
            stock_code TEXT NOT NULL,
            date TEXT NOT NULL,
            close REAL,
            change_pct REAL,
            pe_ttm REAL,
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (stock_code, date)
        )""")

        if args.full:
            start = FULL_START
            print(f'[全量模式] {SYMBOL} 从 {FULL_START} 拉取')
        else:
            r = conn.execute("SELECT MAX(date) FROM index_full_return_daily WHERE stock_code=?", (SYMBOL,)).fetchone()
            if r and r[0]:
                start = r[0].replace('-', '')
                # 从 MAX(date) 当天起重拉（INSERT OR REPLACE 幂等），可顺带修复最后一天脏数据；比 MAX+1 更稳
                print(f'[增量模式] 表内已有数据至 {r[0]}，从 {start} 拉取（幂等覆盖）')
            else:
                start = FULL_START
                print(f'[增量模式] 表为空，回退全量 {FULL_START}')

        print(f'拉取中... ({start} ~ {TODAY})')
        df = fetch_from_akshare(start, TODAY)
        if df is None or len(df) == 0:
            print('❌ 无数据返回')
            return

        print(f'拉取到 {len(df)} 条 ({df["日期"].min()} ~ {df["日期"].max()})')

        # 字段映射
        rows = []
        for _, r in df.iterrows():
            # 日期转标准格式 2026-08-13（与 index_daily_kline 一致；strptime 兼容多种输入格式）
            date = datetime.strptime(str(r['日期']).strip()[:10], '%Y-%m-%d').strftime('%Y-%m-%d')
            close = float(r['收盘']) if r['收盘'] == r['收盘'] else None
            chg = float(r['涨跌幅']) / 100 if r['涨跌幅'] == r['涨跌幅'] else None
            pe = float(r['滚动市盈率']) if r['滚动市盈率'] == r['滚动市盈率'] else None
            rows.append((SYMBOL, date, close, chg, pe))

        # 幂等写入
        conn.executemany(
            "INSERT OR REPLACE INTO index_full_return_daily (stock_code, date, close, change_pct, pe_ttm) VALUES (?,?,?,?,?)",
            rows)
        conn.commit()

        # 统计
        total = conn.execute("SELECT COUNT(*) FROM index_full_return_daily WHERE stock_code=?", (SYMBOL,)).fetchone()[0]
        latest = conn.execute(
            "SELECT date, close FROM index_full_return_daily WHERE stock_code=? ORDER BY date DESC LIMIT 1",
            (SYMBOL,)).fetchone()
        print(f'✅ 入库完成：表内共 {total} 条，最新 {latest[0]} 收盘 {latest[1]}')
    finally:
        if conn:
            conn.close()


if __name__ == '__main__':
    main()
