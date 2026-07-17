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
            ad_ratio_today    REAL,
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
            vol_ma50    REAL,
            amt_ma50    REAL,
            vol_ratio   REAL,
            break_ma    TEXT,
            PRIMARY KEY (date, stock_code)
        );

        CREATE TABLE IF NOT EXISTS market_health_sector_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            group_name TEXT NOT NULL,
            group_label TEXT NOT NULL,
            indices_count INTEGER,
            stocks_count INTEGER,
            total_score INTEGER,
            rating TEXT,
            position INTEGER,
            ma50_above_score INTEGER,
            ma50_above_value REAL,
            hl_ratio_score INTEGER,
            hl_ratio_value REAL,
            ad_ratio_score INTEGER,
            ad_ratio_value REAL,
            vol_breakout_score INTEGER,
            vol_breakout_value INTEGER,
            margin_5d_score INTEGER,
            sector_rot_score INTEGER,
            fear_greed_score INTEGER,
            score_vs_market INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(date, group_name)
        );
    """)
    # 迁移已有表：加新列
    for tbl_col in [
        ("market_health_daily", "ad_ratio_today REAL"),
        ("market_breakout_daily", "vol_ma50 REAL"),
        ("market_breakout_daily", "vol_ratio REAL"),
        ("market_breakout_daily", "break_ma TEXT"),
        ("market_breakout_daily", "amt_ma50 REAL"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {tbl_col[0]} ADD COLUMN {tbl_col[1]}")
        except sqlite3.OperationalError:
            pass  # column already exists
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
    """上涨家数 / 下跌家数，返回 (5日均值, 当日单日比)"""
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
    today_val = values[0] if values else 0
    return round(avg, 2), round(today_val, 2)


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

    # 取当日具体股票列表（含各均线值）
    stock_rows = conn.execute("""
        SELECT stock_code, close, change_pct, volume, amount, vol_ma50, amt_ma50,
               ma5, ma10, ma20, ma50, ma120, ma200
        FROM (
            SELECT date, stock_code, close, change_pct, volume, amount,
                   AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as vol_ma50,
                   AVG(amount) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as amt_ma50,
                   MAX(close) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) as max_20_close,
                   MAX(close) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) as max_20_close,
                   AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) as ma5,
                   AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) as ma10,
                   AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as ma20,
                   AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as ma50,
                   AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW) as ma120,
                   AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) as ma200
            FROM daily_kline
            WHERE date >= date(?, '-250 days') AND date <= ?
        )
        WHERE date = ?
          AND vol_ma50 > 0 AND max_20_close IS NOT NULL
          AND close > max_20_close
          AND volume > vol_ma50 * 1.5
        ORDER BY volume DESC
    """, (target_date, target_date, target_date)).fetchall()

    stock_list = []
    for r in stock_rows:
        close = r['close']
        # 成交量倍数（基于金额）
        vol_ratio = round(r['amount'] / r['amt_ma50'], 2) if r['amt_ma50'] and r['amt_ma50'] > 0 else 0
        # 突破均线：找到被突破的最高级别均线（MA200优先）
        break_ma = ''
        for ma_level in [('MA200', r['ma200']), ('MA120', r['ma120']), ('MA50', r['ma50']),
                          ('MA20', r['ma20']), ('MA10', r['ma10']), ('MA5', r['ma5'])]:
            if ma_level[1] is not None and close > ma_level[1]:
                break_ma = ma_level[0]
                break
        stock_list.append({
            'stock_code': r['stock_code'],
            'close': close,
            'change_pct': r['change_pct'],
            'volume': r['volume'],
            'amount': r['amount'],
            'vol_ma50': r['vol_ma50'],
            'amt_ma50': r['amt_ma50'],
            'vol_ratio': vol_ratio,
            'break_ma': break_ma,
        })

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

    # 综合：三个子指标越高=越恐慌，取反得贪婪指数（0=极度恐慌 / 100=极度贪婪）
    fear_raw = (vol_rank + width_pct + momentum_pct) / 3
    composite = round(100 - fear_raw, 1)
    return composite, score_fear_greed(composite)


def score_fear_greed(val): return _tier6(val, [80, 60, 40, 20], [10, 8, 6, 4, 0])


# ═══════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════

def compute_all(target_date):
    conn = get_db()
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"🐺 大盘健康度计算 — {target_date}")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # 1
    ad, ad_today = compute_ad_ratio(conn, target_date)
    ad_s = score_ad_ratio(ad)
    logger.info(f"  涨跌家数比: {ad}(5日)/{ad_today}(当日) → {ad_s}分")

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
            "INSERT INTO market_breakout_daily (date, stock_code, close, change_pct, volume, amount, vol_ma50, amt_ma50, vol_ratio, break_ma) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (target_date, s['stock_code'], s['close'], s['change_pct'], s['volume'], s['amount'], s.get('vol_ma50', 0), s.get('amt_ma50', 0), s.get('vol_ratio', 0), s.get('break_ma', ''))
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
         ad_ratio_value, ad_ratio_today, ad_ratio_score,
         vol_breakout_value, vol_breakout_score,
         margin_5d_value, margin_5d_score,
         sector_rot_score,
         fear_greed_value, fear_greed_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (target_date, total, rating,
          ma50, ma50_s, hl, hl_s, ad, ad_today, ad_s, vb, vb_s, mg, mg_s,
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


# ═══════════════════════════════════════════════
# 行业分组健康分（v3.0）
# ═══════════════════════════════════════════════

def _get_sector_index_rs(conn, target_date):
    """读取当日所有 L2+主题指数的 RS_60"""
    rows = conn.execute("""
        SELECT i.stock_code, i.name, r.rs_60
        FROM index_daily_kline i
        JOIN index_rs_daily r ON i.stock_code = r.stock_code AND r.date = i.date
        WHERE i.date = ? AND (
            i.stock_code IN (SELECT code FROM index_style WHERE category = 'sector_l2')
            OR i.stock_code IN (SELECT code FROM index_style WHERE category = 'thematic')
        )
    """, (target_date,)).fetchall()
    
    # 按 RS_60 分组
    groups = {'strong': [], 'mid': [], 'weak': []}
    for r in rows:
        rs = r['rs_60']
        if rs >= 75:
            groups['strong'].append(r['stock_code'])
        elif rs >= 30:
            groups['mid'].append(r['stock_code'])
        else:
            groups['weak'].append(r['stock_code'])
    
    return groups


def _get_constituent_weights(conn, index_codes):
    """从 index_constituents 获取成分股及权重
    权重 = 该股票在 index_codes 中出现的次数
    """
    if not index_codes:
        return {}, 0
    placeholders = ','.join('?' * len(index_codes))
    rows = conn.execute(f"""
        SELECT stock_code, COUNT(*) as cnt
        FROM index_constituents
        WHERE index_code IN ({placeholders})
          AND date = (SELECT MAX(date) FROM index_constituents)
        GROUP BY stock_code
    """, index_codes).fetchall()
    
    weights = {r['stock_code']: r['cnt'] for r in rows}
    total_stocks = len(weights)
    return weights, total_stocks


def _compute_sector_ma50_above(conn, target_date, stock_weights):
    """带权重的 MA50 上方占比（批量预取+Python算）"""
    if not stock_weights:
        return 0, 0
    codes = list(stock_weights.keys())
    placeholders = ','.join('?' * len(codes))
    
    rows = conn.execute(f"""
        SELECT stock_code, date, close
        FROM daily_kline
        WHERE stock_code IN ({placeholders})
          AND date >= date(?, '-60 days')
          AND date <= ?
        ORDER BY stock_code, date
    """, (*codes, target_date, target_date)).fetchall()
    
    # 按股票分组，取最近60天收盘价
    stock_closes = {}
    for r in rows:
        sc = r['stock_code']
        if sc not in stock_closes:
            stock_closes[sc] = []
        stock_closes[sc].append(r['close'])
    
    above_sum = 0
    total_weight = 0
    for sc, closes in stock_closes.items():
        w = stock_weights.get(sc, 0)
        total_weight += w
        if len(closes) < 2:
            continue
        today_close = closes[-1]
        if len(closes) >= 51:
            sma50 = sum(closes[-51:-1]) / 50
        else:
            sma50 = sum(closes[:-1]) / (len(closes) - 1)
        if today_close > sma50:
            above_sum += w
    
    if total_weight <= 0:
        return 0, 0
    pct = round(above_sum / total_weight * 100, 1)
    return pct, None


def _compute_sector_ad_ratio(conn, target_date, stock_weights):
    """带权重的涨跌家数比（5日均值）"""
    if not stock_weights:
        return 0
    codes = list(stock_weights.keys())
    placeholders = ','.join('?' * len(codes))
    
    rows = conn.execute(f"""
        SELECT date,
               SUM(CASE WHEN close > prev_close THEN weight ELSE 0 END) as up_w,
               SUM(CASE WHEN close < prev_close THEN weight ELSE 0 END) as down_w
        FROM (
            SELECT k.date, k.close, sw.weight,
                   LAG(k.close) OVER (PARTITION BY k.stock_code ORDER BY k.date) as prev_close
            FROM daily_kline k
            JOIN (SELECT val as code, {len(codes)} as dummy FROM (SELECT 1))
            LEFT JOIN (VALUES {','.join('('+str(i)+','+str(w)+')' for i,w in enumerate(stock_weights.values()))}) 
        )
        WHERE prev_close IS NOT NULL
        GROUP BY date ORDER BY date DESC LIMIT 5
    """, (target_date,))
    
    # 简化实现：用 Python 做
    # 拿5日数据，每日期望股票列表一致
    return _compute_sector_ad_ratio_simple(conn, target_date, stock_weights)


def _compute_sector_ad_ratio_simple(conn, target_date, stock_weights):
    """简化版加权 AD 比：用 Python 处理"""
    if not stock_weights:
        return 0
    codes = list(stock_weights.keys())
    placeholders = ','.join('?' * len(codes))
    
    # 取5天数据
    rows = conn.execute(f"""
        SELECT k.date, k.stock_code, k.close,
               LAG(k.close) OVER (PARTITION BY k.stock_code ORDER BY k.date) as prev_close
        FROM daily_kline k
        WHERE k.stock_code IN ({placeholders})
          AND k.date >= date(?, '-10 days')
          AND k.date <= ?
        ORDER BY k.date
    """, (*codes, target_date, target_date)).fetchall()
    
    # 按日期分组
    daily = {}
    for r in rows:
        d = r['date']
        if d not in daily:
            daily[d] = {'up_w': 0, 'down_w': 0}
        w = stock_weights.get(r['stock_code'], 0)
        if r['prev_close'] is not None:
            if r['close'] > r['prev_close']:
                daily[d]['up_w'] += w
            elif r['close'] < r['prev_close']:
                daily[d]['down_w'] += w
    
    # 取最近5天
    dates = sorted(daily.keys(), reverse=True)[:5]
    values = []
    for d in dates:
        dw = daily[d]['down_w']
        if dw > 0:
            values.append(daily[d]['up_w'] / dw)
        else:
            values.append(10.0)
    avg = sum(values) / len(values) if values else 0
    return round(avg, 2)


def _compute_sector_hl_ratio(conn, target_date, stock_weights):
    """带权重的新高新低比，5日均值（批量预取+Python算）"""
    if not stock_weights:
        return 0
    codes = list(stock_weights.keys())
    placeholders = ','.join('?' * len(codes))
    
    # 一次性拉取所有需要的K线（最近300天），在Python里算252日高低
    rows = conn.execute(f"""
        SELECT stock_code, date, close
        FROM daily_kline
        WHERE stock_code IN ({placeholders})
          AND date >= date(?, '-300 days')
          AND date <= ?
        ORDER BY stock_code, date
    """, (*codes, target_date, target_date)).fetchall()
    
    # 按股票分组
    stock_data = {}
    for r in rows:
        sc = r['stock_code']
        if sc not in stock_data:
            stock_data[sc] = []
        stock_data[sc].append({'date': r['date'], 'close': r['close']})
    
    # 获取最近5个交易日（从数据中拿）
    all_dates = sorted(set(r['date'] for r in rows), reverse=True)
    recent_5 = set(all_dates[:5])
    
    daily_records = {d: {'hw': 0.0, 'lw': 0.0} for d in recent_5}
    
    for sc, records in stock_data.items():
        w = float(stock_weights.get(sc, 0))
        if w <= 0:
            continue
        closes = [r['close'] for r in records]
        dates = [r['date'] for r in records]
        n = len(closes)
        # 滑动窗口算252日最高最低
        for i in range(n):
            d = dates[i]
            if d not in recent_5:
                continue
            c = closes[i]
            start = max(0, i-252)
            window = closes[start:i]
            if not window:
                continue
            if c >= max(window):
                daily_records[d]['hw'] += w
            if c <= min(window):
                daily_records[d]['lw'] += w
    
    dates = sorted(daily_records.keys(), reverse=True)[:5]
    values = []
    for d in dates:
        lw = daily_records[d]['lw']
        if lw > 0:
            values.append(daily_records[d]['hw'] / lw)
        elif daily_records[d]['hw'] > 0:
            values.append(10.0)
        else:
            values.append(0)
    avg = sum(values) / len(values) if values else 0
    return round(avg, 2)


def _compute_sector_vol_breakout(conn, target_date, stock_weights):
    """带权重的放量突破数（高效版：SQL聚合）"""
    if not stock_weights:
        return 0, 0
    codes = list(stock_weights.keys())
    placeholders = ','.join('?' * len(codes))
    
    # 当天数据 + 50日均量
    from datetime import datetime, timedelta
    day50_dt = datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=60)
    day50 = day50_dt.strftime('%Y-%m-%d')
    rows = conn.execute(f"""
        SELECT k.stock_code, k.close, k.volume,
               (SELECT AVG(sub.volume) FROM daily_kline sub
                WHERE sub.stock_code=k.stock_code AND sub.date<k.date AND sub.date>=? ) as vol_ma50,
               (SELECT MAX(sub.close) FROM daily_kline sub
                WHERE sub.stock_code=k.stock_code AND sub.date<k.date AND sub.date>=date(k.date, '-20 days')) as max_20
        FROM daily_kline k
        WHERE k.date = ? AND k.stock_code IN ({placeholders})
          AND k.close > 0 AND k.volume > 0
    """, (day50, target_date, *codes)).fetchall()
    
    breakout_sum = 0
    for r in rows:
        w = stock_weights.get(r['stock_code'], 0)
        if r['vol_ma50'] and r['volume'] > r['vol_ma50'] * 1.5 \
           and r['max_20'] and r['close'] > r['max_20']:
            breakout_sum += w
    
    return breakout_sum, None


def compute_sector_health_groups(target_date):
    """计算行业分组健康分，写入 market_health_sector_daily"""
    conn = get_db()
    
    # 如果目标日期没有 RS 数据，自动回退到最近有 RS 数据的日期
    has_rs = conn.execute("SELECT COUNT(*) FROM index_rs_daily WHERE date=?", (target_date,)).fetchone()[0]
    if not has_rs:
        fallback = conn.execute("SELECT MAX(date) FROM index_rs_daily").fetchone()[0]
        if fallback:
            logger.warning(f"  ⚠️ {target_date} 无RS数据，回退到 {fallback}")
            target_date = fallback
    
    logger.info(f"📊 行业分组健康分 — {target_date}")
    
    # 先算全市场的共享值（融资余额、板块轮动、恐慌指数）
    # 复用 compute_all 已算的结果
    row = conn.execute("SELECT * FROM market_health_daily WHERE date=?", (target_date,)).fetchone()
    if not row:
        logger.warning(f"  ⚠️ 全市场健康分未计算，先执行 compute_all({target_date})")
        conn.close()
        return
    
    shared_margin = row['margin_5d_score']
    shared_rot = row['sector_rot_score']
    shared_fear = row['fear_greed_score']
    total_market_score = row['total_score']
    
    # 加载 index_style.yaml 类别映射
    # 因为代码里没有直接表，用硬编码的方式查 index_rs_daily 
    # 直接从 index_rs_daily + index_daily_kline 查
    
    # 分池：sector_l2 和 thematic
    # 从 index_rs_daily 查所有指数的 RS
    all_index_rs = conn.execute(f"""
        SELECT r.stock_code, r.rs_60, i.close
        FROM index_rs_daily r
        JOIN index_daily_kline i ON r.stock_code = i.stock_code AND r.date = i.date
        WHERE r.date = ? AND i.date = ?
    """, (target_date, target_date)).fetchall()
    
    # 从 index_style.yaml 获取分类
    # 使用 Python 解析 yaml 获取分类
    import yaml
    yaml_path = os.path.join(PROJECT_DIR, "config", "index_style.yaml")
    with open(yaml_path, encoding='utf-8') as f:
        style = yaml.safe_load(f)
    
    l2_codes = {item['code'] for item in style['categories'].get('sector_l2', [])}
    theme_codes = {item['code'] for item in style['categories'].get('thematic', [])}
    
    # RS 索引
    rs_map = {r['stock_code']: r['rs_60'] for r in all_index_rs}
    
    # 对每个池分组
    pools = [
        ('l2', 'L2', l2_codes),
        ('theme', '主题', theme_codes),
    ]
    
    all_groups = []
    
    for pool_key, pool_label, pool_codes in pools:
        # 按 RS 分组
        strong_codes = [c for c in pool_codes if rs_map.get(c, 0) >= 75]
        mid_codes = [c for c in pool_codes if 30 <= rs_map.get(c, 0) < 75]
        weak_codes = [c for c in pool_codes if rs_map.get(c, 0) < 30]
        
        for suffix, label_suffix, codes in [
            ('strong', '强势组', strong_codes),
            ('mid', '中性组', mid_codes),
            ('weak', '弱势组', weak_codes),
        ]:
            group_name = f"{pool_key}_{suffix}"
            group_label = f"{pool_label}{label_suffix}"
            
            if not codes:
                all_groups.append((group_name, group_label, 0, 0, None, None, None, None, None, None, None, None, 0))
                continue
            
            # 成分股权重
            weights, stocks_cnt = _get_constituent_weights(conn, codes)
            if not weights:
                logger.info(f"  {group_label}: 成分股为空，跳过")
                all_groups.append((group_name, group_label, len(codes), 0, None, None, None, None, None, None, None, None, 0))
                continue
            
            # 计算各指标
            ma50_val, _ = _compute_sector_ma50_above(conn, target_date, weights)
            ma50_s = score_ma50_above(ma50_val)
            
            ad_val = _compute_sector_ad_ratio_simple(conn, target_date, weights)
            ad_s = score_ad_ratio(ad_val)
            
            hl_val = _compute_sector_hl_ratio(conn, target_date, weights)
            hl_s = score_hl_ratio(hl_val)
            
            vb_val, _ = _compute_sector_vol_breakout(conn, target_date, weights)
            vb_s = score_vol_breakout(vb_val, 1) if vb_val else 0
            
            # 总分（融资余额/板块轮动/恐慌指数用全市场值）
            total_s = ma50_s + ad_s + hl_s + vb_s + shared_margin + shared_rot + shared_fear
            
            # 评级
            if total_s >= 80: rating = "A"
            elif total_s >= 65: rating = "B"
            elif total_s >= 50: rating = "C"
            elif total_s >= 35: rating = "D"
            elif total_s >= 20: rating = "E"
            else: rating = "F"
            
            # 仓位建议
            if total_s >= 80: position = 70
            elif total_s >= 65: position = 50
            elif total_s >= 50: position = 35
            elif total_s >= 35: position = 20
            else: position = 0
            
            score_diff = total_s - total_market_score
            
            logger.info(f"  {group_label}: {len(codes)}指数/{stocks_cnt}只 → {total_s}分/{rating} 仓位{position}%")
            
            all_groups.append((group_name, group_label, len(codes), stocks_cnt,
                               total_s, rating, position, score_diff,
                               ma50_s, ma50_val, ad_s, ad_val,
                               hl_s, hl_val, vb_s, vb_val))
    
    # 写入数据库
    conn.execute("DELETE FROM market_health_sector_daily WHERE date = ?", (target_date,))
    for g in all_groups:
        gn, gl, icnt, scnt, ts, rt, pos, sdiff, *rest = g
        if ts is None:
            conn.execute("""
                INSERT OR REPLACE INTO market_health_sector_daily
                (date, group_name, group_label, indices_count, stocks_count,
                 total_score, rating, position, score_vs_market,
                 ma50_above_score, ma50_above_value, ad_ratio_score, ad_ratio_value,
                 hl_ratio_score, hl_ratio_value, vol_breakout_score, vol_breakout_value,
                 margin_5d_score, sector_rot_score, fear_greed_score)
                VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL,
                        NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                        ?, ?, ?)
            """, (target_date, gn, gl, icnt, scnt, shared_margin, shared_rot, shared_fear))
        else:
            conn.execute("""
                INSERT OR REPLACE INTO market_health_sector_daily
                (date, group_name, group_label, indices_count, stocks_count,
                 total_score, rating, position, score_vs_market,
                 ma50_above_score, ma50_above_value, ad_ratio_score, ad_ratio_value,
                 hl_ratio_score, hl_ratio_value, vol_breakout_score, vol_breakout_value,
                 margin_5d_score, sector_rot_score, fear_greed_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (target_date, gn, gl, icnt, scnt, ts, rt, pos, sdiff,
                   rest[0], rest[1], rest[2], rest[3],
                   rest[4], rest[5], rest[6], rest[7],
                   shared_margin, shared_rot, shared_fear))
    
    conn.commit()
    conn.close()
    logger.info(f"📊 行业分组健康分完成: {len(all_groups)} 组")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="大盘健康度计算")
    parser.add_argument("--date", type=str, default=None, help="目标日期 YYYY-MM-DD")
    parser.add_argument("--sector", action="store_true", help="仅计算行业分组健康分")
    args = parser.parse_args()

    target = args.date or dt_date.today().strftime("%Y-%m-%d")
    ensure_tables()
    if args.sector:
        compute_sector_health_groups(target)
    else:
        compute_all(target)
        compute_sector_health_groups(target)
