#!/usr/bin/env python
"""
scripts/fetch_index_fundamental.py — 拉取指数基本面数据（拥挤度计算依赖）

从 config/index_style.yaml 读取 408 个指数清单，通过理杏仁 /index/fundamental API
拉取近 10 年的市值/成交量/换手率/PE/PB/股息率/融资/分位点等基本面指标。

用法：
    python scripts/fetch_index_fundamental.py --test        # 测试：仅拉取 5 个指数，每个拉近 2 周
    python scripts/fetch_index_fundamental.py                # 全量：408 个指数 × 10 年
    python scripts/fetch_index_fundamental.py --category market  # 仅全市场指数
    python scripts/fetch_index_fundamental.py --incremental      # 增量更新
    python scripts/fetch_index_fundamental.py --dry-run          # 仅统计不写入

API 参考：docs/lixinger_index_apis_help/指数基本面数据.md
"""

import os
import sys
import yaml
import time
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from common import api_post, get_db, log

# ── 配置 ──────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "index_style.yaml")
API_PATH = "/index/fundamental"

# 基本面指标
METRICS = [
    "mc",                        # 总市值
    "tv",                        # 成交量
    "ta",                        # 成交额
    "to_r",                      # 换手率
    "pe_ttm.mcw",                # PE-TTM 市值加权
    "pe_ttm.y10.mcw.cvpos",      # PE 10年分位点
    "pb.mcw",                    # PB 市值加权
    "pb.y10.mcw.cvpos",          # PB 10年分位点
    "dyr.mcw",                   # 股息率 市值加权
    "dyr.y10.mcw.cvpos",         # 股息率 10年分位点
    "fpa",                       # 融资买入金额
    "fb",                        # 融资余额
    "ecmc",                      # 自由流通市值
]

# 建表 SQL
CREATE_TABLE_SQL = """CREATE TABLE IF NOT EXISTS index_fundamental_daily (
    stock_code   TEXT NOT NULL,
    date         TEXT NOT NULL,
    mc           REAL,    -- 总市值
    tv           REAL,    -- 成交量
    ta           REAL,    -- 成交额
    to_r         REAL,    -- 换手率 (%)
    pe_ttm       REAL,    -- PE-TTM
    pe_ttm_pct   REAL,    -- PE 10年分位点 (0~1)
    pb           REAL,    -- PB
    pb_pct       REAL,    -- PB 10年分位点 (0~1)
    dyr          REAL,    -- 股息率 (%)
    dyr_pct      REAL,    -- 股息率 10年分位点 (0~1)
    fpa          REAL,    -- 融资买入金额
    fb           REAL,    -- 融资余额
    ecmc         REAL,    -- 自由流通市值
    updated_at   TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (stock_code, date)
)"""

UPSERT_SQL = """INSERT OR REPLACE INTO index_fundamental_daily
    (stock_code, date, mc, tv, ta, to_r, pe_ttm, pe_ttm_pct,
     pb, pb_pct, dyr, dyr_pct, fpa, fb, ecmc)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

# API 单次区间 ≤ 10 年，用 9 年安全切分
CHUNK_YEARS = 9


# ════════════════════════════════════════════════════════
# 配置加载
# ════════════════════════════════════════════════════════

def load_indices(categories=None):
    """从 index_style.yaml 加载指数，返回 {code: name}"""
    with open(CONFIG_PATH, encoding='utf-8') as f:
        data = yaml.safe_load(f)
    cats = data.get("categories", {})
    result = {}
    for cat_name, indices in cats.items():
        if cat_name == 'etf':
            continue  # ETF 不走理杏仁（价格字段量级错误），由 fetch_hk_etf.py 独立管理
        if categories and cat_name not in categories:
            continue
        for item in indices:
            result[item["code"]] = item["name"]
    return result


# ════════════════════════════════════════════════════════
# 日期切分
# ════════════════════════════════════════════════════════

def date_chunks(start, end, years=CHUNK_YEARS):
    """将 [start, end] 按 years 年切分为多个 (s, e) 区间"""
    chunks = []
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    while s < e:
        chunk_end = min(s + relativedelta(years=years), e)
        chunks.append((s.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        s = chunk_end + timedelta(days=1)
    return chunks


# ════════════════════════════════════════════════════════
# 数据拉取 & 存储
# ════════════════════════════════════════════════════════

def fetch_one_index(stock_code, start_date, end_date):
    """拉取单个指数一段时间的基本面数据"""
    payload = {
        "stockCodes": [stock_code],
        "metricsList": METRICS,
        "startDate": start_date,
        "endDate": end_date,
    }
    return api_post(API_PATH, payload)


def save_fundamental(conn, stock_code, data):
    """将 API 返回的数据写入 index_fundamental_daily 表"""
    rows = []
    for item in data:
        date_str = item["date"][:10]  # "2026-03-10T00:00:00+08:00" → "2026-03-10"
        rows.append((
            stock_code,
            date_str,
            item.get("mc"),
            item.get("tv"),
            item.get("ta"),
            item.get("to_r"),
            item.get("pe_ttm.mcw"),
            item.get("pe_ttm.y10.mcw.cvpos"),
            item.get("pb.mcw"),
            item.get("pb.y10.mcw.cvpos"),
            item.get("dyr.mcw"),
            item.get("dyr.y10.mcw.cvpos"),
            item.get("fpa"),
            item.get("fb"),
            item.get("ecmc"),
        ))
    if rows:
        conn.executemany(UPSERT_SQL, rows)
        conn.commit()
    return len(rows)


def fetch_all(indices, start_date, end_date, dry_run=False, incremental=False, conn=None):
    """
    拉取所有指数的基本面数据。
    indices: {code: name}
    """
    codes = sorted(indices.keys())
    chunks = date_chunks(start_date, end_date)

    total_rows = 0
    for idx, stock_code in enumerate(codes):
        name = indices[stock_code]
        label = f"[{idx+1}/{len(codes)}] {stock_code} {name}"

        # 增量模式：检查该指数最新日期
        if incremental and conn:
            latest = conn.execute(
                "SELECT MAX(date) FROM index_fundamental_daily WHERE stock_code=?",
                (stock_code,)
            ).fetchone()
            if latest and latest[0]:
                # 从最后日期的下一天开始
                last_date = datetime.strptime(latest[0], "%Y-%m-%d") + timedelta(days=1)
                effective_start = last_date.strftime("%Y-%m-%d")
                effective_chunks = date_chunks(effective_start, end_date)
                if not effective_chunks:
                    log.info(f"  {label}: 已是最新，跳过")
                    continue
                log.info(f"  {label}: 增量更新，从 {effective_start} 起")
            else:
                effective_chunks = chunks
        else:
            effective_chunks = chunks

        for ch_start, ch_end in effective_chunks:
            if dry_run:
                log.info(f"  [DRY] {label}  {ch_start} ~ {ch_end}")
                continue

            try:
                data = fetch_one_index(stock_code, ch_start, ch_end)
                n = save_fundamental(conn, stock_code, data)
                total_rows += n
                log.info(f"  {label}  {ch_start} ~ {ch_end}: +{n} 行")
            except Exception as e:
                log.error(f"  {label}  {ch_start} ~ {ch_end}: 失败 - {e}")
                continue

        # 每个指数之间暂停一下，避免触发限流
        if idx < len(codes) - 1:
            time.sleep(0.3)

    return total_rows


# ════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="拉取指数基本面数据")
    parser.add_argument("--test", action="store_true", help="测试模式：仅拉取 5 个指数，每个近 2 周")
    parser.add_argument("--category", type=str, default=None,
                        help="指数分类，如 market,sector_l1（逗号分隔）")
    parser.add_argument("--incremental", action="store_true", help="增量更新：只拉取缺失的日期")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不实际拉取")
    parser.add_argument("--start", type=str, default="2016-01-01", help="起始日期")
    parser.add_argument("--end", type=str, default=None, help="结束日期（默认今天）")
    args = parser.parse_args()

    end_date = args.end or datetime.now().strftime("%Y-%m-%d")

    # 加载指数
    categories = args.category.split(",") if args.category else None
    indices = load_indices(categories)
    log.info(f"加载 {len(indices)} 个指数（分类: {categories or '全部'}）")

    if args.test:
        # 测试模式：每种分类取 1 个指数，共约 5 个
        test_indices = {}
        seen_cats = set()
        with open(CONFIG_PATH, encoding='utf-8') as f:
            data = yaml.safe_load(f)
        for cat_name, idx_list in data.get("categories", {}).items():
            if idx_list and cat_name not in seen_cats:
                item = idx_list[0]
                test_indices[item["code"]] = item["name"]
                seen_cats.add(cat_name)
        indices = test_indices
        # 测试只拉最近 2 周
        test_start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
        log.info(f"测试模式：{len(indices)} 个指数，{test_start} ~ {end_date}")
        start_date = test_start
    else:
        start_date = args.start

    log.info(f"日期范围：{start_date} ~ {end_date}")
    chunks = date_chunks(start_date, end_date)
    log.info(f"切分为 {len(chunks)} 个时间段：{chunks}")

    # 建表 & 拉取
    conn = get_db()
    conn.execute(CREATE_TABLE_SQL)
    conn.commit()

    total = fetch_all(indices, start_date, end_date,
                      dry_run=args.dry_run,
                      incremental=args.incremental,
                      conn=conn)
    log.info(f"完成！共写入 {total} 行数据到 index_fundamental_daily")
    conn.close()


if __name__ == "__main__":
    main()
