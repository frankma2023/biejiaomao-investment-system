#!/usr/bin/env python3
"""
scripts/fetch_kline_multi_adjust.py — 理杏仁四种价格口径全量下载，写入 daily_kline

背景
----
daily_kline.close 历史上混用了三种口径（1996-01-02~2016-09-30 不复权 /
2016-10-10~2018-12-31 前复权 / 2019-01-02 起不复权），导致跨年代回测出现
市场级台阶。本脚本按理杏仁 API 提供的四种口径，从 2016-01-01 起全量重下，
在 daily_kline 上并列开列，让下游程序按需取用，消除 close 的口径歧义。

四种口径的实测性质（务必看）
--------------------------
  1. ex_rights（不复权）        ground truth，原始成交价。乘法语义正确。
  2. lxr_fc_rights（理杏仁前复权）乘法前复权。k=fc/ex 是阶梯常数，只在除权日跳变。
                                ✅ 唯一可直接用于收益计算的前复权口径。
  3. fc_rights（前复权）        理杏仁实现为「加法分红」口径：复权价 = 原价 − 累计现金
                                分红。不是乘法因子，价格比值 != 真实收益。锚点拉远时
                                会算出负数（600309 在 2016 年为 -0.81）。⚠️ 仅作留档。
  4. bc_rights（后复权）        同为「加法分红」口径：复权价 = 原价 × 送转因子 + 累计
                                现金分红。其 change 字段与自身价格比值不符。⚠️ 仅作留档。

写入方案
-------
  daily_kline 新增 16 列（四口径各 o/h/l/c）：
      ex_open/ex_high/ex_low/ex_close            不复权
      lxr_fc_open/lxr_fc_high/lxr_fc_low/lxr_fc_close   理杏仁前复权
      fc_open/fc_high/fc_low/fc_close            前复权（加法，留档）
      bc_open/bc_high/bc_low/bc_close            后复权（加法，留档）
  并把 2016-01-01 起的 open/high/low/close 重写为 ex_rights（不复权），
  使 close 全表单一语义（可用 --no-close-rewrite 关闭重写）。
  2016-01-01 之前的行 close 本来就是不复权，用一条 UPDATE 回填 ex_* 即可，无需 API 调用。

锚点常量（固定，避免每次重拉整条序列平移）
----------------------------------------
  adjustForwardDate  = 2026-12-31   （lxr_fc_rights / fc_rights，须 > endDate）
  adjustBackwardDate = 1996-01-02   （bc_rights，须 <= startDate，固定尺度）

执行效率与并发限制
----------------
  · 规模：6,167 只（2016-01-01 有K线的股票）× 4 口径 × 2 窗口 ≈ 49,336 次 API 调用
  · 限流：scripts/common.py 模块级单例 RateLimiter(max_requests=10, window=2.0)
          = 全进程 5 req/s 上限（线程安全）。这是硬瓶颈。
          实测单请求 0.3~1.0s；2 窗口各 2400 个交易日左右。
  · 实测吞吐：240 任务 / 265,520 行 / 55 秒（8 线程）= 4.4 任务/秒，已贴近限流天花板，
          DB 写入被 8 线程掩盖，不是瓶颈。
  · 预计耗时：49,336 ÷ 4.4 ≈ 11,200s ≈ 3.1 小时
  · 线程数：默认 8。因为限流器全局串行，线程只用于掩盖网络延迟，
          超过 8 线程不再有收益（--threads 12 亦可，不会有大幅提升）。
  · DB 写入：约 4,600 万次 upsert（4 口径各自覆盖 1,157 万行），WAL 模式下
          单写连接 + 锁保护；库体积预计增加 1.2~1.6 GB。
  · ⚠️ 理杏仁 API 在深夜 23:30-00:00 严重降速（单请求 5~25s、频繁超时），
          全量拉取请在白天执行。
  · 断点续传：daily_kline_multi_progress 按 (股票, 口径, 窗口) 登记完成状态，
          中断后重跑自动跳过已完成项。

用法
----
  先小样验证（约 1 分钟）：
      python scripts/fetch_kline_multi_adjust.py --stocks 600309,002648 --types ex,lxr_fc
  只跑一段区间（推荐先试，看效果再跑全量）：
      # 试 2016-2018 污染段（close 重写的核心收益区）
      python scripts/fetch_kline_multi_adjust.py --stocks 600309,002648,000338 --start 2016-01-01 --end 2018-12-31
      # 试最近一年，30 只
      python scripts/fetch_kline_multi_adjust.py --limit 30 --start 2025-09-01
  正式全量（约 3 小时，建议挂着跑）：
      python scripts/fetch_kline_multi_adjust.py
  只补不复权（不碰 fc/bc 留档列）：
      python scripts/fetch_kline_multi_adjust.py --types ex
  校验：
      python scripts/fetch_kline_multi_adjust.py --verify

  ⚠️ 区间参数只限制 API 拉取范围，close 重写也只在该范围内生效。
     进度表按 (股票, 口径, 起始日, 结束日) 登记，试跑过的区间不会被全量重跑视为已完成
     （窗口 key 不同），全量时会重拉一次，不影响正确性。
  参数：
      --stocks a,b,c   指定股票（自动 zfill 6）
      --types ex,lxr_fc,fc,bc   指定口径，默认全部
      --threads N      并发线程，默认 8
      --limit N        只跑前 N 只（按 stock_code 排序）
      --start / --end  覆盖默认窗口边界（YYYY-MM-DD）
      --no-close-rewrite  不把 open/high/low/close 重写为不复权
      --no-pre2016-ex     不回填 2016-01-01 前的 ex_* 列
      --reset          清空进度表后重跑（不删已有数据）
      --verify         只做数据校验，不下载
"""

import argparse
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import api_post, log, DB_PATH  # noqa: E402

# ── 配置 ──────────────────────────────────────────────
API_PATH = "/company/candlestick"

FWD_ANCHOR = "2026-12-31"   # adjustForwardDate：须 > endDate
BC_ANCHOR = "1996-01-02"    # adjustBackwardDate：须 <= startDate（固定尺度）
DATA_START = "2016-01-01"   # 本脚本默认起点（也是 pre-2016 ex_* 回填的边界）
REWRITE_FROM = "2016-10-10"  # close 开始被前复权污染的日期
REWRITE_TO = "2018-12-31"    # 污染段结束（2019-01-02 起 close 本就是不复权）
SNAPSHOT_PATH = "analysis/_close_snapshot_pre_multi_full.csv"
WINDOW_YEARS = 9            # 窗口步长：API 限制 startDate~endDate 跨度 ≤10 年，留余量

# 口径 -> (理杏仁 type, 锚点参数名, 锚点值)
CALIBERS = {
    "ex":     ("ex_rights",     None, None),
    "lxr_fc": ("lxr_fc_rights", "adjustForwardDate", FWD_ANCHOR),
    "fc":     ("fc_rights",     "adjustForwardDate", FWD_ANCHOR),
    "bc":     ("bc_rights",     "adjustBackwardDate", BC_ANCHOR),
}
CALIBER_NAMES = {
    "ex": "不复权", "lxr_fc": "理杏仁前复权", "fc": "前复权(加法)", "bc": "后复权(加法)",
}
ALL_TYPES = ["ex", "lxr_fc", "fc", "bc"]

OHLC = ("open", "high", "low", "close")
COLS = {t: [f"{t}_{f}" for f in OHLC] for t in ALL_TYPES}

PROGRESS_DDL = """
CREATE TABLE IF NOT EXISTS daily_kline_multi_progress (
    stock_code TEXT NOT NULL,
    adj_type   TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date   TEXT NOT NULL,
    rows       INTEGER,
    ts         TEXT,
    PRIMARY KEY (stock_code, adj_type, start_date, end_date)
)
"""

# ── 全局状态 ──────────────────────────────────────────
_db_lock = threading.Lock()
_conn = None
_stat_lock = threading.Lock()
_stat = {"ok": 0, "fail": 0, "rows": 0, "skip": 0}
_failed = []          # 失败任务清单，收尾时汇总输出


# ════════════════════════════════════════════════════════
# 数据库
# ════════════════════════════════════════════════════════

def open_db() -> sqlite3.Connection:
    """专用连接：允许跨线程，配合 _db_lock 使用"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_columns(conn: sqlite3.Connection) -> list:
    """幂等 ALTER TABLE 补齐 16 个口径列，返回实际新增的列名"""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(daily_kline)")}
    added = []
    for t in ALL_TYPES:
        for c in COLS[t]:
            if c not in have:
                conn.execute(f"ALTER TABLE daily_kline ADD COLUMN {c} REAL")
                added.append(c)
    if added:
        conn.commit()
    return added


def stock_universe(conn: sqlite3.Connection, start: str) -> list:
    rows = conn.execute(
        "SELECT DISTINCT stock_code FROM daily_kline WHERE date >= ? ORDER BY stock_code",
        (start,),
    ).fetchall()
    return [r["stock_code"] for r in rows]


def done_windows(conn: sqlite3.Connection) -> set:
    rows = conn.execute(
        "SELECT stock_code, adj_type, start_date, end_date FROM daily_kline_multi_progress"
    ).fetchall()
    return {(r["stock_code"], r["adj_type"], r["start_date"], r["end_date"]) for r in rows}


def mark_done(conn, stock_code, adj_type, s, e, rows):
    conn.execute(
        "INSERT OR REPLACE INTO daily_kline_multi_progress "
        "(stock_code, adj_type, start_date, end_date, rows, ts) VALUES (?,?,?,?,?,?)",
        (stock_code, adj_type, s, e, rows, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def upsert(conn, stock_code, klines, adj_type, rewrite_close):
    """把一个窗口的某口径 K 线 upsert 进 daily_kline"""
    cols = COLS[adj_type]
    sql_cols = ", ".join(cols)
    ph = ", ".join("?" * (2 + len(cols)))
    sets = ", ".join(f"{c}=excluded.{c}" for c in cols)
    if adj_type == "ex" and rewrite_close:
        # 不复权同时回写主价格列，使 close 全表单一语义
        sets += ", " + ", ".join(f"{f}=excluded.ex_{f}" for f in OHLC)
    sql = (f"INSERT INTO daily_kline (stock_code, date, {sql_cols}) VALUES ({ph}) "
           f"ON CONFLICT(stock_code, date) DO UPDATE SET {sets}")

    rows = []
    for k in klines:
        d = k.get("date")
        if not d:
            continue
        rows.append((k.get("stockCode") or stock_code, d[:10],
                     k.get("open"), k.get("high"), k.get("low"), k.get("close")))
    if not rows:
        return 0
    with _db_lock:
        conn.executemany(sql, rows)
        conn.commit()
    return len(rows)


# ════════════════════════════════════════════════════════
# 抓取
# ════════════════════════════════════════════════════════

def build_windows(start: str, end: str) -> list:
    """按 API 限制（startDate~endDate 跨度 ≤10 年）自动切分窗口

    默认 start=2016-01-01 时切出 [2016-01-01, 2025-12-31] + [2026-01-01, today]，
    与历史进度表的 key 一致；传更早的 start 也会自动按 9 年步长滚动切分。
    """
    wins = []
    cur = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    while cur <= end_dt:
        seg_end = min(datetime(cur.year + WINDOW_YEARS, 12, 31), end_dt)
        wins.append((cur.strftime("%Y-%m-%d"), seg_end.strftime("%Y-%m-%d")))
        if seg_end >= end_dt:
            break
        cur = datetime(seg_end.year + 1, 1, 1)
    return wins


def fetch_one(stock, adj_type, start, end, retries=3):
    api_type, anchor_key, anchor_val = CALIBERS[adj_type]
    payload = {"type": api_type, "stockCode": stock, "startDate": start, "endDate": end}
    if anchor_key:
        payload[anchor_key] = anchor_val
    last = None
    for i in range(retries):
        try:
            data = api_post(API_PATH, payload, timeout=180)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(str(data["error"])[:160])
            return []
        except Exception as e:  # noqa: BLE001
            last = e
            msg = str(e)
            if "ForbiddenError" in msg or "HTTP 400" in msg:
                break  # 参数/权限问题，重试无意义
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(str(last)[:160])


def run_task(stock, adj_type, s, e, rewrite_close, conn, done, counter):
    if (stock, adj_type, s, e) in done:
        with _stat_lock:
            _stat["skip"] += 1
        return
    with _stat_lock:
        counter[0] += 1
        n_done = counter[0]
        if n_done % 200 == 0:
            log.info(f"  已处理 {n_done}  ok={_stat['ok']} rows={_stat['rows']:,} fail={_stat['fail']}")
    try:
        klines = fetch_one(stock, adj_type, s, e)
        n = upsert(conn, stock, klines, adj_type, rewrite_close)
        with _db_lock:
            mark_done(conn, stock, adj_type, s, e, n)
        with _stat_lock:
            _stat["ok"] += 1
            _stat["rows"] += n
    except Exception as ex:  # noqa: BLE001
        with _stat_lock:
            _stat["fail"] += 1
            _failed.append((stock, adj_type, s, e, str(ex)[:120]))
        log.warning(f"❌ {stock} {adj_type} {s}~{e}: {ex}")


# ════════════════════════════════════════════════════════
# 快照与校验
# ════════════════════════════════════════════════════════

def snapshot_close(conn: sqlite3.Connection, path: str) -> int:
    """重写 close 前导出退路：把真正会被改写的行（2016-10-10~2018-12-31，ex_close 仍为 NULL）落成 CSV。

    只导出这个窗口，因为它是 close 唯一被前复权污染的一段；窗口之外 rewrite 前后数值相同。
    重写后 close == ex_close，旧值永久丢失，所以这一步不可跳过。幂等：文件已存在则跳过。
    """
    import csv
    if os.path.exists(path):
        log.info(f"📦 close 快照已存在，跳过：{path}")
        return 0
    n = conn.execute("SELECT COUNT(*) FROM daily_kline WHERE date >= ? AND date <= ? "
                     "AND ex_close IS NULL", (REWRITE_FROM, REWRITE_TO)).fetchone()[0]
    if not n:
        log.info("📦 close 快照：无待改写的行，跳过")
        return 0
    cur = conn.execute("SELECT stock_code, date, open, high, low, close FROM daily_kline "
                       "WHERE date >= ? AND date <= ? AND ex_close IS NULL "
                       "ORDER BY stock_code, date", (REWRITE_FROM, REWRITE_TO))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["stock_code", "date", "open", "high", "low", "close"])
        while True:
            chunk = cur.fetchmany(200000)
            if not chunk:
                break
            w.writerows(chunk)
    log.info(f"📦 close 快照已导出 {n:,} 行 → {path}")
    return n


def verify(conn, samples=("600309", "002648", "000338", "300750", "601088")):
    print("\n" + "=" * 78)
    print("校验：四口径覆盖度 / 前复权阶梯性 / 加法口径偏差")
    print("=" * 78)
    for t in ALL_TYPES:
        c = COLS[t][3]
        row = conn.execute(
            f"SELECT COUNT(*) n, MIN(date) a, MAX(date) b FROM daily_kline "
            f"WHERE date>='{DATA_START}' AND {c} IS NOT NULL").fetchone()
        print(f"  {t:7s} {CALIBER_NAMES[t]:14s} 非空 {row['n']:>12,} 行  {row['a']} ~ {row['b']}")

    print("\n  [按年覆盖] 总行数 / ex_close 非空 / lxr_fc_close 非空 / close≠ex_close（残留混用）")
    for r in conn.execute(
            "SELECT substr(date,1,4) y, COUNT(*) n, "
            "SUM(CASE WHEN ex_close IS NOT NULL THEN 1 ELSE 0 END) ex, "
            "SUM(CASE WHEN lxr_fc_close IS NOT NULL THEN 1 ELSE 0 END) fq, "
            "SUM(CASE WHEN ex_close IS NOT NULL AND abs(ex_close-close)>1e-6 THEN 1 ELSE 0 END) df "
            "FROM daily_kline WHERE date>=? GROUP BY y ORDER BY y", (DATA_START,)):
        flag = "  ⚠ 未统一" if r["df"] else ""
        print(f"    {r['y']}  总 {r['n']:>9,}   ex {r['ex']:>9,}   lxr_fc {r['fq']:>9,}   "
              f"close≠ex {r['df']:>8,}{flag}")

    print("\n  [前复权阶梯性] k = lxr_fc_close / close，除权日之外应恒定")
    for code in samples:
        rows = conn.execute(
            "SELECT date, close, lxr_fc_close, fc_close, bc_close FROM daily_kline "
            "WHERE stock_code=? AND date>='2016-01-01' AND lxr_fc_close IS NOT NULL "
            "ORDER BY date", (code,)).fetchall()
        if len(rows) < 10:
            print(f"    {code}: 数据不足")
            continue
        jumps = 0
        prev = None
        for r in rows:
            if r["close"]:
                k = r["lxr_fc_close"] / r["close"]
                if prev is not None and abs(k / prev - 1) > 0.001:
                    jumps += 1
                prev = k
        neg = sum(1 for r in rows if (r["fc_close"] or 0) < 0)
        print(f"    {code}: {len(rows):>4} 行  lxr_fc 跳变 {jumps:>3} 次   "
              f"fc_close 负值 {neg:>4} 行   bc_close 末值 {rows[-1]['bc_close']}")
    print()


# ════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--stocks")
    ap.add_argument("--types", default=",".join(ALL_TYPES))
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--start", default=DATA_START)
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--no-close-rewrite", action="store_true")
    ap.add_argument("--no-snapshot-close", action="store_true",
                    help="不在重写前导出 close 退路快照（默认导出）")
    ap.add_argument("--no-pre2016-ex", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    types = [t.strip() for t in args.types.split(",") if t.strip()]
    bad = [t for t in types if t not in CALIBERS]
    if bad:
        log.error(f"未知口径 {bad}，可选：{ALL_TYPES}")
        return 1

    conn = open_db()
    global _conn
    _conn = conn

    if args.verify:
        verify(conn)
        conn.close()
        return 0

    added = ensure_columns(conn)
    conn.execute(PROGRESS_DDL)
    conn.commit()
    if added:
        log.info(f"✅ daily_kline 新增列 {len(added)} 个: {', '.join(added)}")

    # 重写 close 不可逆（新值覆盖旧值），动手前先导出退路快照
    if not args.no_close_rewrite and not args.no_snapshot_close:
        snapshot_close(conn, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), SNAPSHOT_PATH))

    if args.reset:
        conn.execute("DELETE FROM daily_kline_multi_progress")
        conn.commit()
        log.info("🗑 进度表已清空")

    # 2016 之前的 ex_* 回填（无需 API：那段 close 本就是不复权）
    # 边界固定为 min(start, DATA_START)：即使只跑 2024 一段，也不能拿 2016-2023 的
    # close 去填 ex_*（那段 close 可能是前复权口径，会污染 ex_*）。
    cut = min(args.start, DATA_START)
    if not args.no_pre2016_ex:
        n = conn.execute(
            "SELECT COUNT(*) FROM daily_kline WHERE date < ? AND ex_close IS NULL",
            (cut,)).fetchone()[0]
        if n:
            log.info(f"↩️  回填 {cut} 前 ex_* 列（{n:,} 行，源自 close 不复权值）…")
            with _db_lock:
                conn.execute(
                    "UPDATE daily_kline SET ex_open=open, ex_high=high, ex_low=low, "
                    "ex_close=close WHERE date < ? AND ex_close IS NULL", (cut,))
                conn.commit()

    stocks = ([s.strip().zfill(6) for s in args.stocks.split(",") if s.strip()]
              if args.stocks else stock_universe(conn, args.start))
    if args.limit:
        stocks = stocks[:args.limit]

    windows = build_windows(args.start, args.end)
    tasks = [(s, t, w[0], w[1]) for s in stocks for t in types for w in windows]
    done = done_windows(conn)
    todo = [t for t in tasks if (t[0], t[1], t[2], t[3]) not in done]

    est = len(todo) / 5.0
    log.info("─" * 70)
    log.info(f"股票 {len(stocks)} 只 × 口径 {types} × 窗口 {windows}")
    log.info(f"任务 {len(tasks):,} 个，已完成 {len(tasks) - len(todo):,}，待跑 {len(todo):,}")
    log.info(f"API 并发上限 5 req/s（common.py 全局限流器），线程 {args.threads}")
    log.info(f"预计耗时 ≈ {est / 60:.0f} 分钟（{est / 3600:.1f} 小时）")
    log.info(f"close 重写为不复权：{'❌ 否' if args.no_close_rewrite else '✅ 是'}")
    log.info("─" * 70)

    if not todo:
        log.info("✅ 无待跑任务")
        verify(conn)
        conn.close()
        return 0

    counter = [0]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futs = [pool.submit(run_task, *t, not args.no_close_rewrite, conn, done, counter)
                for t in todo]
        for f in as_completed(futs):
            f.result()
    dt = time.time() - t0

    log.info("─" * 70)
    log.info(f"🏁 完成：成功 {_stat['ok']:,} / 失败 {_stat['fail']:,} / 跳过 {_stat['skip']:,}"
             f"  写入 {_stat['rows']:,} 行  耗时 {dt / 60:.1f} 分钟")
    if _failed:
        log.warning(f"失败清单（{len(_failed)} 项，未登记进度，重跑本脚本会自动重试）：")
        for s, t, sd, ed, err in _failed[:30]:
            log.warning(f"   {s} {t} {sd}~{ed}  {err}")
        if len(_failed) > 30:
            log.warning(f"   …其余 {len(_failed) - 30} 项略")
        by_stock = {}
        for s, t, sd, ed, _ in _failed:
            by_stock[s] = by_stock.get(s, 0) + 1
        log.warning("   失败股票：" + ', '.join(
            f"{s}({n}项)" for s, n in sorted(by_stock.items(), key=lambda x: -x[1])[:15]))

    verify(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
