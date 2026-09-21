#!/usr/bin/env python3
"""
指数 RS 强度计算引擎

计算 408 个指数的 RS_20/RS_60/RS_250、MA50/150/200、AD线斜率。
结果存入 index_rs_daily，供最强指数筛选使用。

用法：python src/scanners/index_rs.py --date 2026-05-12
"""

import sys, os, argparse, sqlite3
from datetime import datetime, date as dt_date

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, "src"))
os.chdir(PROJECT_DIR)

from scripts.common import log as logger

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_table():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS index_rs_daily (
            stock_code   TEXT NOT NULL,
            date         TEXT NOT NULL,
            close        REAL,
            ret_20       REAL,  ret_60   REAL,  ret_120  REAL,  ret_250  REAL,
            rs_20        INTEGER, rs_60  INTEGER, rs_120  INTEGER, rs_250  INTEGER,
            ma50         REAL,
            ma150        REAL,
            ma200        REAL,
            ad_line      REAL,
            ad_slope_20d REAL,
            PRIMARY KEY (stock_code, date)
        );
    """)
    conn.commit()
    conn.close()


def load_index_codes():
    """从 index_style.yaml 加载全部指数代码"""
    import yaml
    cfg_path = os.path.join(PROJECT_DIR, "config", "index_style.yaml")
    with open(cfg_path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    codes = []
    for cat_name, items in cfg.get("categories", {}).items():
        for item in items:
            if item["code"] not in codes:
                codes.append(item["code"])
    return codes


def compute(target_date):
    conn = get_db()
    logger.info(f"指数RS计算 — {target_date}")

    codes = load_index_codes()
    logger.info(f"  指数池: {len(codes)} 个")

    # ── 批量查询K线（足够长的历史） ──
    ph = ','.join(['?' for _ in codes])
    rows = conn.execute(f"""
        SELECT stock_code, date, open, high, low, close, volume
        FROM index_daily_kline
        WHERE kline_type='normal' AND stock_code IN ({ph})
          AND date >= date(?, '-400 days') AND date <= ?
        ORDER BY stock_code, date
    """, codes + [target_date, target_date]).fetchall()

    if not rows:
        logger.info("  无K线数据")
        conn.close()
        return

    # ── 按指数分组 ──
    klines_by_code = {}
    for r in rows:
        code = r['stock_code']
        if code not in klines_by_code:
            klines_by_code[code] = []
        klines_by_code[code].append({
            'date': r['date'], 'open': r['open'], 'high': r['high'],
            'low': r['low'], 'close': r['close'], 'volume': r['volume'] or 0,
        })

    # ── 为每个指数找到目标日期索引并计算指标 ──
    results = []
    for code, klines in klines_by_code.items():
        if len(klines) < 250:
            continue

        # 找到 target_date 对应的索引（或最近的前一个交易日）
        idx = None
        for i, k in enumerate(klines):
            if k['date'] <= target_date:
                idx = i
        if idx is None or idx < 250:
            continue

        close = klines[idx]['close']
        if not close or close <= 0:
            continue

        # 收益率
        ret_20 = (close / klines[idx - 20]['close'] - 1) * 100 if idx >= 20 and klines[idx - 20]['close'] else None
        ret_60 = (close / klines[idx - 60]['close'] - 1) * 100 if idx >= 60 and klines[idx - 60]['close'] else None
        ret_120 = (close / klines[idx - 120]['close'] - 1) * 100 if idx >= 120 and klines[idx - 120]['close'] else None
        ret_250 = (close / klines[idx - 250]['close'] - 1) * 100 if idx >= 250 and klines[idx - 250]['close'] else None

        # MA
        ma50 = sum(k['close'] for k in klines[idx - 49:idx + 1]) / 50 if idx >= 49 else None
        ma150 = sum(k['close'] for k in klines[idx - 149:idx + 1]) / 150 if idx >= 149 else None
        ma200 = sum(k['close'] for k in klines[idx - 199:idx + 1]) / 200 if idx >= 199 else None

        # AD line (Chaikin) + 20日斜率（方法一：首尾对比）
        ad_values = []
        ad_line = 0
        for i, k in enumerate(klines):
            if k['high'] != k['low']:
                mfm = ((k['close'] - k['low']) - (k['high'] - k['close'])) / (k['high'] - k['low'])
            elif i > 0 and klines[i-1]['close'] > 0:
                # 一字板：用涨跌幅符号代替
                mfm = (k['close'] / klines[i-1]['close']) - 1
            else:
                mfm = 0
            ad_line += mfm * (k['volume'] or 0)
            ad_values.append(ad_line)

        ad_20d_ago = ad_values[idx - 20] if idx >= 20 else ad_values[0]
        # 斜率方向：>0 表示 A/D 线 20 日内上升
        ad_slope_positive = ad_values[idx] > ad_20d_ago
        # 显示值：线性回归斜率（更直观）
        if idx >= 20:
            ys = ad_values[idx - 19:idx + 1]
            n_pts = len(ys)
            xs = list(range(n_pts))
            xm = sum(xs) / n_pts; ym = sum(ys) / n_pts
            num = sum((xs[i] - xm) * (ys[i] - ym) for i in range(n_pts))
            den = sum((xs[i] - xm) ** 2 for i in range(n_pts))
            ad_slope_display = num / den if den != 0 else 0
        else:
            ad_slope_display = 0

        results.append({
            'code': code, 'close': close,
            'ret_20': ret_20, 'ret_60': ret_60, 'ret_120': ret_120, 'ret_250': ret_250,
            'ma50': ma50, 'ma150': ma150, 'ma200': ma200,
            'ad_line': ad_line, 'ad_slope_20d': ad_slope_positive,
        })

    # ── 计算RS百分位排名（池内独立计算，0-99） ──
    import yaml
    with open(os.path.join(PROJECT_DIR, "config", "index_style.yaml"), encoding='utf-8') as f:
        idx_cfg = yaml.safe_load(f)
    pools = {}
    for cat_name, items in idx_cfg.get("categories", {}).items():
        pools[cat_name] = [item["code"] for item in items]

    for pool_name, pool_codes in pools.items():
        pool_set = set(pool_codes)
        pool_results = [r for r in results if r['code'] in pool_set]
        for period in ['20', '60', '120', '250']:
            key = 'ret_' + period
            rs_key = 'rs_' + period
            vals = sorted([r[key] for r in pool_results if r[key] is not None], reverse=True)
            if len(vals) < 2:
                continue
            n = len(vals)
            for r in pool_results:
                if r[key] is None:
                    continue
                # 计算高于自己的个数（不包括自己）
                beats = sum(1 for v in vals if v < r[key])
                r[rs_key] = round(beats / (n - 1) * 99)

    # ── 写入 ──
    conn.execute("DELETE FROM index_rs_daily WHERE date = ?", (target_date,))
    for r in results:
        conn.execute("""
            INSERT INTO index_rs_daily (stock_code, date, close, ret_20, ret_60, ret_120, ret_250,
                rs_20, rs_60, rs_120, rs_250, ma50, ma150, ma200, ad_line, ad_slope_20d)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (r['code'], target_date, r['close'],
              r['ret_20'], r['ret_60'], r['ret_120'], r['ret_250'],
              r.get('rs_20'), r.get('rs_60'), r.get('rs_120'), r.get('rs_250'),
              r['ma50'], r['ma150'], r['ma200'],
              r['ad_line'], r['ad_slope_20d']))

    conn.commit()
    conn.close()
    logger.info(f"  [OK] {len(results)} 个指数已写入 index_rs_daily")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="指数RS强度计算")
    parser.add_argument("--date", type=str, default=None)
    args = parser.parse_args()
    target = args.date or dt_date.today().strftime("%Y-%m-%d")
    ensure_table()
    compute(target)
