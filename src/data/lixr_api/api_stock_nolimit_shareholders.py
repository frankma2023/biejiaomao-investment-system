"""
理杏仁 API #19 — 前十大流通股东（股票）

获取股票前十大流通股东持股信息。
数据按季度披露，包含股东名称、流通A股持股数、占流通A股比例等。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class NolimitShareholdersAPI(LixingerBase):
    """前十大流通股东 API"""

    API_PATH = "/company/nolimit-shareholders"
    API_NAME = "前十大流通股东"

    def get(
        self,
        stock_code: str,
        start_date: str,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取前十大流通股东持股数据。

        Args:
            stock_code: 股票代码
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期（默认上周一）
            limit: 返回最近 N 条

        Returns:
            股东列表（每条含 date, name, holdings, proportionOfOutstandingSharesA 等）
        """
        payload = {"stockCode": stock_code, "startDate": start_date}
        if end_date:
            payload["endDate"] = end_date
        if limit:
            payload["limit"] = limit

        result = self._request(payload)
        return result.get("data", [])
