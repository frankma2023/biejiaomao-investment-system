"""
理杏仁 API — 指数K线数据

获取指数日K线，支持 normal（正常点位）和 total_return（全收益率点位）。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class IndexCandlestickAPI(LixingerBase):
    """指数K线 API"""

    API_PATH = "/index/candlestick"
    API_NAME = "指数K线"

    def get(
        self,
        stock_code: str,
        stock_type: str = "normal",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取指数K线数据。

        Args:
            stock_code: 指数代码
            stock_type: 点位类型 normal / total_return
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期
            limit: 最近 N 条

        Returns:
            K线数据列表
        """
        payload = {"stockCode": stock_code, "type": stock_type}

        # 至少传日期范围或 limit 之一
        if start_date:
            payload["startDate"] = start_date
        if end_date:
            payload["endDate"] = end_date
        if limit:
            payload["limit"] = limit

        result = self._request(payload)
        return result.get("data", [])
