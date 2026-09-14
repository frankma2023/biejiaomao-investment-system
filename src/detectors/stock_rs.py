"""
个股RS强度计算引擎

基于欧奈尔RPS理念: 个股N日涨幅在全市场内百分位排名 → 0~99
双周期: RPS_250(长期趋势) + RPS_20(短期动能)
双强选股: 稳健龙头(RPS_250≥90, RPS_20≥85) | 加速爆发(RPS_250≥80, RPS_20≥95)
RS线: adj_close / 中证全指(000985).close

数据源: daily_kline.adj_close, 中证全指 index_daily_kline
持久化: stock_rs_daily 表
"""

import sqlite3, os
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "lixinger.db")

CREATE_TABLE = """CREATE TABLE IF NOT EXISTS stock_rs_daily (
    stock_code TEXT NOT NULL,
    date TEXT NOT NULL,
    close REAL,
    adj_close REAL,
    ret_20 REAL,
    ret_250 REAL,
    rps_20 INTEGER,
    rps_250 INTEGER,
    rs_line REAL,
    amount REAL,
    updated_at TEXT,
    PRIMARY KEY (stock_code, date)
)"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def compute_returns(stock_klines, benchmark_klines, as_of_date):
    """
    对全市场股票计算 ret_20, ret_250, rs_line。

    优先使用 adj_close（前复权），若无则降级到 close。
    返回: [(stock_code, date, close, price, ret_20, ret_250, rs_line, amount), ...]
    """
    db = get_db()
    db.execute(CREATE_TABLE)

    # 获取基准指数数据(000985)
    bench = db.execute('''SELECT date, close FROM index_daily_kline
        WHERE stock_code='000985' AND kline_type='normal'
        AND date >= date(?, '-400 days') AND date <= ?
        ORDER BY date''', (as_of_date, as_of_date)).fetchall()

    bench_map = {r['date']: r['close'] for r in bench}

    # 获取所有正常交易股票
    stocks = db.execute('''SELECT stock_code FROM stock_basic
        WHERE listing_status='normally_listed'
        ORDER BY stock_code''').fetchall()
    stock_codes = [s['stock_code'] for s in stocks]

    # 批量查询K线(取300天保证250日够用)
    lookback = 350
    results = []
    batch_size = 500

    for i in range(0, len(stock_codes), batch_size):
        batch = stock_codes[i:i+batch_size]
        ph = ','.join(['?' for _ in batch])

        rows = db.execute(f'''SELECT stock_code, date, raw_close AS close, close AS adj_close, amount
            FROM daily_kline_adj WHERE stock_code IN ({ph})
            AND date >= date(?, '-{lookback} days') AND date <= ?
            ORDER BY stock_code, date''',
            batch + [as_of_date, as_of_date]).fetchall()

        # 按股票分组
        klines = {}
        for r in rows:
            if r['stock_code'] not in klines:
                klines[r['stock_code']] = []
            klines[r['stock_code']].append(r)

        for code in batch:
            kl = klines.get(code, [])
            if not kl:
                continue

            # 定位截止日
            as_of_idx = None
            for j, k in enumerate(kl):
                if k['date'] == as_of_date:
                    as_of_idx = j; break
            if as_of_idx is None:
                for j in range(len(kl)-1, -1, -1):
                    if kl[j]['date'] <= as_of_date:
                        as_of_idx = j; break
            if as_of_idx is None:
                continue

            actual_date = kl[as_of_idx]['date']
            adj_close = kl[as_of_idx]['adj_close']
            close_val = kl[as_of_idx]['close']
            amount = kl[as_of_idx]['amount']

            if not adj_close or adj_close <= 0:
                continue

            # ret_20
            ret_20 = None
            if as_of_idx >= 10:  # 至少10个有效交易日
                idx_20 = max(0, as_of_idx - 20)
                past_adj = kl[idx_20]['adj_close']
                if past_adj and past_adj > 0:
                    ret_20 = (adj_close / past_adj - 1) * 100

            # ret_250
            ret_250 = None
            if as_of_idx >= 125:  # 至少125个有效交易日
                idx_250 = max(0, as_of_idx - 250)
                past_adj = kl[idx_250]['adj_close']
                if past_adj and past_adj > 0:
                    ret_250 = (adj_close / past_adj - 1) * 100

            # RS线
            rs_line = None
            bench_close = bench_map.get(actual_date)
            if bench_close and bench_close > 0:
                rs_line = adj_close / bench_close

            results.append((code, actual_date, round(close_val, 2), round(adj_close, 2),
                          round(ret_20, 2) if ret_20 is not None else None,
                          round(ret_250, 2) if ret_250 is not None else None,
                          round(rs_line, 6) if rs_line is not None else None,
                          round(amount, 1) if amount else None))

    db.close()
    return results


def compute_rps(results):
    """
    在全市场范围内计算 RPS_20 和 RPS_250。
    过滤掉 ret=None 的股票。
    """
    # RPS_250
    valid_250 = [(i, r[5]) for i, r in enumerate(results) if r[5] is not None]
    if valid_250:
        valid_250.sort(key=lambda x: x[1], reverse=True)
        n = len(valid_250)
        for rank, (idx, _) in enumerate(valid_250):
            rps = round((1 - rank / (n - 1)) * 99) if n > 1 else 50
            results[idx] = results[idx] + (rps,)
        for i, r in enumerate(results):
            if len(r) == 8:
                results[i] = r + (None,)
    else:
        results = [r + (None,) for r in results]

    # RPS_20
    valid_20 = [(i, r[4]) for i, r in enumerate(results) if r[4] is not None]
    if valid_20:
        valid_20.sort(key=lambda x: x[1], reverse=True)
        n = len(valid_20)
        for rank, (idx, _) in enumerate(valid_20):
            rps = round((1 - rank / (n - 1)) * 99) if n > 1 else 50
            results[idx] = results[idx] + (rps,)
        for i, r in enumerate(results):
            if len(r) == 9:
                results[i] = r + (None,)
    else:
        results = [r + (None,) for r in results]

    return results


def save_to_db(results):
    """批量写入 stock_rs_daily"""
    db = get_db()
    db.execute(CREATE_TABLE)

    rows = []
    for r in results:
        code, date, close, adj_close, ret_20, ret_250, rs_line, amount, rps_250, rps_20 = r
        rows.append((code, date, close, adj_close,
                     ret_20, ret_250, rps_20, rps_250,
                     rs_line, amount, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    db.executemany('''INSERT OR REPLACE INTO stock_rs_daily
        (stock_code, date, close, adj_close, ret_20, ret_250, rps_20, rps_250, rs_line, amount, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)''', rows)
    db.commit()
    db.close()
    return len(rows)


def select_double_strong(results, mode='稳健龙头'):
    """
    双强选股筛选。

    mode: '稳健龙头' (RPS_250≥90, RPS_20≥85)
          '加速爆发' (RPS_250≥80, RPS_20≥95)
          'all' (两者都选)
    """
    selected = []
    for r in results:
        code, date, close, adj_close, ret_20, ret_250, rs_line, amount, rps_250, rps_20 = r
        if rps_250 is None or rps_20 is None:
            continue

        is_稳健 = rps_250 >= 90 and rps_20 >= 85
        is_加速 = rps_250 >= 80 and rps_20 >= 95

        if mode == 'all':
            if is_稳健 or is_加速:
                tag = '稳健龙头' if is_稳健 else '加速爆发'
                selected.append((code, tag, rps_250, rps_20, close, amount))
        elif mode == '稳健龙头' and is_稳健:
            selected.append((code, '稳健龙头', rps_250, rps_20, close, amount))
        elif mode == '加速爆发' and is_加速:
            selected.append((code, '加速爆发', rps_250, rps_20, close, amount))

    return selected


def compute_for_date(as_of_date):
    """全量计算并持久化指定日期的个股RS"""
    results = compute_returns(None, None, as_of_date)
    results = compute_rps(results)
    n = save_to_db(results)
    return n


if __name__ == '__main__':
    import sys
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y-%m-%d')
    print(f'Computing stock RS for {date}...')
    n = compute_for_date(date)
    print(f'Done: {n} stocks saved to stock_rs_daily')
