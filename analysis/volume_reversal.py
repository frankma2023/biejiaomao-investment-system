"""
T1 v2: 放量滞涨双日信号检测引擎（简化第2日：仅下跌+放量1.8x）
第1日: 涨幅>=T1 且 量比>=R1
第2日: change_pct<0 且 量比>=1.8
9档位 = 涨幅(2/3/4%) x 量比(1.5/2.0/2.5)
"""
import sqlite3
import pandas as pd
import numpy as np

DB = 'D:/hanako/investment-system/data/lixinger.db'
START = '2023-08-01'
END = '2026-07-31'

# 9档位（涨幅 x 量比）
LEVELS = []
for t1 in [0.02, 0.03, 0.04]:
    for r1 in [1.5, 2.0, 2.5]:
        LEVELS.append({
            't1': t1, 'r1': r1,
            'label': f'+{int(t1*100)}% x {r1}x',
        })

R2 = 1.8  # 第2日量比固定


def load_all_klines():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    st_codes = set(r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_basic WHERE name LIKE '%ST%' OR name LIKE '%*ST%'"
    ).fetchall())
    rows = conn.execute("""
        SELECT stock_code, date, open, close, change_pct, amount
        FROM daily_kline
        WHERE date >= ? AND date <= ?
        ORDER BY stock_code, date
    """, (START, END)).fetchall()
    ipo = {r[0]: r[1] for r in conn.execute("SELECT stock_code, ipo_date FROM stock_basic").fetchall()}
    conn.close()

    data = []
    for r in rows:
        code = r['stock_code']
        if code in st_codes:
            continue
        ip = ipo.get(code)
        if ip and ip > '2023-06-01':
            continue
        data.append({
            'stock_code': code, 'date': r['date'],
            'open': r['open'], 'close': r['close'],
            'change_pct': r['change_pct'], 'amount': r['amount'],
        })
    df = pd.DataFrame(data)
    print(f"K线: {len(df)} 行, {df['stock_code'].nunique()} 只")
    return df


def detect_all_levels(df):
    """一次计算所有档位的事件（预计算量比等公共量）"""
    df = df.sort_values(['stock_code', 'date']).reset_index(drop=True)
    g = df.groupby('stock_code')['amount']
    df['ma20_prev'] = g.transform(lambda x: x.shift(1).rolling(20, min_periods=15).mean())
    df['vol_ratio'] = df['amount'] / df['ma20_prev']
    df['ma20_ok'] = df['ma20_prev'] > 0

    # 次日数据（同股票偏移）
    df['next_change'] = df.groupby('stock_code')['change_pct'].shift(-1)
    df['next_vol_ratio'] = df.groupby('stock_code')['vol_ratio'].shift(-1)
    df['next_date'] = df.groupby('stock_code')['date'].shift(-1)
    df['next_close'] = df.groupby('stock_code')['close'].shift(-1)
    # 第3日开盘
    df['buy_date'] = df.groupby('stock_code')['date'].shift(-2)
    df['buy_open'] = df.groupby('stock_code')['open'].shift(-2)
    df['d2_close'] = df.groupby('stock_code')['close'].shift(-1)

    # 第2日条件: 下跌 + 放量1.8x（用第2日自己的量比）
    d2_cond = (df['next_change'] < 0) & (df['next_vol_ratio'] >= R2)

    # 一字板排除: 第3日开盘/第2日收盘 - 1 > 9.5%
    df['yiziban'] = (df['buy_open'] / df['d2_close'] - 1) > 0.095

    results = {}
    for lv in LEVELS:
        d1_cond = (df['change_pct'] >= lv['t1']) & (df['vol_ratio'] >= lv['r1']) & df['ma20_ok']
        pair = d1_cond & d2_cond & ~df['yiziban'] & df['buy_open'].notna()

        # 冷却期去重（20交易日）：按股票遍历，用交易日索引判断
        events = []
        # 每只股票的行号索引（用于计算交易日间隔）
        df['_row'] = np.arange(len(df))
        for code, sub in df[pair].groupby('stock_code'):
            # sub 已按日期排序
            last_row = None
            for _, row in sub.iterrows():
                if last_row is None or row['_row'] - last_row >= 20:
                    events.append({
                        'stock_code': code,
                        'd1_date': row['date'],
                        'd1_close': row['close'],
                        'd2_date': row['next_date'],
                        'd2_close': row['next_close'],
                        'buy_date': row['buy_date'],
                        'buy_open': row['buy_open'],
                    })
                    last_row = row['_row']
        results[lv['label']] = events
    return results


def main():
    df = load_all_klines()
    results = detect_all_levels(df)
    print("\n各档位事件数:")
    for label, events in results.items():
        print(f"  {label}: {len(events)}")
    # 验证隆基
    base = results['+2% x 2.0x']
    longi = [e for e in base if e['stock_code'] == '601012' and e['d1_date'] >= '2026-03-01']
    print(f"\n隆基 2026-03 事件: {longi if longi else '未命中'}")


if __name__ == '__main__':
    main()
