"""
理杏仁 API — 指数成分股

获取指数包含的成分股票列表。
一次最多查询 100 个指数，返回每个指数下的股票代码列表。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class IndexConstituentsAPI(LixingerBase):
    """指数成分股 API"""

    API_PATH = "/index/constituents"
    API_NAME = "指数成分股"

    def get(
        self,
        stock_codes: List[str],
        date: str = "latest",
    ) -> List[Dict[str, Any]]:
        """
        获取指定指数的成分股列表。

        Args:
            stock_codes: 指数代码列表，如 ["000016", "000300"]
                         长度 1~100
            date: 日期，"latest" 表示最新，或 "YYYY-MM-DD"

        Returns:
            列表，每项包含:
            - stockCode: 指数代码
            - constituents: 成分股列表 [{stockCode, areaCode, market}, ...]
        """
        payload = {
            "stockCodes": stock_codes,
            "date": date,
        }
        result = self._request(payload)
        return result.get("data", [])

    def get_all(
        self,
        index_codes: List[str],
        date: str = "latest",
        batch_size: int = 100,
        on_progress: Optional[callable] = None,
    ) -> Dict[str, List[str]]:
        """
        批量获取所有指数的成分股，自动分批（每批最多100个）。

        Args:
            index_codes: 全部指数代码列表
            date: 日期，"latest" 或 "YYYY-MM-DD"
            batch_size: 每批数量，默认100（API上限）
            on_progress: 进度回调 on_progress(done, total)

        Returns:
            {index_code: [stock_code_1, stock_code_2, ...]} 成分股映射
        """
        result_map: Dict[str, List[str]] = {}
        total = len(index_codes)
        done = 0

        for i in range(0, total, batch_size):
            batch = index_codes[i:i + batch_size]
            data = self.get(batch, date)

            for item in data:
                idx_code = item["stockCode"]
                constituents = item.get("constituents", [])
                result_map[idx_code] = [c["stockCode"] for c in constituents]

            done += len(batch)
            logger.info(
                f"[{self.API_NAME}] 进度: {done}/{total} 指数, "
                f"本批 {len(data)} 个有返回"
            )
            if on_progress:
                on_progress(done, total)

        total_stocks = sum(len(v) for v in result_map.values())
        logger.info(
            f"[{self.API_NAME}] 完成，{len(result_map)} 个指数，"
            f"共 {total_stocks} 条成分股关系"
        )
        return result_map
