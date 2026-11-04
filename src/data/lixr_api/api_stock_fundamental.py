"""
理杏仁 API #26 — 基本面数据（股票）

获取 PE、PB、市值、涨跌幅、成交量等估值指标及其历史分位点。
- 支持多股票批量查询（stockCodes 最多 100 只）
- 指标通过 metricsList 指定，原始指标 + 统计指标
- 多股票用 date 参数，单股票可用 startDate/endDate 区间查询

限制：
- stockCodes > 1 时最多 48 个指标，= 1 时最多 36 个
- startDate 与 endDate 间隔不超过 10 年
- 区间查询（startDate）时只能传 1 个 stockCode
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class FundamentalAPI(LixingerBase):
    """基本面数据 API"""

    API_PATH = "/company/fundamental/non_financial"
    API_NAME = "基本面数据"

    # 常用指标预设，避免每次手动拼列表
    # 估值核心指标
    METRICS_VALUATION = [
        "pe_ttm", "pb", "ps_ttm", "dyr", "pcf_ttm",
        "ev_ebit_r", "ev_ebitda_r", "mc",
    ]
    # 市值与规模
    METRICS_MARKET_CAP = [
        "mc", "cmc", "ecmc", "mc_om",
    ]
    # 交易数据
    METRICS_TRADING = [
        "sp", "spc", "tv", "ta", "to_r",
    ]
    # 持仓数据
    METRICS_HOLDING = [
        "shn", "ha_sh", "ha_shm",
    ]
    # 融资融券
    METRICS_MARGIN = [
        "fpa", "fra", "fnpa", "fb",
        "ssa", "sra", "snsa", "sb",
    ]
    # 估值分位点（近3年）
    METRICS_PERCENTILE_Y3 = [
        "pe_ttm.y3.cvpos", "pb.y3.cvpos", "ps_ttm.y3.cvpos",
    ]

    def get_by_date(
        self,
        stock_codes: List[str],
        date: str,
        metrics_list: List[str],
    ) -> List[Dict[str, Any]]:
        """
        指定日期查询多只股票的基本面指标。

        Args:
            stock_codes: 股票代码列表（最多 100 只）
            date: 查询日期 YYYY-MM-DD，或 "latest" 获取最新
            metrics_list: 指标代码列表，如 ["pe_ttm", "pb", "mc"]

        Returns:
            每只股票一条数据，包含 date, stockCode 和各指标值
        """
        if len(stock_codes) > 100:
            raise ValueError(f"stockCodes 最多 100 只，传入 {len(stock_codes)} 只")

        if len(stock_codes) > 1 and len(metrics_list) > 48:
            logger.warning(
                f"多股票模式指标上限 48，当前 {len(metrics_list)}，可能报错"
            )

        payload = {
            "stockCodes": stock_codes,
            "date": date,
            "metricsList": metrics_list,
        }
        result = self._request(payload)
        return result.get("data", [])

    def get_by_range(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        metrics_list: List[str],
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        按时间范围查询单只股票的基本面指标。

        Args:
            stock_code: 单只股票代码
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD（默认上周一）
            metrics_list: 指标代码列表（单股票最多 36 个）
            limit: 返回最近 N 条

        Returns:
            按日期的指标序列
        """
        payload = {
            "stockCodes": [stock_code],
            "startDate": start_date,
            "endDate": end_date,
            "metricsList": metrics_list,
        }
        if limit:
            payload["limit"] = limit

        result = self._request(payload)
        return result.get("data", [])

    def get_all_stocks_by_date(
        self,
        date: str,
        metrics_list: List[str],
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        获取指定日期全市场所有股票的指标。

        分批请求（每次最多 100 只），自动翻页。

        Args:
            date: 查询日期 YYYY-MM-DD
            metrics_list: 指标代码列表
            page_size: 每批股票数，默认 100

        Returns:
            全市场股票的指标数据
        """
        # 第一步：获取全量股票代码
        from .api_stock_company import CompanyAPI
        stock_api = CompanyAPI()
        all_stocks = stock_api.get_all()

        stock_codes = [s["stockCode"] for s in all_stocks]
        logger.info(
            f"[{self.API_NAME}] 全市场查询: {len(stock_codes)} 只股票, "
            f"{len(metrics_list)} 个指标, 每批 {page_size} 只"
        )

        # 第二步：分批请求
        all_data = []
        for i in range(0, len(stock_codes), page_size):
            batch = stock_codes[i:i + page_size]
            data = self.get_by_date(batch, date, metrics_list)
            all_data.extend(data)

            if (i // page_size + 1) % 10 == 0:
                logger.info(
                    f"[{self.API_NAME}] 进度: {min(i + page_size, len(stock_codes))}/{len(stock_codes)}"
                )

        logger.info(
            f"[{self.API_NAME}] 全市场查询完成: {len(all_data)} 条"
        )
        return all_data
