"""
理杏仁 API #27 — 财报数据（股票）

获取资产负债表、利润表、现金流量表及财务指标。
- 指标格式：[粒度].[报表].[字段].[计算类型]，如 q.ps.toi.t
- 多股票用 date 参数，区间查询只能单股票
- 多股票最多 48 个指标，单股票最多 128 个

粒度：q(季度), hy(半年), y(年)
计算类型：t(当期), c(单季), ttm, t_y2y(同比), c_y2y(单季同比) 等

限制：
- startDate 与 endDate 间隔不超过 10 年
- 区间查询（startDate）时只能传 1 个 stockCode
"""

import logging
from typing import Any, Dict, List, Optional

from .base_api import LixingerBase

logger = logging.getLogger(__name__)


class FinancialStatementAPI(LixingerBase):
    """财报数据 API"""

    API_PATH = "/company/fs/non_financial"
    API_NAME = "财报数据"

    # 常用指标预设（季度粒度）
    # 利润表核心
    METRICS_INCOME = [
        "q.ps.toi.t",            # 营业总收入（累计）
        "q.ps.toi.c_y2y",        # 营收单季同比
        "q.ps.oc.t",             # 营业成本（累计）
        "q.ps.gp_m.t",           # 毛利率
        "q.ps.op.t",             # 营业利润（累计）
        "q.ps.op_s_r.t",         # 营业利润率
        "q.ps.np.t",             # 净利润（累计）
        "q.ps.np.c_y2y",         # 单季净利润同比
        "q.ps.npatoshopc.t",     # 归母净利润
        "q.ps.npatoshopc.c_y2y", # 归母净利润单季同比
        "q.ps.beps.t",           # 基本每股收益
        "q.ps.beps.c_y2y",       # EPS 单季同比
        "q.ps.np_ttm.ttm",       # 净利润 TTM
        "q.ps.npatoshopc.ttm.ttm",  # 归母净利润 TTM
    ]

    # 资产负债表核心
    METRICS_BALANCE = [
        "q.bs.ta.t",             # 资产总计
        "q.bs.tl.t",             # 负债合计
        "q.bs.toe.t",            # 所有者权益
        "q.bs.cabb.t",           # 货币资金
        "q.bs.ar.t",             # 应收账款
        "q.bs.i.t",              # 存货
        "q.bs.fa.t",             # 固定资产
        "q.bs.tl_ta_r.t",        # 资产负债率
        "q.bs.lwi_ta_r.t",       # 有息负债率
        "q.bs.tca_tcl_r.t",      # 流动比率
        "q.bs.q_r.t",            # 速动比率
    ]

    # 现金流量表核心
    METRICS_CASHFLOW = [
        "q.cfs.ncffoa.t",        # 经营活动现金流净额
        "q.cfs.ncffoa_np_r.t",   # 经营现金流/净利润
        "q.cfs.ncffia.t",        # 投资活动现金流净额
        "q.cfs.ncfffa.t",        # 筹资活动现金流净额
        "q.cfs.ncffoaiafa.t",    # 经投融现金流净额
    ]

    # 财务指标核心
    METRICS_FINANCIAL = [
        "q.m.roe.t",             # ROE
        "q.m.roa.t",             # ROA
        "q.m.gp_m.t",            # 毛利率
        "q.m.np_s_r.t",          # 净利润率
        "q.m.c_r.t",             # 流动比率
        "q.m.fcf.t",             # 自由现金流
        "q.m.i_tor.t",           # 存货周转率
        "q.m.ar_tor.t",          # 应收账款周转率
        "q.m.i_ds.t",            # 存货周转天数
        "q.m.ar_ds.t",           # 应收账款周转天数
    ]

    # 全部预设指标合并（方便直接使用）
    METRICS_ALL = METRICS_INCOME + METRICS_BALANCE + METRICS_CASHFLOW + METRICS_FINANCIAL

    def get_by_date(
        self,
        stock_codes: List[str],
        date: str,
        metrics_list: List[str],
    ) -> List[Dict[str, Any]]:
        """
        指定日期查询多只股票的财报指标。

        Args:
            stock_codes: 股票代码列表（最多 100 只）
            date: 财报日期（季末日期如 "2025-09-30"），或 "latest"
            metrics_list: 指标代码列表

        Returns:
            嵌套结构的财报数据
        """
        if len(stock_codes) > 100:
            raise ValueError(f"stockCodes 最多 100 只，传入 {len(stock_codes)} 只")

        payload = {
            "stockCodes": stock_codes,
            "date": date,
            "metricsList": metrics_list,
        }
        result = self._request(payload)
        return result.get("data", [])

    def get_by_range(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        metrics_list: List[str],
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        按时间范围查询单只股票的财报指标。

        Args:
            stock_code: 单只股票代码
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            metrics_list: 指标代码列表
            limit: 返回最近 N 条

        Returns:
            按日期的财报数据序列
        """
        payload = {
            "stockCodes": [stock_code],
            "startDate": start_date,
            "endDate": end_date,
            "metricsList": metrics_list,
        }
        if limit:
            payload["limit"] = limit

        result = self._request(payload)
        return result.get("data", [])

    def get_latest(
        self,
        stock_codes: List[str],
        metrics_list: List[str],
    ) -> List[Dict[str, Any]]:
        """
        获取最新财报数据。

        Args:
            stock_codes: 股票代码列表
            metrics_list: 指标代码列表

        Returns:
            最新财报数据
        """
        return self.get_by_date(stock_codes, "latest", metrics_list)
