"""
理杏仁 API #5 — 股东人数（股票）

获取股票股东人数及变化趋势。
数据按季度披露。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class ShareholdersNumAPI(LixingerBase):
    """股东人数 API"""

    API_PATH = "/company/shareholders-num"
    API_NAME = "股东人数"

    def get(
        self,
        stock_code: str,
        start_date: str,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取股东人数数据。

        Args:
            stock_code: 股票代码
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期（默认上周一）
            limit: 返回最近 N 条

        Returns:
            股东人数列表
        """
        payload = {"stockCode": stock_code, "startDate": start_date}
        if end_date:
            payload["endDate"] = end_date
        if limit:
            payload["limit"] = limit

        result = self._request(payload)
        return result.get("data", [])
