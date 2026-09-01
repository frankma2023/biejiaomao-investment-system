# -*- coding: utf-8 -*-
"""
scripts/fetch_hk_etf.py — 港股红利ETF 每日数据拉取（akshare）
=================================================================
数据源：新浪 fund_etf_hist_sina（主）+ 东财 fund_etf_hist_em（备）
表：hk_etf_daily（与 index_daily_kline 同构）
ETF 清单（配置在脚本内，含静态信息：费率/规模/跟踪指数）：
  513820 港股通高股息        0.60% 汇添富
  159691 港股通高股息精选    0.52% 工银瑞信
  513630 标普港股红利低波    0.60% 摩根
  159545 恒生港股通高息低波  0.20% 易方达
更新：盘后（港股 16:00 后）增量更新
"""
import os
import sys
import sqlite3
import time
import argparse
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')

# 港股红利 ETF 清单（code, 名称, 管理费+托管费%, 跟踪指数, 基金管理人）
HK_ETFS = [
    ('513820', '港股通高股息', 0.60, '中证港股通高股息投资指数(930914)', '汇添富'),
    ('159691', '港股通高股息精选', 0.52, '中证港股通高股息精选指数(930839)', '工银瑞信'),
    ('513630', '标普港股红利低波', 0.60, '标普港股通低波红利指数（S&P Access HK Low Vol High Div，标普编制）', '摩根'),
    ('159545', '恒生港股通高息低波', 0.20, '恒生港股通高股息低波动指数(HSHYLV)', '易方达'),
    # A股 ETF（券商网格用，type=a）
    ('512000', '券商ETF', 0.50, '证券公司指数(399975)', '华宝'),
]

CREATE_SQL = """CREATE TABLE IF NOT EXISTS hk_etf_daily (
    stock_code TEXT NOT NULL,
    date       TEXT NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    volume     REAL,
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (stock_code, date)
)"""

# 全收益（后复权）K线表——分红再投后的真实收益序列（腾讯 hfq 源）
CREATE_FR_SQL = """CREATE TABLE IF NOT EXISTS hk_etf_full_return (
    stock_code TEXT NOT NULL,
    date       TEXT NOT NULL,
    close      REAL,
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (stock_code, date)
)"""


def sina_symbol(code):
    return ('sh' if code.startswith('5') else 'sz') + code


def fetch_sina(code):
    import akshare as ak
    df = ak.fund_etf_hist_sina(symbol=sina_symbol(code))
    rows = []
    for _, r in df.iterrows():
        d = str(r['date'])[:10]
        rows.append((code, d, float(r['open']), float(r['high']), float(r['low']),
                     float(r['close']), float(r['volume']) if r['volume'] == r['volume'] else None))
    return rows


def fetch_em(code):
    import akshare as ak
    df = ak.fund_etf_hist_em(symbol=code, period="daily", start_date="20100101",
                             end_date=datetime.now().strftime('%Y%m%d'), adjust="")
    rows = []
    for _, r in df.iterrows():
        rows.append((code, str(r['日期']), float(r['开盘']), float(r['最高']), float(r['最低']),
                     float(r['收盘']), float(r['成交量'])))
    return rows


def fetch_tx_hfq(code):
    """腾讯后复权（全收益口径，含分红再投）"""
    import requests
    sym = ('sh' if code.startswith('5') else 'sz') + code
    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
    end_d = datetime.now().strftime('%Y-%m-%d')
    params = {'param': f'{sym},day,2010-01-01,{end_d},2000,hfq'}
    r = requests.get(url, params=params, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
    j = r.json()
    data = j.get('data', {}).get(sym, {})
    key = 'hfq' if 'hfq' in data else 'hfqday'
    klines = data.get(key) or []
    return [(code, k[0], float(k[2])) for k in klines]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--full', action='store_true', help='全量拉取（忽略表内已有）')
    args = ap.parse_args()

    db = sqlite3.connect(DB_PATH)
    db.execute(CREATE_SQL)
    db.execute(CREATE_FR_SQL)

    for code, name, fee, index_name, mgr in HK_ETFS:
        try:
            rows = fetch_sina(code)
        except Exception as e:
            print(f'⚠️ {code} 新浪失败: {str(e)[:60]}，尝试东财...')
            time.sleep(3)
            try:
                rows = fetch_em(code)
            except Exception as e2:
                print(f'❌ {code} 东财也失败: {str(e2)[:60]}')
                continue

        # 增量过滤
        if not args.full:
            r = db.execute("SELECT MAX(date) FROM hk_etf_daily WHERE stock_code=?", (code,)).fetchone()
            last = r[0] if r and r[0] else '0000-00-00'
            rows = [x for x in rows if x[1] > last]

        if rows:
            db.executemany("""INSERT OR REPLACE INTO hk_etf_daily
                (stock_code, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)""", rows)
            db.commit()
            cnt = db.execute("SELECT COUNT(*) FROM hk_etf_daily WHERE stock_code=?", (code,)).fetchone()[0]
            print(f'✅ {code} {name}: 日线入库 {len(rows)} 条（累计 {cnt}）')

        # 全收益（后复权）——W7 修复：全量 DELETE+INSERT（后复权价随每次分红全历史重算，增量会新旧口径混；数据量小成本可忽略）
        try:
            fr = fetch_tx_hfq(code)
            if fr:
                db.execute("DELETE FROM hk_etf_full_return WHERE stock_code=?", (code,))
                db.executemany("INSERT OR REPLACE INTO hk_etf_full_return (stock_code, date, close) VALUES (?,?,?)", fr)
                db.commit()
                cnt2 = db.execute("SELECT COUNT(*) FROM hk_etf_full_return WHERE stock_code=?", (code,)).fetchone()[0]
                print(f'✅ {code} {name}: 全收益全量刷新 {len(fr)} 条（累计 {cnt2}）')
        except Exception as e:
            db.rollback()  # review: DELETE+INSERT 半截时回滚，防下一轮 commit 带脏
            print(f'⚠️ {code} 全收益拉取失败: {str(e)[:60]}')

    db.close()


if __name__ == '__main__':
    main()
