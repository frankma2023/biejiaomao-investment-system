"""
理杏仁 API #4 — K线数据（股票）

获取股票日K线数据（OHLCV），支持不复权/前复权/后复权。
- 单日查询（date）：返回当日所有股票K线，无需 stockCode
- 区间查询（startDate+endDate）：需指定 stockCode
- 最近N条（limit）：需配合 startDate 使用
- 批量下载：按日并发拉取全市场数据，避免逐只股票请求

限制：
- 单次时间范围不得超过 10 年，否则 API 报错
- 每日增量更新应只下载最新交易日数据，避免全量重复下载
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Callable

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class CandlestickAPI(LixingerBase):
    """K线数据 API"""

    API_PATH = "/company/candlestick"
    API_NAME = "K线数据"

    # 复权类型常量
    TYPE_NO_ADJUST = "ex_rights"          # 不复权
    TYPE_LXR_FORWARD = "lxr_fc_rights"   # 理杏仁前复权
    TYPE_FORWARD = "fc_rights"           # 前复权
    TYPE_BACKWARD = "bc_rights"          # 后复权

    def get_by_date(self, date: str) -> List[Dict[str, Any]]:
        """
        单日查询：获取指定日期所有股票的K线（一次请求，无需 stockCode）。

        Args:
            date: 日期，格式 YYYY-MM-DD

        Returns:
            K线列表（非交易日返回空列表）
        """
        payload = {"date": date}
        result = self._request(payload)
        return result.get("data", [])

    def get_by_range(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        adjust_type: str = TYPE_LXR_FORWARD,
        adjust_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        区间查询：获取指定股票一段时间内的K线。

        Args:
            stock_code: 股票代码，如 "300750"
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            adjust_type: 复权类型，默认理杏仁前复权
            adjust_date: 复权基准日
                         - 前复权时需 >= end_date（默认等于 end_date）
                         - 后复权时需 <= start_date（默认等于 start_date）

        Returns:
            K线列表，按日期降序
        """
        payload = {
            "stockCode": stock_code,
            "startDate": start_date,
            "endDate": end_date,
            "type": adjust_type,
        }
        if adjust_date:
            if adjust_type in (self.TYPE_FORWARD, self.TYPE_LXR_FORWARD):
                payload["adjustForwardDate"] = adjust_date
            elif adjust_type == self.TYPE_BACKWARD:
                payload["adjustBackwardDate"] = adjust_date

        result = self._request(payload)
        return result.get("data", [])

    def get_recent(
        self,
        stock_code: str,
        limit: int = 10,
        adjust_type: str = TYPE_LXR_FORWARD,
    ) -> List[Dict[str, Any]]:
        """
        获取最近N条K线。

        注意：理杏仁 API 使用 limit 时仍需传 startDate 和 endDate，
        这里自动设置 10 年前的日期作为 startDate。

        Args:
            stock_code: 股票代码
            limit: 返回条数，默认 10
            adjust_type: 复权类型

        Returns:
            K线列表
        """
        start = (datetime.now() - timedelta(days=3650)).strftime("%Y-%m-%d")
        end = datetime.now().strftime("%Y-%m-%d")

        payload = {
            "stockCode": stock_code,
            "startDate": start,
            "endDate": end,
            "limit": limit,
            "type": adjust_type,
        }
        result = self._request(payload)
        return result.get("data", [])

    def batch_download(
        self,
        start_date: str,
        end_date: str,
        max_workers: int = 5,
        on_progress: Optional[Callable[[int, int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        批量下载指定日期范围内所有股票的K线数据。

        核心思路：
        - 利用 date 参数一次返回全市场数据，将 N只股票×M天 的 N×M 次调用
          降为 M 次调用（M = 交易日数）
        - 线程池并发，进一步缩短总耗时

        限制：
        - 单次时间范围不得超过 10 年，否则 API 报错
        - 每日增量更新应只下载最新交易日，避免全量重复

        示例：下载 1 年全量K线
        - 逐只方式：5000 只 × 1 次 = 5000 次请求 → 约 2 小时
        - 本方法：~250 个交易日 × 1 次 = 250 次请求 → 并发后约 1-2 分钟

        Args:
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            max_workers: 并发线程数，默认 5（注意 API 限流）
            on_progress: 进度回调 on_progress(done, trading_days, total_stocks)
                        可用于进度条或日志输出

        Returns:
            所有交易日全量K线列表
        """
        # 生成日期范围内的所有工作日（跳过周末）
        weekdays = self._generate_weekdays(start_date, end_date)
        logger.info(
            f"[{self.API_NAME}] 批量下载: {start_date} ~ {end_date}, "
            f"共 {len(weekdays)} 个工作日, 并发数 {max_workers}"
        )

        all_data: List[Dict[str, Any]] = []
        done_count = 0
        trading_days = 0

        def _fetch_one(date_str: str) -> List[Dict[str, Any]]:
            """单个日期的下载任务"""
            try:
                return self.get_by_date(date_str)
            except Exception as e:
                logger.warning(f"[{self.API_NAME}] {date_str} 下载失败: {e}")
                return []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_map = {
                executor.submit(_fetch_one, d): d for d in weekdays
            }

            # 按完成顺序收集结果
            for future in as_completed(future_map):
                date_str = future_map[future]
                data = future.result()

                if data:  # 非空说明是交易日
                    trading_days += 1
                    all_data.extend(data)

                done_count += 1
                if on_progress:
                    on_progress(done_count, trading_days, len(all_data))
                elif done_count % 20 == 0 or done_count == len(weekdays):
                    logger.info(
                        f"[{self.API_NAME}] 进度: {done_count}/{len(weekdays)} "
                        f"工作日, 交易日 {trading_days} 天, "
                        f"累计 {len(all_data)} 条K线"
                    )

        logger.info(
            f"[{self.API_NAME}] 批量下载完成: "
            f"{trading_days} 个交易日, 共 {len(all_data)} 条K线"
        )
        return all_data

    @staticmethod
    def _generate_weekdays(start_date: str, end_date: str) -> List[str]:
        """生成日期范围内的所有工作日（周一到周五），跳过周末和节假日由 API 自动过滤。"""
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        weekdays = []
        current = start
        while current <= end:
            # 0=周一, 6=周日，跳过周末
            if current.weekday() < 5:
                weekdays.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

        return weekdays
