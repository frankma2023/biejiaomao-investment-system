"""
理杏仁 API #3 — 股本变动

获取单只股票的股本变动历史（增发、送转、回购、期权行权等）。
每次变动记录包含变动日期、变动原因、总股本、流通A股等。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class EquityChangeAPI(LixingerBase):
    """股本变动 API"""

    API_PATH = "/company/equity-change"
    API_NAME = "股本变动"

    def get_history(
        self,
        stock_code: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取单只股票的股本变动历史。

        Args:
            stock_code: 股票代码，如 "300750"
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD，默认上周一

        Returns:
            股本变动记录列表，按日期倒序
        """
        payload = {
            "stockCode": stock_code,
            "startDate": start_date,
        }
        if end_date:
            payload["endDate"] = end_date

        result = self._request(payload)
        data = result.get("data", [])
        logger.debug(
            f"[{self.API_NAME}] {stock_code}: {start_date}~{end_date}, "
            f"{len(data)} 条变动"
        )
        return data
