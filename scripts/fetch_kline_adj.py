# -*- coding: utf-8 -*-
"""
fetch_kline_adj.py — 三口径 K 线复权数据重建

背景（2026-09-10 审计结论）：
  daily_kline.close 分三段口径（不复权 → 前复权 → 不复权），接缝在
  2016-10-10（2,494 只）与 2019-01-02（2,985 只）；
  daily_kline.adj_* 四列被 pull_adj_close.py 写成了不复权原始价。
  两者均不可用于跨年代回测。本脚本直接向理杏仁重拉。

理杏仁 API 要点（实测，见 docs/lixinger_apis_help/4. K线数据API.md）：
  type=ex_rights       不复权（原始成交价）
  type=lxr_fc_rights   前复权，必须传 adjustForwardDate，且 **严格大于 endDate**
                       不传锚点等同不复权（这是 adj_* 污染的根因）
  type=bc_rights       后复权 —— **不采用**，见下
  date 模式（{"date": d}）只能取不复权，锚点参数被忽略
  单次 startDate/endDate 跨度 ≤ 10 年；API 不返回 complexFactor

为什么后复权自己算：
  实测 bc_rights 用的是「加法分红复权」（复权价 = 原价×送转因子 + 累计现金分红），
  不是行业标准的乘法复权。后果：神华 4,523 天里有 286 天日收益与 change 偏差 >1%，
  万华 59 天。这种口径会污染长周期回测。
  改用本地乘法推导：hfq(t) = raw(t) × [qfq(t)/raw(t)] / [qfq(d0)/raw(d0)]，
  锚定上市首日（hfq(d0) = raw(d0)），与前复权完全同构，仅差一个常数。

表：
  stock_kline_adj(stock_code, adj_type, date, open, high, low, close, change)
  adj_type ∈ {raw 不复权, qfq 前复权, hfq 后复权}

用法：
  python scripts/fetch_kline_adj.py --stocks 600309,002648 --types raw,qfq,hfq
  python scripts/fetch_kline_adj.py --all --types raw --date-mode --threads 6
  python scripts/fetch_kline_adj.py --all --types raw,qfq,hfq --threads 6
  python scripts/fetch_kline_adj.py --verify --stocks 600309,601088
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, 'scripts')
from common import api_post, DB_PATH, log  # noqa: E402

# 前复权锚点：必须严格大于 endDate；固定为常量，避免每次重拉整条序列平移
QFQ_ANCHOR = '2026-12-31'
WINDOW_YEARS = 10

TYPES = ('raw', 'qfq', 'hfq')
API_TYPES = ('raw', 'qfq')          # hfq 本地推导，不走 API
TYPE_TO_API = {'raw': 'ex_rights', 'qfq': 'lxr_fc_rights'}

DDL = """
CREATE TABLE IF NOT EXISTS stock_kline_adj (
    stock_code TEXT NOT NULL,
    adj_type   TEXT NOT NULL,
    date       TEXT NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    change     REAL,
    PRIMARY KEY (stock_code, adj_type, date)
)
"""
IDX = "CREATE INDEX IF NOT EXISTS idx_kadj_date ON stock_kline_adj(date, adj_type)"

# 按窗口登记完成状态：比「日期区间包含」更精确，避免重复调用 API
PROGRESS_DDL = """
CREATE TABLE IF NOT EXISTS stock_kline_adj_progress (
    stock_code TEXT NOT NULL,
    adj_type   TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date   TEXT NOT NULL,
    rows       INTEGER,
    ts         TEXT,
    PRIMARY KEY (stock_code, adj_type, start_date, end_date)
)
"""

_db_lock = threading.Lock()
_stat = {'rows': 0, 'calls': 0, 'fail': 0, 'skip': 0}
_stat_lock = threading.Lock()


def bump(**kw):
    with _stat_lock:
        for k, v in kw.items():
            _stat[k] += v


def split_windows(start: str, end: str, years: int = WINDOW_YEARS):
    """按 ≤years 年切窗，返回 [(s, e), ...]"""
    a = dt.date.fromisoformat(start)
    b = dt.date.fromisoformat(end)
    out = []
    while a <= b:
        e = min(b, a.replace(year=a.year + years) - dt.timedelta(days=1))
        out.append((a.isoformat(), e.isoformat()))
        a = e + dt.timedelta(days=1)
    return out


def fetch_window(stock_code: str, adj_type: str, start: str, end: str, retries: int = 3):
    """拉单个 (股票, 口径, 窗口)，返回 rows 或 None"""
    payload = dict(stockCode=stock_code, type=TYPE_TO_API[adj_type],
                   startDate=start, endDate=end)
    if adj_type == 'qfq':
        payload['adjustForwardDate'] = QFQ_ANCHOR

    for attempt in range(retries):
        try:
            rows = api_post('/company/candlestick', payload, timeout=120)
            bump(calls=1)
            return rows
        except Exception as e:
            msg = str(e)[:120]
            if 'ForbiddenError' in msg or 'ValidationError' in msg:
                log.error('%s %s %s~%s 参数被拒: %s', stock_code, adj_type, start, end, msg)
                return None
            if attempt == retries - 1:
                log.warning('%s %s %s~%s 放弃: %s', stock_code, adj_type, start, end, msg)
                bump(fail=1)
                return None
    return None


def upsert(conn: sqlite3.Connection, stock_code: str, adj_type: str, rows):
    if not rows:
        return 0
    recs = []
    for r in rows:
        d = (r.get('date') or '')[:10]
        if not d:
            continue
        recs.append((stock_code, adj_type, d, r.get('open'), r.get('high'),
                     r.get('low'), r.get('close'), r.get('change')))
    if not recs:
        return 0
    with _db_lock:
        conn.executemany(
            "INSERT INTO stock_kline_adj (stock_code, adj_type, date, open, high, low, close, change)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(stock_code, adj_type, date) DO UPDATE SET"
            " open=excluded.open, high=excluded.high, low=excluded.low,"
            " close=excluded.close, change=excluded.change", recs)
        conn.commit()
    bump(rows=len(recs))
    return len(recs)


def done_ranges(conn, stock_code: str, adj_type: str):
    """已入库的日期区间（用于断点续传）"""
    with _db_lock:
        row = conn.execute(
            "SELECT MIN(date) a, MAX(date) b, COUNT(*) n FROM stock_kline_adj"
            " WHERE stock_code=? AND adj_type=?", (stock_code, adj_type)).fetchone()
    return row if row and row[2] else None


def done_windows(conn, stock_code: str, adj_type: str):
    """已成功拉取的窗口集合"""
    with _db_lock:
        return {(r[0], r[1]) for r in conn.execute(
            "SELECT start_date, end_date FROM stock_kline_adj_progress"
            " WHERE stock_code=? AND adj_type=?", (stock_code, adj_type))}


def mark_window(conn, stock_code: str, adj_type: str, s: str, e: str, n: int):
    with _db_lock:
        conn.execute("INSERT OR REPLACE INTO stock_kline_adj_progress"
                     " (stock_code, adj_type, start_date, end_date, rows, ts)"
                     " VALUES (?,?,?,?,?,datetime('now'))", (stock_code, adj_type, s, e, n))
        conn.commit()


def work_stock(conn, stock_code: str, adj_type: str, start: str, end: str):
    wins = split_windows(start, end)
    done = done_windows(conn, stock_code, adj_type)
    if all(w in done for w in wins):
        bump(skip=1)
        return
    total = 0
    for s, e in wins:
        if (s, e) in done:
            continue
        rows = fetch_window(stock_code, adj_type, s, e)
        if rows is None:
            continue  # 失败/被拒：不登记进度，下次重试
        n = upsert(conn, stock_code, adj_type, rows)
        mark_window(conn, stock_code, adj_type, s, e, n)
        total += n
    log.info('  %s %-4s %s~%s → %d 行', stock_code, adj_type, start, end, total)


def work_day(conn, adj_type: str, day: str):
    """date 模式：一次拿全市场（仅支持不复权）"""
    for attempt in range(3):
        try:
            rows = api_post('/company/candlestick', {'date': day, 'type': TYPE_TO_API[adj_type]},
                            timeout=180)
            bump(calls=1)
            n = upsert_all(conn, adj_type, rows)
            log.info('  %s %-4s → %d 行', day, adj_type, n)
            return
        except Exception as e:
            if attempt == 2:
                log.warning('  %s %s 放弃: %s', day, adj_type, str(e)[:100])
                bump(fail=1)
                return


def upsert_all(conn, adj_type: str, rows):
    if not rows:
        return 0
    recs = []
    for r in rows:
        d = (r.get('date') or '')[:10]
        code = r.get('stockCode')
        if not d or not code:
            continue
        recs.append((code, adj_type, d, r.get('open'), r.get('high'),
                     r.get('low'), r.get('close'), r.get('change')))
    with _db_lock:
        conn.executemany(
            "INSERT INTO stock_kline_adj (stock_code, adj_type, date, open, high, low, close, change)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(stock_code, adj_type, date) DO UPDATE SET"
            " open=excluded.open, high=excluded.high, low=excluded.low,"
            " close=excluded.close, change=excluded.change", recs)
        conn.commit()
    bump(rows=len(recs))
    return len(recs)


# ══ ══════════════════════════════════════════════════════════
# 后复权推导（乘法口径）
# ═══════════════════════════════════════════════════════════════

def derive_hfq(conn, codes):
    """hfq(t) = raw(t) * [qfq(t)/raw(t)] / [qfq(d0)/raw(d0)]，锚定该股首个交易日
    与前复权同构，仅差一个常数；日收益率与 qfq 完全一致。"""
    n_rows, n_done = 0, 0
    for code in codes:
        raw = {r[0]: r for r in conn.execute(
            "SELECT date, open, high, low, close, change FROM stock_kline_adj"
            " WHERE stock_code=? AND adj_type='raw' ORDER BY date", (code,))}
        qfq = {r[0]: r for r in conn.execute(
            "SELECT date, open, high, low, close, change FROM stock_kline_adj"
            " WHERE stock_code=? AND adj_type='qfq' ORDER BY date", (code,))}
        days = sorted(set(raw) & set(qfq))
        if len(days) < 2:
            log.warning('  %s 缺 raw/qfq，跳过后复权推导', code)
            continue
        d0 = days[0]
        # 比例因子 K(t) = qfq/raw，再统一归一到 d0
        base = qfq[d0][4] / raw[d0][4] if raw[d0][4] else 1.0
        recs = []
        for d in days:
            r, q = raw[d], qfq[d]
            if not r[4] or not q[4]:
                continue
            k = (q[4] / r[4]) / base
            recs.append((code, 'hfq', d,
                         r[1] * k, r[2] * k, r[3] * k, r[4] * k, r[5]))
        with _db_lock:
            conn.executemany(
                "INSERT INTO stock_kline_adj (stock_code, adj_type, date, open, high, low, close, change)"
                " VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(stock_code, adj_type, date) DO UPDATE SET"
                " open=excluded.open, high=excluded.high, low=excluded.low,"
                " close=excluded.close, change=excluded.change", recs)
            conn.commit()
        n_rows += len(recs)
        n_done += 1
    bump(rows=n_rows)
    log.info('后复权推导：%d 只 / %d 行', n_done, n_rows)


# ══════════════════════════════════════════════════════════
# 校验
# ══════════════════════════════════════════════════════════

def verify(conn, codes):
    print('=' * 96)
    print('%-8s %-4s %6s | %-30s | %s' % ('code', 'type', 'rows', 'hfq/raw 或 qfq/hfq 比值跳变', '日收益率 vs change'))
    print('-' * 96)
    for code in codes:
        base = {r[0]: r for r in conn.execute(
            "SELECT date, close, change FROM stock_kline_adj WHERE stock_code=? AND adj_type='raw' ORDER BY date",
            (code,))}
        if not base:
            print('%-8s  无 raw 数据' % code)
            continue
        for t in ('hfq', 'qfq'):
            ser = [r for r in conn.execute(
                "SELECT date, close FROM stock_kline_adj WHERE stock_code=? AND adj_type=? ORDER BY date",
                (code, t))]
            if not ser:
                continue
            ratios, prev = [], None
            for d, cl in ser:
                r = base.get(d)
                if not r or not r[1] or not cl:
                    continue
                v = cl / r[1]
                if prev is not None and abs(v - prev) / prev > 0.001:
                    ratios.append(d)
                prev = v
            # 收益率一致性：复权序列日收益 vs change
            bad = 0
            ls = [x for x in ser if base.get(x[0])]
            for i in range(1, len(ls)):
                d, cl = ls[i]
                pcl = ls[i - 1][1]
                ch = base[d][2]
                if ch is None or not pcl:
                    continue
                if abs(cl / pcl - 1 - ch) > 0.01:
                    bad += 1
            print('%-8s %-4s %6d | 比值跳变 %3d 次 %s | 收益不符 %d/%d' % (
                code, t, len(ser), len(ratios), (ratios[:3] if ratios else ''),
                bad, len(ls) - 1))
    print('=' * 96)


# ══════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--types', default='raw,qfq,hfq')
    ap.add_argument('--stocks', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--date-mode', action='store_true', help='按日全市场拉取（仅 raw）')
    ap.add_argument('--start', default='')
    ap.add_argument('--end', default='')
    ap.add_argument('--threads', type=int, default=6)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()

    # check_same_thread=False：多线程共享连接；写入由 _db_lock 串行化，WAL 下并发读安全
    conn = sqlite3.connect(str(DB_PATH), timeout=60, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute(DDL)
    conn.execute(IDX)
    conn.execute(PROGRESS_DDL)
    conn.commit()

    if args.verify:
        codes = args.stocks.split(',') if args.stocks else [
            r[0] for r in conn.execute(
                "SELECT DISTINCT stock_code FROM stock_kline_adj LIMIT 20")]
        verify(conn, codes)
        return

    types = [t.strip() for t in args.types.split(',') if t.strip()]
    for t in types:
        assert t in TYPES, '未知口径: %s' % t
    api_types = [t for t in types if t in API_TYPES]
    want_hfq = 'hfq' in types

    # 股票池（zfill(6) 兜底：PowerShell 会把 000338 当数字吃掉前导零）
    def norm(x):
        x = str(x).strip()
        return x.zfill(6) if x.isdigit() else x

    if args.stocks:
        codes = [norm(c) for c in args.stocks.split(',') if c.strip()]
    else:
        codes = [r[0] for r in conn.execute(
            "SELECT stock_code FROM (SELECT stock_code, MIN(date) a, MAX(date) b FROM daily_kline"
            " GROUP BY stock_code) WHERE b >= '1996-01-03' ORDER BY stock_code")]
    codes = [c for c in codes if c not in ('000000', '')]
    if args.limit:
        codes = codes[:args.limit]
    print('股票数 %d，口径 %s，线程 %d' % (len(codes), types, args.threads))

    # 每只股票的日期范围
    rng = {}
    for c, a, b in conn.execute(
            "SELECT stock_code, MIN(date), MAX(date) FROM daily_kline GROUP BY stock_code"):
        rng[c] = (args.start or a, args.end or b)
    miss = [c for c in codes if c not in rng]
    print('日期范围表 %d 条；无范围股票 %d 只 %s' % (len(rng), len(miss), miss[:10]))

    t0 = dt.datetime.now()
    if args.date_mode:
        days = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM daily_kline WHERE date >= ? AND date <= ? ORDER BY date",
            (args.start or '1996-01-03', args.end or '2030-01-01'))]
        done = {r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM stock_kline_adj WHERE adj_type='raw'")}
        todo = [d for d in days if d not in done]
        print('date 模式：%d 个交易日待拉（已覆盖 %d）' % (len(todo), len(done)))
        lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futs = [ex.submit(work_day, conn, 'raw', d) for d in todo]
            for i, f in enumerate(as_completed(futs), 1):
                f.result()
                if i % 100 == 0:
                    print('  进度 %d/%d  (%s)' % (i, len(todo), dt.datetime.now().strftime('%H:%M:%S')))
    else:
        jobs = []
        for c in codes:
            if c not in rng:
                continue
            s, e = rng[c]
            for t in api_types:
                jobs.append((c, t, s, e))
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futs = [ex.submit(work_stock, conn, c, t, s, e) for c, t, s, e in jobs]
            for i, f in enumerate(as_completed(futs), 1):
                f.result()
                if i % 50 == 0:
                    print('  进度 %d/%d  行=%d 调用=%d 失败=%d  (%s)' % (
                        i, len(jobs), _stat['rows'], _stat['calls'], _stat['fail'],
                        dt.datetime.now().strftime('%H:%M:%S')))

    if want_hfq and not args.date_mode:
        derive_hfq(conn, codes)

    dur = (dt.datetime.now() - t0).total_seconds()
    print('完成：行 %d  调用 %d  失败 %d  跳过 %d  耗时 %.0fs' % (
        _stat['rows'], _stat['calls'], _stat['fail'], _stat['skip'], dur))
    conn.close()


if __name__ == '__main__':
    main()
