"""
批量 CAN SLIM 评分 v2 — 独立引擎调用，不依赖 engine_registry

用法:
  python scripts/batch_canslim_score.py --force
  python scripts/batch_canslim_score.py --limit 100
  python scripts/batch_canslim_score.py --workers 4
"""

import os, sys, time, sqlite3, argparse
from datetime import datetime, timedelta, date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / 'src'))

from scanners.canslim_score import score_stock, load_params

DB_PATH = PROJECT_DIR / "data" / "lixinger.db"


def clean_klines(rows):
    """过滤掉 close/volume 为 None 的 K 线记录"""
    return [dict(r) for r in rows if r['close'] is not None and r['volume'] is not None]


def get_stock_klines(db, stock_code, target_date, lookback=500):
    """获取并清洗 K 线数据"""
    rows = db.execute(f"""SELECT date, open, high, low, close, volume FROM daily_kline
        WHERE stock_code='{stock_code}' AND date<='{target_date}' ORDER BY date DESC LIMIT {lookback}
    """).fetchall()
    rows = list(reversed(rows))
    return clean_klines(rows)


def get_breakout_signals(klines):
    """直接调 base_breakout，不经过 engine_registry"""
    try:
        from scanners.base_breakout import detect, load_params as load_bp
        params = load_bp()
        return detect(klines, params)
    except Exception:
        return []


def get_pocket_pivot_signals(klines):
    """直接调 pocket_pivot"""
    try:
        from scanners.pocket_pivot import detect, load_params as load_pp
        params = load_pp()
        return detect(klines=klines)
    except Exception:
        return []


def get_all_signals(klines):
    """获取 base_breakout + pocket_pivot + cdl/talib 信号"""
    signals = []
    # 基部突破
    for s in get_breakout_signals(klines):
        s['source'] = 'base_breakout'
        s['type'] = 'bullish'
        signals.append(s)
    # 口袋支点
    for s in get_pocket_pivot_signals(klines):
        s['source'] = 'pocket_pivot'
        s['type'] = 'bullish'
        signals.append(s)
    return signals


def score_one(code, name, target_date, params):
    """对单只股票评分并入库"""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    try:
        klines = get_stock_klines(db, code, target_date)
        if len(klines) < 50:
            return None

        signals = get_all_signals(klines)
        result = score_stock(code, target_date, params, save=True, signals=signals)
        return result
    except Exception as e:
        return None
    finally:
        db.close()


def get_stocks(limit=None):
    conn = sqlite3.connect(str(DB_PATH))
    q = """SELECT stock_code, name FROM stock_basic
        WHERE listing_status='normally_listed'
        AND name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%'
        ORDER BY stock_code"""
    if limit:
        q += " LIMIT %d" % limit
    rows = conn.execute(q).fetchall()
    conn.close()
    return [(r[0], r[1]) for r in rows]


def get_skip_set(days=7):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS cansim_scores (
        stock_code TEXT, date TEXT, score_c REAL, score_a REAL, score_n REAL,
        score_s REAL, score_l REAL, score_i REAL, raw_total REAL,
        score INTEGER, grade TEXT, detail_json TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (stock_code, date))""")
    conn.commit()
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    cur = conn.execute(
        "SELECT DISTINCT stock_code FROM cansim_scores WHERE date >= ?", (cutoff,))
    result = {r[0] for r in cur.fetchall()}
    conn.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--date', type=str, default=None)
    args = parser.parse_args()

    target_date = args.date or date.today().strftime("%Y-%m-%d")
    print(f"Date: {target_date}")

    stocks = get_stocks(args.limit or None)
    skip_set = set() if args.force else get_skip_set()
    to_score = [(c, n) for c, n in stocks if c not in skip_set]
    print(f"Stocks: {len(stocks)} total, {len(skip_set)} skip, {len(to_score)} to score")

    if not to_score:
        print("All stocks up to date.")
        return

    params = load_params()
    t0 = time.time()
    done = 0; scored = 0

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(score_one, c, n, target_date, params): c for c, n in to_score}
            for future in as_completed(futures):
                done += 1
                if future.result():
                    scored += 1
                if done % 50 == 0:
                    elapsed = time.time() - t0
                    print(f"  [{done}/{len(to_score)}] {scored} scored ({done/elapsed:.1f}/s)")
    else:
        for code, name in to_score:
            done += 1
            if score_one(code, name, target_date, params):
                scored += 1
            if done % 50 == 0:
                elapsed = time.time() - t0
                print(f"  [{done}/{len(to_score)}] {scored} scored ({done/elapsed:.1f}/s)")

    elapsed = time.time() - t0
    print(f"Done: {scored}/{len(to_score)} scored in {elapsed:.0f}s ({len(to_score)/elapsed:.1f}/s)")


if __name__ == '__main__':
    main()
