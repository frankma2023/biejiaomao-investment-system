"""
观察池日更任务

从 stock_rs_daily 读取最新 RS 数据，按 RPS 分类筛选（稳健龙头/加速爆发/短期爆发/双强股），
扫描入选股票的 CANSLIM 评分和基础财务，写入 discipline_observation_pool 宽表。

使用方式：
    python src/discipline/observation.py                     # 计算最新日期
    python src/discipline/observation.py --date 2026-05-16   # 指定日期
"""

import os
import sys
import sqlite3
import json
import argparse
from datetime import datetime

# 项目根
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ════════════════════════════════════════════════════════
# RS 分类规则
# ════════════════════════════════════════════════════════

RS_CATEGORIES = {
    # 排他性分类规则（按优先级）：
    # 1. 稳健龙头: RPS250≥90 AND RPS20≥85
    # 2. 加速爆发: RPS250≥80 AND RPS20≥95
    # 3. 短期爆发: RPS20≥95 AND NOT 加速爆发 (即 RPS250<80)
    # 4. 长期稳健: RPS250≥85 AND NOT 稳健龙头 (即 RPS20<85)
    # 双强股: 同时满足稳健龙头 + 加速爆发 (即 RPS250≥90 AND RPS20≥95)
}


def classify_rs(rps_20, rps_250):
    """RS 分类，排他性规则避免重叠歧义"""
    categories = []

    # 稳健龙头: 长期极强 + 近期也强
    is_robust = rps_250 >= 90 and rps_20 >= 85

    # 加速爆发: 长期有底子 + 近期极强
    is_burst = rps_250 >= 80 and rps_20 >= 95

    # 短期爆发: 近期极强但长期不够（排他：不满足加速爆发）
    is_short_burst = rps_20 >= 95 and not is_burst

    # 长期稳健: 长期强但近期温和（排他：不满足稳健龙头）
    is_steady = rps_250 >= 85 and not is_robust

    if is_robust and is_burst:
        categories.append('双强')
    else:
        if is_robust:
            categories.append('龙头')
        if is_burst:
            categories.append('加速')
    if is_short_burst:
        categories.append('短爆')
    if is_steady:
        categories.append('稳健')

    return categories


# 最小成交额过滤（避免仙股）
MIN_AMOUNT_20D = 50_000_000  # 5000万

# 综合评分阈值（低于此分不入池）
COMPOSITE_THRESHOLD = 45

# 信号评分权重（10天内，同类型可多次计分）
# 单次贡献值 = 权重 × 30（信号满分30分）
SIGNAL_WEIGHTS = {
    'base_breakout': 9,        # 30% × 30 → 基部突破（统一替代各种基底形态）
    'pocket_pivot': 9,          # 30% × 30 → 口袋支点
    'talib_bullish': 4.5,       # 15% × 30 → TA-Lib技术指标看涨
    'cdl_bullish': 3,           # 10% × 30 → K线看涨
}

# 信号评分窗口（天）
SIGNAL_LOOKBACK_DAYS = 10


def compute_signal_score(stock_code, target_date, db):
    """根据过去10天的买入信号计算信号评分 (0-30)，含时间衰减"""
    from datetime import datetime, timedelta
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    start_dt = target_dt - timedelta(days=SIGNAL_LOOKBACK_DAYS)
    start_str = start_dt.strftime('%Y-%m-%d')
    
    rows = db.execute("""
        SELECT date, signals_json FROM pattern_scan_signals
        WHERE stock_code = ? AND date >= ? AND date <= ?
        ORDER BY date DESC
    """, (stock_code, start_str, target_date)).fetchall()
    
    if not rows:
        return 0
    
    score = 0.0
    
    for signal_date, signals_json in rows:
        if not signals_json:
            continue
        try:
            signals = json.loads(signals_json)
        except Exception:
            continue
        
        if not isinstance(signals, list):
            signals = [signals]
        
        # 计算时间衰减系数
        sig_dt = datetime.strptime(signal_date, '%Y-%m-%d')
        days_ago = (target_dt - sig_dt).days
        if days_ago <= 3:
            decay = 1.0
        elif days_ago <= 6:
            decay = 0.7
        else:
            decay = 0.4
        
        for sig in signals:
            source = sig.get('source', '')
            sig_type = sig.get('signal_type', sig.get('type', sig.get('pattern', '')))
            
            # 基部突破
            if source == 'base_breakout':
                score += SIGNAL_WEIGHTS['base_breakout'] * decay
            
            # 口袋支点
            elif source == 'pocket_pivot':
                score += SIGNAL_WEIGHTS['pocket_pivot'] * decay
            
            # TA-Lib看涨
            elif source == 'talib' and sig_type == 'bullish':
                score += SIGNAL_WEIGHTS['talib_bullish'] * decay
            
            # K线看涨
            elif source == 'cdl' and sig_type == 'bullish':
                score += SIGNAL_WEIGHTS['cdl_bullish'] * decay
    
    return min(round(score, 1), 30)


# ════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════

def run(target_date=None):
    """执行一次观察池日更"""
    db = get_db()

    # 1. 确定目标日期（默认取 stock_rs_daily 最新日期）
    if target_date is None:
        row = db.execute("SELECT MAX(date) FROM stock_rs_daily").fetchone()
        if not row or not row[0]:
            print("[observation] stock_rs_daily 无数据")
            db.close()
            return 0
        target_date = row[0]

    print(f"[observation] 目标日期: {target_date}")

    # 2. 读取 RS 数据 + 股票基本信息 + 行业
    rs_rows = db.execute("""
        SELECT r.stock_code, b.name AS stock_name,
               r.rps_20, r.rps_60, r.rps_120, r.rps_250,
               r.amount
        FROM stock_rs_daily r
        JOIN stock_basic b ON r.stock_code = b.stock_code
        WHERE r.date = ?
          AND b.listing_status = 'normally_listed'
          AND r.rps_250 IS NOT NULL
    """, (target_date,)).fetchall()

    if not rs_rows:
        print("[observation] 无 RS 数据")
        db.close()
        return 0

    # 3. 加载行业映射
    industry_map = {}
    ind_rows = db.execute("SELECT stock_code, industry_name FROM stock_sw_industry").fetchall()
    for r in ind_rows:
        industry_map[r['stock_code']] = r['industry_name']

    # 4. RS 分类筛选
    candidates = []
    for r in rs_rows:
        rps_20 = r['rps_20'] or 0
        rps_250 = r['rps_250'] or 0
        amount = r['amount'] or 0

        if amount < MIN_AMOUNT_20D:
            continue

        cats = classify_rs(rps_20, rps_250)
        if not cats:
            continue

        stock_code = r['stock_code']
        candidates.append({
            'stock_code': stock_code,
            'stock_name': r['stock_name'],
            'industry_name': industry_map.get(stock_code, ''),
            'rs_category': ' / '.join(cats),
            'rps_20': rps_20,
            'rps_60': r['rps_60'],
            'rps_120': r['rps_120'],
            'rps_250': rps_250,
        })

    print(f"[observation] RS 筛选后: {len(candidates)} 只")

    if not candidates:
        db.close()
        return 0

    # 5. 加载 CANSLIM 最新评分（不限日期，取每只股票最近一次评分）
    canslim_map = {}
    cs_rows = db.execute("""
        SELECT stock_code, score,
               score_c, score_a, score_n, score_s, score_l, score_i
        FROM cansim_scores
        WHERE (stock_code, date) IN (
            SELECT stock_code, MAX(date) FROM cansim_scores GROUP BY stock_code
        )
    """).fetchall()
    for r in cs_rows:
        canslim_map[r['stock_code']] = dict(r)

    # 6. 加载最新年度财务
    fin_map = {}
    latest_fin = db.execute("SELECT MAX(report_date) FROM stock_financials_annual").fetchone()
    if latest_fin and latest_fin[0]:
        fin_rows = db.execute("""
            SELECT stock_code, roe, revenue_yoy, gross_margin,
                   asset_liability_ratio AS debt_ratio
            FROM stock_financials_annual
            WHERE report_date = ?
        """, (latest_fin[0],)).fetchall()
        for r in fin_rows:
            fin_map[r['stock_code']] = dict(r)

    # 6.5. V2: 加载形态信号（从 pattern_scan_signals 读取当天信号）
    signal_map = {}
    sig_rows = db.execute("""
        SELECT stock_code, signals_json
        FROM pattern_scan_signals
        WHERE date = ?
    """, (target_date,)).fetchall()
    for r in sig_rows:
        signal_map[r['stock_code']] = r['signals_json']

    # 7. 组装宽表数据 + 写入
    to_insert = []
    below_threshold = 0
    for c in candidates:
        code = c['stock_code']
        cs = canslim_map.get(code, {})
        fin = fin_map.get(code, {})

        # 先计算综合分，用于阈值过滤
        composite = round(
            0.2 * c['rps_250'] +
            0.2 * c['rps_20'] +
            0.3 * (cs.get('score') or 0) +
            compute_signal_score(code, target_date, db),
            1
        )
        if composite < COMPOSITE_THRESHOLD:
            below_threshold += 1
            continue

        row = (
            code,
            target_date,
            c['stock_name'],
            c['industry_name'],
            c['rs_category'],
            c['rps_20'],
            c['rps_60'],
            c['rps_120'],
            c['rps_250'],
            cs.get('score'),
            cs.get('score_c'),
            cs.get('score_a'),
            cs.get('score_n'),
            cs.get('score_s'),
            cs.get('score_l'),
            cs.get('score_i'),
            None,
            fin.get('roe'),
            None,
            fin.get('revenue_yoy'),
            fin.get('debt_ratio'),
            fin.get('gross_margin'),
            None, None, None, None,
            None, None,
            signal_map.get(code),
            composite,
            None,  # grade
            None,  # suggestion
        )
        to_insert.append(row)

    print(f"[observation] 综合评分 ≥ {COMPOSITE_THRESHOLD}: {len(to_insert)} 只（RS筛后{len(candidates)}只中过滤掉{below_threshold}只）")

    # 8. 替换写入（truncate + insert）
    db.execute("DELETE FROM discipline_observation_pool WHERE date = ?", (target_date,))
    db.executemany("""
        INSERT INTO discipline_observation_pool (
            stock_code, date, stock_name, industry_name,
            rs_category, rps_20, rps_60, rps_120, rps_250,
            canslim_total, canslim_c, canslim_a, canslim_n, canslim_s, canslim_l, canslim_i, canslim_m,
            roe, eps_yoy, revenue_yoy, debt_ratio, gross_margin,
            pe_ttm, pb, pe_percentile, market_cap,
            buy_signals_json, sell_signals_json, signals_json,
            composite_score, grade, suggestion
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, to_insert)
    db.commit()

    print(f"[observation] 写入 {len(to_insert)} 条到观察池")
    db.close()
    return len(to_insert)


# ════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='观察池日更任务')
    parser.add_argument('--date', type=str, default=None, help='目标日期 YYYY-MM-DD')
    args = parser.parse_args()
    run(target_date=args.date)
