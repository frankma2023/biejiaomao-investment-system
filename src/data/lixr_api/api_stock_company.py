"""
理杏仁 API #1 — 股票信息

获取所有股票的基础信息：代码、名称、交易所、上市状态、上市日期等。
支持分页查询，支持按财报类型、互联互通类型筛选。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class CompanyAPI(LixingerBase):
    """股票信息 API"""

    API_PATH = "/company"
    API_NAME = "股票信息"

    def get_all(
        self,
        include_delisted: bool = False,
        fs_table_type: Optional[str] = None,
        mutual_markets: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取所有股票信息（自动翻页）。

        Args:
            include_delisted: 是否包含退市股，默认 False
            fs_table_type: 财报类型过滤（non_financial/bank/insurance/security/other_financial）
            mutual_markets: 互联互通类型过滤（如 ["ha"]）

        Returns:
            股票信息列表，每项包含 stockCode, name, exchange, market 等
        """
        all_data = []
        page_index = 0

        while True:
            payload = {"pageIndex": page_index}
            if include_delisted:
                payload["includeDelisted"] = True
            if fs_table_type:
                payload["fsTableType"] = fs_table_type
            if mutual_markets:
                payload["mutualMarkets"] = mutual_markets

            result = self._request(payload)
            data = result.get("data", [])
            total = result.get("total", 0)

            all_data.extend(data)
            logger.info(
                f"[{self.API_NAME}] 第{page_index}页，"
                f"本页{len(data)}条，累计{len(all_data)}/{total}条"
            )

            if len(all_data) >= total:
                break
            page_index += 1

        logger.info(f"[{self.API_NAME}] 完成，共 {len(all_data)} 只股票")
        return all_data

    def get_by_codes(
        self,
        stock_codes: List[str],
    ) -> List[Dict[str, Any]]:
        """
        按股票代码列表获取指定股票信息。

        Args:
            stock_codes: 股票代码列表，如 ["300750", "600519"]

        Returns:
            股票信息列表
        """
        payload = {"stockCodes": stock_codes}
        result = self._request(payload)
        data = result.get("data", [])
        logger.info(f"[{self.API_NAME}] 查询 {len(stock_codes)} 只，返回 {len(data)} 条")
        return data

    def get_by_page(
        self,
        page_index: int = 0,
        include_delisted: bool = False,
        fs_table_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        单页查询（获取总数等信息）。

        Args:
            page_index: 页码，从 0 开始
            include_delisted: 是否包含退市股
            fs_table_type: 财报类型过滤

        Returns:
            包含 total, data 的字典
        """
        payload = {"pageIndex": page_index}
        if include_delisted:
            payload["includeDelisted"] = True
        if fs_table_type:
            payload["fsTableType"] = fs_table_type

        result = self._request(payload)
        return {"total": result.get("total", 0), "data": result.get("data", [])}
