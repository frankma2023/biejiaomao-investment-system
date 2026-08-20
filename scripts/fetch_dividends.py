# -*- coding: utf-8 -*-
"""
scripts/fetch_dividends.py — 分红记录全量拉取（三类数据源）
=================================================================
个股      → 理杏仁 /company/dividend（每股/派息率/总额/状态）
场外基金  → akshare fund_open_fund_info_em 分红送配（每10份）
场内ETF   → 复权差异反推（hfq vs raw 跳变 ≈ 每股分红）
表：dividend_records（code, kind, ex_date, dividend, payout_ratio, total_amount, status, source）
"""
import os
import sys
import sqlite3
import argparse
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))

CREATE_SQL = """CREATE TABLE IF NOT EXISTS dividend_records (
    code         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    ex_date      TEXT NOT NULL,
    dividend     REAL,
    payout_ratio REAL,
    total_amount REAL,
    status       TEXT,
    source       TEXT NOT NULL,
    PRIMARY KEY (code, ex_date, source)
)"""

# 配置：个股清单（后续可扩展）
STOCKS = ['600519', '000651', '601318', '600887', '601899']
# 场外基金清单
FUNDS_OFF = ['100032', '012643', '023917']
# 场内ETF清单
FUNDS_ETF = ['515100', '513820', '159691', '513630', '159545']


def fetch_stock(code):
    """理杏仁个股分红（最近10年）"""
    import common
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=10 * 365)).strftime('%Y-%m-%d')
    data = common.api_post('/company/dividend', {
        'stockCode': code, 'startDate': start, 'endDate': end,
    }, timeout=60)
    rows = []
    for d in data:
        ex_date = str(d.get('exDate') or d.get('date') or '')[:10]
        if not ex_date:  # W3：空日期跳过，避免主键污染
            continue
        rows.append((code, 'stock', ex_date,
                     d.get('dividend'), d.get('annualNetProfitDividendRatio'),  # W4: 派息率为0-1小数（实测0.519），展示时×100
                     d.get('dividendAmount'), d.get('status'), 'lixinger'))
    return rows


def fetch_fund_off(code):
    """场外基金分红（akshare 分红送配）"""
    import akshare as ak
    import re
    df = ak.fund_open_fund_info_em(symbol=code, indicator="分红送配详情")
    rows = []
    if df is not None and len(df):
        for _, r in df.iterrows():
            d = str(r.get('除息日') or r.get('权益登记日') or '')[:10]  # O3：优先除息日
            raw = str(r.get('每10份分红') or '')
            m = re.search(r'派现金([0-9.]+)元', raw)
            if not m:
                print(f'⚠️ {code} 分红格式未匹配: {raw}')
                continue
            if d:
                rows.append((code, 'fund_off', d, float(m.group(1)) / 10, None, None, 'implemented', 'akshare'))
    return rows


def fetch_fund_etf(code):
    """场内ETF分红：腾讯 raw vs hfq 差异反推（东财 fhsp 接口已废，datetime.now().strftime('%Y-%m-%d') 实测）"""
    import requests
    sym = ('sh' if code.startswith('5') else 'sz') + code

    def tx(fq):
        url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
        end_d = datetime.now().strftime('%Y-%m-%d')
        params = {'param': f'{sym},day,2010-01-01,{end_d},2000,{fq}'}
        r = requests.get(url, params=params, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
        j = r.json()
        data = j.get('data', {}).get(sym, {})
        key = fq if fq in data else (fq + 'day')
        return data.get(key) or data.get('day') or []

    hfq = tx('hfq')
    raw = tx('')
    if not hfq or not raw:
        return []
    h = {k[0]: float(k[2]) for k in hfq}
    r_ = {k[0]: float(k[2]) for k in raw}
    rows = []
    prev = None
    prev_d = None
    for d in sorted(h.keys()):
        if d in r_ and r_[d]:
            ratio = h[d] / r_[d]
            if prev is not None and abs(ratio - prev) > 0.002 and prev_d in r_ and r_[d]:
                # 每股分红 ≈ 复权跳变 × 当日raw价（无送转时近似）
                div = round((ratio - prev) * r_[d], 4)
                # W8：合理性过滤——送转 10送10 会被误判为"每股分红≈股价"，超 raw价×30% 视为疑似送转跳过
                if 0.0005 < div < r_[d] * 0.30:
                    rows.append((code, 'fund_etf', d, div, None, None, 'implemented', 'tx_reverse'))
                elif div >= r_[d] * 0.30:
                    print(f'⚠️ {code} {d}: 疑似送转（跳变 {div:.4f} ≈ raw价 {r_[d]:.4f} 的{div/r_[d]*100:.0f}%），已跳过')
            prev = ratio
            prev_d = d
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--full', action='store_true', help='全量（默认增量）')
    ap.add_argument('--kind', choices=['stock', 'fund_off', 'fund_etf'], help='只拉某类')
    args = ap.parse_args()

    db = sqlite3.connect(DB_PATH)
    db.execute(CREATE_SQL)

    jobs = []
    if not args.kind or args.kind == 'stock':
        for c in STOCKS:
            jobs.append(('stock', c))
    if not args.kind or args.kind == 'fund_off':
        for c in FUNDS_OFF:
            jobs.append(('fund_off', c))
    if not args.kind or args.kind == 'fund_etf':
        for c in FUNDS_ETF:
            jobs.append(('fund_etf', c))

    for kind, code in jobs:
        try:
            if kind == 'stock':
                rows = fetch_stock(code)
            elif kind == 'fund_off':
                rows = fetch_fund_off(code)
            else:
                rows = fetch_fund_etf(code)
        except Exception as e:
            print(f'❌ {code} ({kind}): {str(e)[:80]}')
            continue
        # W1: 默认增量（ex_date > MAX），--full 全量
        if not args.full:
            r = db.execute("SELECT MAX(ex_date) FROM dividend_records WHERE code=? AND kind=?", (code, kind)).fetchone()
            last = r[0] if r and r[0] else '0000-00-00'
            rows = [x for x in rows if x[1] > last]
        if not rows:
            print(f'{code} ({kind}): 无新数据')
            continue
        db.executemany("""INSERT OR REPLACE INTO dividend_records
            (code, kind, ex_date, dividend, payout_ratio, total_amount, status, source)
            VALUES (?,?,?,?,?,?,?,?)""", rows)
        db.commit()
        print(f'✅ {code} ({kind}): {len(rows)} 条')

    cnt = db.execute("SELECT COUNT(*) FROM dividend_records").fetchone()[0]
    print(f'表内共 {cnt} 条')
    db.close()


if __name__ == '__main__':
    main()
