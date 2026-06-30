#!/usr/bin/env python3
"""
大盘健康度计算引擎

7 个辅助指标每日计算，结果存入 market_health_daily。
由 daily_update.py 调用：python src/scanners/market_health.py --date 2026-05-12

依赖：daily_kline, stock_margin, index_daily_kline（均已通过每日更新拉取）
"""

import sys
import os
import json
import argparse
import sqlite3
from datetime import datetime, date as dt_date, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, "src"))
os.chdir(PROJECT_DIR)

try:
    import polars as pl
    HAS_POLARS = True
except ImportError:
    HAS_POLARS = False

from scripts.common import log as logger


# ═══════════════════════════════════════════════
# 数据库
# ═══════════════════════════════════════════════

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ═══════════════════════════════════════════════
# 建表
# ═══════════════════════════════════════════════

def ensure_tables():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS market_health_daily (
            date              TEXT PRIMARY KEY,
            total_score       REAL,
            rating            TEXT,
            ma50_above_value  REAL,
            ma50_above_score  INTEGER,
            hl_ratio_value    REAL,
            hl_ratio_score    INTEGER,
            ad_ratio_value    REAL,
            ad_ratio_score    INTEGER,
            vol_breakout_value REAL,
            vol_breakout_score INTEGER,
            margin_5d_value   REAL,
            margin_5d_score   INTEGER,
            sector_rot_score  INTEGER,
            fear_greed_value  REAL,
            fear_greed_score  INTEGER,
            created_at        TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS market_rotation_daily (
            date        TEXT NOT NULL,
            pool        TEXT NOT NULL,
            method      TEXT NOT NULL,
            value       REAL,
            top5_current TEXT,
            top5_last    TEXT,
            overlap_count INTEGER,
            PRIMARY KEY (date, pool)
        );

        CREATE TABLE IF NOT EXISTS market_breakout_daily (
            date        TEXT NOT NULL,
            stock_code  TEXT NOT NULL,
            close       REAL,
            change_pct  REAL,
            volume      REAL,
            amount      REAL,
            PRIMARY KEY (date, stock_code)
        );
    """)
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════
# 评分函数（100分制，6档通用）
# ═══════════════════════════════════════════════

def _tier6(val, thresholds, scores):
    for i, t in enumerate(thresholds):
        if val >= t:
            return scores[i]
    return scores[-1]


# ═══════════════════════════════════════════════
# 指标 1: 涨跌家数比
# ═══════════════════════════════════════════════

def compute_ad_ratio(conn, target_date):
    """上涨家数 / 下跌家数，5日均值"""
    rows = conn.execute("""
        SELECT date,
               SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) as up,
               SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) as down
        FROM (
            SELECT date, close,
                   LAG(close) OVER (PARTITION BY stock_code ORDER BY date) as prev_close
            FROM daily_kline
            WHERE date >= date(?, '-10 days') AND date <= ?
        )
        WHERE prev_close IS NOT NULL
        GROUP BY date ORDER BY date DESC LIMIT 5
    """, (target_date, target_date)).fetchall()

    values = []
    for r in rows:
        if r['down'] and r['down'] > 0:
            values.append(r['up'] / r['down'])
        else:
            values.append(10.0)
    avg = sum(values) / len(values) if values else 0
    return round(avg, 2)


def score_ad_ratio(val): return _tier6(val, [2.0, 1.5, 1.0, 0.6, 0.3], [15, 12, 9, 6, 3, 0])


# ═══════════════════════════════════════════════
# 指标 2: 新高新低比
# ═══════════════════════════════════════════════

def compute_hl_ratio(conn, target_date):
    """52周新高数 / 52周新低数，5日均值"""
    # 过去大约 300 个交易日以覆盖 252 日窗口
    rows = conn.execute("""
        SELECT date,
               SUM(CASE WHEN high >= max_252_high THEN 1 ELSE 0 END) as new_high,
               SUM(CASE WHEN low <= min_252_low THEN 1 ELSE 0 END) as new_low
        FROM (
            SELECT date, stock_code, high, low,
                   MAX(high) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 252 PRECEDING AND 1 PRECEDING) as max_252_high,
                   MIN(low)  OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 252 PRECEDING AND 1 PRECEDING) as min_252_low
            FROM daily_kline
            WHERE date >= date(?, '-400 days') AND date <= ?
        )
        WHERE max_252_high IS NOT NULL
        GROUP BY date ORDER BY date DESC LIMIT 5
    """, (target_date, target_date)).fetchall()

    values = []
    for r in rows:
        nl = r['new_low'] or 1
        if nl > 0:
            values.append(r['new_high'] / nl)
        else:
            values.append(10.0)
    avg = sum(values) / len(values) if values else 0
    return round(avg, 2)


def score_hl_ratio(val): return _tier6(val, [2.0, 1.5, 1.0, 0.5, 0.2], [15, 12, 9, 6, 3, 0])


# ═══════════════════════════════════════════════
# 指标 3: MA50上方占比
# ═══════════════════════════════════════════════

def compute_ma50_above(conn, target_date):
    """收盘价 > MA50 的个股占比"""
    rows = conn.execute("""
        SELECT date,
               AVG(CASE WHEN close > ma50 THEN 1.0 ELSE 0.0 END) * 100 as pct
        FROM (
            SELECT date, stock_code, close,
                   AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as ma50
            FROM daily_kline
            WHERE date >= date(?, '-100 days') AND date <= ?
        )
        WHERE ma50 IS NOT NULL
        GROUP BY date ORDER BY date DESC LIMIT 5
    """, (target_date, target_date)).fetchall()

    values = [r['pct'] for r in rows if r['pct'] is not None]
    avg = sum(values) / len(values) if values else 0
    return round(avg, 1)


def score_ma50_above(val): return _tier6(val, [70, 60, 50, 40, 30], [15, 12, 9, 6, 3, 0])


# ═══════════════════════════════════════════════
# 指标 4: 放量突破数
# ═══════════════════════════════════════════════

def compute_vol_breakout(conn, target_date):
    """放量突破个股数，与过去20日均值比较。返回 (count, avg_20, stock_list)"""
    rows = conn.execute("""
        SELECT date, COUNT(*) as cnt
        FROM (
            SELECT date, stock_code, close, volume,
                   AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as vol_ma50,
                   MAX(close) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) as max_20_close
            FROM daily_kline
            WHERE date >= date(?, '-120 days') AND date <= ?
        )
        WHERE vol_ma50 > 0 AND max_20_close IS NOT NULL
          AND close > max_20_close
          AND volume > vol_ma50 * 1.5
        GROUP BY date ORDER BY date DESC LIMIT 21
    """, (target_date, target_date)).fetchall()

    if len(rows) < 2:
        return 0, 0, []
    today_cnt = rows[0]['cnt']
    past_20 = [r['cnt'] for r in rows[1:21]]
    avg_20 = sum(past_20) / len(past_20) if past_20 else 0

    # 取当日具体股票列表
    stock_rows = conn.execute("""
        SELECT stock_code, close, change_pct, volume, amount
        FROM (
            SELECT date, stock_code, close, change_pct, volume, amount,
                   AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as vol_ma50,
                   MAX(close) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) as max_20_close
            FROM daily_kline
            WHERE date >= date(?, '-100 days') AND date <= ?
        )
        WHERE date = ?
          AND vol_ma50 > 0 AND max_20_close IS NOT NULL
          AND close > max_20_close
          AND volume > vol_ma50 * 1.5
        ORDER BY volume DESC
    """, (target_date, target_date, target_date)).fetchall()

    stock_list = [{
        'stock_code': r['stock_code'],
        'close': r['close'],
        'change_pct': r['change_pct'],
        'volume': r['volume'],
        'amount': r['amount'],
    } for r in stock_rows]

    return today_cnt, avg_20, stock_list


def score_vol_breakout(today_cnt, avg_20):
    if avg_20 <= 0: return 0
    return _tier6(today_cnt / avg_20, [1.5, 1.2, 1.0, 0.8, 0.5], [15, 12, 9, 6, 3, 0])  # simplified: 4/2/0 tiers need more granularity but keep simple


# ═══════════════════════════════════════════════
# 指标 5: 融资余额5日变化
# ═══════════════════════════════════════════════

def compute_margin_5d(conn, target_date):
    """全市场融资余额 5 日累计变化率"""
    rows = conn.execute("""
        SELECT date, SUM(mtaslb_fb) as total
        FROM stock_margin
        WHERE date >= date(?, '-10 days') AND date <= ?
        GROUP BY date ORDER BY date DESC LIMIT 6
    """, (target_date, target_date)).fetchall()

    if len(rows) < 2:
        return 0
    latest = rows[0]['total'] or 0
    five_days_ago_idx = min(5, len(rows) - 1)
    prev = rows[five_days_ago_idx]['total'] or 0
    if prev <= 0:
        return 0
    return round((latest - prev) / prev * 100, 2)


def score_margin_5d(val): return _tier6(val, [1.5, 1.0, 0.5, -0.5, -1.0], [15, 12, 9, 6, 3, 0])


# ═══════════════════════════════════════════════
# 指标 6: 板块轮动
# ═══════════════════════════════════════════════

def load_index_pool(conn, pool_name):
    """从 index_style.yaml 解析行业指数池"""
    import yaml
    config_path = os.path.join(PROJECT_DIR, "config", "index_style.yaml")
    with open(config_path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    indices = cfg.get("categories", {}).get(pool_name, [])
    return [(item["code"], item["name"]) for item in indices]


def compute_5d_return(conn, index_code, target_date):
    """单个指数 5 日收益率"""
    rows = conn.execute("""
        SELECT close FROM index_daily_kline
        WHERE stock_code = ? AND kline_type = 'normal'
          AND date <= ? ORDER BY date DESC LIMIT 6
    """, (index_code, target_date)).fetchall()

    if len(rows) < 2:
        return None
    today = rows[0]['close']
    # 取最近第 6 行（约 5 个交易日前）或第 5 行
    idx_5d = min(5, len(rows) - 1)
    prev = rows[idx_5d]['close']
    if prev and prev != 0:
        return (today - prev) / prev * 100
    return None


def compute_sector_rotation(conn, target_date):
    """计算 4 个池的板块轮动指标，返回 (L1+L2均分, rotation_details)"""
    pools = {
        "sector_l1": {"name": "一级行业", "icon": "🏭", "participates": True},
        "sector_l2": {"name": "二级行业", "icon": "🔧", "participates": True},
        "theme":     {"name": "主题指数", "icon": "🎯", "participates": False},
        "strategy":  {"name": "策略指数", "icon": "🧩", "participates": False},
    }

    # 上周同期（约 5 个交易日）
    last_week_rows = conn.execute("""
        SELECT DISTINCT date FROM index_daily_kline
        WHERE kline_type = 'normal' AND date <= ?
        ORDER BY date DESC LIMIT 10
    """, (target_date,)).fetchall()
    last_week_date = last_week_rows[5]['date'] if len(last_week_rows) > 5 else target_date

    rotation_details = []
    l1_score = 0
    l2_score = 0

    for pool_key, meta in pools.items():
        try:
            indices = load_index_pool(conn, pool_key)
        except Exception:
            continue

        if not indices:
            continue

        n = len(indices)
        # 当前排名
        current_returns = []
        for code, name in indices:
            ret = compute_5d_return(conn, code, target_date)
            if ret is not None:
                current_returns.append((name, ret))
        current_returns.sort(key=lambda x: x[1], reverse=True)

        # 上周排名
        last_returns = []
        for code, name in indices:
            ret = compute_5d_return(conn, code, last_week_date)
            if ret is not None:
                last_returns.append((name, ret))
        last_returns.sort(key=lambda x: x[1], reverse=True)

        if n <= 50:  # 小池用 Top 5 重叠率
            top5_curr = set(name for name, _ in current_returns[:5])
            top5_last = set(name for name, _ in last_returns[:5])
            overlap = len(top5_curr & top5_last)
            method = "overlap"

            if meta["participates"]:
                score = _tier6(overlap, [4, 3, 2, 1, 0.1], [15, 12, 9, 6, 3, 0])
                if pool_key == "sector_l1":
                    l1_score = score
                else:
                    l2_score = score

            rotation_details.append({
                "name": meta["name"],
                "icon": meta["icon"],
                "count": n,
                "method": method,
                "value": overlap,
                "participates": meta["participates"],
                "top5_current": [name for name, _ in current_returns[:5]],
                "top5_last": [name for name, _ in last_returns[:5]],
                "top5_overlap": list(top5_curr & top5_last),
            })
        else:  # 大池用 Spearman 秩相关系数
            rankings_curr = {name: i for i, (name, _) in enumerate(current_returns)}
            rankings_last = {name: i for i, (name, _) in enumerate(last_returns)}
            common_names = set(rankings_curr.keys()) & set(rankings_last.keys())
            if len(common_names) < 2:
                rotation_details.append({
                    "name": meta["name"], "icon": meta["icon"], "count": n,
                    "method": "spearman", "value": 0, "participates": False,
                    "top5_current": [], "top5_last": [], "top5_overlap": [],
                })
                continue

            n_common = len(common_names)
            d_sq_sum = sum((rankings_curr[name] - rankings_last[name]) ** 2 for name in common_names)
            rho = 1 - (6 * d_sq_sum) / (n_common * (n_common ** 2 - 1))

            rotation_details.append({
                "name": meta["name"],
                "icon": meta["icon"],
                "count": n,
                "method": "spearman",
                "value": round(rho, 3),
                "participates": False,
                "top5_current": [],
                "top5_last": [],
                "top5_overlap": [],
            })

    sector_score = round((l1_score + l2_score) / 2) if (l1_score + l2_score) > 0 else 0
    return sector_score, rotation_details


# ═══════════════════════════════════════════════
# 指标 7: 恐慌/贪婪指数
# ═══════════════════════════════════════════════

def compute_fear_greed(conn, target_date, ma50_pct=None):
    """基于中证全指(000985)的综合恐慌指数"""
    # 子指标 1: ATR(20) / close 的 252 日百分位
    rows = conn.execute("""
        SELECT close,
               (high - low) / close as daily_range
        FROM index_daily_kline
        WHERE stock_code = '000985' AND kline_type = 'normal'
          AND date <= ? ORDER BY date DESC LIMIT 300
    """, (target_date,)).fetchall()

    if len(rows) < 30:
        return 50, 0

    # ATR(20)
    ranges = [r['daily_range'] for r in rows[:20] if r['daily_range'] is not None]
    atr = sum(ranges) / len(ranges) if ranges else 0
    close = rows[0]['close']
    vol_pct = (atr / close * 100) if close else 0

    # 252 日历史百分位
    all_ranges = [r['daily_range'] / r['close'] * 100 for r in rows if r['daily_range'] is not None and r['close']]
    all_ranges.sort()
    vol_rank = sum(1 for v in all_ranges if v <= vol_pct) / len(all_ranges) * 100 if all_ranges else 50

    # 子指标 2: 1 - MA50上方占比（已算好，越大越恐慌）
    width_pct = 100 - (ma50_pct or 50)  # 100 - MA50上方占比

    # 子指标 3: 5 日涨跌幅的倒数
    if len(rows) >= 6:
        ret_5d = (rows[0]['close'] - rows[5]['close']) / rows[5]['close'] * 100 if rows[5]['close'] else 0
    else:
        ret_5d = 0
    momentum_pct = max(0, -ret_5d) / 10 * 100  # 跌幅越大越恐慌

    # 综合
    composite = (vol_rank + width_pct + momentum_pct) / 3
    return round(composite, 1), score_fear_greed(composite)


def score_fear_greed(val): return _tier6(val, [80, 60, 40, 20], [0, 4, 6, 8, 10])


# ═══════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════

def compute_all(target_date):
    conn = get_db()
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"🐺 大盘健康度计算 — {target_date}")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # 1
    ad = compute_ad_ratio(conn, target_date)
    ad_s = score_ad_ratio(ad)
    logger.info(f"  涨跌家数比: {ad} → {ad_s}分")

    # 2
    hl = compute_hl_ratio(conn, target_date)
    hl_s = score_hl_ratio(hl)
    logger.info(f"  新高新低比: {hl} → {hl_s}分")

    # 3 (先算，给恐慌指数用)
    ma50 = compute_ma50_above(conn, target_date)
    ma50_s = score_ma50_above(ma50)
    logger.info(f"  MA50上方占比: {ma50}% → {ma50_s}分")

    # 4
    vb, vb_avg20, vb_stocks = compute_vol_breakout(conn, target_date)
    vb_s = score_vol_breakout(vb, vb_avg20)
    logger.info(f"  放量突破数: {vb}只(均{vb_avg20:.0f}) → {vb_s}分")

    # 写入突破个股明细
    conn.execute("DELETE FROM market_breakout_daily WHERE date = ?", (target_date,))
    for s in vb_stocks:
        conn.execute(
            "INSERT INTO market_breakout_daily (date, stock_code, close, change_pct, volume, amount) VALUES (?, ?, ?, ?, ?, ?)",
            (target_date, s['stock_code'], s['close'], s['change_pct'], s['volume'], s['amount'])
        )

    # 5
    mg = compute_margin_5d(conn, target_date)
    mg_s = score_margin_5d(mg)
    logger.info(f"  融资余额5日变化: {mg}% → {mg_s}分")

    # 6
    sector_score, rotation_details = compute_sector_rotation(conn, target_date)
    logger.info(f"  板块轮动: {sector_score}分")

    # 7
    fg, fg_s = compute_fear_greed(conn, target_date, ma50)
    logger.info(f"  恐慌/贪婪: {fg}% → {fg_s}分")

    # 总分 & 评级（100分制）
    total = ad_s + hl_s + ma50_s + vb_s + mg_s + sector_score + fg_s
    if total >= 80: rating = "A"
    elif total >= 65: rating = "B"
    elif total >= 50: rating = "C"
    elif total >= 35: rating = "D"
    elif total >= 20: rating = "E"
    else: rating = "F"

    logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"  总分: {total}/100  评级: {rating}")
    logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # 写入
    conn.execute("""
        INSERT OR REPLACE INTO market_health_daily
        (date, total_score, rating,
         ma50_above_value, ma50_above_score,
         hl_ratio_value, hl_ratio_score,
         ad_ratio_value, ad_ratio_score,
         vol_breakout_value, vol_breakout_score,
         margin_5d_value, margin_5d_score,
         sector_rot_score,
         fear_greed_value, fear_greed_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (target_date, total, rating,
          ma50, ma50_s, hl, hl_s, ad, ad_s, vb, vb_s, mg, mg_s,
          sector_score, fg, fg_s))
    conn.commit()

    # 写入轮动明细
    for rd in rotation_details:
        top5_c = json.dumps(rd.get("top5_current", []), ensure_ascii=False)
        top5_l = json.dumps(rd.get("top5_last", []), ensure_ascii=False)
        ov = len(rd.get("top5_overlap", [])) if rd["method"] == "overlap" else 0
        conn.execute("""
            INSERT OR REPLACE INTO market_rotation_daily
            (date, pool, method, value, top5_current, top5_last, overlap_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (target_date, rd["name"], rd["method"], rd["value"], top5_c, top5_l, ov))
    conn.commit()
    conn.close()

    return total, rating


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="大盘健康度计算")
    parser.add_argument("--date", type=str, default=None, help="目标日期 YYYY-MM-DD")
    args = parser.parse_args()

    target = args.date or dt_date.today().strftime("%Y-%m-%d")
    ensure_tables()
    compute_all(target)
