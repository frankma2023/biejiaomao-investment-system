"""
理杏仁 API — 指数基础信息

获取指数详细信息（代码、名称、类型、调样频率等）。
不传 stockCodes 时返回所有指数。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class IndexInfoAPI(LixingerBase):
    """指数基础信息 API"""

    API_PATH = "/index"
    API_NAME = "指数信息"

    def get(self, stock_codes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        获取指数信息。

        Args:
            stock_codes: 指数代码列表，为空时返回所有指数

        Returns:
            指数信息列表
        """
        payload = {}
        if stock_codes:
            payload["stockCodes"] = stock_codes

        result = self._request(payload)
        return result.get("data", [])
