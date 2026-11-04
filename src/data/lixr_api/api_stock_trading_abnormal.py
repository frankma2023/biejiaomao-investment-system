"""
理杏仁 API #8 — 龙虎榜（股票）

获取股票龙虎榜上榜记录。
包含上榜日期、买入/卖出金额、营业部席位等。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class TradingAbnormalAPI(LixingerBase):
    """龙虎榜 API"""

    API_PATH = "/company/trading-abnormal"
    API_NAME = "龙虎榜"

    def get(
        self,
        stock_code: str,
        start_date: str,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取龙虎榜数据。

        Args:
            stock_code: 股票代码
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期（默认上周一）
            limit: 返回最近 N 条

        Returns:
            龙虎榜记录列表
        """
        payload = {"stockCode": stock_code, "startDate": start_date}
        if end_date:
            payload["endDate"] = end_date
        if limit:
            payload["limit"] = limit

        result = self._request(payload)
        return result.get("data", [])
