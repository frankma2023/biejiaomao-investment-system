"""Baostock 分钟级 K 线数据拉取 + SQLite 缓存

数据源: Baostock (http://baostock.com)
覆盖: A 股 15 分钟 / 60 分钟 K 线，1999-07-26 至今
复权: 前复权 (adjustflag="2")
限制: 无指数分钟线，仅个股

使用方式:
    from data.lixr_api.api_stock_minute import sync_minute_kline
    
    # 拉取并缓存到数据库
    sync_minute_kline('000338', '15', '2025-12-01', '2026-05-29')
"""

import sqlite3, logging
from datetime import datetime
from contextlib import contextmanager

import baostock as bs
import pandas as pd

logger = logging.getLogger(__name__)

DB_PATH = r"D:\hanako\investment-system\data\lixinger.db"
FREQ_MAP = {"15": "15", "60": "60", "30": "30", "5": "5"}
TABLE_MAP = {"15": "stock_kline_15min", "60": "stock_kline_60min",
             "30": "stock_kline_30min", "5": "stock_kline_5min"}


def _to_bs_code(code: str) -> str:
    """000338 → sz.000338, 600519 → sh.600519"""
    code = code.strip()
    if code.startswith(('0', '3', '1')):
        return f'sz.{code}'
    elif code.startswith(('6', '9', '4', '8')):
        return f'sh.{code}'
    return f'sz.{code}'


@contextmanager
def _session():
    """baostock 登录上下文管理器"""
    bs.login()
    try:
        yield
    finally:
        bs.logout()


def _ensure_table(conn, table_name):
    """确保分钟 K 线表存在"""
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL, amount REAL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(stock_code, date, time)
        )
    """)
    conn.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_{table_name}_code_date 
        ON {table_name}(stock_code, date)
    """)
    conn.commit()


def fetch_minute_kline(code: str, frequency: str, start_date: str, end_date: str, auto_session: bool = True) -> pd.DataFrame:
    """从 baostock 拉取分钟 K 线数据
    
    Args:
        code: 股票代码，如 '000338'
        frequency: '5' | '15' | '30' | '60'
        start_date: 起始日期 'YYYY-MM-DD'
        end_date: 结束日期 'YYYY-MM-DD'
    
    Returns:
        DataFrame，列: date, time, open, high, low, close, volume, amount
    """
    freq = FREQ_MAP.get(frequency, '15')
    bs_code = _to_bs_code(code)
    
    if auto_session:
        with _session():
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,time,code,open,high,low,close,volume,amount,adjustflag",
                start_date=start_date,
                end_date=end_date,
                frequency=freq,
                adjustflag="2"
            )
            df = rs.get_data()
    else:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,time,code,open,high,low,close,volume,amount,adjustflag",
            start_date=start_date,
            end_date=end_date,
            frequency=freq,
            adjustflag="2"
        )
        df = rs.get_data()
    
    if df.empty:
        logger.warning(f"baostock 返回空数据: {code} {frequency}min {start_date}~{end_date}")
        return df
    
    # 类型转换
    for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # 过滤无效行
    df = df.dropna(subset=['open', 'close', 'high', 'low'])
    
    return df[['date', 'time', 'open', 'high', 'low', 'close', 'volume', 'amount']]


def sync_minute_kline(code: str, frequency: str, start_date: str = None, end_date: str = None):
    """拉取分钟 K 线并写入数据库（增量：已有数据不重复拉取）
    
    Args:
        code: 股票代码
        frequency: '15' | '60'
        start_date: 起始日期，默认取库中最新日期
        end_date: 结束日期，默认今天
    """
    freq = FREQ_MAP.get(frequency, '15')
    table = TABLE_MAP[freq]
    
    if end_date is None:
        end_date = datetime.now().strftime('%Y-%m-%d')
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_table(conn, table)
    
    # 检查已有数据的最大日期
    if start_date is None:
        row = conn.execute(
            f"SELECT MAX(date) FROM {table} WHERE stock_code=?", (code,)
        ).fetchone()
        if row and row[0]:
            start_date = row[0]
        else:
            # 无缓存，取最近 4 个月（覆盖一次日线分析的区间套需求）
            start_date = (datetime.now().replace(day=1) - pd.DateOffset(months=4)).strftime('%Y-%m-%d')
    
    if start_date >= end_date:
        logger.info(f"{code} {frequency}min 数据已是最新 (缓存到 {start_date})")
        conn.close()
        return
    
    # 拉取数据
    df = fetch_minute_kline(code, frequency, start_date, end_date)
    if df.empty:
        conn.close()
        return
    
    # 增量写入（跳过已有记录）
    inserted = 0
    for _, row in df.iterrows():
        try:
            conn.execute(
                f"INSERT OR IGNORE INTO {table} (stock_code, date, time, open, high, low, close, volume, amount) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code, str(row['date']), str(row['time']),
                 float(row['open']), float(row['high']), float(row['low']), float(row['close']),
                 float(row['volume']), float(row['amount']))
            )
            if conn.total_changes > 0:
                inserted += 1
        except Exception:
            pass
    
    conn.commit()
    conn.close()
    logger.info(f"{code} {frequency}min: 拉取 {len(df)} 条, 新增 {inserted} 条 ({start_date}~{end_date})")
    return inserted


def get_cached_klines(code: str, frequency: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """从数据库读取已缓存的分钟 K 线
    
    Args:
        code: 股票代码
        frequency: '15' | '60'
        start_date: 起始日期（可选）
        end_date: 结束日期（可选）
    
    Returns:
        DataFrame，按 date+time 升序排列
    """
    freq = FREQ_MAP.get(frequency, '15')
    table = TABLE_MAP[freq]
    
    conn = sqlite3.connect(DB_PATH)
    
    sql = f"SELECT date, time, open, high, low, close, volume, amount FROM {table} WHERE stock_code = ?"
    params = [code]
    
    if start_date:
        sql += " AND date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND date <= ?"
        params.append(end_date)
    
    sql += " ORDER BY date, time"
    
    df = pd.read_sql(sql, conn, params=params)
    conn.close()
    return df


if __name__ == "__main__":
    # 自检：拉取茅台 60 分钟数据
    print("拉取 600519 60 分钟数据 (最近 1 个月)...")
    sync_minute_kline('600519', '60')
    df = get_cached_klines('600519', '60')
    print(f"缓存数据: {len(df)} 条")
    if not df.empty:
        print(f"日期范围: {df.date.min()} ~ {df.date.max()}")
        print(f"最后5条:\n{df.tail()}")
