#!/usr/bin/env python3
"""
scripts/fetch_stock_financials.py — 增量拉取A股所有股票季度和年度财务数据

用法：
    python scripts/fetch_stock_financials.py                    # 增量更新（默认）
    python scripts/fetch_stock_financials.py --quarters-only    # 仅季度数据
    python scripts/fetch_stock_financials.py --annual-only      # 仅年度数据
    python scripts/fetch_stock_financials.py --year 2023        # 指定年度（年度数据）

季度数据：批量模式，50只/请求，速度快。
年度数据：逐只查询（API 限制），约每分钟 900 只。

参考：
    - docs/理杏仁财务API接口踩坑指南.md
    - docs/lixinger_apis_help/27. 财报数据API.md
"""

import sys
import json
import time
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from common import (
    api_post, get_db, get_all_stock_codes, get_latest_date,
    ensure_tables, log, RateLimiter,
)

# ════════════════════════════════════════════════════════
# 表结构
# ════════════════════════════════════════════════════════

SCHEMA_SQL = """
-- 季度财务数据
CREATE TABLE IF NOT EXISTS stock_financials_quarterly (
    stock_code              TEXT NOT NULL,
    report_date             TEXT NOT NULL,          -- 报告期 "2025-03-31"
    announce_date           TEXT,                    -- 公告日期
    revenue_single          REAL,                   -- 当季营业收入
    revenue_yoy             REAL,                   -- 当季营收同比(%)
    revenue_qoq             REAL,                   -- 当季营收环比(%)
    net_profit_single       REAL,                   -- 当季归母净利润
    net_profit_margin       REAL,                   -- 归母净利润率(%)
    net_profit_yoy          REAL,                   -- 归母净利润同比(%)
    net_profit_qoq          REAL,                   -- 归母净利润环比(%)
    net_profit_adj_single   REAL,                   -- 当季扣非净利润
    net_profit_adj_margin   REAL,                   -- 扣非净利润率(%)  [计算]
    net_profit_adj_yoy      REAL,                   -- 扣非净利润同比(%)
    net_profit_adj_qoq      REAL,                   -- 扣非净利润环比(%)
    gross_margin_single     REAL,                   -- 当季毛利率(%)  [计算]
    roe_single              REAL,                   -- 当季ROE
    free_cash_flow          REAL,                   -- 当季自由现金流
    asset_liability_ratio   REAL,                   -- 当季资产负债率(%)
    interest_bearing_debt_ratio REAL,               -- 当季有息负债率(%)
    current_ratio           REAL,                   -- 当季流动比率
    quick_ratio             REAL,                   -- 当季速动比率
    receivables_turnover    REAL,                   -- 当季应收账款周转率
    inventory_turnover      REAL,                   -- 当季存货周转率
    updated_at              TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (stock_code, report_date)
);

-- 年度财务数据
CREATE TABLE IF NOT EXISTS stock_financials_annual (
    stock_code              TEXT NOT NULL,
    report_date             TEXT NOT NULL,          -- 报告期 "2025-12-31"
    announce_date           TEXT,
    revenue                 REAL,                   -- 营业总收入
    revenue_yoy             REAL,                   -- 营收同比(%)
    net_profit              REAL,                   -- 归母净利润
    net_profit_yoy          REAL,                   -- 归母净利润同比(%)
    net_profit_adj          REAL,                   -- 扣非净利润
    gross_margin            REAL,                   -- 毛利率(%)
    roe                     REAL,                   -- 归母ROE(%)
    roe_adj                 REAL,                   -- 扣非归母ROE(%)
    operating_cash_flow     REAL,                   -- 经营活动现金流净额
    free_cash_flow          REAL,                   -- 自由现金流
    free_cash_flow_yoy      REAL,                   -- 自由现金流同比(%)
    asset_liability_ratio   REAL,                   -- 资产负债率(%)
    interest_bearing_debt_ratio REAL,               -- 有息负债率(%)
    current_ratio           REAL,                   -- 流动比率
    quick_ratio             REAL,                   -- 速动比率
    receivables_turnover    REAL,                   -- 应收账款周转率
    inventory_turnover      REAL,                   -- 存货周转率
    total_assets            REAL,                   -- 资产总计
    total_liabilities       REAL,                   -- 负债合计
    interest_bearing_debt   REAL,                   -- 有息负债
    updated_at              TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (stock_code, report_date)
);

CREATE INDEX IF NOT EXISTS idx_fq_date ON stock_financials_quarterly(report_date);
CREATE INDEX IF NOT EXISTS idx_fa_date ON stock_financials_annual(report_date);
"""

# ════════════════════════════════════════════════════════
# 指标定义
# ════════════════════════════════════════════════════════

API_PATH = "/company/fs/non_financial"

# 季度指标（批量模式，≤48个指标）
# 映射：[api_metric, db_column, is_pct]
#   is_pct=True → API返回小数（如0.25=25%），写入时×100转为百分比
QUARTERLY_METRICS = [
    # [API 指标路径, DB 列名, 是否百分比]
    ["q.ps.toi.c",              "revenue_single",           False],
    ["q.ps.toi.c_y2y",          "revenue_yoy",              True],
    ["q.ps.toi.c_c2c",          "revenue_qoq",              True],
    ["q.ps.npatoshopc.c",       "net_profit_single",        False],
    ["q.ps.npatoshopc.c_y2y",   "net_profit_yoy",           True],
    ["q.ps.npatoshopc.c_c2c",   "net_profit_qoq",           True],
    ["q.ps.npadnrpatoshaopc.c",     "net_profit_adj_single",     False],
    ["q.ps.npadnrpatoshaopc.c_y2y", "net_profit_adj_yoy",        True],
    ["q.ps.npadnrpatoshaopc.c_c2c", "net_profit_adj_qoq",        True],
    ["q.ps.gp_m.t",             "gross_margin_raw",         True],  # 累计毛利率(基准)
    ["q.ps.oc.c",               "cost_single",              False],  # 单季营业成本（用于计算）
    ["q.ps.np_s_r.t",           "net_profit_margin_raw",    True],  # 累计净利润率(基准)
    ["q.m.roe.t",               "roe_single",               True],
    ["q.m.fcf.t",               "free_cash_flow",           False],
    ["q.bs.tl_ta_r.t",          "asset_liability_ratio",    True],
    ["q.bs.lwi_ta_r.t",         "interest_bearing_debt_ratio", True],
    ["q.bs.tca_tcl_r.t",        "current_ratio",            False],
    ["q.bs.q_r.t",              "quick_ratio",              False],
    ["q.m.ar_tor.t",            "receivables_turnover",     False],
    ["q.m.i_tor.t",             "inventory_turnover",       False],
]
# 共 20 个指标，远低于 48 上限

# 年度指标（逐只模式，≤128个指标）
ANNUAL_METRICS = [
    ["y.ps.toi.t",              "revenue",                  False],
    ["y.ps.toi.t_y2y",          "revenue_yoy",              True],
    ["y.ps.npatoshopc.t",       "net_profit",               False],
    ["y.ps.npatoshopc.t_y2y",   "net_profit_yoy",           True],
    ["y.ps.npadnrpatoshaopc.t", "net_profit_adj",           False],
    ["y.ps.gp_m.t",             "gross_margin",             True],
    ["y.m.roe.t",               "roe",                      True],
    ["y.m.roe_adnrpatoshaopc.t", "roe_adj",                 True],
    ["y.cfs.ncffoa.t",          "operating_cash_flow",      False],
    ["y.m.fcf.t",               "free_cash_flow",           False],
    ["y.m.fcf.t_y2y",           "free_cash_flow_yoy",       True],
    ["y.bs.tl_ta_r.t",          "asset_liability_ratio",    True],
    ["y.bs.lwi_ta_r.t",         "interest_bearing_debt_ratio", True],
    ["y.bs.tca_tcl_r.t",        "current_ratio",            False],
    ["y.bs.q_r.t",              "quick_ratio",              False],
    ["y.m.ar_tor.t",            "receivables_turnover",     False],
    ["y.m.i_tor.t",             "inventory_turnover",       False],
    ["y.bs.ta.t",               "total_assets",             False],
    ["y.bs.tl.t",               "total_liabilities",        False],
    ["y.bs.lwi.t",              "interest_bearing_debt",    False],
]
# 共 17 个指标，远低于 128 上限


# ════════════════════════════════════════════════════════
# 季度数据拉取
# ════════════════════════════════════════════════════════

def extract_nested(data: Dict, api_metric: str) -> Optional[float]:
    """
    从 API 返回的嵌套结构中提取指标值。
    
    api_metric 格式: "q.ps.toi.c"
    对应 data["q"]["ps"]["toi"]["c"]
    """
    keys = api_metric.split(".")
    val = data
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return None
        if val is None:
            return None
    if isinstance(val, (int, float)):
        return float(val)
    return None


def pct_val(v: Optional[float], is_pct: bool) -> Optional[float]:
    """百分比值转换：API 返回小数 -> 百分比"""
    if v is None:
        return None
    return round(v * 100, 2) if is_pct else round(v, 2)


def fetch_quarterly(codes: List[str], date_str: str) -> List[Dict]:
    """批量拉取季度财报 - 50只/批"""
    payload = {
        "stockCodes": codes,
        "date": date_str,
        "metricsList": [m[0] for m in QUARTERLY_METRICS],
    }
    return api_post(API_PATH, payload)


def save_quarterly(conn, raw_data: List[Dict]) -> int:
    """解析API返回并写入季度表"""
    if not raw_data:
        return 0

    rows = []
    for item in raw_data:
        code = item.get("stockCode")
        if not code:
            continue
        date_str = item.get("date", "")[:10]
        announce = (item.get("reportDate") or "")[:10] or None

        # 提取所有指标
        vals = {}
        for api_metric, db_col, is_pct in QUARTERLY_METRICS:
            v = extract_nested(item, api_metric)
            vals[db_col] = pct_val(v, is_pct)

        # 计算字段：单季毛利率 = (营收 - 成本) / 营收
        rev = vals.get("revenue_single")
        cost = vals.get("cost_single")
        if rev and rev != 0 and cost is not None:
            vals["gross_margin_single"] = round((rev - cost) / rev * 100, 2)
        elif vals.get("gross_margin_raw") is not None:
            vals["gross_margin_single"] = vals["gross_margin_raw"]

        # 计算字段：归母净利润率 = 归母净利润 / 营收
        np_single = vals.get("net_profit_single")
        if np_single is not None and rev and rev != 0:
            vals["net_profit_margin"] = round(np_single / rev * 100, 2)
        elif vals.get("net_profit_margin_raw") is not None:
            vals["net_profit_margin"] = vals["net_profit_margin_raw"]

        # 计算字段：扣非净利润率 = 扣非净利润 / 营收
        np_adj = vals.get("net_profit_adj_single")
        if np_adj is not None and rev and rev != 0:
            vals["net_profit_adj_margin"] = round(np_adj / rev * 100, 2)

        # 清理中间字段
        vals.pop("cost_single", None)
        vals.pop("gross_margin_raw", None)
        vals.pop("net_profit_margin_raw", None)

        rows.append((
            code, date_str, announce,
            vals.get("revenue_single"),
            vals.get("revenue_yoy"),
            vals.get("revenue_qoq"),
            vals.get("net_profit_single"),
            vals.get("net_profit_margin"),
            vals.get("net_profit_yoy"),
            vals.get("net_profit_qoq"),
            vals.get("net_profit_adj_single"),
            vals.get("net_profit_adj_margin"),
            vals.get("net_profit_adj_yoy"),
            vals.get("net_profit_adj_qoq"),
            vals.get("gross_margin_single"),
            vals.get("roe_single"),
            vals.get("free_cash_flow"),
            vals.get("asset_liability_ratio"),
            vals.get("interest_bearing_debt_ratio"),
            vals.get("current_ratio"),
            vals.get("quick_ratio"),
            vals.get("receivables_turnover"),
            vals.get("inventory_turnover"),
        ))

    if rows:
        conn.executemany("""INSERT OR REPLACE INTO stock_financials_quarterly
            (stock_code, report_date, announce_date,
             revenue_single, revenue_yoy, revenue_qoq,
             net_profit_single, net_profit_margin, net_profit_yoy, net_profit_qoq,
             net_profit_adj_single, net_profit_adj_margin, net_profit_adj_yoy, net_profit_adj_qoq,
             gross_margin_single, roe_single, free_cash_flow,
             asset_liability_ratio, interest_bearing_debt_ratio,
             current_ratio, quick_ratio,
             receivables_turnover, inventory_turnover)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
        conn.commit()
    return len(rows)


# ════════════════════════════════════════════════════════
# 年度数据拉取（时间范围模式 — 一次拉多年，减少80%请求）
# ════════════════════════════════════════════════════════
# 踩坑指南坑1：y. 前缀指标不能批量 stockCodes，只能逐只拉。
# 优化：用 startDate+endDate 一次拉10年，替代逐只×逐年的双重循环。

def fetch_annual_range(code: str, start_date: str, end_date: str) -> List[Dict]:
    """逐只按时间范围拉取年度财报（一次拉多年，≤10年）"""
    payload = {
        "stockCodes": [code],
        "startDate": start_date,
        "endDate": end_date,
        "metricsList": [m[0] for m in ANNUAL_METRICS],
    }
    return api_post(API_PATH, payload)


def get_existing_annual_years(conn, code: str, check_new_cols: bool = False) -> set:
    """查询某只股票已有的年度数据年份。check_new_cols=True 时，排除新列为NULL的年份。"""
    if check_new_cols:
        rows = conn.execute(
            "SELECT report_date FROM stock_financials_annual WHERE stock_code = ? AND total_liabilities IS NOT NULL",
            (code,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT report_date FROM stock_financials_annual WHERE stock_code = ?",
            (code,)
        ).fetchall()
    return {r["report_date"][:4] for r in rows}


def save_annual_one(conn, raw_data: List[Dict]) -> int:
    """解析并写入单只股票的年度数据"""
    if not raw_data:
        return 0

    rows = []
    for item in raw_data:
        code = item.get("stockCode")
        if not code:
            continue
        date_str = item.get("date", "")[:10]
        announce = (item.get("reportDate") or "")[:10] or None

        vals = {}
        for api_metric, db_col, is_pct in ANNUAL_METRICS:
            v = extract_nested(item, api_metric)
            vals[db_col] = pct_val(v, is_pct)

        rows.append((
            code, date_str, announce,
            vals.get("revenue"),
            vals.get("revenue_yoy"),
            vals.get("net_profit"),
            vals.get("net_profit_yoy"),
            vals.get("net_profit_adj"),
            vals.get("gross_margin"),
            vals.get("roe"),
            vals.get("roe_adj"),
            vals.get("operating_cash_flow"),
            vals.get("free_cash_flow"),
            vals.get("free_cash_flow_yoy"),
            vals.get("asset_liability_ratio"),
            vals.get("interest_bearing_debt_ratio"),
            vals.get("current_ratio"),
            vals.get("quick_ratio"),
            vals.get("receivables_turnover"),
            vals.get("inventory_turnover"),
            vals.get("total_assets"),
            vals.get("total_liabilities"),
            vals.get("interest_bearing_debt"),
        ))

    if rows:
        conn.executemany("""INSERT OR REPLACE INTO stock_financials_annual
            (stock_code, report_date, announce_date,
             revenue, revenue_yoy,
             net_profit, net_profit_yoy,
             net_profit_adj, gross_margin,
             roe, roe_adj,
             operating_cash_flow, free_cash_flow, free_cash_flow_yoy,
             asset_liability_ratio, interest_bearing_debt_ratio,
             current_ratio, quick_ratio,
             receivables_turnover, inventory_turnover,
             total_assets,
             total_liabilities,
             interest_bearing_debt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
        conn.commit()
    return len(rows)


# ════════════════════════════════════════════════════════
# 日期工具
# ════════════════════════════════════════════════════════

def recent_report_dates(years_back: int = 10) -> List[str]:
    """生成最近的季度报告日期列表（从 YYYY-03-31 到当前最新季度）"""
    today = datetime.now()
    current_quarter = (today.month - 1) // 3 + 1
    current_year = today.year

    dates = []
    for y in range(current_year - years_back, current_year + 1):
        for q in [1, 2, 3, 4]:
            month = q * 3
            day = 31 if month in (3, 12) else 30
            d = f"{y}-{month:02d}-{day:02d}"
            # 不取未来日期（当前季度可能未结束）
            if y < current_year or (y == current_year and q <= current_quarter):
                dates.append(d)
    return dates


def due_report_dates(n: int = 3) -> List[str]:
    """当前应拉取的季度报告期（披露窗口内，覆盖写补晚披露公司）

    设计：公司披露时间差异大（如万华中报 8-24 发布），不能等披露截止（8-31）后才拉。
    改为：报告期结束后即进入拉取窗口，窗口内每天覆盖拉取（INSERT OR REPLACE 幂等），
    理杏仁每次返回"截至目前已披露的公司"——早披露早入库，晚披露的下次跑自动补上。

    窗口（按报告期法定披露截止）：
      Q1(03-31) → +60 天（4-30 截止）
      中报(06-30) → +70 天（8-31 截止）
      三季报(09-30) → +60 天（10-31 截止）
      年报(12-31) → +150 天（次年 4-30 截止）
    """
    today = datetime.now()
    y = today.year
    q = ((today.month - 1) // 3) * 3 + 3  # 当前季度末月: 3/6/9/12
    dates = []
    for _ in range(8):
        end_d = f"{y}-{q:02d}-{'31' if q in (3, 12) else '30'}"
        end_date = datetime.strptime(end_d, '%Y-%m-%d')
        window = 150 if q == 12 else (70 if q == 6 else 60)
        age = (today - end_date).days
        if 0 <= age <= window:  # 报告期已结束且在披露窗口内
            dates.append(end_d)
        q -= 3
        if q == 0:
            q = 12
            y -= 1
        if len(dates) >= n:
            break
    dates.reverse()
    return dates


def recent_annual_dates(years_back: int = 10) -> List[str]:
    """生成最近的年度报告日期"""
    today = datetime.now()
    dates = []
    for y in range(today.year - years_back, today.year + 1):
        dates.append(f"{y}-12-31")
    return dates


# ════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════

def main():
    conn = get_db()
    ensure_tables(conn, SCHEMA_SQL)

    do_quarterly = "--annual-only" not in sys.argv
    do_annual = "--quarters-only" not in sys.argv
    recent_mode = "--recent" in sys.argv  # 只拉应披露报告期（财报季触发）

    # 指定年度覆盖
    if any(a.startswith("--year") for a in sys.argv):
        # 手动指定年度
        years = []
        for a in sys.argv:
            if a.startswith("--year") and "=" in a:
                years.append(int(a.split("=")[1]))
            elif a.startswith("--year") and not a.startswith("--year="):
                # next arg
                idx = sys.argv.index(a)
                if idx + 1 < len(sys.argv):
                    try:
                        years.append(int(sys.argv[idx + 1]))
                    except ValueError:
                        pass
        if years:
            annual_dates = [f"{y}-12-31" for y in years]
            quarterly_dates = [f"{y}-{m}" for y in years for m in ["03-31", "06-30", "09-30", "12-31"]]
        else:
            annual_dates = recent_annual_dates()
            quarterly_dates = recent_report_dates()
    elif recent_mode:
        # 财报季增量：只拉应披露报告期（Q1/年报→5月, 中报→9月, 三季报→11月）
        quarterly_dates = due_report_dates(2)
        annual_dates = [d for d in due_report_dates(4) if d.endswith("12-31")]
    else:
        annual_dates = recent_annual_dates()
        quarterly_dates = recent_report_dates()

    # ── 季度数据 ──
    if do_quarterly:
        log.info("=" * 60)
        log.info("📊 季度财务数据（批量模式，50只/批）")
        log.info(f"   报告期: {quarterly_dates}")

        all_codes = get_all_stock_codes(conn)
        log.info(f"   股票数量: {len(all_codes)}")
        batch_size = 50

        total_q_saved = 0
        for date_str in quarterly_dates:
            log.info(f"  [{date_str}] 开始拉取...")
            date_saved = 0
            for i in range(0, len(all_codes), batch_size):
                batch = all_codes[i:i + batch_size]
                try:
                    raw = fetch_quarterly(batch, date_str)
                    n = save_quarterly(conn, raw)
                    total_q_saved += n
                    date_saved += n
                    log.info(f"    [{i+len(batch)}/{len(all_codes)}] +{n} 条")
                except Exception as e:
                    log.error(f"    [{i+len(batch)}/{len(all_codes)}] ❌ {e}")
                    time.sleep(5)
            log.info(f"  [{date_str}] ✅ 完成: {date_saved} 条")

        log.info(f"📊 季度合计: {total_q_saved} 条")

    # ── 年度数据 ──
    if do_annual:
        log.info("=" * 60)
        log.info("📊 年度财务数据（时间范围模式，1次/只拉10年）")
        log.info(f"   报告期: {annual_dates[0]} ~ {annual_dates[-1]}")

        all_codes = get_all_stock_codes(conn)
        log.info(f"   股票数量: {len(all_codes)}")

        # 确定目标年份集合
        target_years = set(d[:4] for d in annual_dates)
        start_date = annual_dates[0]
        end_date = annual_dates[-1]

        total_a_saved = 0
        skipped = 0
        batch_size = 200  # 进度汇报间隔

        for i, code in enumerate(all_codes):
            # 跳过已有全部目标年份数据的股票
            existing = get_existing_annual_years(conn, code, check_new_cols=True)
            if target_years.issubset(existing):
                skipped += 1
                if (i + 1) % batch_size == 0:
                    log.info(f"    [{i+1}/{len(all_codes)}] 已跳过 {skipped} 只（数据完整）")
                continue

            try:
                raw = fetch_annual_range(code, start_date, end_date)
                n = save_annual_one(conn, raw)
                total_a_saved += n
            except Exception as e:
                log.error(f"    [{i+1}/{len(all_codes)}] {code} ❌ {e}")
                time.sleep(5)

            if (i + 1) % batch_size == 0:
                log.info(f"    [{i+1}/{len(all_codes)}] +{total_a_saved} 条 (跳过 {skipped} 只)")

        log.info(f"  ✅ 完成: +{total_a_saved} 条, 跳过 {skipped} 只（已有完整数据）")
        log.info(f"📊 年度合计: {total_a_saved} 条")

    # ── 打印最终统计 ──
    for tbl in ["stock_financials_quarterly", "stock_financials_annual"]:
        row = conn.execute(f"SELECT COUNT(*) as cnt FROM {tbl}").fetchone()
        log.info(f"   {tbl}: {row['cnt']:,} 条")

    conn.close()
    log.info("🏁 全部完成")


if __name__ == "__main__":
    main()
