"""
src/scanners/stock_rs.py — 个股RS强度计算引擎

基于 Polars 高表结构，对全市场A股计算 RPS_250/RPS_120/RPS_60/RPS_20，
筛选双强股票（稳健龙头 / 加速爆发）。

运行：
    python src/scanners/stock_rs.py                          # 计算最新日期
    python src/scanners/stock_rs.py --date 2026-05-07        # 指定日期
    python src/scanners/stock_rs.py --start 2025-01-01       # 日期范围（用于回测）

API 集成：
    from scanners.stock_rs import compute, get_double_strong
"""

import os
import sqlite3
import polars as pl
from datetime import datetime, timedelta

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "lixinger.db")

# ── 配置 ──────────────────────────────────────────────
PERIODS = [20, 60, 120, 250]
N_VALID = {20: 10, 60: 30, 120: 60, 250: 125}  # 最少有效K线数

# 双强阈值
DOUBLE_STRONG = {
    "稳健龙头": {"rps_250": 90, "rps_20": 85},
    "加速爆发": {"rps_250": 80, "rps_20": 95},
}

# 过滤默认值
DEFAULT_EXCLUDE_ST = True
DEFAULT_MIN_AMOUNT_20D = 5000_0000  # 5000万（单位：元）

# 计算基准：至少需要这么多天历史数据
LOOKBACK_YEARS = 3  # 从目标日期往回取3年数据，保证250日窗口充足


# ════════════════════════════════════════════════════════

def load_data(conn, target_date=None, start_date=None):
    """
    从 SQLite 加载数据到 Polars DataFrame。

    返回: (kline_df, index_df, stock_info_df)
    """
    # 确定日期范围
    if target_date:
        end = datetime.strptime(target_date, "%Y-%m-%d")
    else:
        end = datetime.now()
    if start_date:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        # 往前多拉 2 年历史数据，确保 ret_250 可算
        start = start - timedelta(days=730)
    else:
        start = end - timedelta(days=LOOKBACK_YEARS * 365)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    # ── 加载个股K线 ──
    kline_rows = conn.execute("""
        SELECT stock_code, date, COALESCE(adj_close, close) as adj_close, amount
        FROM daily_kline
        WHERE date >= ? AND date <= ?
        ORDER BY stock_code, date
    """, (start_str, end_str)).fetchall()

    # ── 加载指数K线（中证全指 000985）──
    idx_rows = conn.execute("""
        SELECT date, close
        FROM index_daily_kline
        WHERE stock_code = '000985'
          AND kline_type = 'normal'
          AND date >= ? AND date <= ?
        ORDER BY date
    """, (start_str, end_str)).fetchall()

    # ── 加载股票基础信息 ──
    info_rows = conn.execute("""
        SELECT stock_code, name, listing_status
        FROM stock_basic
        WHERE listing_status IN ('normally_listed', 'special_treatment', 'delisting_risk_warning')
    """).fetchall()

    # 转 Polars DataFrame
    kline = pl.DataFrame(
        [(r["stock_code"], r["date"], float(r["adj_close"] or 0), float(r["amount"] or 0)) for r in kline_rows],
        schema=["stock_code", "date", "adj_close", "amount"],
        orient="row",
    )

    index_df = pl.DataFrame(
        [(r["date"], r["close"]) for r in idx_rows],
        schema=["date", "idx_close"],
        orient="row",
    )

    stock_info = pl.DataFrame(
        [(r["stock_code"], r["name"], r["listing_status"]) for r in info_rows],
        schema=["stock_code", "name", "listing_status"],
        orient="row",
    )

    return kline, index_df, stock_info


def compute(target_date=None, start_date=None,
            exclude_st=True, min_amount_20d=DEFAULT_MIN_AMOUNT_20D,
            double_strong_cfg=None):
    """
    主计算函数。返回 Polars DataFrame，包含所有股票的 RPS 值。

    参数:
        target_date: 目标日期，None=今天
        start_date: 起始日期，None=自动回溯3年
        exclude_st: 是否剔除ST
        min_amount_20d: 流动性过滤阈值（元），0=不过滤
        double_strong_cfg: 双强阈值，None=使用默认

    返回:
        result_df: Polars DataFrame (stock_code, date, adj_close, amount,
                   ret_20/60/120/250, rps_20/60/120/250, rs_line_norm, ...)
    """
    if double_strong_cfg is None:
        double_strong_cfg = DOUBLE_STRONG

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    kline, index_df, stock_info = load_data(conn, target_date, start_date)
    conn.close()

    # ── 预排序 + 去重防御 ──
    kline = kline.sort(["stock_code", "date"]).unique(
        subset=["stock_code", "date"], keep="last"
    )
    index_df = index_df.sort("date")

    # ── 关联股票基础信息（过滤ST/退市）──
    kline = kline.join(
        stock_info.select(["stock_code", "listing_status", "name"]),
        on="stock_code", how="left"
    ).sort(["stock_code", "date"])  # join 不保证顺序，必须重排

    if exclude_st:
        kline = kline.filter(
            ~pl.col("listing_status").is_in(["special_treatment", "delisting_risk_warning"])
        )
    kline = kline.filter(pl.col("listing_status") != "delisted")

    # ── 预计算辅助列 ──
    kline = kline.with_columns([
        # 每条K线在其股票序列中的序号（从0开始）
        pl.int_range(pl.len()).over("stock_code").alias("_bar_index"),
        # 20日均成交额
        pl.col("amount").rolling_mean(20, min_samples=10)
        .over("stock_code").alias("avg_amount_20d"),
    ])

    # ── 流动性过滤（排名前剔除）──
    if min_amount_20d > 0:
        kline = kline.filter(pl.col("avg_amount_20d") >= min_amount_20d)

    # ── 计算四个周期涨跌幅 ──
    kline = kline.with_columns([
        (pl.col("adj_close") / pl.col("adj_close").shift(n).over("stock_code") - 1)
        .alias(f"ret_{n}")
        for n in PERIODS
    ])

    # ── 标记有效样本 ──
    kline = kline.with_columns([
        (pl.col("_bar_index") >= N_VALID[n]).alias(f"valid_{n}")
        for n in PERIODS
    ])

    # ── 计算 RPS 排名 ──
    kline = kline.with_columns([
        pl.when(pl.col(f"valid_{n}"))
        .then(pl.col(f"ret_{n}").rank("min", descending=True).over("date"))
        .alias(f"rank_{n}")
        for n in PERIODS
    ])

    # ── rank → RPS (0-99)，防御除零 ──
    kline = kline.with_columns([
        pl.when(pl.col(f"valid_{n}"))
        .then(
            ((1 - (pl.col(f"rank_{n}") - 1) /
              pl.max_horizontal(pl.col(f"rank_{n}").max().over("date") - 1, 1)) * 99)
            .round(0).cast(pl.Int32)
        )
        .alias(f"rps_{n}")
        for n in PERIODS
    ])

    # ── RS 线计算（归一化，基日 = 第一个有效日期）──
    kline = kline.join(
        index_df.select(["date", pl.col("idx_close").alias("_idx_close")]),
        on="date", how="left"
    )

    # 个股/指数比值
    kline = kline.with_columns(
        (pl.col("adj_close") / pl.col("_idx_close")).alias("rs_line_raw")
    )

    # 归一化：基日 = 每只股票的第一个有效RS线值 → 100
    kline = kline.with_columns(
        (pl.col("rs_line_raw") / pl.col("rs_line_raw").first().over("stock_code") * 100)
        .alias("rs_line_norm")
    )

    # ── 双强标记 ──
    robust = double_strong_cfg.get("稳健龙头", {})
    burst = double_strong_cfg.get("加速爆发", {})
    kline = kline.with_columns([
        pl.when(
            (pl.col("rps_250") >= robust.get("rps_250", 90)) &
            (pl.col("rps_20") >= robust.get("rps_20", 85))
        ).then(pl.lit("稳健龙头"))
        .when(
            (pl.col("rps_250") >= burst.get("rps_250", 80)) &
            (pl.col("rps_20") >= burst.get("rps_20", 95))
        ).then(pl.lit("加速爆发"))
        .otherwise(pl.lit(None))
        .alias("double_strong")
    ])

    # 清理辅助列
    result = kline.drop(["_bar_index", "_idx_close"])

    return result


def get_double_strong(result_df):
    """从计算结果中提取双强股票"""
    return result_df.filter(pl.col("double_strong").is_not_null())


# ════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="计算A股RS强度")
    parser.add_argument("--date", type=str, default=None, help="目标日期 YYYY-MM-DD")
    parser.add_argument("--start", type=str, default=None, help="起始日期")
    parser.add_argument("--end", type=str, default=None, help="结束日期（默认今天）")
    args = parser.parse_args()

    print("计算中...")
    result = compute(target_date=args.end or args.date, start_date=args.start)

    # 最新日期摘要
    latest_date = result["date"].max()
    latest = result.filter(pl.col("date") == latest_date)
    total = len(latest)
    valid_250 = latest.filter(pl.col("valid_250")).shape[0]
    ds = latest.filter(pl.col("double_strong").is_not_null())

    print(f"日期: {latest_date}")
    print(f"有效股票: {total} (RPS_250有效: {valid_250})")
    print(f"双强股票: {ds.shape[0]}")

    if ds.shape[0] > 0:
        print("\n双强股票 TOP10:")
        top = ds.sort("rps_250", descending=True).head(10)
        for row in top.iter_rows(named=True):
            print(f"  {row['stock_code']} {row['name']} "
                  f"RPS_250={row['rps_250']} RPS_20={row['rps_20']} "
                  f"类型={row['double_strong']}")

    # ── 写入 stock_rs_daily（只保存用户要求的日期范围）──
    import sqlite3
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "lixinger.db")
    conn = sqlite3.connect(db_path)
    total = 0
    save_dates = [d for d in result["date"].unique().to_list() if str(d) >= (args.start or '1900-01-01')]
    for date_val in save_dates:
        day_data = result.filter(pl.col("date") == date_val)
        conn.execute("DELETE FROM stock_rs_daily WHERE date = ?", (str(date_val),))
        rows = []
        for row in day_data.iter_rows(named=True):
            rows.append((
                row['stock_code'], str(date_val),
                row['adj_close'], row['adj_close'],
                row['ret_20'], row['ret_60'], row['ret_120'], row['ret_250'],
                row['rps_20'], row['rps_60'], row['rps_120'], row['rps_250'],
                row['rs_line_norm'], row['amount'],
            ))
        conn.executemany(
            """INSERT INTO stock_rs_daily
            (stock_code, date, close, adj_close, ret_20, ret_60, ret_120, ret_250,
             rps_20, rps_60, rps_120, rps_250, rs_line, amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows
        )
        total += len(rows)
    conn.commit()
    conn.close()
    print(f"已写入 {total} 条到 stock_rs_daily（{result['date'].n_unique()} 个日期）")
