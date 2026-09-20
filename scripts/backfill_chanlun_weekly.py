# -*- coding: utf-8 -*-
"""周K线缠论笔全量回填（ISO 真周K + CZSC Freq.W 真周线笔）

产出表：
- chanlun_weekly_bi_json(stock_code, scan_date, bi_json, WEEKLY_ALGO_VERSION)：
  每周一行「当时可见」的周线笔快照（scan_date=周代表日=该周最后交易日）。
  PK(stock_code, scan_date)，写库为 DELETE+INSERT 覆盖写，幂等可断点续跑。
  algo_version='czsc101_w'：增量续跑只跳过已完成的周（镜像日线 chanlun_scan_daily 做法）。

用法：
  python scripts/backfill_chanlun_weekly.py --start 2016-01-01 --end 2026-09-19 --workers 8
  python scripts/backfill_chanlun_weekly.py --incremental          # 跳过已有 czsc101_w 周
  python scripts/backfill_chanlun_weekly.py --codes 688432,600309  # 指定股票（验证用）
  python scripts/backfill_chanlun_weekly.py --purge                # 清表全量重跑
  python scripts/backfill_chanlun_weekly.py --stats                # 仅看统计

设计（参考 scripts/backfill_chanlun.py by-stock 范式）：
- by-stock 分片：每只股票 1 次加载日线 → ISO 聚合周K → CZSC 逐根 update，
  每个目标周用截至该周的笔列表产出（防未来函数）。
- 增量：按 (code, algo_version='czsc101_w') 已完成周过滤，天然断点续跑。
- 多进程：ProcessPoolExecutor + as_completed，worker 直接写库（单事务 busy 重试），
  主进程只收 (code, saved, err) 小结果。
- 失败清单落盘 logs/chanlun_weekly_failed_codes.txt
"""

import argparse
import os
import sqlite3
import sys
import os
import time
from datetime import datetime

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT, 'src'))
sys.path.insert(0, PROJECT)

from scanners.chanlun_weekly import WEEKLY_ALGO_VERSION

DB = os.path.join(PROJECT, 'data', 'lixinger.db')
LOG_DIR = os.path.join(PROJECT, 'logs')

# 股票池：与日线缠论扫描一致的流动性过滤（全市场正常上市、非ST、近20日日均成交额≥5000万）
POOL_SQL = """
    SELECT DISTINCT k.stock_code
    FROM daily_kline_adj k
    INNER JOIN stock_basic b ON k.stock_code=b.stock_code
    WHERE b.listing_status='normally_listed'
      AND b.name NOT LIKE '%ST%'
      AND k.date >= date('now','-20 days')
    GROUP BY k.stock_code
    HAVING AVG(k.amount) >= 50000000
"""


def get_week_targets(conn, start, end):
    """区间内的周代表日列表 = 市场日历各周的「最大交易日」

    用上证指数日历（index_daily_kline 000001）确定交易周：某周只要有交易，
    该周就是交易周；代表日 = 该周内市场最大交易日。
    个股停牌整周时自身聚合无该周，回填时补 (d, None)，与日线回填行为一致。
    """
    rows = conn.execute("""
        SELECT date FROM index_daily_kline
        WHERE stock_code='000001' AND date>=? AND date<=?
        ORDER BY date
    """, (start, end)).fetchall()
    dates = [r[0] for r in rows]
    targets = []
    cur_key = None
    cur_rep = None
    for d in dates:
        dt = datetime.strptime(d, '%Y-%m-%d')
        iso = dt.isocalendar()
        gk = f"{iso[0]}-W{iso[1]:02d}"
        if gk != cur_key:
            if cur_rep is not None:
                targets.append(cur_rep)
            cur_key = gk
            cur_rep = d
        else:
            cur_rep = d
    if cur_rep is not None:
        targets.append(cur_rep)
    return targets


def save_stock_results(db_path, code, dates_all, results, max_retry=8):
    """单只股票全部目标周的结果写库（worker 内调用，单事务，busy 重试）

    results: [(rep_date, summary) or None]
    - DELETE 覆盖全部目标周旧行（幂等）
    - 写入 algo_version='czsc101_w'（增量续跑按 code+algo_version 过滤）
    - 返回: 成功写入的周数
    """
    import time as _time
    for attempt in range(max_retry):
        try:
            db = sqlite3.connect(db_path, timeout=60)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.executemany(
                    "DELETE FROM chanlun_weekly_bi_json WHERE stock_code=? AND scan_date=?",
                    [(code, d) for d in dates_all])
                for d, s in results:
                    if not s or not s.get('bi_json'):
                        continue
                    db.execute(
                        "INSERT OR REPLACE INTO chanlun_weekly_bi_json "
                        "(stock_code, scan_date, bi_json, WEEKLY_ALGO_VERSION) VALUES (?,?,?,?)",
                        (code, d, s['bi_json'], WEEKLY_ALGO_VERSION))
                db.commit()
                return len([s for _, s in results if s])
            finally:
                db.close()
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < max_retry - 1:
                _time.sleep(2 * (attempt + 1))
            else:
                raise
    return 0


def scan_stock_weekly_worker(args):
    """单只股票全历史周线扫描 + 直接写库

    args: (code, dates, incremental)
    incremental 时按 code 查已有 czsc101_w 周，只算缺失周（断点续跑）
    Returns: (code, saved_count, err_msg) 小结果
    """
    code, dates, incremental = args
    try:
        if incremental:
            db = sqlite3.connect(DB, timeout=30)
            rows = db.execute(
                "SELECT scan_date FROM chanlun_weekly_bi_json "
                "WHERE stock_code=? AND algo_version=?", (code, WEEKLY_ALGO_VERSION)).fetchall()
            db.close()
            done = {r[0] for r in rows}
            dates = [d for d in dates if d not in done]
            if not dates:
                return (code, 0, None)
        from scanners.chanlun_weekly import scan_stock_weekly_all
        res = scan_stock_weekly_all(code, dates)
        n = save_stock_results(DB, code, dates, res)
        return (code, n, None)
    except Exception as e:
        return (code, 0, f"{code}: {str(e)[:120]}")


def print_stats():
    db = sqlite3.connect(DB, timeout=30)
    try:
        n_stock, n_week = db.execute(
            "SELECT COUNT(DISTINCT stock_code), COUNT(*) FROM chanlun_weekly_bi_json").fetchone()
        dmin, dmax = db.execute(
            "SELECT MIN(scan_date), MAX(scan_date) FROM chanlun_weekly_bi_json").fetchone()
        print(f"chanlun_weekly_bi_json: {n_stock} 只 / {n_week} 周 / 范围 {dmin} ~ {dmax}")
        from scanners.chanlun_weekly import WEEKLY_ALGO_VERSION
        rows = db.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT stock_code FROM chanlun_weekly_bi_json)").fetchone()
        print(f"覆盖股票数: {rows[0]}（当前 ALGO_VERSION={WEEKLY_ALGO_VERSION}）")
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser(description='周K线缠论笔回填（ISO 真周K）')
    ap.add_argument('--start', default='2016-01-01')
    ap.add_argument('--end', default=None, help='默认=市场日历最新交易日')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--codes', default=None, help='逗号分隔股票代码（验证用）')
    ap.add_argument('--incremental', action='store_true', help='跳过已有 czsc101_w 周')
    ap.add_argument('--purge', action='store_true', help='清空周线笔表全量重跑')
    ap.add_argument('--stats', action='store_true', help='仅打印统计')
    a = ap.parse_args()

    if a.stats:
        print_stats()
        return

    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    from scanners.chanlun_weekly import ensure_tables
    ensure_tables(conn)
    # 确保表结构含 algo_version（新表建表已含；旧表补列）
    try:
        conn.execute("ALTER TABLE chanlun_weekly_bi_json ADD COLUMN algo_version TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()

    if a.purge:
        n = conn.execute("SELECT COUNT(*) FROM chanlun_weekly_bi_json").fetchone()[0]
        conn.execute("DELETE FROM chanlun_weekly_bi_json")
        conn.commit()
        print(f"已清空周线笔表（{n} 行）")

    end = a.end
    if not end:
        r = conn.execute("SELECT MAX(date) FROM index_daily_kline WHERE stock_code='000001'").fetchone()
        end = r[0]
    week_targets = get_week_targets(conn, a.start, end)
    if a.codes:
        codes = [c.strip().zfill(6) if c.strip().isdigit() and len(c.strip()) < 6
                 else c.strip() for c in a.codes.split(',') if c.strip()]
        ph = ','.join('?' * len(codes))
        rows = conn.execute(
            f"SELECT DISTINCT stock_code FROM daily_kline_adj WHERE stock_code IN ({ph})",
            tuple(codes)).fetchall()
        codes = [r[0] for r in rows]
    else:
        codes = [r[0] for r in conn.execute(POOL_SQL).fetchall()]
    conn.close()

    print(f"周线笔回填: {len(codes)} 只 × {len(week_targets)} 周 "
          f"({week_targets[0]} ~ {week_targets[-1]}) | {a.workers} 进程"
          + (" | 增量" if a.incremental else ""))
    print()

    os.makedirs(LOG_DIR, exist_ok=True)
    err_path = os.path.join(LOG_DIR, 'chanlun_weekly_failed_codes.txt')

    t0 = time.time()
    done = 0
    n_saved = 0
    err_codes = []
    tasks = [(c, week_targets, a.incremental) for c in codes]
    if a.workers <= 1 or len(tasks) < 8:
        for t in tasks:
            code, n, err = scan_stock_weekly_worker(t)
            done += 1
            n_saved += n
            if err:
                err_codes.append(err)
            if done % 50 == 0 or done == len(tasks):
                el = time.time() - t0
                print(f"  {done}/{len(tasks)} 只 | 写入 {n_saved} 周 | {el:.0f}s "
                      f"(ETA {el / done * (len(tasks) - done):.0f}s)", flush=True)
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(scan_stock_weekly_worker, t): t[0] for t in tasks}
            for fut in as_completed(futs):
                code, n, err = fut.result()
                done += 1
                n_saved += n
                if err:
                    err_codes.append(err)
                if done % 50 == 0 or done == len(tasks):
                    el = time.time() - t0
                    print(f"  {done}/{len(tasks)} 只 | 写入 {n_saved} 周 | {el:.0f}s "
                          f"(ETA {el / done * (len(tasks) - done):.0f}s)", flush=True)

    if err_codes:
        with open(err_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(err_codes))
        print(f"\n失败 {len(err_codes)} 只 → {err_path}")
        for e in err_codes[:10]:
            print(f"  {e}")
    else:
        if os.path.exists(err_path):
            os.remove(err_path)
    print(f"\n完成: {len(codes)} 只 | 写入 {n_saved} 周快照 | 失败 {len(err_codes)} | 耗时 {time.time() - t0:.0f}s")
    print_stats()


if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    main()
