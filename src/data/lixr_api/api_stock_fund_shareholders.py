"""
理杏仁 API #20 — 公募基金持股（股票）

获取公募基金对股票的持仓信息。
包含基金代码、名称、持仓量、市值、占基金规模比例等。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class FundShareholdersAPI(LixingerBase):
    """公募基金持股 API"""

    API_PATH = "/company/fund-shareholders"
    API_NAME = "公募基金持股"

    def get(
        self,
        stock_code: str,
        start_date: str,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取公募基金持股数据。

        Args:
            stock_code: 股票代码
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期（默认上周一）
            limit: 返回最近 N 条

        Returns:
            基金持仓列表（每条含 date, fundCode, name, holdings, marketCap 等）
        """
        payload = {"stockCode": stock_code, "startDate": start_date}
        if end_date:
            payload["endDate"] = end_date
        if limit:
            payload["limit"] = limit

        result = self._request(payload)
        return result.get("data", [])
