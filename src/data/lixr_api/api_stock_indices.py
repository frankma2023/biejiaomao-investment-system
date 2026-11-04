"""
理杏仁 API #13 — 所属指数（股票）

获取股票所属的各类指数成分信息。
支持中证(csi)、国证(cni)、恒生(hsi)、美指(usi)、理杏仁(lxri)来源。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class IndicesAPI(LixingerBase):
    """所属指数 API"""

    API_PATH = "/company/indices"
    API_NAME = "所属指数"

    def get(self, stock_code: str, date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        获取股票所属指数信息。

        Args:
            stock_code: 股票代码，如 "300750"
            date: 查询日期 YYYY-MM-DD，默认最新

        Returns:
            指数列表
        """
        payload = {"stockCode": stock_code}
        if date:
            payload["date"] = date

        result = self._request(payload)
        return result.get("data", [])
