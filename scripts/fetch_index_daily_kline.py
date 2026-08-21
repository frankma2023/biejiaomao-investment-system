#!/usr/bin/env python3
"""
scripts/fetch_index_daily_kline.py — 增量拉取指数日K线（配置驱动）

用法：
    python scripts/fetch_index_daily_kline.py                        # 默认：全市场 + L1 + L2（~85个指数）
    python scripts/fetch_index_daily_kline.py --all                  # 全部分类（~407个指数）
    python scripts/fetch_index_daily_kline.py --category market      # 仅全市场指数
    python scripts/fetch_index_daily_kline.py --category market,sector_l1  # 指定多个分类
    python scripts/fetch_index_daily_kline.py --start 2015-01-01     # 指定起始日期
    python scripts/fetch_index_daily_kline.py --start 2010-01-01 --end 2020-12-31

分类说明（定义于 config/index_style.yaml）：
    market      — 全市场指数（29个）
    sector_l1   — L1 一级行业（11个）
    sector_l2   — L2 二级行业（45个）
    thematic    — L3 行业主题（229个）
    strategy    — 策略指数（93个）

注意：理杏仁API限制单次请求不超过10年。超过10年的区间会自动分批拉取。
指数K线保存到 index_daily_kline 表（独立于股票 daily_kline）。
"""

import os
import sys
import yaml
from datetime import datetime, timedelta
from common import api_post, get_db, get_latest_date, log

# ── 指数配置加载 ──────────────────────────────────────

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "index_style.yaml")


def load_index_config(path: str = None) -> dict:
    """从 index_style.yaml 加载指数配置，返回 {code: name, ...} 的扁平字典"""
    path = path or CONFIG_PATH
    with open(path, "r", encoding='utf-8') as f:
        data = yaml.safe_load(f)
    categories = data.get("categories", {})
    result = {}
    for cat_name, indices in categories.items():
        if cat_name == 'etf':
            continue  # ETF 不走理杏仁（价格字段量级错误），由 fetch_hk_etf.py 独立管理
        for item in indices:
            result[item["code"]] = item["name"]
    return result


def load_index_config_by_category(path: str = None) -> dict:
    """从 index_style.yaml 加载指数配置，按分类分组返回 {category: {code: name, ...}}"""
    path = path or CONFIG_PATH
    with open(path, "r", encoding='utf-8') as f:
        data = yaml.safe_load(f)
    result = {}
    for cat_name, indices in data.get("categories", {}).items():
        if cat_name == 'etf':
            continue  # ETF 不走理杏仁（价格字段量级错误），由 fetch_hk_etf.py 独立管理
        result[cat_name] = {item["code"]: item["name"] for item in indices}
    return result

# ── API ───────────────────────────────────────────────
API_PATH = "/index/candlestick"

UPSERT_SQL = """INSERT OR REPLACE INTO index_daily_kline
    (stock_code, date, kline_type, open, close, high, low,
     volume, amount, change)
    VALUES (?, ?, 'normal', ?, ?, ?, ?, ?, ?, ?)"""


# ════════════════════════════════════════════════════════

def fetch_index_kline(stock_code: str, start_date: str, end_date: str) -> list:
    """拉取单只指数的区间K线"""
    payload = {
        "stockCode": stock_code,
        "type": "normal",
        "startDate": start_date,
        "endDate": end_date,
    }
    return api_post(API_PATH, payload)


def fetch_index_kline_batched(stock_code: str, start_date: str, end_date: str,
                              max_years: int = 9) -> list:
    """分批拉取K线，每批不超过 max_years 年（理杏仁限制 ≤10年）"""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    span = (end - start).days

    if span <= max_years * 365:
        # 单批即可
        return fetch_index_kline(stock_code, start_date, end_date)

    # 需要分批
    chunk_delta = timedelta(days=max_years * 365)
    all_klines = []
    chunk_start = start

    while chunk_start < end:
        chunk_end = min(chunk_start + chunk_delta, end)
        s = chunk_start.strftime("%Y-%m-%d")
        e = chunk_end.strftime("%Y-%m-%d")
        log.info(f"    分批: {s} → {e}")
        chunk = fetch_index_kline(stock_code, s, e)
        all_klines.extend(chunk)
        chunk_start = chunk_end + timedelta(days=1)

    # 去重（按 date 字段）
    seen = set()
    deduped = []
    for k in all_klines:
        d = k["date"][:10]
        if d not in seen:
            seen.add(d)
            deduped.append(k)
    return deduped


def save_klines(conn, stock_code: str, klines: list) -> int:
    """写入指数K线"""
    if not klines:
        return 0
    rows = []
    for k in klines:
        rows.append((
            stock_code,
            k["date"][:10],
            k.get("open"),
            k.get("close"),
            k.get("high"),
            k.get("low"),
            k.get("volume"),
            k.get("amount"),
            k.get("change"),
        ))
    conn.executemany(UPSERT_SQL, rows)
    conn.commit()
    return len(rows)


def main():
    conn = get_db()

    # ── 解析参数 ──────────────────────────────────────────
    user_start = None
    user_end = None
    for i, arg in enumerate(sys.argv):
        if arg == "--start" and i + 1 < len(sys.argv):
            user_start = sys.argv[i + 1]
        if arg == "--end" and i + 1 < len(sys.argv):
            user_end = sys.argv[i + 1]

    # 加载指数配置（按分类）
    cats = load_index_config_by_category()

    # ── 确定拉取哪些分类 ──────────────────────────────────
    VALID_CATS = {"market", "sector_l1", "sector_l2", "thematic", "strategy"}

    if "--all" in sys.argv:
        selected_cats = list(cats.keys())  # 全部分类
        log.info("🎯 拉取全部 5 个分类（共 %d 个指数）", sum(len(cats[c]) for c in cats))
    elif any(a.startswith("--category") for a in sys.argv):
        # --category market,sector_l1
        selected_cats = []
        for a in sys.argv:
            if a.startswith("--category="):
                selected_cats.extend(c.strip() for c in a.split("=", 1)[1].split(","))
            elif a == "--category":
                idx = sys.argv.index(a)
                if idx + 1 < len(sys.argv):
                    selected_cats.extend(c.strip() for c in sys.argv[idx + 1].split(","))
        # 过滤非法分类
        selected_cats = [c for c in selected_cats if c in VALID_CATS]
        if not selected_cats:
            log.error(f"无效分类，支持: {', '.join(sorted(VALID_CATS))}")
            return
        log.info("🎯 指定分类: %s", ", ".join(selected_cats))
    elif any(a.startswith("--tier-1") for a in sys.argv):
        # 向后兼容：旧 --tier-1 = 全市场指数
        selected_cats = ["market"]
        log.info("🎯 仅拉取全市场指数（--tier-1 兼容）")
    else:
        # 默认：全市场 + L1 + L2（≈ 旧版 tier-1 + tier-2）
        selected_cats = ["market", "sector_l1", "sector_l2"]
        log.info("🎯 默认：全市场 + L1一级 + L2二级（共 %d 个指数）",
                 sum(len(cats[c]) for c in selected_cats))

    # 合并选中分类的指数为统一字典
    indices = {}
    for cat in selected_cats:
        indices.update(cats.get(cat, {}))

    default_end = datetime.now().strftime("%Y-%m-%d")
    total_saved = 0

    for idx_code, idx_name in indices.items():
        # 查询该指数已存的最新日期
        latest = get_latest_date(conn, "index_daily_kline", stock_code=idx_code)

        if user_start:
            start_date = user_start
        elif latest:
            start = datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)
            start_date = start.strftime("%Y-%m-%d")
            if start_date > default_end:
                log.info(f"  {idx_code} {idx_name} ✅ 已最新")
                continue
        else:
            start_date = "2000-01-01"

        end_date = user_end if user_end else default_end

        try:
            klines = fetch_index_kline_batched(idx_code, start_date, end_date)
            n = save_klines(conn, idx_code, klines)
            total_saved += n
            log.info(f"  {idx_code} {idx_name}: +{n} 条 {start_date}~{end_date}")
        except Exception as e:
            log.error(f"  {idx_code} {idx_name}: ❌ {e}")

    log.info(f"🏁 完成: 共保存 {total_saved} 条指数K线")
    conn.close()


if __name__ == "__main__":
    main()
