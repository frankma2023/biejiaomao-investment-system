#!/usr/bin/env python3
"""
scripts/fetch_stock_daily_kline.py — 个股日K线拉取（两种模式）

════════════════════════════════════════════════════════════════
模式一：基础日K线（默认，不加 --caliber）
════════════════════════════════════════════════════════════════
用理杏仁 date 参数单日拉全市场（一天 1 次调用），写入不复权价格。

    python scripts/fetch_stock_daily_kline.py                     # 增量：从表内最新日期+1天起
    python scripts/fetch_stock_daily_kline.py --full              # 全量：从 2000-01-01 起
    python scripts/fetch_stock_daily_kline.py --start 2024-01-01  # 指定起始
    python scripts/fetch_stock_daily_kline.py --start A --end B   # 指定区间

写入列：open / close / high / low / volume / amount / change_pct / turnover_rate / complex_factor
耗时：秒级（每个工作日 1 次调用）。不写 ex_*/lxr_fc_*/fc_*/bc_*。

════════════════════════════════════════════════════════════════
模式二：四口径增量（--caliber）
════════════════════════════════════════════════════════════════
"口径"指价格复权方式，这里四种：ex(不复权) / lxr_fc(理杏仁前复权) / fc(前复权) / bc(后复权)。

⚠️ date 单日查询带不上复权口径（type 被忽略），所以四口径只能逐个股票调。
   调用量 = 股票数 × 口径数，**与窗口长度无关**（拉 1 天和拉 30 天调用次数一样）。
   实测 4.8 次/秒 → 全市场约 6266 只 × 3 口径 ≈ 65 分钟。这不是卡死，是正常耗时。

    python scripts/fetch_stock_daily_kline.py --caliber                          # 全市场，约 65 分钟
    python scripts/fetch_stock_daily_kline.py --caliber --types lxr_fc           # 只补 lxr_fc，约 22 分钟
    python scripts/fetch_stock_daily_kline.py --caliber --stocks 600309,002648   # 指定股票（测试）
    python scripts/fetch_stock_daily_kline.py --caliber --limit 100              # 前 100 只（测试）
    python scripts/fetch_stock_daily_kline.py --caliber --only-missing           # 只补最新日仍缺的股票
    python scripts/fetch_stock_daily_kline.py --caliber --reset                  # 清空当日进度后重跑

可选参数：
    --types    要拉的口径，逗号分隔，默认 lxr_fc,fc,bc
               （ex 不在其中：close 已统一为不复权口径，ex_* 由本地回填，不耗 API）
    --window   每次回拉的交易日天数，默认 30。用于覆盖期间可能发生的除权
    --threads  并发线程，默认 8。限流器全局 5 req/s，加线程只是掩盖网络延迟
    --stocks   指定股票，逗号分隔（测试用）
    --limit    只跑前 N 只（测试用）

写入列：ex_open/high/low/close、lxr_fc_*、fc_*、bc_* 共 16 列（UPDATE 已有行，不建新行）
耗时：约 65 分钟。**没有干跑模式**，试跑请用 --stocks 或 --limit。

断点续传：进度写入表 daily_kline_caliber_progress（股票 + 口径 + 日期），
    同日重跑自动跳过已完成项；--only-missing 只补最新日仍缺口径的股票（适合被中断后快速补齐）；
    --reset 清空当日进度。

⚠️ 尺度对齐：前复权序列的绝对水平会随每次新除权整体平移。本模式默认做法是
   **发现新除权时把该股全历史按同一因子缩放**，使库内序列与理杏仁官方 / 通达信同尺度；
   代价是必须重写该股全历史（只有当日发生除权的股票才会触发，量小）。
   加 `--keep-scale` 则反过来把新数据缩放回旧尺度、历史保持不变，
   代价是库内绝对价位与理杏仁/通达信存在一个常数偏移（收益率同样正确）。

使用顺序（daily_update.py 步骤 5 与 5b）：
    先跑模式一建行，再跑模式二补口径列。顺序不能反，模式二只做 UPDATE。
"""

import sys
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from common import api_post, get_db, get_latest_date, log, RateLimiter, DB_PATH

# ── 配置 ──────────────────────────────────────────────
API_PATH = "/company/candlestick"

UPSERT_SQL = """INSERT INTO daily_kline
    (stock_code, date, open, close, high, low,
     volume, amount, change_pct, turnover_rate, complex_factor)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(stock_code, date) DO UPDATE SET
    open=excluded.open, close=excluded.close, high=excluded.high, low=excluded.low,
    volume=excluded.volume, amount=excluded.amount, change_pct=excluded.change_pct,
    turnover_rate=excluded.turnover_rate, complex_factor=excluded.complex_factor"""


# ════════════════════════════════════════════════════════

def fetch_date(date_str: str) -> list:
    """拉取单日全市场K线"""
    payload = {"date": date_str}
    return api_post(API_PATH, payload)


def save_klines(conn, klines: list):
    """批量写入K线到数据库"""
    if not klines:
        return 0
    rows = []
    for k in klines:
        rows.append((
            k["stockCode"],
            k["date"][:10],
            k.get("open"),
            k.get("close"),
            k.get("high"),
            k.get("low"),
            k.get("volume"),
            k.get("amount"),
            k.get("change"),
            k.get("to_r"),
            k.get("complexFactor"),
        ))
    conn.executemany(UPSERT_SQL, rows)
    conn.commit()
    return len(rows)


def generate_weekdays(start: str, end: str) -> list:
    """生成日期范围内所有工作日（周一~周五）"""
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    days = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:
            days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return days


def parse_date_arg(argv: list, flag: str) -> str | None:
    """从命令行参数提取日期值，例如 --start 2024-01-01"""
    try:
        idx = argv.index(flag)
        return argv[idx + 1]
    except (ValueError, IndexError):
        return None


def main():
    conn = get_db()
    full_mode = "--full" in sys.argv
    arg_start = parse_date_arg(sys.argv, "--start")
    arg_end = parse_date_arg(sys.argv, "--end")

    if arg_start:
        start_date = arg_start
        log.info(f"📅 指定起始日: {start_date}")
    elif full_mode:
        start_date = "2000-01-01"
        log.info("🔁 全量模式：从 2000-01-01 开始拉取")
    else:
        latest = get_latest_date(conn, "daily_kline") or "2000-01-01"
        start = datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)
        start_date = start.strftime("%Y-%m-%d")
        log.info(f"📅 增量模式：最新数据日期 {latest}，从 {start_date} 开始")

    end_date = arg_end or datetime.now().strftime("%Y-%m-%d")
    weekdays = generate_weekdays(start_date, end_date)

    if not weekdays:
        log.info("✅ 没有需要拉取的日期，数据已最新")
        conn.close()
        return

    log.info(f"📊 共 {len(weekdays)} 个工作日待拉取")

    total_klines = 0
    trading_days = 0

    for i, date_str in enumerate(weekdays, 1):
        try:
            klines = fetch_date(date_str)
            if klines:
                n = save_klines(conn, klines)
                total_klines += n
                trading_days += 1
                log.info(f"[{i}/{len(weekdays)}] {date_str} ✅ 交易日，{n} 条K线")
            else:
                log.info(f"[{i}/{len(weekdays)}] {date_str} ⏭ 非交易日，跳过")
        except Exception as e:
            log.error(f"[{i}/{len(weekdays)}] {date_str} ❌ 失败: {e}")
            # 继续下一个日期，不中断

    log.info(f"🏁 完成: {trading_days} 个交易日，{total_klines} 条K线")

    # 打印汇总
    if trading_days > 0:
        row = conn.execute("SELECT COUNT(*) as cnt FROM daily_kline").fetchone()
        log.info(f"   daily_kline 表总计: {row['cnt']:,} 条记录")

    conn.close()


# ══════════════════════════════════════════════════════════════════
# 四口径增量拉取（--caliber）
# ══════════════════════════════════════════════════════════════════
# 背景：date 单日全市场查询**带不上复权口径**（type 被忽略），
#       所以四口径只能逐个股票调（API 强制要求 stockCode，不支持批量）。
#
# ⚠️ 关键：前复权序列的绝对水平依赖「基准日当时已知的除权事件集合」，
#    每发生一次新除权，整个历史序列会整体平移一个因子。因此**不能裸追加**，
#    否则新旧数据之间会出现一个等于该次分红率的尺度接缝，
#    跨接缝的收益率会凭空多出一次跌幅。
#    做法：每次回拉一个窗口，用**重叠日对齐**把新数据缩放到已存序列的尺度上。
#
# 调用量：股票数 × 口径数（与窗口长度无关）。ex_* 本地推导不耗调用。
FWD_ANCHOR = '2026-12-31'
BC_ANCHOR = '1996-01-02'
CALIBERS = {
    'lxr_fc': ('lxr_fc_rights', {'adjustForwardDate': FWD_ANCHOR}),
    'fc':     ('fc_rights',     {'adjustForwardDate': FWD_ANCHOR}),
    'bc':     ('bc_rights',     {'adjustBackwardDate': BC_ANCHOR}),
}
CAL_COLS = {t: [f'{t}_{f}' for f in ('open', 'high', 'low', 'close')] for t in CALIBERS}
DEFAULT_WINDOW = 30        # 每次回拉天数（覆盖期间可能发生的除权）

_db_lock = threading.Lock()
_stat = {'ok': 0, 'fail': 0, 'rows': 0, 'skip': 0, 'rescaled': 0, 'refetched': 0}
_failed = []

# True = 发现新除权时把全历史缩放到 API 当前尺度（与理杏仁官方 / 通达信一致）
# False = 保留已存序列的尺度（会产生一个常数偏移，但收益率同样正确）
_RESCALE_ALL = True

# 断点续传：长任务（65 分钟）被超时/中断后，重跑能跳过当天已完成的 (股票, 口径)
PROGRESS_DDL = """
CREATE TABLE IF NOT EXISTS daily_kline_caliber_progress (
    stock_code TEXT NOT NULL,
    adj_type   TEXT NOT NULL,
    sync_date  TEXT NOT NULL,
    rows       INTEGER,
    ts         TEXT,
    PRIMARY KEY (stock_code, adj_type, sync_date)
)
"""


def caliber_db():
    """线程专用连接"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    return conn


def backfill_ex(conn):
    """ex_* ← open/high/low/close

    自 2026-09 起 close 已统一为不复权口径，与 ex_* 同义，回填是等值操作。
    这样 ex_* 完全不耗 API 调用。
    """
    # 只看近期窗口：全表 COUNT 在 1900 万行的视图上要跑 2 分钟，而且只关心新数据
    n = conn.execute("""SELECT COUNT(*) FROM daily_kline
                         WHERE ex_close IS NULL AND close IS NOT NULL
                           AND date >= date((SELECT MAX(date) FROM daily_kline), '-400 days')"""
                     ).fetchone()[0]
    if not n:
        log.info("  ex_* 无需回填")
        return 0
    with _db_lock:
        conn.execute("""UPDATE daily_kline SET ex_open=open, ex_high=high,
                        ex_low=low, ex_close=close
                        WHERE ex_close IS NULL AND close IS NOT NULL""")
        conn.commit()
    log.info(f"  ✅ ex_* 已回填 {n:,} 行（本地推导，无 API 调用）")
    return n


def fetch_caliber(code, typ, start, end, retries=3):
    api_type, extra = CALIBERS[typ]
    payload = {'type': api_type, 'stockCode': code, 'startDate': start, 'endDate': end}
    payload.update(extra)
    last = None
    for i in range(retries):
        try:
            d = api_post(API_PATH, payload, timeout=180)
            if isinstance(d, list):
                return d
            if isinstance(d, dict) and 'error' in d:
                raise RuntimeError(str(d['error'])[:140])
            return []
        except Exception as e:  # noqa: BLE001
            last = e
            msg = str(e)
            if 'ForbiddenError' in msg or 'ValidationError' in msg:
                break
            import time as _t
            _t.sleep(1.5 * (i + 1))
    raise RuntimeError(str(last)[:140])


def run_one(conn, code, typ, window):
    cols = CAL_COLS[typ]
    close_col = f'{typ}_close'
    end = datetime.now().strftime('%Y-%m-%d')
    end_dt = datetime.strptime(end, '%Y-%m-%d')
    limit_start = (end_dt - timedelta(days=int(365.25 * 9))).strftime('%Y-%m-%d')

    with _db_lock:
        anchor = conn.execute(
            f"SELECT date, {close_col} v FROM daily_kline WHERE stock_code=? "
            f"AND {close_col} IS NOT NULL ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        first = conn.execute("SELECT MIN(date) d FROM daily_kline WHERE stock_code=?",
                             (code,)).fetchone()

    if anchor and anchor['date']:
        a_date = anchor['date']
        start = (datetime.strptime(a_date, '%Y-%m-%d')
                 - timedelta(days=window)).strftime('%Y-%m-%d')
    else:
        # 无历史口径数据（新股 / 早期退市股）：从该股最早 K 线起，但不超过 10 年限制
        a_date = None
        start = (first['d'] if first and first['d'] else limit_start)
    start = max(start, limit_start)          # API 限制：区间跨度 ≤ 10 年

    data = fetch_caliber(code, typ, start, end)
    if not data:
        return 0

    # 重叠日对齐：把新数据缩放到已存序列的尺度
    scale = 1.0
    if anchor and anchor['v']:
        for x in data:
            if str(x.get('date') or '')[:10] == a_date and x.get('close'):
                scale = anchor['v'] / x['close']
                break
        else:
            # 锚点日不在窗口内（停牌等）→ 用窗口内任意重叠日
            with _db_lock:
                for x in sorted(data, key=lambda y: str(y.get('date') or ''), reverse=True):
                    d0 = str(x.get('date') or '')[:10]
                    if not d0 or not x.get('close'):
                        continue
                    r = conn.execute(f"SELECT {close_col} v FROM daily_kline "
                                     f"WHERE stock_code=? AND date=?", (code, d0)).fetchone()
                    if r and r['v']:
                        scale = r['v'] / x['close']
                        break

    # scale ≠ 1 说明期间发生了新除权/配股等事件（前复权序列被理杏仁整体重算）。
    # 默认做法：**把该股整条序列重新下一次，直接写入接口原值**，不做任何推导。
    # （接口自己会回算，这是唯一不会出错的做法；配股这类非线性事件下，
    #   用常数因子缩放会算出错值且静默无声。）
    # 加 --keep-scale 则保留旧尺度（把新数据缩放回旧序列，历史不动）。
    if _RESCALE_ALL and anchor and anchor['v'] and abs(scale - 1.0) > 1e-9:
        first = conn.execute("SELECT MIN(date) d FROM daily_kline WHERE stock_code=?",
                             (code,)).fetchone()
        # 起点取该股实际最早日期：口径列已覆盖到 2014（2026-09 补下过 2014-2015），不能硬夹 2016
        f0 = first['d'] if first and first['d'] else '2016-01-01'
        wins, cur = [], datetime.strptime(f0, '%Y-%m-%d')
        end_dt = datetime.strptime(end, '%Y-%m-%d')
        while cur <= end_dt:                 # API 限制单窗跨度 ≤10 年
            seg = min(datetime(cur.year + 9, 12, 31), end_dt)
            wins.append((cur.strftime('%Y-%m-%d'), seg.strftime('%Y-%m-%d')))
            if seg >= end_dt:
                break
            cur = datetime(seg.year + 1, 1, 1)
        full, failed = [], False
        for (s0, e0) in wins:
            try:
                full.extend(fetch_caliber(code, typ, s0, e0))
            except Exception:  # noqa: BLE001
                failed = True
                break
        if full and not failed and any(x.get('close') is not None for x in full):
            data = full          # 直接用接口原值，不做缩放
            scale = 1.0
            with _db_lock:
                _stat['refetched'] += 1
        else:
            # 兜底：整条重下失败时，按常数因子缩放（配股等场景会不准）
            sets = ', '.join(f'{c}={c}/?' for c in cols)
            with _db_lock:
                cur2 = conn.execute(
                    f"UPDATE daily_kline SET {sets} "
                    f"WHERE stock_code=? AND {close_col} IS NOT NULL",
                    (*([scale] * len(cols)), code))
                conn.commit()
            if cur2.rowcount:
                with _db_lock:
                    _stat['rescaled'] += 1
            scale = 1.0

    payload_rows = []
    for x in data:
        raw_d = str(x.get('date') or '')[:10]
        if len(raw_d) != 10 or raw_d[4] != '-':      # 跳过空/异常日期
            continue
        vals = [x.get(f) for f in ('open', 'high', 'low', 'close')]
        if vals[3] is None:
            continue
        vals = [(v * scale if v is not None else None) for v in vals]
        payload_rows.append(tuple(vals) + (code, raw_d))
    if not payload_rows:
        return 0

    sets = ', '.join(f'{c}=?' for c in cols)
    with _db_lock:
        conn.executemany(
            f"UPDATE daily_kline SET {sets} WHERE stock_code=? AND date=?", payload_rows)
        conn.commit()
    return len(payload_rows)


def run_task(conn, code, types, window, counter, total, done, sync_date):
    for typ in types:
        if (code, typ) in done:
            with _db_lock:
                _stat['skip'] += 1
            continue
        try:
            n = run_one(conn, code, typ, window)
            with _db_lock:
                _stat['ok'] += 1
                _stat['rows'] += n
                conn.execute(
                    "INSERT OR REPLACE INTO daily_kline_caliber_progress "
                    "(stock_code, adj_type, sync_date, rows, ts) VALUES (?,?,?,?,?)",
                    (code, typ, sync_date, n,
                     datetime.now().isoformat(timespec='seconds')))
                conn.commit()
        except Exception as e:  # noqa: BLE001
            with _db_lock:
                _stat['fail'] += 1
                _failed.append((code, typ, str(e)[:110]))
    with _db_lock:
        counter[0] += 1
        if counter[0] % 300 == 0 or counter[0] == total:
            log.info(f"  进度 {counter[0]}/{total}  ok={_stat['ok']} "
                     f"skip={_stat['skip']} rows={_stat['rows']:,} fail={_stat['fail']}")


def run_caliber(types, threads, window, limit=None, only=None, only_missing=False):
    """逐只拉取四口径（ex 本地推导，其余走 API）"""
    bad = [t for t in types if t not in CALIBERS]
    if bad:
        log.error(f"未知口径 {bad}，可选 {list(CALIBERS)}")
        return 1
    if '--keep-scale' in sys.argv:
        _RESCALE_ALL = False
        log.info('  --keep-scale：保留已存序列尺度（绝对价位会与理杏仁/通达信有常数偏移）')
    conn = caliber_db()
    log.info("─" * 70)
    log.info("四口径增量拉取")
    log.info("─" * 70)
    with _db_lock:
        conn.executescript(PROGRESS_DDL)
    sync_date = datetime.now().strftime('%Y-%m-%d')
    if '--reset' in sys.argv:
        with _db_lock:
            conn.execute("DELETE FROM daily_kline_caliber_progress WHERE sync_date=?",
                         (sync_date,))
            conn.commit()
        log.info("  🗑 已清空当日进度")
    with _db_lock:
        done = {(r['stock_code'], r['adj_type']) for r in conn.execute(
            "SELECT stock_code, adj_type FROM daily_kline_caliber_progress "
            "WHERE sync_date=?", (sync_date,))}
    if done:
        log.info(f"  当日已完成 {len(done):,} 项，将跳过（如需重跑加 --reset）")
    backfill_ex(conn)

    if only:
        codes = [c.strip().zfill(6) for c in only.split(',') if c.strip()]
    elif only_missing:
        # 只补「最新交易日仍缺口径列」的股票（被超时/中断后的快速补全）
        last = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
        cond = " OR ".join(f"{t}_close IS NULL" for t in types)
        with _db_lock:
            codes = [r[0] for r in conn.execute(
                f"SELECT DISTINCT stock_code FROM daily_kline "
                f"WHERE date=? AND ({cond}) ORDER BY stock_code", (last,))]
        log.info(f"  --only-missing：最新交易日 {last} 仍缺 {types} 的股票 {len(codes):,} 只")
    else:
        with _db_lock:
            codes = [r[0] for r in conn.execute(
                "SELECT DISTINCT stock_code FROM daily_kline ORDER BY stock_code")]
        if limit:
            codes = codes[:limit]
    total = len(codes)
    est = total * len(types) / 5.0
    log.info(f"  股票 {total:,} 只 × 口径 {types} = {total * len(types):,} 次调用")
    log.info(f"  窗口 {window} 天｜线程 {threads}｜限流 5 req/s → 预计 {est / 60:.1f} 分钟")
    log.info("  （调用次数与窗口长度无关；ex_* 已本地推导不耗调用）")
    counter = [0]
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futs = [pool.submit(run_task, conn, c, types, window, counter, total, done, sync_date)
                for c in codes]
        for f in as_completed(futs):
            f.result()
    log.info("─" * 70)
    log.info(f"🏁 完成：成功 {_stat['ok']:,} / 跳过 {_stat['skip']:,} / 失败 {_stat['fail']:,}  "
             f"写入 {_stat['rows']:,} 行")
    if _stat['refetched']:
        log.info(f"  其中 {_stat['refetched']:,} 项检测到新除权，已重新下整条序列（接口原值直写）")
    if _stat['rescaled']:
        log.info(f"  其中 {_stat['rescaled']:,} 项整条重下失败，退回常数因子缩放（配股场景会不准）")
    if _failed:
        log.warning(f"失败 {len(_failed)} 项（未写库，重跑会自动重试）：")
        for c, t, e in _failed[:20]:
            log.warning(f"   {c} {t}  {e}")
    r = conn.execute("""SELECT COUNT(*) FROM daily_kline
                        WHERE date >= date((SELECT MAX(date) FROM daily_kline), '-60 days')
                          AND lxr_fc_close IS NULL AND ex_close IS NULL"""
                     ).fetchone()[0]
    log.info(f"  近 60 日仍缺价格的股票/日行数：{r:,}（0 为正常；ETF 走 ex_* 回退）")
    conn.close()
    return 0


if __name__ == "__main__":
    if "--caliber" in sys.argv:
        _argv = sys.argv

        def _opt(flag, default):
            try:
                return _argv[_argv.index(flag) + 1]
            except (ValueError, IndexError):
                return default

        _types = [x.strip() for x in _opt('--types', 'lxr_fc,fc,bc').split(',') if x.strip()]
        _limit = _opt('--limit', None)
        sys.exit(run_caliber(_types, int(_opt('--threads', 8)),
                             int(_opt('--window', DEFAULT_WINDOW)),
                             int(_limit) if _limit else None,
                             _opt('--stocks', None),
                             '--only-missing' in sys.argv))
    main()
