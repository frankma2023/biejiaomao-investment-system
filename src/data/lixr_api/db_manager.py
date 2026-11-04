"""
数据库管理器

负责建表、数据写入（INSERT OR REPLACE），供各 API 调用。
所有写入操作通过 db_manager 完成，不散落在各 API 脚本中。
"""

import sqlite3
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class DBManager:
    """SQLite 数据库管理器"""

    def __init__(self, config_path: Optional[str] = None):
        self._load_config(config_path)
        self.conn = self._connect()

    def _load_config(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = PROJECT_ROOT / "config" / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

    def _connect(self) -> sqlite3.Connection:
        db_path = PROJECT_ROOT / self.config["database"]["path"]
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")    # 并发读写性能更好
        conn.execute("PRAGMA synchronous=NORMAL")  # 平衡安全与性能
        conn.row_factory = sqlite3.Row             # 返回字典格式
        return conn

    # ---- 建表 ----

    def init_tables(self):
        """读取 schema.sql 建表"""
        schema_path = PROJECT_ROOT / "db" / "schema.sql"
        with open(schema_path, "r", encoding="utf-8") as f:
            self.conn.executescript(f.read())
        self.conn.commit()
        logger.info("数据库表初始化完成")

    # ---- 通用写入 ----

    def execute_many(self, sql: str, rows: List[tuple]):
        """
        批量执行 SQL（INSERT OR REPLACE）。

        Args:
            sql: 带 ? 占位符的 SQL
            rows: 参数元组列表
        """
        if not rows:
            return
        self.conn.executemany(sql, rows)
        self.conn.commit()

    # ---- 各表专用写入方法 ----

    def upsert_stock_basic(self, stocks: List[Dict[str, Any]]):
        """写入股票基础信息"""
        sql = """INSERT OR REPLACE INTO stock_basic
            (stock_code, name, market, exchange, area_code,
             listing_status, ipo_date, delisted_date, fs_table_type,
             mutual_market_flag, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))"""
        rows = [(
            s.get("stockCode"), s.get("name"), s.get("market"),
            s.get("exchange"), s.get("areaCode"), s.get("listingStatus"),
            s.get("ipoDate"), s.get("delistedDate"), s.get("fsTableType"),
            1 if s.get("mutualMarketFlag") else 0,
        ) for s in stocks]
        self.execute_many(sql, rows)
        logger.info(f"[DB] stock_basic: 写入 {len(rows)} 条")

    def upsert_daily_kline(self, klines: List[Dict[str, Any]]):
        """写入日K线数据"""
        sql = """INSERT OR REPLACE INTO daily_kline
            (stock_code, date, open, close, high, low,
             volume, amount, change_pct, turnover_rate, complex_factor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        rows = [(
            k["stockCode"], k["date"][:10],
            k.get("open"), k.get("close"), k.get("high"), k.get("low"),
            k.get("volume"), k.get("amount"), k.get("change"),
            k.get("to_r"), k.get("complexFactor"),
        ) for k in klines]
        self.execute_many(sql, rows)
        logger.info(f"[DB] daily_kline: 写入 {len(rows)} 条")

    def upsert_fundamental(self, data: List[Dict[str, Any]]):
        """
        写入基本面指标（key-value 结构）。

        data 中每项来自 API #26 返回，结构为：
        {"date": ..., "stockCode": ..., "pe_ttm": 23.7, "mc": 1234, ...}
        除 date 和 stockCode 外的字段均为指标。
        """
        sql = """INSERT OR REPLACE INTO fundamental_indicator
            (stock_code, date, metric_code, value) VALUES (?, ?, ?, ?)"""
        rows = []
        for item in data:
            date_str = item["date"][:10]
            code = item["stockCode"]
            for key, val in item.items():
                if key in ("date", "stockCode", "currency") or val is None:
                    continue
                try:
                    rows.append((code, date_str, key, float(val)))
                except (TypeError, ValueError):
                    continue  # 跳过非数值字段
        self.execute_many(sql, rows)
        logger.info(f"[DB] fundamental_indicator: 写入 {len(rows)} 条")

    def upsert_financial(self, data: List[Dict[str, Any]]):
        """
        写入财报数据（key-value 结构）。

        data 中每项来自 API #27 返回，含嵌套结构：
        {"date": ..., "stockCode": ..., "q": {"ps": {"toi": {"t": 123}}, ...}, ...}
        需要展开嵌套，将指标编码为 "q.ps.toi.t" 格式。
        """
        sql = """INSERT OR REPLACE INTO financial_statement
            (stock_code, report_date, announce_date, metric_code, value) VALUES (?, ?, ?, ?, ?)"""
        rows = []
        for item in data:
            date_str = item["date"][:10]
            code = item["stockCode"]
            announce = item.get("reportDate", "")[:10] if item.get("reportDate") else None

            # 递归展开嵌套字典，生成 metric_code
            for metric_code, val in self._flatten_nested(item, code):
                try:
                    rows.append((code, date_str, announce, metric_code, float(val)))
                except (TypeError, ValueError):
                    continue
        self.execute_many(sql, rows)
        logger.info(f"[DB] financial_statement: 写入 {len(rows)} 条")

    def upsert_shareholders_num(self, data: List[Dict[str, Any]], stock_code: str = None):
        """写入股东人数"""
        sql = """INSERT OR REPLACE INTO shareholders_num
            (stock_code, date, total, change_rate, price_change) VALUES (?, ?, ?, ?, ?)"""
        rows = [(
            stock_code or item.get("stockCode"),
            item["date"][:10],
            item.get("total") or item.get("num"),
            item.get("shareholdersNumberChangeRate"),
            item.get("spc"),
        ) for item in data]
        self.execute_many(sql, rows)
        logger.info(f"[DB] shareholders_num: 写入 {len(rows)} 条")

    def upsert_index_constituents(self, relations: Dict[str, List[str]], date: str):
        """
        写入指数成分股关系。

        Args:
            relations: {index_code: [stock_code_1, stock_code_2, ...]}
            date: 成分股日期
        """
        sql = """INSERT OR REPLACE INTO index_constituents
            (index_code, stock_code, date) VALUES (?, ?, ?)"""
        rows = []
        for idx_code, stock_codes in relations.items():
            for sc in stock_codes:
                rows.append((idx_code, sc, date))
        self.execute_many(sql, rows)
        logger.info(f"[DB] index_constituents: 写入 {len(rows)} 条 ({len(relations)} 个指数)")

    def upsert_constituent_weightings(self, rows: list):
        """
        写入指数成分股权重。

        Args:
            rows: [(index_code, stock_code, date, weighting), ...]
        """
        sql = """INSERT OR REPLACE INTO index_constituent_weightings
            (index_code, stock_code, date, weighting) VALUES (?, ?, ?, ?)"""
        self.execute_many(sql, rows)
        logger.info(f"[DB] index_constituent_weightings: 写入 {len(rows)} 条")

    def upsert_sector_rs_daily(self, records: list):
        """
        写入行业板块RS每日结果。

        Args:
            records: [dict, ...] 每项包含 date, sector_code, sector_name, ... 等字段
        """
        sql = """INSERT OR REPLACE INTO sector_rs_daily
            (date, sector_code, sector_name, rs_ratio,
             score_20, score_120, score_250,
             rps_20, rps_120, rps_250,
             price_vs_ma200, ma200_trend,
             daily_change_pct, vol_ratio_20, vol_ratio_5,
             rs20_trend_up,
             is_leading, is_momentum, is_setup, is_compact,
             internal_status, internal_count, internal_weighted,
             top_stocks)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        rows = [(
            r["date"], r["sector_code"], r["sector_name"], r.get("rs_ratio"),
            r.get("score_20"), r.get("score_120"), r.get("score_250"),
            r.get("rps_20"), r.get("rps_120"), r.get("rps_250"),
            r.get("price_vs_ma200"), r.get("ma200_trend"),
            r.get("daily_change_pct"), r.get("vol_ratio_20"), r.get("vol_ratio_5"),
            1 if r.get("rs20_trend_up") else (None if r.get("rs20_trend_up") is None else 0),
            1 if r.get("is_leading") else 0,
            1 if r.get("is_momentum") else 0,
            1 if r.get("is_setup") else 0,
            1 if r.get("is_compact") else 0,
            r.get("internal_status"), r.get("internal_count"), r.get("internal_weighted"),
            r.get("top_stocks"),
        ) for r in records]
        self.execute_many(sql, rows)
        logger.info(f"[DB] sector_rs_daily: 写入 {len(rows)} 条")

    # ---- 查询 ----

    def query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """通用查询，返回字典列表"""
        cursor = self.conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def table_count(self, table_name: str) -> int:
        """获取表记录数"""
        result = self.query(f"SELECT COUNT(*) as cnt FROM {table_name}")
        return result[0]["cnt"] if result else 0

    def tables(self) -> List[str]:
        """列出所有表名"""
        result = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [r["name"] for r in result]

    # ---- 工具方法 ----

    @staticmethod
    def _flatten_nested(data: Dict, stock_code: str) -> List[tuple]:
        """
        递归展开 API #27 的嵌套返回结构。

        输入: {"date":"..", "stockCode":"300750", "q":{"ps":{"toi":{"t":123}}}}
        输出: [("q.ps.toi.t", 123)]
        """
        results = []
        skip_keys = {"date", "stockCode", "currency", "reportDate",
                     "standardDate", "reportType", "auditOpinionType"}

        def _walk(obj, prefix=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in skip_keys:
                        continue
                    new_prefix = f"{prefix}.{k}" if prefix else k
                    _walk(v, new_prefix)
            elif isinstance(obj, (int, float)):
                results.append((prefix, obj))

        _walk(data)
        return results

    def upsert_stock_margin(self, data_list: List[Dict[str, Any]]):
        """[已废弃] stock_margin 表已删除，融资融券统一使用 daily_margin_history"""
        pass

    def upsert_shareholders_v2(self, data_list: List[Dict[str, Any]]):
        """写入股东人数V2数据"""
        sql = """INSERT OR REPLACE INTO shareholders_num_v2
            (stock_code, date, shnc_rln, shnc_d90, shnc_qln, shnc_q1, shnc_q2, shnc_q3,
             shnc_y1, shnc_y2)
            VALUES (?,?,?,?,?,?,?,?,?,?)"""
        rows = []
        for item in data_list:
            code = item.get("stockCode")
            if not code:
                continue
            date_str = str(item.get("shnc_rld") or item.get("date", ""))[:10]
            if not date_str:
                continue
            rows.append((
                code, date_str,
                item.get("shnc_rln") or item.get("total"),
                item.get("shnc_d90"),
                item.get("shnc_qln"),
                item.get("shnc_q1"), item.get("shnc_q2"), item.get("shnc_q3"),
                item.get("shnc_y1"), item.get("shnc_y2"),
            ))
        self.execute_many(sql, rows)
        logger.info(f"[DB] shareholders_num_v2: 写入 {len(rows)} 条")

    def upsert_stock_candidates(self, records: List[Dict[str, Any]]):
        """写入个股扫描候选结果"""
        sql = """INSERT OR REPLACE INTO stock_candidates_daily
            (stock_code, date, stock_name, industry_name,
             rs_score, rs_mkt_long,
             fundamental_score, eps_ttm, eps_yoy, revenue_yoy, roe, debt_ratio,
             vol_price_score, price_vs_ma50, price_vs_ma200, dist_from_high,
             avg_volume_20d, volume_trend, ma_trend,
             pattern_score, pattern_health, pattern_type,
             composite_score, grade)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        rows = [(
            r["stock_code"], r["date"], r.get("stock_name"), r.get("industry_name"),
            r.get("rs_score"), r.get("rs_mkt_long"),
            r.get("fundamental_score"), r.get("eps_ttm"), r.get("eps_yoy"),
            r.get("revenue_yoy"), r.get("roe"), r.get("debt_ratio"),
            r.get("vol_price_score"), r.get("price_vs_ma50"), r.get("price_vs_ma200"),
            r.get("dist_from_high"), r.get("avg_volume_20d"),
            r.get("volume_trend"), r.get("ma_trend"),
            r.get("pattern_score"), r.get("pattern_health"), r.get("pattern_type"),
            r.get("composite_score"), r.get("grade"),
        ) for r in records]
        self.execute_many(sql, rows)
        logger.info(f"[DB] stock_candidates_daily: 写入 {len(rows)} 条")

    def upsert_weekly_kline(self, rows: List[tuple]):
        """写入周K线数据（由日K线聚合生成）"""
        sql = """INSERT OR REPLACE INTO weekly_kline
            (stock_code, week_start_date, week_end_date, year_week,
             open, close, high, low, volume, amount,
             change_pct, turnover_rate, trade_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        self.execute_many(sql, rows)
        logger.info(f"[DB] weekly_kline: 写入 {len(rows)} 条")

    def close(self):
        self.conn.close()
