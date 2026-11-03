#!/usr/bin/env python3
"""
大盘卖出评分引擎

根据《大盘环境判断卖出评分卡》每日计算卖出信号评分。
总分越高，卖出建议越强。

使用方式:
  python src/scanners/market_sell_score.py --date 2026-05-30

信号列表（12项）。分值与《大盘环境判断卖出评分卡》§5.1 一致，
阈值口径以该文档 §7（2026-11-03 修复记录）为准：
  抛盘日 25日内 ≥5个=10 / ≥7个=20 / ≥9个=40  — 高位（距52周高<5%）×2
  追盘日失效 = 30
  龙头股见顶 = 25（RPS≥99 股池中 ≥50% 回落>10%）
  垃圾股补涨 = 25
  指数背离 = 20 / 领涨指数背离 = 40（上限 60）
  跌破50日线（10日内≥5日在MA50下且当前仍在下方）= 30
  跌破200日线 = 40（状态型，非事件型）
  均线死叉（MA50 下穿 MA200）= 60
  AD比率 近5日≥4日<0.7 = 20   ← 用当日涨跌比，非 5 日均值
  AD比率 当日<0.15 = 60       ← 同上；清仓级，触发率约 6.6%
  NH/NL比率 <0.5连续3天 = 30
  上涨无量 近5日≥3日涨且5日均量<20日均量×80% = 20

复跑校准：scripts/diag_sell_score_thresholds.py
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

# ═══════════════════════════════════════════════
# 阈值配置（config/market/market_sell_score.yaml）
# 改阈值只改 YAML，不要改代码；校准依据见 scripts/diag_sell_score_thresholds.py
# ═══════════════════════════════════════════════

_CFG_CACHE = None
# 兜底默认值：与 YAML 同步维护。配置缺失/键缺失时使用，保证行为不变。
_CFG_DEFAULT = {
    'distribution_days': {'tiers': [{'min_days': 9, 'score': 40},
                                    {'min_days': 7, 'score': 20},
                                    {'min_days': 5, 'score': 10}],
                          'high_zone_pct': 0.95},
    'follow_through_day': {'score': 30, 'lookback_days': 30},
    'leader': {'score': 25, 'rps_threshold': 99, 'min_pool_size': 5,
               'fall_pct': -0.10, 'fall_ratio': 0.50},
    'junk_rally': {'score': 25, 'cheap_pct': 0.20, 'expensive_pct': 0.80,
                   'lookback_days': 20, 'ratio': 1.5, 'trade_days': 5, 'sample_size': 50},
    'divergence': {'indexes': ['000985', '000001', '399001', '399006', '000300'],
                   'leader': '000985', 'index_min_gain': 0.005, 'ad_ratio_max': 1.0,
                   'streak_days': 2, 'score_normal': 20, 'score_leader': 40, 'score_cap': 60},
    'moving_average': {'ma50_score': 30, 'ma50_lookback': 10, 'ma50_min_below': 5,
                       'ma200_score': 40, 'death_cross_score': 60},
    'market_width': {'ad_low5_score': 20, 'ad_low5_trade_days': 5, 'ad_low5_min_days': 4,
                     'ad_low5_threshold': 0.7, 'ad_crash_score': 60,
                     'ad_crash_threshold': 0.15, 'hlnl_score': 30,
                     'hlnl_threshold': 0.5, 'hlnl_streak': 3},
    'vol_dry': {'score': 20, 'min_up_days': 3, 'window': 5,
                'vol_ratio': 0.80, 'vol_ma_days': 20},
    'position_tiers': {'clear': 140, 'reduce_25': 100, 'reduce_50': 60},
    'meltdown': {'cleared_signals': ['ma200_broken', 'death_cross', 'ad_crash'],
                 'single_cap': 25},
}


def cfg():
    """读取阈值配置；文件缺失或解析失败时返回兜底默认值。"""
    global _CFG_CACHE
    if _CFG_CACHE is not None:
        return _CFG_CACHE
    path = os.path.join(PROJECT_DIR, 'config', 'market', 'market_sell_score.yaml')
    merged = {k: dict(v) if isinstance(v, dict) else v for k, v in _CFG_DEFAULT.items()}
    try:
        import yaml
        with open(path, encoding='utf-8') as f:
            loaded = yaml.safe_load(f) or {}
        for k, v in loaded.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v
    except Exception as e:                      # 配置文件缺失不阻塞计算
        logger.warning(f"读取 {path} 失败，使用兜底默认值: {e}")
    _CFG_CACHE = merged
    return _CFG_CACHE




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
        if latest_close >= high_52w * cfg()['distribution_days']['high_zone_pct']:
            high_zone = 1

    # 评分
    # 阈值 5/7/9 来自 2026 年逐日实测的 25 日内抛盘日数分布（中位 5、P75 7、P90 9）。
    # 原阈值 4/5/6 的起点落在中位数之下，导致低档几乎恒真。
    # 复跑：scripts/diag_sell_score_thresholds.py
    base_score = 0
    for tier in cfg()['distribution_days']['tiers']:
        if dist_count >= tier['min_days']:
            base_score = tier['score']
            break

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
    # 取 pool_date 当日（或之前最近一个有数据的交易日）的 RPS≥99 股票。
    # 原实现 WHERE date <= pool_date 无下限，会把「历史曾经 RPS≥99」的股票全部纳入
    # （实测累积到 1067 只，而当日实际仅 27 只），导致回落比例被严重稀释。
    snap = conn.execute(
        "SELECT MAX(date) AS d FROM stock_rs_daily WHERE date <= ?", (pool_date,)).fetchone()
    snap_date = snap['d'] if snap and snap['d'] else pool_date
    rs_rows = conn.execute("""
        SELECT stock_code, rps_250 FROM stock_rs_daily
        WHERE date = ? AND rps_250 >= 99
    """, (snap_date,)).fetchall()
    pool_stocks = {r['stock_code']: r['rps_250'] for r in rs_rows}

    if len(pool_stocks) < 5:
        return 0, 0, f"龙头股池仅{len(pool_stocks)}只，样本不足"

    # 取各股票入池时（pool_date）的收盘价和当前收盘价
    fallen_count = 0
    for code in pool_stocks:
        entry_price_row = conn.execute("""
            SELECT close FROM daily_kline_adj
            WHERE stock_code = ? AND date <= ? ORDER BY date DESC LIMIT 1
        """, (code, pool_date)).fetchone()
        current_price_row = conn.execute("""
            SELECT close FROM daily_kline_adj
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

    # 取近 5 个「交易日」（不是日历日），且每个交易日重新计算价格分位。
    # PRD 2.4 要求「每日重排、≥5 交易日」；原实现只在 target_date 算一次分位，
    # 再按日历日回溯 5 次（跨周末会漏交易日），与 PRD 不符。
    trade_days = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_kline_adj WHERE date <= ? "
        "ORDER BY date DESC LIMIT 5", (target_date,))]
    if len(trade_days) < 5:
        return 0, 0, f"交易日不足({len(trade_days)})"

    streak = 0
    for check_date in trade_days:
        # 每个交易日重新计算价格分位（PRD 2.4「每日重排」）
        prices = conn.execute("""
            SELECT stock_code, close FROM daily_kline_adj
            WHERE date = ? AND close > 0
        """, (check_date,)).fetchall()
        if len(prices) < 100:
            continue
        prices = sorted(prices, key=lambda r: r['close'])
        n = len(prices)
        low_cutoff = prices[int(n * 0.2)]['close']
        high_cutoff = prices[int(n * 0.8)]['close']
        low_codes = [r['stock_code'] for r in prices if r['close'] <= low_cutoff][:50]
        high_codes = [r['stock_code'] for r in prices if r['close'] >= high_cutoff][:50]

        # 低价股 / 高价股 20 日涨幅
        low_gains, high_gains = [], []
        for code in low_codes:
            row = conn.execute("""
                SELECT close FROM daily_kline_adj
                WHERE stock_code = ? AND date <= ? ORDER BY date DESC LIMIT 21
            """, (code, check_date)).fetchall()
            if len(row) >= 21 and row[0]['close'] and row[-1]['close']:
                low_gains.append((row[0]['close'] - row[-1]['close']) / row[-1]['close'] * 100)
        for code in high_codes:
            row = conn.execute("""
                SELECT close FROM daily_kline_adj
                WHERE stock_code = ? AND date <= ? ORDER BY date DESC LIMIT 21
            """, (code, check_date)).fetchall()
            if len(row) >= 21 and row[0]['close'] and row[-1]['close']:
                high_gains.append((row[0]['close'] - row[-1]['close']) / row[-1]['close'] * 100)

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
    """指数与成分股背离（PRD 2.5）。

    判定：指数当日涨 >0.5%，但其**自身成分股**中跌家数多于涨家数（成分股涨跌比 <1.0），
    连续 2 个交易日成立即计分。领涨指数（中证全指）背离 40 分，其余每个 20 分，
    累加后上限 60 分——与 PRD 2.5「分别计分、上限 60」一致。

    成分股用 index_constituents 中 ≤ target_date 的最新快照。
    该表更新滞后时仍可用——指数成分变动缓慢，不影响方向判断。
    """
    INDEXES = ['000985', '000001', '399001', '399006', '000300']
    LEADER = '000985'

    # 1) 各指数最近 3 个交易日（判断是否连续上涨）
    klines = {}
    for code in INDEXES:
        klines[code] = conn.execute(
            "SELECT date, change FROM index_daily_kline "
            "WHERE stock_code = ? AND kline_type='normal' AND date <= ? "
            "ORDER BY date DESC LIMIT 3", (code, target_date)).fetchall()

    # 2) 各指数成分股集合（≤ target_date 的最新快照）
    members = {}
    for code in INDEXES:
        snap = conn.execute(
            "SELECT MAX(date) AS d FROM index_constituents "
            "WHERE index_code = ? AND date <= ?", (code, target_date)).fetchone()
        if not snap or not snap['d']:
            members[code] = set()
            continue
        members[code] = {r[0] for r in conn.execute(
            "SELECT stock_code FROM index_constituents "
            "WHERE index_code = ? AND date = ?", (code, snap['d']))}

    # 3) 预取最近 2 个交易日的全市场涨跌（每日一次，供 5 个指数共用）
    need_dates = set()
    for code in INDEXES:
        for d in klines[code][:2]:
            need_dates.add(d['date'])
    day_ud = {}          # date -> {stock_code: +1/-1/0}
    for d in need_dates:
        rows = conn.execute("""
            SELECT stock_code, close,
                   LAG(close) OVER (PARTITION BY stock_code ORDER BY date) AS prev_close
            FROM daily_kline_adj
            WHERE date <= ? AND date >= date(?, '-5 days')
        """, (d, d)).fetchall()
        m = {}
        for x in rows:
            if x['prev_close'] is None:
                continue
            if x['close'] > x['prev_close']:
                m[x['stock_code']] = 1
            elif x['close'] < x['prev_close']:
                m[x['stock_code']] = -1
            else:
                m[x['stock_code']] = 0
        day_ud[d] = m

    # 4) 逐指数判定：连续 2 日「指数涨但成分股涨跌比 <1.0」
    triggered, leader_triggered = [], False
    for code in INDEXES:
        kl, mem = klines[code], members[code]
        if len(kl) < 3 or not mem:
            continue
        streak = 0
        for d in kl[:2]:
            chg = d['change']
            if chg is None or chg <= 0.005:      # change 是小数，0.005 = 0.5%
                break
            m = day_ud.get(d['date'], {})
            up = sum(1 for c in mem if m.get(c) == 1)
            down = sum(1 for c in mem if m.get(c) == -1)
            if down > 0 and (up / down) < 1.0:
                streak += 1
            else:
                break
        if streak >= 2:
            triggered.append(code)
            if code == LEADER:
                leader_triggered = True

    # 5) 计分：领涨 40，其余每个 20，上限 60
    if leader_triggered:
        total_score = 40 + 20 * (len(triggered) - 1)
    else:
        total_score = 20 * len(triggered)
    total_score = min(total_score, 60)

    names = [INDEX_NAMES.get(c, c) for c in triggered]
    detail = ('背离: ' + '、'.join(names) + ('（含领涨）' if leader_triggered else '')) \
        if triggered else '无背离'
    return total_score, 1 if triggered else 0, 1 if leader_triggered else 0, detail


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
    if below_count >= cfg()['moving_average']['ma50_min_below'] and below_50_now:
        ma50_broken = 1
        ma50_score = cfg()['moving_average']['ma50_score']

    # 200日线：收盘跌破。
    # 分值由 80 降为 40（2026-11-03）：该信号是「状态」而非「事件」，
    # 指数在年线下方会持续数月，80 分使其独占日均总分的 44%（54.7/124.4），
    # 让评分卡退化为「指数在不在年线上方」的二元判断。
    if closes[0] < ma200_now:
        ma200_broken = 1
        ma200_score = cfg()['moving_average']['ma200_score']

    # 死叉：MA50 下穿 MA200
    if ma50_now < ma200_now and ma50_5d_ago >= ma200_5d_ago:
        death_cross = 1
        death_cross_score = cfg()['moving_average']['death_cross_score']

    detail = []
    if ma50_broken:
        detail.append("跌破50日线")
    if ma200_broken:
        detail.append("跌破200日线")
    if death_cross:
        detail.append("均线死叉")

    # 返回各项分值与触发标识。调用方必须使用这里的分值，
    # 不得另行硬编码（历史上 compute_all 曾重复硬编码 30/80/60，
    # 导致修改本函数无效——2026-11-03 修复）。
    return (ma50_score, ma200_score, death_cross_score,
            ma50_broken, ma200_broken, death_cross,
            "; ".join(detail) if detail else "均线正常")


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
    # 原实现读 market_health_daily.ad_ratio_value（已是 5 日均值），再要求连续 5 天，
    # 实际等于 9 天窗口，全历史最长连续仅 3 天 → 永不触发。
    # 改为：现算近 5 个交易日的「当日涨跌比」，要求其中 ≥3 日 < 0.85（约 P25）。
    daily_up_down = []  # 近 5 个交易日的当日涨跌比
    dr = conn.execute("""
        SELECT date,
               SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) AS up,
               SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) AS down
        FROM (
            SELECT date, close,
                   LAG(close) OVER (PARTITION BY stock_code ORDER BY date) AS prev_close
            FROM daily_kline_adj
            WHERE date >= date(?, '-15 days') AND date <= ?
        )
        WHERE prev_close IS NOT NULL
        GROUP BY date ORDER BY date DESC LIMIT 5
    """, (target_date, target_date)).fetchall()
    for r in dr:
        if r['down'] and r['down'] > 0:
            daily_up_down.append(r['up'] / r['down'])
    # 阈值 0.7 + 5日内≥4日：0.7 保留 PRD 原意，把「连续5日」放宽为「4 of 5」
    # （连续5日在宽基指数上等于永假，实测全历史最长连续仅 3 天）。
    # 复跑：scripts/diag_sell_score_thresholds.py
    _mw = cfg()['market_width']
    low_days = sum(1 for v in daily_up_down if v < _mw['ad_low5_threshold'])
    if len(daily_up_down) >= _mw['ad_low5_trade_days'] and low_days >= _mw['ad_low5_min_days']:
        ad_low5 = 1
        ad_low5_score = _mw['ad_low5_score']

    # AD < 0.5：必须用「当日」涨跌比。原实现用 ad（=market_health_daily 的 5 日均值），
    # 2026 年实测：当日<0.5 有 67 天，平滑<0.5 仅 1 天 → 60 分信号与清仓级熔断近乎失效。
    # 阈值由 0.5 收紧为 0.15（2026-11-03）：该信号 60 分且属清仓级熔断条件，
    # 0.5 的触发率达 33.7%（69/196 日），作为「清仓级」过于频繁；
    # 0.15 触发率 6.6%（约每月一次），对应「跌家数为涨家数 6.7 倍」的真实恐慌日。
    # 校准：scripts/diag_sell_score_thresholds.py
    today_ratio = daily_up_down[0] if daily_up_down else None
    ad_crash = 0
    ad_crash_score = 0
    if today_ratio is not None and today_ratio < _mw['ad_crash_threshold']:
        ad_crash = 1
        ad_crash_score = _mw['ad_crash_score']

    # NH/NL < 0.5 连续 3 天。原实现读 market_health_daily.hl_ratio_value，
    # 但该字段的「连续」叠加在可能已被平滑的序列上，与 ad_low5 同型缺陷。
    # 这里保留读 market_health_daily（该字段确为当日新高新低比，compute_hl_ratio 未平滑），
    # 但显式排除 None，并在注释里标明口径来源，避免下次误判。
    hlnl_low = 0
    hlnl_score = 0
    hl_rows = conn.execute("""
        SELECT hl_ratio_value FROM market_health_daily
        WHERE date <= ? AND hl_ratio_value IS NOT NULL
        ORDER BY date DESC LIMIT 3
    """, (target_date,)).fetchall()
    if len(hl_rows) >= 3 and all(r['hl_ratio_value'] < 0.5 for r in hl_rows):
        hlnl_low = 1
        hlnl_score = 30

    # 返回各分量分值与触发标识。调用方必须使用这里的分值，不得另行硬编码
    # （compute_all 曾重复硬编码 20/60/30，与 ma_break 属同类遮蔽问题——2026-11-03 修复）。
    return (ad_low5_score, ad_crash_score, hlnl_score,
            ad_low5, ad_crash, hlnl_low,
            f"AD={ad:.2f} HL={hl:.2f}")


def score_vol_dry(conn, target_date):
    """上涨无量：连续 5 日涨但量萎缩"""
    rows = conn.execute("""
        SELECT date, close, volume, change FROM index_daily_kline
        WHERE stock_code = '000985' AND date <= ? ORDER BY date DESC LIMIT 30
    """, (target_date,)).fetchall()

    if len(rows) < 25:
        return 0, 0, "数据不足"

    # 原条件要求「连续 5 日上涨」，宽基指数一年难有几次（2026 年 0 次）。
    # 改为：近 5 日中 ≥3 日上涨，且 5 日均量 < 20 日均量 × 80%。
    recent5 = rows[:5]
    up_days = sum(1 for r in recent5 if r['change'] and r['change'] > 0)

    if up_days < cfg()['vol_dry']['min_up_days']:
        return 0, 0, f"5日内仅{up_days}日上涨（需≥{cfg()['vol_dry']['min_up_days']}日）"

    # 日均量 vs 20 日均量
    vol_5d = sum(r['volume'] for r in recent5) / 5
    vol_20d = sum(r['volume'] for r in rows[5:25]) / 20 if len(rows) >= 25 else vol_5d

    if vol_20d > 0 and vol_5d < vol_20d * 0.8:
        return 1, 20, f"5日内{up_days}日涨但量萎缩(5日均量{vol_5d/1e8:.1f}亿 < 20日均量{vol_20d/1e8:.1f}亿×80%)"

    return 0, 0, f"无量信号未触发"


# ═══════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════

def compute_all(target_date):
    conn = get_db()
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"大盘卖出评分 — {target_date}")
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
    (ma50_score, ma200_score, death_score,
     ma50_b, ma200_b, death_c, ma_detail) = score_ma_break(conn, target_date)
    ma_total = ma50_score + ma200_score + death_score
    logger.info(f"  均线: {ma_detail} → {ma_total}分")

    # 7. 市场宽度
    # 分值统一取自 score_market_width 的返回值，不得在此另行硬编码——
    # 与 score_ma_break 属同类遮蔽问题（2026-11-03 修复）。
    (ad_low5_score, ad_crash_score, hlnl_score,
     ad_low5, ad_crash, hlnl_low, width_detail) = score_market_width(conn, target_date)
    width_total = ad_low5_score + ad_crash_score + hlnl_score
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
        # PRD 5.1 熔断规则：任意两个清仓级信号同时触发 → 强制清仓
        meltdown = 1
        position_advice = "强制清仓"
    elif len(cleared_signals) == 1:
        # PRD 5.1 熔断规则：单个清仓级信号 → 仓位上限 ≤25%，3 个交易日内完成减仓
        # 原实现缺此分支，导致「仅跌破200日线」时仍输出「减仓至50%」，与 PRD 冲突
        if position_advice in ("正常仓位(70-100%)", "减仓至50%"):
            position_advice = "减仓至25%(单级熔断)"

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
        logger.info(f"  [WARN] 熔断触发: {', '.join(cleared_signals)}")
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