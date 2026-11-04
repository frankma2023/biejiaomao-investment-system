"""
理杏仁 API — 指数成分股权重

获取指数成分股在指定时间段内的权重数据。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class IndexConstituentWeightingsAPI(LixingerBase):
    """指数成分股权重 API"""

    API_PATH = "/index/constituent-weightings"
    API_NAME = "成分股权重"

    def get(
        self,
        index_code: str,
        start_date: str,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取指数成分股权重。

        Args:
            index_code: 指数代码，如 "000016"
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD（默认上周一）
            limit: 返回条数限制

        Returns:
            [{date, stockCode, weighting}, ...]
        """
        payload: Dict[str, Any] = {
            "stockCode": index_code,
            "startDate": start_date,
        }
        if end_date:
            payload["endDate"] = end_date
        if limit:
            payload["limit"] = limit

        result = self._request(payload)
        return result.get("data", [])
