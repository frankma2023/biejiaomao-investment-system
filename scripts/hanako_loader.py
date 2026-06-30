"""
Hanako 本地 SQLite 数据加载器
将 D:\hanako\investment-system\data\lixinger.db 中的 A 股数据
通过 Vibe-Trading 的 DataLoader 协议暴露，替代 Tushare/AKShare 等不稳定源。

安装：
  1. 将此文件复制到 Vibe-Trading 的 agent/backtest/loaders/ 目录
  2. 在 registry.py 的 _loader_modules 中添加 "backtest.loaders.hanako_loader"
  3. 在 VALID_SOURCES 中添加 "hanako"
  4. 在 FALLBACK_CHAINS 的 a_share 链中加入 "hanako"

数据表映射：
  daily_kline         → OHLCV + volume + adj_close
  index_daily_kline   → 指数 OHLCV
  fundamental_indicator → 基本面指标（pe_ttm, pb, mc 等）
  stock_basic         → 股票列表与名称
  stock_financials_quarterly → 季度财报
"""
from __future__ import annotations

import logging
import sqlite3
import os
from typing import Any, Dict, List, Optional

import pandas as pd

from backtest.loaders.base import validate_date_range, validate_ohlc
from backtest.loaders.registry import register

logger = logging.getLogger(__name__)

# 你的数据库路径
DEFAULT_DB_PATH = r"D:\hanako\investment-system\data\lixinger.db"


def _is_a_share(code: str) -> bool:
    """判断股票代码是否为 A 股格式。"""
    code = code.upper()
    # 纯数字 6 位：深交所/上交所
    if code.isdigit() and len(code) == 6:
        return True
    # tushare 风格：000001.SZ / 600000.SH
    if code.endswith(".SZ") or code.endswith(".SH"):
        return True
    # 带 sz/sh 前缀
    if code.startswith("SZ") or code.startswith("SH"):
        return True
    return False


def _normalize_code(code: str) -> str:
    """将各种风格的股票代码统一为 lixinger.db 中的 6 位纯数字格式。"""
    code = code.upper().strip()
    # 去掉 .SZ / .SH 后缀
    if code.endswith(".SZ") or code.endswith(".SH"):
        code = code[:-3]
    # 去掉 sz/ sh 前缀
    if code.startswith("SZ") or code.startswith("SH"):
        code = code[2:]
    # 去零前缀保留 6 位
    return code.zfill(6)[:6]


def _ensure_db_path() -> str:
    """获取数据库路径，优先从环境变量读取。"""
    return os.environ.get("HANAKO_DB_PATH", DEFAULT_DB_PATH)


@register
class DataLoader:
    """从 Hanako 系统的 lixinger.db 读取 A 股 OHLCV 数据。

    完全替代 Tushare/AKShare/BaoStock 等外部数据源。
    支持前复权价格（adj_close 字段）。
    支持指数数据（从 index_daily_kline 表读取）。
    """

    name = "hanako"
    markets = {"a_share"}
    requires_auth = False

    def __init__(self) -> None:
        self._db_path: str | None = None
        self._conn: sqlite3.Connection | None = None

    def is_available(self) -> bool:
        """数据库文件存在即可用"""
        return os.path.exists(_ensure_db_path())

    def _get_conn(self) -> sqlite3.Connection:
        """获取或创建数据库连接（WAL 模式，只读）"""
        db_path = _ensure_db_path()
        if self._conn is None:
            self._conn = sqlite3.connect(db_path, timeout=10)
            self._conn.execute("PRAGMA query_only=ON")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._db_path = db_path
        return self._conn

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """获取 OHLCV 数据。

        Args:
            codes: 股票代码列表，支持 000001 / 000001.SZ / SZ000001 格式
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD
            interval: 仅支持 "1D"（日线）
            fields: 可选字段列表，如 ["open","high","low","close","volume","adj_close"]

        Returns:
            {normalized_code: OHLCV DataFrame}
        """
        validate_date_range(start_date, end_date)
        conn = self._get_conn()

        result: Dict[str, pd.DataFrame] = {}

        # 将代码分批查询（SQLite IN 子句限制）
        batch_size = 200
        for i in range(0, len(codes), batch_size):
            batch = codes[i:i + batch_size]
            normalized = [_normalize_code(c) for c in batch]
            placeholders = ",".join("?" for _ in normalized)

            # 查询日 K 线，优先用前复权价格
            sql = f"""
                SELECT stock_code, date,
                       open, high, low, close, volume, amount,
                       adj_close
                FROM daily_kline
                WHERE stock_code IN ({placeholders})
                  AND date >= ? AND date <= ?
                ORDER BY stock_code, date
            """
            params = normalized + [start_date, end_date]

            try:
                df = pd.read_sql_query(sql, conn, params=params)
            except Exception as exc:
                logger.warning("hanako_loader: SQL error for batch %s: %s", normalized[:3], exc)
                continue

            if df.empty:
                continue

            # 使用前复权价格
            df["close"] = df["adj_close"].fillna(df["close"])
            df = df.drop(columns=["adj_close"])

            # 按股票拆分
            for stock_code, group in df.groupby("stock_code"):
                group = group.copy()
                group["trade_date"] = pd.to_datetime(group["date"])
                group = group.set_index("trade_date").sort_index()

                # 保留标准 OHLCV 列
                ohlcv = group[["open", "high", "low", "close", "volume", "amount"]]
                ohlcv.columns = ["open", "high", "low", "close", "volume", "amount"]
                ohlcv = ohlcv.astype("float64")

                # OHLC 完整性检查
                ohlcv = validate_ohlc(ohlcv)
                if ohlcv.empty:
                    continue

                result[stock_code] = ohlcv

        return result

    def fetch_index(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
    ) -> Dict[str, pd.DataFrame]:
        """获取指数 OHLCV 数据（来自 index_daily_kline 表）。

        指数代码格式：000985.SH（中证全指）、000300.SH（沪深300）等
        """
        validate_date_range(start_date, end_date)
        conn = self._get_conn()

        result: Dict[str, pd.DataFrame] = {}
        batch_size = 50

        for i in range(0, len(codes), batch_size):
            batch = codes[i:i + batch_size]
            # 指数代码保持原样（已在 db 中）
            placeholders = ",".join("?" for _ in batch)

            sql = f"""
                SELECT stock_code, date, open, close, high, low, volume, amount
                FROM index_daily_kline
                WHERE stock_code IN ({placeholders})
                  AND kline_type = 'normal'
                  AND date >= ? AND date <= ?
                ORDER BY stock_code, date
            """
            params = batch + [start_date, end_date]

            try:
                df = pd.read_sql_query(sql, conn, params=params)
            except Exception as exc:
                logger.warning("hanako_loader: index SQL error: %s", exc)
                continue

            if df.empty:
                continue

            for index_code, group in df.groupby("stock_code"):
                group = group.copy()
                group["trade_date"] = pd.to_datetime(group["date"])
                group = group.set_index("trade_date").sort_index()
                ohlcv = group[["open", "high", "low", "close", "volume", "amount"]].astype("float64")
                ohlcv.columns = ["open", "high", "low", "close", "volume", "amount"]
                ohlcv = validate_ohlc(ohlcv)
                if not ohlcv.empty:
                    result[index_code] = ohlcv

        return result

    def fetch_fundamental(
        self,
        code: str,
        metric: str = "pe_ttm",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame | None:
        """获取单个股票的基本面时间序列。

        Args:
            code: 股票代码
            metric: 指标代码，如 pe_ttm / pb / mc / pe_ttm.y3.cvpos 等
            start_date: YYYY-MM-DD（可选）
            end_date: YYYY-MM-DD（可选）

        Returns:
            DataFrame with columns [date, value]
        """
        conn = self._get_conn()
        stock_code = _normalize_code(code)

        sql = """
            SELECT date, value
            FROM fundamental_indicator
            WHERE stock_code = ? AND metric_code = ?
        """
        params: list[Any] = [stock_code, metric]

        if start_date:
            sql += " AND date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND date <= ?"
            params.append(end_date)

        sql += " ORDER BY date"

        try:
            df = pd.read_sql_query(sql, conn, params=params)
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
            return df
        except Exception as exc:
            logger.warning("hanako_loader: fundamental SQL error: %s", exc)
            return None

    def search_symbol(self, keyword: str) -> List[Dict[str, str]]:
        """搜索股票代码/名称。"""
        conn = self._get_conn()
        try:
            df = pd.read_sql_query(
                """SELECT stock_code, name FROM stock_basic
                   WHERE stock_code LIKE ? OR name LIKE ?
                   LIMIT 20""",
                conn,
                params=[f"%{keyword}%", f"%{keyword}%"],
            )
            return df.to_dict("records") if not df.empty else []
        except Exception as exc:
            logger.warning("hanako_loader: search error: %s", exc)
            return []

    def __del__(self):
        if self._conn:
            self._conn.close()
