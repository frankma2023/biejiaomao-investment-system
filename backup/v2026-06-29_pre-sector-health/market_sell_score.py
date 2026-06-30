#!/usr/bin/env python3
"""
大盘卖出评分引擎

根据《大盘环境判断卖出评分卡》每日计算卖出信号评分。
总分越高，卖出建议越强。

使用方式:
  python src/scanners/market_sell_score.py --date 2026-05-30

信号列表（12项，满分 400）：
  抛盘日(4个)=10 / (5个)=20 / (≥6个)=40  — 高位 ×2
  追盘日失效 = 30
  龙头股见顶 = 25
  垃圾股补涨 = 25
  指数背离 = 20 / 领涨指数背离 = 40（上限 60）
  跌破50日线(5日未收复) = 30
  跌破200日线 = 50
  均线死叉(MA50下穿MA200) = 50
  AD比率 <0.7连续5天 = 20
  AD比率 <0.5 = 40
  NH/NL比率 <0.5连续3天 = 30
  上涨无量(连续5日涨但量<20日均量×80%) = 20

熔断规则（不依赖总分）：
  - 单信号触发"清仓级"：仓位上限 ≤ 25%
  - 任意两个"清仓级"信号叠加：强制清仓

结果写入 market_sell_score_daily 表。
"""

import sys
import os
import argparse
import sqlite3
from datetime import datetime, date as dt_date, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, "src"))
os.chdir(PROJECT_DIR)

import logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(message)s')

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_tables():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS market_sell_score_daily (
            date                    TEXT PRIMARY KEY,
            total_score             INTEGER,
            position_advice         TEXT,
            meltdown_triggered      INTEGER DEFAULT 0,
            dist_days_25d           INTEGER,
            dist_score              INTEGER,
            dist_high_zone          INTEGER DEFAULT 0,
            ftd_failed              INTEGER DEFAULT 0,
            ftd_score               INTEGER,
            leader_top_fallen       INTEGER DEFAULT 0,
            leader_score            INTEGER,
            junk_rally              INTEGER DEFAULT 0,
            junk_score              INTEGER,
            divergence_count        INTEGER DEFAULT 0,
            divergence_leader       INTEGER DEFAULT 0,
            divergence_score        INTEGER,
            ma50_broken             INTEGER DEFAULT 0,
            ma50_score              INTEGER,
            ma200_broken            INTEGER DEFAULT 0,
            ma200_score             INTEGER,
            death_cross             INTEGER DEFAULT 0,
            death_cross_score       INTEGER,
            ad_ratio_low5           INTEGER DEFAULT 0,
            ad_low5_score           INTEGER,
            ad_ratio_crash          INTEGER DEFAULT 0,
            ad_crash_score          INTEGER,
            hlnl_ratio_low          INTEGER DEFAULT 0,
            hlnl_score              INTEGER,
            vol_dry_up              INTEGER DEFAULT 0,
            vol_dry_score           INTEGER,
            signal_details          TEXT,
            cleared_signals         TEXT
        )
    """)
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════
# 1. 抛盘日
# ═══════════════════════════════════════════════

def score_distribution(conn, target_date):
    """计算抛盘日数量并评分。高位（距52周高点<5%）权重×2"""
    # 取最近 25 个交易日的 distribution_days_detail 数据
    start = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=40)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT date, is_distribution, distribution_type, index_code, close FROM distribution_days_detail
        WHERE index_code = '000985' AND date BETWEEN ? AND ?
        ORDER BY date DESC
    """, (start, target_date)).fetchall()

    if not rows:
        return 0, 0, 0, "无数据"

    # 取最近 25 个交易日的抛盘日
    dist_dates = sorted(set(r['date'] for r in rows))
    dist_25d = dist_dates[-min(25, len(dist_dates)):]
    dist_count = sum(1 for d in dist_25d if any(
        r['is_distribution'] and r['date'] == d for r in rows
    ))

    # 高位判断：距 52 周高点 < 5%
    high_zone = 0
    kline_rows = conn.execute("""
        SELECT close FROM index_daily_kline
        WHERE stock_code = '000985' AND date <= ?
        ORDER BY date DESC LIMIT 252
    """, (target_date,)).fetchall()

    if kline_rows:
        high_52w = max(r['close'] for r in kline_rows if r['close'])
        latest_close = kline_rows[0]['close'] if kline_rows[0]['close'] else 0
        if latest_close >= high_52w * 0.95:
            high_zone = 1

    # 评分
    if dist_count >= 6:
        base_score = 40
    elif dist_count >= 5:
        base_score = 20
    elif dist_count >= 4:
        base_score = 10
    else:
        base_score = 0

    final_score = base_score * 2 if high_zone else base_score

    return dist_count, final_score, high_zone, f"25日内{dist_count}个抛盘日{'（高位×2）' if high_zone else ''}"


# ═══════════════════════════════════════════════
# 2. 追盘日失效
# ═══════════════════════════════════════════════

def score_ftd_failure(conn, target_date):
    """检查追盘日是否失效"""
    ftd = conn.execute("""
        SELECT * FROM follow_through_days
        WHERE index_code = '000985' AND ftd_date <= ?
        AND ftd_date >= date(?, '-30 days')
        AND (invalidated_date IS NOT NULL OR is_valid = 1)
        ORDER BY ftd_date DESC LIMIT 1
    """, (target_date, target_date)).fetchone()

    if not ftd or not ftd['ftd_date']:
        return 0, 0, "无有效追盘日"

    # 已标记失效则直接返回
    if ftd['invalidated_date']:
        return 1, 30, f"追盘日({ftd['ftd_date']})已失效: {ftd['invalidated_reason'] or '—'}"

    # 检查随后 10 日是否出现 ≥3 个抛盘日 或跌破追盘日低点
    ftd_date = ftd['ftd_date']
    end_check = (datetime.strptime(ftd_date, "%Y-%m-%d") + timedelta(days=10)).strftime("%Y-%m-%d")

    dist_count = conn.execute("""
        SELECT COUNT(*) as cnt FROM distribution_days_detail
        WHERE index_code = '000985' AND date > ? AND date <= ? AND is_distribution = 1
    """, (ftd_date, min(end_check, target_date))).fetchone()['cnt']

    # 追盘日 K 线最低价
    ftd_kline = conn.execute("""
        SELECT low FROM index_daily_kline
        WHERE stock_code = '000985' AND date = ?
    """, (ftd_date,)).fetchone()

    breached = False
    if ftd_kline:
        post_rows = conn.execute("""
            SELECT MIN(close) as min_close FROM index_daily_kline
            WHERE stock_code = '000985' AND date > ? AND date <= ?
        """, (ftd_date, min(end_check, target_date))).fetchone()
        if post_rows['min_close'] and post_rows['min_close'] < ftd_kline['low']:
            breached = True

    if dist_count >= 3 or breached:
        return 1, 30, f"追盘日({ftd_date})失效: 抛盘日{int(dist_count)}个{' + 跌破追盘日低点' if breached else ''}"
    return 0, 0, f"追盘日({ftd_date})有效"


# ═══════════════════════════════════════════════
# 3. 龙头股见顶
# ═══════════════════════════════════════════════

def score_leader_top(conn, target_date):
    """RS≥99 的龙头股池中，≥50% 回落 >10%"""
    # 取最近的星期一作为定池日期
    td = datetime.strptime(target_date, "%Y-%m-%d")
    days_since_monday = td.weekday()
    pool_date = (td - timedelta(days=days_since_monday)).strftime("%Y-%m-%d")

    # 取 RS ≥99 的股票（取 pool_date 或之前最近的数据）
    rs_rows = conn.execute("""
        SELECT stock_code, rps_250 FROM stock_rs_daily
        WHERE date <= ? AND rps_250 >= 99
        ORDER BY date DESC
    """, (pool_date,)).fetchall()

    # 去重取每个 stock_code 最新值
    pool_stocks = {}
    for r in rs_rows:
        if r['stock_code'] not in pool_stocks:
            pool_stocks[r['stock_code']] = r['rps_250']

    if len(pool_stocks) < 5:
        return 0, 0, f"龙头股池仅{len(pool_stocks)}只，样本不足"

    # 取各股票入池时（pool_date）的收盘价和当前收盘价
    fallen_count = 0
    for code in pool_stocks:
        entry_price_row = conn.execute("""
            SELECT close FROM daily_kline
            WHERE stock_code = ? AND date <= ? ORDER BY date DESC LIMIT 1
        """, (code, pool_date)).fetchone()
        current_price_row = conn.execute("""
            SELECT close FROM daily_kline
            WHERE stock_code = ? AND date <= ? ORDER BY date DESC LIMIT 1
        """, (code, target_date)).fetchone()

        if entry_price_row and current_price_row:
            entry_p = entry_price_row['close']
            current_p = current_price_row['close']
            if entry_p > 0 and (current_p - entry_p) / entry_p <= -0.10:
                fallen_count += 1

    ratio = fallen_count / len(pool_stocks) if pool_stocks else 0
    if ratio >= 0.5:
        return 1, 25, f"龙头股池{len(pool_stocks)}只，{fallen_count}只回落>10% ({ratio:.0%})"
    return 0, 0, f"龙头股池{len(pool_stocks)}只，{fallen_count}只回落>10% ({ratio:.0%})"


# ═══════════════════════════════════════════════
# 4. 垃圾股补涨
# ═══════════════════════════════════════════════

def score_junk_rally(conn, target_date):
    """低价股后20%涨幅 > 高价股前20%涨幅 ×1.5，持续5天"""
    # 取最近 30 天数据
    start = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")

    # 取 target_date 当天全市场股价分位
    prices = conn.execute("""
        SELECT stock_code, close FROM daily_kline
        WHERE date = (SELECT MAX(date) FROM daily_kline WHERE date <= ?)
        AND close > 0
    """, (target_date,)).fetchall()

    if len(prices) < 100:
        return 0, 0, "样本不足"

    prices.sort(key=lambda r: r['close'])
    n = len(prices)
    low_cutoff = prices[int(n * 0.2)]['close']
    high_cutoff = prices[int(n * 0.8)]['close']

    low_codes = [r['stock_code'] for r in prices if r['close'] <= low_cutoff]
    high_codes = [r['stock_code'] for r in prices if r['close'] >= high_cutoff]

    # 检查过去 5 天是否每天都满足条件
    streak = 0
    for i in range(5):
        check_date = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d")

        # 低价股 20 日涨幅
        low_gains = []
        for code in low_codes[:50]:  # 采样50只
            row = conn.execute("""
                SELECT close FROM daily_kline
                WHERE stock_code = ? AND date <= ? ORDER BY date DESC LIMIT 21
            """, (code, check_date)).fetchall()
            if len(row) >= 21 and row[0]['close'] and row[-1]['close']:
                g = (row[0]['close'] - row[-1]['close']) / row[-1]['close'] * 100
                low_gains.append(g)

        # 高价股 20 日涨幅
        high_gains = []
        for code in high_codes[:50]:
            row = conn.execute("""
                SELECT close FROM daily_kline
                WHERE stock_code = ? AND date <= ? ORDER BY date DESC LIMIT 21
            """, (code, check_date)).fetchall()
            if len(row) >= 21 and row[0]['close'] and row[-1]['close']:
                g = (row[0]['close'] - row[-1]['close']) / row[-1]['close'] * 100
                high_gains.append(g)

        if low_gains and high_gains:
            low_avg = sum(low_gains) / len(low_gains)
            high_avg = sum(high_gains) / len(high_gains)
            if low_avg > high_avg * 1.5:
                streak += 1
            else:
                break

    if streak >= 5:
        return 1, 25, f"低价股涨幅持续5天远超高价股"
    return 0, 0, f"垃圾股补涨信号未触发(连续{streak}天)"


# ═══════════════════════════════════════════════
# 5. 指数与成分股背离
# ═══════════════════════════════════════════════

def score_divergence(conn, target_date):
    """5个指数 × 成分股涨跌比。领涨指数(rs_60最高) 权重×2"""
    INDEXES = ['000985', '000001', '399001', '399006', '000688']
    INDEX_NAMES = {'000985': '中证全指', '000001': '上证指数', '399001': '深证成指',
                   '399006': '创业板指', '000688': '科创50'}

    # 确定领涨指数
    rs_max = -1
    leader_code = None
    for code in INDEXES:
        row = conn.execute("""
            SELECT rs_60 FROM index_rs_daily
            WHERE stock_code = ? AND date <= ? ORDER BY date DESC LIMIT 1
        """, (code, target_date)).fetchone()
        if row and row['rs_60'] and row['rs_60'] > rs_max:
            rs_max = row['rs_60']
            leader_code = code

    # 检查每个指数连续2天背离
    total_score = 0
    triggered = []
    leader_triggered = False

    for code in INDEXES:
        # 取最近 5 天数据
        klines = conn.execute("""
            SELECT date, close, change FROM index_daily_kline
            WHERE stock_code = ? AND date <= ? ORDER BY date DESC LIMIT 5
        """, (code, target_date)).fetchall()

        if len(klines) < 3:
            continue

        streak = 0
        for i in range(min(3, len(klines))):
            d = klines[i]
            if not d['change'] or d['change'] <= 0.5:
                break
            # 成分股涨跌比（用全市场 AD 近似——精确计算需要成分股权重表）
            # 这里用已计算的 ad_ratio 近似
            ad_row = conn.execute("""
                SELECT ad_ratio_value FROM market_health_daily
                WHERE date = ?
            """, (d['date'],)).fetchone()
            if ad_row and ad_row['ad_ratio_value'] and ad_row['ad_ratio_value'] < 0.67:  # <0.67 ≈ 上涨占比<40%
                streak += 1
            else:
                break

        if streak >= 2:
            triggered.append(INDEX_NAMES.get(code, code))
            if code == leader_code:
                leader_triggered = True

    if leader_triggered:
        total_score = 40
    elif triggered:
        total_score = min(20 * len(triggered), 60)

    detail = f"{len(triggered)}个指数背离: {','.join(triggered)}" if triggered else "无背离"
    if leader_triggered:
        detail += f" [领涨: {INDEX_NAMES.get(leader_code, leader_code)}]"

    return len(triggered), 1 if leader_triggered else 0, total_score, detail


# ═══════════════════════════════════════════════
# 6. 跌破均线 + 死叉 + 市场宽度 + 成交量
# ═══════════════════════════════════════════════

def score_ma_break(conn, target_date):
    """50日线 / 200日线 / 死叉"""
    rows = conn.execute("""
        SELECT date, close FROM index_daily_kline
        WHERE stock_code = '000985' AND date <= ? ORDER BY date DESC LIMIT 250
    """, (target_date,)).fetchall()

    if len(rows) < 200:
        return 0, 0, 0, "K线数据不足"

    closes = [r['close'] for r in rows]

    # 50 日均线
    ma50_now = sum(closes[:50]) / 50 if len(closes) >= 50 else 0
    ma50_5d_ago = sum(closes[5:55]) / 50 if len(closes) >= 55 else 0

    # 200 日均线
    ma200_now = sum(closes[:200]) / 200 if len(closes) >= 200 else 0
    ma200_5d_ago = sum(closes[5:205]) / 200 if len(closes) >= 205 else 0

    ma50_score = 0
    ma200_score = 0
    death_cross_score = 0
    ma50_broken = 0
    ma200_broken = 0
    death_cross = 0

    # 50日线：跌破且 5 日未收复
    below_50_now = closes[0] < ma50_now
    # 简化：检查最近是否连续 5 天低于 MA50
    below_count = sum(1 for i in range(min(10, len(closes))) if closes[i] < ma50_now)
    if below_count >= 5 and below_50_now:
        ma50_broken = 1
        ma50_score = 30

    # 200日线：收盘跌破
    if closes[0] < ma200_now:
        ma200_broken = 1
        ma200_score = 80

    # 死叉：MA50 下穿 MA200
    if ma50_now < ma200_now and ma50_5d_ago >= ma200_5d_ago:
        death_cross = 1
        death_cross_score = 60

    detail = []
    if ma50_broken:
        detail.append("跌破50日线")
    if ma200_broken:
        detail.append("跌破200日线")
    if death_cross:
        detail.append("均线死叉")

    return ma50_score + ma200_score + death_cross_score, ma50_broken, ma200_broken, death_cross, \
           "; ".join(detail) if detail else "均线正常"


def score_market_width(conn, target_date):
    """AD比率和NH/NL比率"""
    row = conn.execute("""
        SELECT ad_ratio_value, hl_ratio_value FROM market_health_daily
        WHERE date = ?
    """, (target_date,)).fetchone()

    if not row:
        return 0, 0, 0, 0, 0, "无宽度数据"

    ad = row['ad_ratio_value'] or 1.0
    hl = row['hl_ratio_value'] or 1.0

    # AD < 0.7 连续 5 天
    ad_low5 = 0
    ad_low5_score = 0
    ad_rows = conn.execute("""
        SELECT ad_ratio_value FROM market_health_daily
        WHERE date <= ? ORDER BY date DESC LIMIT 5
    """, (target_date,)).fetchall()
    if len(ad_rows) >= 5 and all(r['ad_ratio_value'] and r['ad_ratio_value'] < 0.7 for r in ad_rows):
        ad_low5 = 1
        ad_low5_score = 20

    # AD < 0.5
    ad_crash = 0
    ad_crash_score = 0
    if ad < 0.5:
        ad_crash = 1
        ad_crash_score = 60

    # NH/NL < 0.5 连续 3 天
    hlnl_low = 0
    hlnl_score = 0
    hl_rows = conn.execute("""
        SELECT hl_ratio_value FROM market_health_daily
        WHERE date <= ? ORDER BY date DESC LIMIT 3
    """, (target_date,)).fetchall()
    if len(hl_rows) >= 3 and all(r['hl_ratio_value'] and r['hl_ratio_value'] < 0.5 for r in hl_rows):
        hlnl_low = 1
        hlnl_score = 30

    return ad_low5_score + ad_crash_score + hlnl_score, ad_low5, ad_crash, hlnl_low, 0, \
           f"AD={ad:.2f} HL={hl:.2f}"


def score_vol_dry(conn, target_date):
    """上涨无量：连续 5 日涨但量萎缩"""
    rows = conn.execute("""
        SELECT date, close, volume, change FROM index_daily_kline
        WHERE stock_code = '000985' AND date <= ? ORDER BY date DESC LIMIT 30
    """, (target_date,)).fetchall()

    if len(rows) < 25:
        return 0, 0, "数据不足"

    # 检查最近 5 天是否严格连续上涨（每日 change > 0）
    recent5 = rows[:5]
    all_up = all(r['change'] and r['change'] > 0 for r in recent5)

    if not all_up:
        return 0, 0, "未连续5日上涨"

    # 日均量 vs 20 日均量
    vol_5d = sum(r['volume'] for r in recent5) / 5
    vol_20d = sum(r['volume'] for r in rows[5:25]) / 20 if len(rows) >= 25 else vol_5d

    if vol_20d > 0 and vol_5d < vol_20d * 0.8:
        return 1, 20, f"连续5日涨但量萎缩(5日均量{vol_5d/1e8:.1f}亿 < 20日均量{vol_20d/1e8:.1f}亿×80%)"

    return 0, 0, f"无量信号未触发"


# ═══════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════

def compute_all(target_date):
    conn = get_db()
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"📉 大盘卖出评分 — {target_date}")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # 1. 抛盘日
    dist_count, dist_score, dist_high, dist_detail = score_distribution(conn, target_date)
    logger.info(f"  抛盘日: {dist_count}个 → {dist_score}分 {'(高位)' if dist_high else ''}")

    # 2. 追盘日失效
    ftd_failed, ftd_score, ftd_detail = score_ftd_failure(conn, target_date)
    logger.info(f"  追盘日: {ftd_detail} → {ftd_score}分")

    # 3. 龙头股见顶
    leader_trig, leader_score, leader_detail = score_leader_top(conn, target_date)
    logger.info(f"  龙头股: {leader_detail} → {leader_score}分")

    # 4. 垃圾股补涨
    junk_trig, junk_score, junk_detail = score_junk_rally(conn, target_date)
    logger.info(f"  垃圾股补涨: {junk_detail} → {junk_score}分")

    # 5. 背离
    div_count, div_leader, div_score, div_detail = score_divergence(conn, target_date)
    logger.info(f"  指数背离: {div_detail} → {div_score}分")

    # 6. 均线
    ma_total, ma50_b, ma200_b, death_c, ma_detail = score_ma_break(conn, target_date)
    ma50_score = 30 if ma50_b else 0
    ma200_score = 80 if ma200_b else 0
    death_score = 60 if death_c else 0
    logger.info(f"  均线: {ma_detail} → {ma_total}分")

    # 7. 市场宽度
    width_total, ad_low5, ad_crash, hlnl_low, _, width_detail = score_market_width(conn, target_date)
    ad_low5_score = 20 if ad_low5 else 0
    ad_crash_score = 60 if ad_crash else 0
    hlnl_score = 30 if hlnl_low else 0
    logger.info(f"  市场宽度: {width_detail} → {width_total}分")

    # 8. 上涨无量
    vol_dry, vol_dry_score, vol_dry_detail = score_vol_dry(conn, target_date)
    logger.info(f"  上涨无量: {vol_dry_detail} → {vol_dry_score}分")

    # 汇总
    total = (dist_score + ftd_score + leader_score + junk_score + div_score +
             ma50_score + ma200_score + death_score +
             ad_low5_score + ad_crash_score + hlnl_score + vol_dry_score)

    # 仓位建议
    if total >= 140:
        position_advice = "清仓"
    elif total >= 100:
        position_advice = "减仓至25%"
    elif total >= 60:
        position_advice = "减仓至50%"
    else:
        position_advice = "正常仓位(70-100%)"

    # 熔断检查
    meltdown = 0
    cleared_signals = []
    if ma200_b:
        cleared_signals.append("跌破200日线")
    if death_c:
        cleared_signals.append("均线死叉")
    if ad_crash:
        cleared_signals.append("AD比率<0.5")
    if len(cleared_signals) >= 2:
        meltdown = 1
        position_advice = "强制清仓"

    signal_details = {
        "dist_days_25d": dist_count,
        "dist_high_zone": bool(dist_high),
        "ftd_failed": bool(ftd_failed),
        "leader_fallen": leader_detail,
        "junk_rally": bool(junk_trig),
        "divergence_count": div_count,
        "divergence_leader": bool(div_leader),
        "ma50_broken": bool(ma50_b),
        "ma200_broken": bool(ma200_b),
        "death_cross": bool(death_c),
        "ad_low5": bool(ad_low5),
        "ad_crash": bool(ad_crash),
        "hlnl_low": bool(hlnl_low),
        "vol_dry": bool(vol_dry),
    }

    logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"  卖出总分: {total}  仓位建议: {position_advice}")
    if meltdown:
        logger.info(f"  ⚠️ 熔断触发: {', '.join(cleared_signals)}")
    logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # 写入
    conn.execute("""
        INSERT OR REPLACE INTO market_sell_score_daily
        (date, total_score, position_advice, meltdown_triggered,
         dist_days_25d, dist_score, dist_high_zone,
         ftd_failed, ftd_score,
         leader_top_fallen, leader_score,
         junk_rally, junk_score,
         divergence_count, divergence_leader, divergence_score,
         ma50_broken, ma50_score,
         ma200_broken, ma200_score,
         death_cross, death_cross_score,
         ad_ratio_low5, ad_low5_score,
         ad_ratio_crash, ad_crash_score,
         hlnl_ratio_low, hlnl_score,
         vol_dry_up, vol_dry_score,
         signal_details, cleared_signals)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (target_date, total, position_advice, meltdown,
          dist_count, dist_score, dist_high,
          ftd_failed, ftd_score,
          leader_trig, leader_score,
          junk_trig, junk_score,
          div_count, div_leader, div_score,
          ma50_b, ma50_score,
          ma200_b, ma200_score,
          death_c, death_score,
          ad_low5, ad_low5_score,
          ad_crash, ad_crash_score,
          hlnl_low, hlnl_score,
          vol_dry, vol_dry_score,
          json.dumps(signal_details, ensure_ascii=False),
          json.dumps(cleared_signals, ensure_ascii=False)))
    conn.commit()
    conn.close()

    return total, position_advice


import json

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="大盘卖出评分计算")
    parser.add_argument("--date", type=str, default=None, help="目标日期 YYYY-MM-DD")
    args = parser.parse_args()

    target = args.date or dt_date.today().strftime("%Y-%m-%d")
    ensure_tables()
    compute_all(target)