"""
理杏仁 API #14 — 所属行业（股票）

获取股票所属行业分类信息。
支持申万（sw/sw_2021）和国证（cni）行业体系。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class IndustriesAPI(LixingerBase):
    """所属行业 API"""

    API_PATH = "/company/industries"
    API_NAME = "所属行业"

    def get(self, stock_code: str, date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        获取股票所属行业信息。

        Args:
            stock_code: 股票代码，如 "300750"
            date: 查询日期 YYYY-MM-DD，默认最新

        Returns:
            行业分类列表，可能包含多个层级和多个来源
        """
        payload = {"stockCode": stock_code}
        if date:
            payload["date"] = date

        result = self._request(payload)
        return result.get("data", [])
