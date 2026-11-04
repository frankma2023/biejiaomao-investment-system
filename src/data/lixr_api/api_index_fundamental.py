"""
理杏仁 API — 指数基本面数据

获取指数估值数据：PE/PB/分位点/市值/换手率等。
metricsList 支持3种格式：
  - 简单: mc, tv, ta, cp, cpc ...
  - 带类型: pe_ttm.mcw, pb.ew, dyr.mcw ...
  - 带分位统计: pe_ttm.y10.mcw.cvpos ...
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class IndexFundamentalAPI(LixingerBase):
    """指数基本面 API"""

    API_PATH = "/index/fundamental"
    API_NAME = "指数基本面"

    def get(
        self,
        stock_codes: List[str],
        metrics_list: List[str],
        date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取指数基本面数据。

        Args:
            stock_codes: 指数代码列表（1~100个，startDate模式下仅1个）
            metrics_list: 指标列表，如 ["pe_ttm.mcw", "pb.ew", "mc"]
            date: 指定日期（与 startDate 二选一）
            start_date: 起始日期
            end_date: 结束日期
            limit: 最近 N 条

        Returns:
            基本面数据列表
        """
        payload = {
            "stockCodes": stock_codes,
            "metricsList": metrics_list,
        }

        if date:
            payload["date"] = date
        if start_date:
            payload["startDate"] = start_date
        if end_date:
            payload["endDate"] = end_date
        if limit:
            payload["limit"] = limit

        result = self._request(payload)
        return result.get("data", [])
