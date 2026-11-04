"""
理杏仁 API #7 — 大股东增减持（股票）

获取上市公司大股东增减持股票的明细记录。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class MajorShareChangeAPI(LixingerBase):
    """大股东增减持 API"""

    API_PATH = "/company/major-shareholders-shares-change"
    API_NAME = "大股东增减持"

    def get(
        self,
        stock_code: str,
        start_date: str,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取大股东增减持数据。

        Args:
            stock_code: 股票代码
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期（默认上周一）
            limit: 返回最近 N 条

        Returns:
            增减持记录列表
        """
        payload = {"stockCode": stock_code, "startDate": start_date}
        if end_date:
            payload["endDate"] = end_date
        if limit:
            payload["limit"] = limit

        result = self._request(payload)
        return result.get("data", [])
