"""
理杏仁 API — 融资融券（股票）

获取股票融资融券余额及变化趋势。
"""

import logging
from typing import Any, Dict, List

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class MarginAPI(LixingerBase):
    """融资融券 API"""

    API_PATH = "/company/hot/mtasl"
    API_NAME = "融资融券"

    def get(self, stock_codes: List[str]) -> List[Dict[str, Any]]:
        """
        批量获取融资融券数据。

        Args:
            stock_codes: 股票代码列表（每批最多100只）

        Returns:
            融资融券数据列表
        """
        results = []
        for i in range(0, len(stock_codes), 100):
            batch = stock_codes[i:i + 100]
            payload = {"stockCodes": batch}
            result = self._request(payload)
            batch_data = result.get("data", [])
            results.extend(batch_data)
            if i + 100 < len(stock_codes):
                import time
                time.sleep(0.5)
        return results
