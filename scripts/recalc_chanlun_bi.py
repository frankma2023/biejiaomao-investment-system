#!/usr/bin/env python3
"""
scripts/recalc_chanlun_bi.py — 缠论笔全量重算（前复权口径）

════════════════════════════════════════════════════════════════
为什么要重算
════════════════════════════════════════════════════════════════
chanlun 引擎已迁到 `daily_kline_adj`（理杏仁前复权），但 `chanlun_bi_json` 里的历史
是**未复权价**画的。未复权序列上的除权缺口会被 czsc 当成真实 K 线——例如 301489 的
10 转 4 在 2025-06-04 形成一根 −28.8% 的假大阴线，直接造出假笔、假笔顶。

而 2026-09-12 起新增的行是复权价画的 → **表内口径已混合**，必须整体重画。

════════════════════════════════════════════════════════════════
两种模式（看清再选）
════════════════════════════════════════════════════════════════
  --mode latest   只重画每只股票的**最新一条快照**（默认）
                  CPA 的 load_bi_tops、MW 的实盘路径都只读最新快照
                  （`ORDER BY scan_date DESC LIMIT 1`）→ 实测约 16 分钟（6.9 只/秒，8 进程）

  --mode full     重画全部交易日（每只股票约 2,600 天）
                  **约 9 小时**（8 进程实测 5.3 秒/只）。只有需要逐日历史快照才跑：
                  scripts/backfill_mw.py 等历史回填脚本。
                  可中断续跑，也可用 --limit N 重复执行来分批推进。

  跑完 `--mode latest` 之后，表里仍会留着旧的（未复权口径）逐日行。
  要不要清掉见 `--purge-old`。

════════════════════════════════════════════════════════════════
效率与可靠性设计
════════════════════════════════════════════════════════════════
· 按股票分片：一只股票**只加载一次 K 线**，再用 CZSC 逐根增量 update，
  复用 `scanners.chanlun_scan.scan_stock_all`（已含 max_bi_num=50 截断契约）
· 进程池并行，默认用满 CPU 核数
· 断点续传：进度写 `chanlun_recalc_progress`，按 (股票, 版本, 模式) 登记；中断后重跑自动跳过
  （模式必须入键：否则跑完 latest 会把 full 全部跳过，反之亦然）
· 写库带 busy 重试；worker 异常被捕获并汇总，不中断整体
· `--backup` 可先把两张表备份到 data/backup/

════════════════════════════════════════════════════════════════
用法
════════════════════════════════════════════════════════════════
    python scripts\\recalc_chanlun_bi.py --plan                      # 只看规模与耗时预估
    python scripts\\recalc_chanlun_bi.py --mode latest --workers 8   # 重画最新快照（推荐先跑）
    python scripts\\recalc_chanlun_bi.py --mode full --workers 8     # 重画全部（可中断续跑）
    python scripts\\recalc_chanlun_bi.py --mode latest --codes 600309,002648 --reset
    python scripts\\recalc_chanlun_bi.py --mode latest --limit 100    # 先试 100 只
    python scripts\\recalc_chanlun_bi.py --backup                    # 备份两表
    python scripts\\recalc_chanlun_bi.py --purge-old                 # 删掉非本次版本的行
    python scripts\\recalc_chanlun_bi.py --verify                    # 校验覆盖
"""

import argparse
import atexit
import json
import multiprocessing as mp
import os
import sqlite3
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from common import DB_PATH  # noqa: E402

VERSION = 'czsc101_adj'          # 本脚本写出的版本标记（前复权口径）
END_SENTINEL = '2099-12-31'      # --end 缺省时的上界（远未来，不是真实交易日）

# 预估系数 est = n_stock * SEC_PER_STOCK[mode] / workers，均为本机实测反推：
#   latest：8 进程实测 11.9 只/秒 → 1.2
#   full  ：单只单独跑 16s（计算 13.4s + 写库 3s），但 8 进程争抢下实测
#           64 只 / 342s = 5.34s/只——比单进程推算慢 2.7 倍（8 核上跑 9 个进程
#           + SQLite 单写者序列化），所以系数取 43 而不是 16。
# 换机或改数据量后重测这个值，用于 --plan 的耗时预估。
SEC_PER_STOCK = {'latest': 1.2, 'full': 43.0}

# DB 连接超时（三处取值不同是刻意的）：
#   TIMEOUT_PROBE —— 进度探测要快速失败，等不到就走人
#   TIMEOUT_READ  —— 单次只读查询
#   TIMEOUT_WRITE —— 写库要容忍多进程竞争，配 60s busy_timeout
TIMEOUT_PROBE = 10
TIMEOUT_READ = 30
TIMEOUT_WRITE = 60

PROGRESS_DDL = """
CREATE TABLE IF NOT EXISTS chanlun_recalc_progress (
    stock_code TEXT NOT NULL,
    version    TEXT NOT NULL,
    mode       TEXT NOT NULL DEFAULT 'latest',
    dates_n    INTEGER,
    saved_n    INTEGER,
    ts         TEXT,
    PRIMARY KEY (stock_code, version, mode)
)
"""


def progress_has_mode_column(c):
    """进度表是否带 mode 列（旧版没有）。用途：不靠异常类型猜表结构。"""
    try:
        return 'mode' in [r[1] for r in c.execute('PRAGMA table_info(chanlun_recalc_progress)')]
    except sqlite3.Error:
        return False


def ensure_progress_table(c):
    """建/升级进度表。

    进度键必须带 mode：第一版 PK 是 (stock_code, version)，跑完 --mode latest 后
    全市场都登记为已完成，再跑 --mode full 会被全部跳过（实测：股票 0 只，无待处理项）。
    反向也会咬：跑完 full 后，隔一阵跑 latest（补新交易日）同样会被跳过。

    迁移用「建新表 → 搬数据 → 删旧表 → 改名」四步并在一个事务里完成：
    `DROP TABLE` 是 DDL，在 sqlite3 里会隐式提交，中途崩溃就把进度弄没了。
    """
    cols = [r[1] for r in c.execute('PRAGMA table_info(chanlun_recalc_progress)')]
    if not cols:
        c.executescript(PROGRESS_DDL)
        c.commit()
        return
    if 'mode' in cols:
        return

    c.execute('BEGIN IMMEDIATE')
    try:
        c.execute('ALTER TABLE chanlun_recalc_progress RENAME TO _progress_old')
        c.execute(PROGRESS_DDL.replace('IF NOT EXISTS ', ''))
        c.execute("INSERT OR REPLACE INTO chanlun_recalc_progress "
                  "(stock_code, version, mode, dates_n, saved_n, ts) "
                  "SELECT stock_code, version, 'latest', dates_n, saved_n, ts "
                  "FROM _progress_old")
        n = c.execute('SELECT COUNT(*) FROM chanlun_recalc_progress').fetchone()[0]
        c.execute('DROP TABLE _progress_old')
        c.commit()
    except Exception:
        c.rollback()
        raise
    log(f'  进度表已升级（旧记录 {n:,} 条归入 mode=latest）')


# ══════════════════════════════════════════════════════════
# 日志
# ══════════════════════════════════════════════════════════
_LOGF = None


def log(msg):
    line = f'[{datetime.now().strftime("%H:%M:%S")}] {msg}'
    print(line, flush=True)
    if _LOGF:
        _LOGF.write(line + '\n')
        _LOGF.flush()


# ══════════════════════════════════════════════════════════
# 目标枚举
# ══════════════════════════════════════════════════════════
def db(timeout=60):
    conn = sqlite3.connect(str(DB_PATH), timeout=timeout)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=60000')
    conn.row_factory = sqlite3.Row
    return conn


def trading_dates(conn, start, end):
    """全市场交易日历。取 daily_kline_adj（扫描读的同一张表），避免两表覆盖不一致时
    对最新几天拿到 None、旧行永远不被覆盖。"""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_kline_adj WHERE date>=? AND date<=? ORDER BY date",
        (start, end))]


def latest_dates(conn):
    """每只股票各自的最新交易日（停牌股取自己的最后一天）"""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT stock_code, MAX(date) FROM daily_kline_adj GROUP BY stock_code")}


# ══════════════════════════════════════════════════════════
# Worker
# ══════════════════════════════════════════════════════════
def save_one(db_path, code, version, dates, results, max_retry=8):
    """单只股票结果写库（单事务 + busy 重试），返回实际写入的日期点数。

    先取一次「目标区间内该股实际有 K 线的日期集合」，它同时服务两件事：
      · FR-3b：区分「本就无数据」与「引擎静默失败」
        ——上游 `scan_stock.scan_stock_all` 对这两种情况返回同一个值
          `[(d, None) for d in dates]`，从返回值分不出来。
          库里有 K 线却零产出 → 引擎静默失败，**抛错**（worker 出错
          不登记进度 → 下次重跑会重试），且绝不删旧数据；
          库内本就无 K 线 → 无事可做，返回 0 不算失败。
      · FR-3c：找出需要清理的陈旧行（目标日当天该股根本没有 K 线）
    """
    name = code
    try:
        c0 = sqlite3.connect(db_path, timeout=TIMEOUT_READ)
        r = c0.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
        if r and r[0]:
            name = r[0]
        c0.close()
    except Exception:
        pass

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    pairs = [(d, s) for d, s in results if s]

    c0 = sqlite3.connect(db_path, timeout=TIMEOUT_READ)
    try:
        have = {r[0] for r in c0.execute(
            "SELECT date FROM daily_kline_adj WHERE stock_code=? AND date>=? AND date<=?",
            (code, min(dates), max(dates)))}
    finally:
        c0.close()

    if not pairs:
        # 判定必须是「目标区间内有没有 K 线」，不能写成「有没有 K 线」：
        # 退市股（如 000003 最后交易日 2002-04-26）的 K 线全在 2014 之前，
        # full 模式的目标日全在它之后 → 引擎合理地零产出，写在区间外的
        # 判定会把这 87 只全部误判为「引擎静默失败」。
        if have:
            raise RuntimeError('EMPTY: 引擎未产出任何笔（疑似静默失败），已保留旧数据')
        return 0

    # 目标日里「该股当天根本没有 K 线」的：旧扫描器会在这些日子写入陈旧内容
    # （停牌期每个市场日一行），必须清掉，否则表内口径仍然混合。
    stale = [(code, d) for d in dates if d not in have]

    for attempt in range(max_retry):
        try:
            c = sqlite3.connect(db_path, timeout=TIMEOUT_WRITE)
            try:
                c.execute('PRAGMA journal_mode=WAL')
                c.execute('PRAGMA busy_timeout=60000')
                # 只删「本次确实要写入」的日期 + 「当天无 K 线」的陈旧日，
                # 不动其余日期：万一写入前出错，旧行会原样保留。
                keys = [(code, d) for d, _ in pairs] + stale
                c.executemany("DELETE FROM chanlun_scan_daily WHERE stock_code=? AND scan_date=?",
                              keys)
                c.executemany("DELETE FROM chanlun_bi_json WHERE stock_code=? AND scan_date=?",
                              keys)
                # 比「该股最后一根有结果的 K 线」更晚日期的残留行也清掉：
                # 停牌股常被旧路径用「市场扫描日」标注（内容其实与最后一根 K 线相同），
                # 这种行会让「取最新快照」的消费方（CPA/MW）读到旧口径数据。
                # 上界取「最后一个有产出日」与「最后一根 K 线日」的较大者：
                # 若只因末根 K 线异常而最后一日无产出，该日旧行必须保留（FR-3），
                # 不能当成停牌残留一并删掉。
                hi = max(max(d for d, _ in pairs), max(have))
                c.execute("DELETE FROM chanlun_scan_daily WHERE stock_code=? AND scan_date>?",
                          (code, hi))
                c.execute("DELETE FROM chanlun_bi_json WHERE stock_code=? AND scan_date>?",
                          (code, hi))
                for d, s in pairs:
                    c.execute("""INSERT INTO chanlun_scan_daily
                        (scan_date, stock_code, stock_name, bi_count, zs_count, segment_count,
                         latest_bi_dir, latest_bi_power, divergence_count, latest_div_type,
                         trade_signal_count, latest_trade_type, latest_trade_side, latest_trade_price,
                         resonance_strength, created_at, algo_version)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (d, code, name, s.get('bi_count', 0), s.get('zs_count', 0),
                         s.get('segment_count', 0), s.get('latest_bi_dir', ''),
                         s.get('latest_bi_power', 0), s.get('divergence_count', 0),
                         s.get('latest_div_type', ''), s.get('trade_signal_count', 0),
                         s.get('latest_trade_type', ''), s.get('latest_trade_side', ''),
                         s.get('latest_trade_price', 0), s.get('resonance_strength', ''),
                         now, version))
                    if s.get('bi_json'):
                        c.execute("INSERT OR REPLACE INTO chanlun_bi_json "
                                  "(stock_code, scan_date, bi_json) VALUES (?,?,?)",
                                  (code, d, s['bi_json']))
                c.commit()
                return len(pairs)
            finally:
                c.close()
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < max_retry - 1:
                time.sleep(2 * (attempt + 1))
            else:
                raise RuntimeError(f'save failed: {str(e)[:100]}')
    raise RuntimeError(f'save failed after {max_retry} retries')


def worker(task):
    """task = (code, dates, version, mode, reset) → (code, dates_n, saved_n, err)"""
    code, dates, version, mode, reset = task
    try:
        dbp = str(DB_PATH)
        if not reset:
            # 进度表由主进程建好；worker 不再重复执行 DDL（那是多进程锁竞争源）。
            # 探测失败一律当作「没做过」继续扫描，让主进程的汇总说了算。
            try:
                c = sqlite3.connect(dbp, timeout=TIMEOUT_PROBE)
                try:
                    c.execute('PRAGMA busy_timeout=10000')
                    r = c.execute("SELECT 1 FROM chanlun_recalc_progress "
                                  "WHERE stock_code=? AND version=? AND mode=?",
                                  (code, version, mode)).fetchone()
                finally:
                    c.close()
                if r:
                    return (code, len(dates), 0, 'SKIP')
            except sqlite3.Error:
                pass

        from scanners.chanlun_scan import scan_stock_all
        res = scan_stock_all(code, dates)
        n = save_one(dbp, code, version, dates, res)

        c = sqlite3.connect(dbp, timeout=TIMEOUT_WRITE)
        try:
            # 表由主进程保证已建好（见 ensure_progress_table）；这里不再重复 DDL。
            c.execute('PRAGMA busy_timeout=60000')
            for attempt in range(5):
                try:
                    c.execute("INSERT OR REPLACE INTO chanlun_recalc_progress "
                              "(stock_code, version, mode, dates_n, saved_n, ts) "
                              "VALUES (?,?,?,?,?,?)",
                              (code, version, mode, len(dates), n,
                               datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                    c.commit()
                    break
                except sqlite3.OperationalError:
                    if attempt == 4:
                        raise
                    time.sleep(1 + attempt)
        finally:
            c.close()
        return (code, len(dates), n, None)
    except Exception as e:  # noqa: BLE001
        return (code, len(dates), 0, f'{type(e).__name__}: {str(e)[:150]}')


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════
def do_backup():
    bdir = os.path.join(ROOT, 'data', 'backup')
    os.makedirs(bdir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    c = db()
    for t in ('chanlun_bi_json', 'chanlun_scan_daily'):
        dst = os.path.join(bdir, f'{t}_{ts}.json')
        n = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        log(f'  备份 {t}（{n:,} 行）→ {dst}')
        with open(dst, 'w', encoding='utf-8') as f:
            for r in c.execute(f'SELECT * FROM {t}'):
                f.write(json.dumps(dict(r), ensure_ascii=False) + '\n')
    c.close()
    log(f'[OK] 备份完成 → {bdir}')


def do_verify():
    c = db()
    log('─' * 74)
    log('chanlun_scan_daily 按 algo_version 分布：')
    for r in c.execute("""SELECT COALESCE(algo_version,'(null)') v, COUNT(*) n,
                                 COUNT(DISTINCT stock_code) s
                          FROM chanlun_scan_daily GROUP BY v ORDER BY n DESC"""):
        tag = '' if r['v'] == VERSION else '   ← 旧口径，待重算'
        log(f'   {r["v"]:<14} {r["n"]:>12,} 行 / {r["s"]:>6,} 只{tag}')

    # 兼容旧进度表（无 mode 列）
    try:
        prog = c.execute("SELECT mode, COUNT(*) n FROM chanlun_recalc_progress "
                         "WHERE version=? GROUP BY mode", (VERSION,)).fetchall()
    except sqlite3.OperationalError:
        prog = []
    if prog:
        parts = '｜'.join(f'{r["mode"]} {r["n"]:,} 只' for r in prog)
        log(f'重算进度表（{VERSION}）：{parts}')
    else:
        log(f'重算进度表（{VERSION}）：暂无记录')
    log('')
    log('chanlun_bi_json 最新快照的版本归属（抽样 500 只，随机）：')
    rows = c.execute("""SELECT b.stock_code, b.scan_date, s.algo_version v
                        FROM chanlun_bi_json b
                        JOIN (SELECT stock_code, MAX(scan_date) md FROM chanlun_bi_json
                              GROUP BY stock_code) m
                          ON b.stock_code=m.stock_code AND b.scan_date=m.md
                        LEFT JOIN chanlun_scan_daily s
                          ON s.stock_code=b.stock_code AND s.scan_date=b.scan_date
                        ORDER BY RANDOM() LIMIT 500""").fetchall()
    from collections import Counter
    cnt = Counter(r['v'] or '(null)' for r in rows)
    for k, v in cnt.most_common():
        log(f'   {k:<14} {v:>5} / {len(rows)}')
    log('')
    r = c.execute("SELECT COUNT(*) FROM chanlun_recalc_progress WHERE version=?",
                  (VERSION,)).fetchone()[0]
    log(f'重算进度表合计：{r:,} 条记录（版本 {VERSION}）')
    c.close()


def do_purge_old():
    c = db()
    n1 = c.execute("""SELECT COUNT(*) FROM chanlun_scan_daily
                      WHERE COALESCE(algo_version,'(null)') != ?""", (VERSION,)).fetchone()[0]
    n2 = c.execute("""SELECT COUNT(*) FROM chanlun_bi_json b
                      WHERE NOT EXISTS (SELECT 1 FROM chanlun_scan_daily s
                        WHERE s.stock_code=b.stock_code AND s.scan_date=b.scan_date
                          AND s.algo_version=?)""", (VERSION,)).fetchone()[0]
    log(f'将删除：chanlun_scan_daily {n1:,} 行 / chanlun_bi_json {n2:,} 行（非 {VERSION} 的行）')

    # 交叉核对——避免「删了却不会被重算覆盖」的静默丢失：
    # 1) 只存在于 chanlun 表、不在 daily_kline_adj 里的股票（永远不会被扫描到）
    orphan_s = c.execute("""SELECT COUNT(DISTINCT stock_code) FROM chanlun_bi_json b
                             WHERE NOT EXISTS (SELECT 1 FROM daily_kline_adj k
                                               WHERE k.stock_code=b.stock_code)""").fetchone()[0]
    orphan_r = c.execute("""SELECT COUNT(*) FROM chanlun_bi_json b
                             WHERE NOT EXISTS (SELECT 1 FROM daily_kline_adj k
                                               WHERE k.stock_code=b.stock_code)""").fetchone()[0]
    # 2) 属于尚未登记进度的股票（还没重算过，删了就没了）
    undone = c.execute("""SELECT COUNT(*) FROM chanlun_bi_json b
                          WHERE NOT EXISTS (SELECT 1 FROM chanlun_recalc_progress p
                                            WHERE p.stock_code=b.stock_code
                                              AND p.version=?)""", (VERSION,)).fetchone()[0]
    done_n = c.execute("SELECT COUNT(DISTINCT stock_code) FROM chanlun_recalc_progress "
                       "WHERE version=?", (VERSION,)).fetchone()[0]
    log('')
    log(f'  重算进度：已登记 {done_n:,} 只（latest {done_n:,}）')
    full_n = c.execute("SELECT COUNT(*) FROM chanlun_recalc_progress "
                       "WHERE version=? AND mode='full'", (VERSION,)).fetchone()[0]
    if full_n < 100:
        log(f'  [WARN][WARN] 尚未跑过 --mode full（逐日快照仍是旧口径，full 只登记了 {full_n} 只）。')
        log(f'      此时 purge 会把历史逐日行删掉且**不会重建**（那些日期未重算）。')
        log(f'      建议先跑完 full 再 purge。')
    if orphan_s:
        log(f'  [WARN] {orphan_s:,} 只股票只在 chanlun 表、不在 daily_kline_adj 里'
            f'（{orphan_r:,} 行）—— 扫描永远覆盖不到它们，删后不会重建')
    if undone:
        log(f'  [WARN] {undone:,} 行属于尚未登记进度的股票 —— 删后不会重建')
    log('')

    ans = input('确认删除？输入 yes 执行：').strip().lower()
    if ans != 'yes':
        log('已取消')
        c.close()
        return
    with c:
        c.execute("DELETE FROM chanlun_scan_daily WHERE COALESCE(algo_version,'(null)') != ?",
                  (VERSION,))
        c.execute("""DELETE FROM chanlun_bi_json WHERE NOT EXISTS (
                       SELECT 1 FROM chanlun_scan_daily s
                       WHERE s.stock_code=chanlun_bi_json.stock_code
                         AND s.scan_date=chanlun_bi_json.scan_date
                         AND s.algo_version=?)""", (VERSION,))
    log('[OK] 已清理')
    c.close()


def main():
    global _LOGF
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('--mode', choices=['latest', 'full'], default='latest')
    ap.add_argument('--workers', type=int, default=0, help='并行进程数，0=CPU核数')
    ap.add_argument('--codes', help='只处理指定股票，逗号分隔')
    ap.add_argument('--limit', type=int, help='本次只处理前 N 只（试跑/分批用；分批时重复执行同一命令即可推进）')
    ap.add_argument('--start', default='2014-01-01',
                    help='full 模式的起始日期（默认 2014-01-01，覆盖现存表的完整范围）')
    ap.add_argument('--end', default=None, help='full 模式的结束日期')
    ap.add_argument('--reset', action='store_true', help='忽略进度表，强制重算')
    ap.add_argument('--plan', action='store_true', help='只打印规模与预估，不执行')
    ap.add_argument('--backup', action='store_true', help='备份两张表后退出')
    ap.add_argument('--purge-old', action='store_true', help='删除非本次版本的行后退出')
    ap.add_argument('--verify', action='store_true', help='只做覆盖校验')
    ap.add_argument('--log', action='store_true', help='同时写 logs/recalc_chanlun_bi.log')
    a = ap.parse_args()

    if a.log:
        os.makedirs(os.path.join(ROOT, 'logs'), exist_ok=True)
        _LOGF = open(os.path.join(ROOT, 'logs', 'recalc_chanlun_bi.log'),
                     'a', encoding='utf-8')
        atexit.register(_LOGF.close)   # 异常/中断退出时也能关掉日志文件

    if a.backup:
        do_backup()
        return 0
    if a.verify:
        do_verify()
        return 0
    if a.purge_old:
        do_purge_old()
        return 0

    if a.workers < 0:
        log('--workers 不能为负')
        return 1
    if a.limit is not None and a.limit <= 0:
        log('--limit 必须是正整数')
        return 1

    workers = a.workers or (os.cpu_count() or 4)
    c = db()

    # 空库场景给个明确提示，而不是让 6,000 个 worker 报「no such table」
    for _t in ('chanlun_scan_daily', 'chanlun_bi_json'):
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                         (_t,)).fetchone():
            log(f'缺少表 {_t}，请先跑缠论扫描建表（src/scanners/chanlun_scan.py）')
            c.close()
            return 1

    if a.codes:
        codes = [x.strip().zfill(6) for x in a.codes.split(',') if x.strip()]
        if a.mode == 'latest':
            m = latest_dates(c)
            tasks = []
            for x in codes:
                d = m.get(x)
                if not d:
                    d = c.execute("SELECT MAX(date) FROM daily_kline_adj WHERE stock_code=?",
                                  (x,)).fetchone()[0]
                if d:
                    tasks.append((x, [d]))
                else:
                    log(f'  [WARN] {x} 在 daily_kline_adj 中无数据，跳过')
        else:
            dates = trading_dates(c, a.start, a.end or END_SENTINEL)
            tasks = [(x, dates) for x in codes]
    elif a.mode == 'latest':
        m = latest_dates(c)
        tasks = [(k, [v]) for k, v in sorted(m.items()) if v]
    else:
        end = a.end or c.execute("SELECT MAX(date) FROM daily_kline_adj").fetchone()[0]
        dates = trading_dates(c, a.start, end)
        if not dates:
            log(f'区间 [{a.start} ~ {end}] 内无交易日，请检查 --start / --end')
            c.close()
            return 1
        stk = [r[0] for r in c.execute(
            "SELECT DISTINCT stock_code FROM daily_kline_adj ORDER BY stock_code")]
        tasks = [(x, dates) for x in stk]

    n_stock0 = len(tasks)
    n_scan0 = sum(len(d) for _, d in tasks)
    if not a.reset:
        # 用 PRAGMA 判表结构，不靠异常类型猜：busy_timeout 耗尽抛的锁错误同样是
        # OperationalError，误入兼容分支会把 full 的记录当成 latest 已做完，
        # 从此静默不再更新最新快照。
        if progress_has_mode_column(c):
            done = {r[0] for r in c.execute(
                "SELECT stock_code FROM chanlun_recalc_progress "
                "WHERE version=? AND mode=?", (VERSION, a.mode))}
        else:
            done = ({r[0] for r in c.execute(
                "SELECT stock_code FROM chanlun_recalc_progress WHERE version=?",
                (VERSION,))} if a.mode == 'latest' else set())
        before = n_stock0
        tasks = [t for t in tasks if t[0] not in done]
        if before != len(tasks):
            log(f'断点续传：跳过已完成 {before - len(tasks):,} 只')

    # --limit 放在续传过滤之后：重复执行同一命令即可一批一批往后推进。
    # 截断后必须重算 n_stock/n_scan，否则头部计数、ETA、以及 `i == n_stock`
    # 的进度行条件全部按截断前的值走（小批次会一条进度行都打不出来）。
    if a.limit:
        total = len(tasks)
        tasks = tasks[:a.limit]
        if total > len(tasks):
            log(f'--limit {a.limit}：本次处理 {len(tasks):,} / 待处理 {total:,} 只')

    n_stock = len(tasks)
    n_scan = sum(len(d) for _, d in tasks)

    est = n_stock * SEC_PER_STOCK[a.mode] / max(workers, 1)
    log('═' * 74)
    log(f'缠论笔重算｜模式={a.mode}｜版本标记={VERSION}')
    log(f'  股票 {n_stock:,} 只｜待扫描 {n_scan:,} 个(股票,日期)点｜进程 {workers}')
    log(f'  预计耗时 ≈ {est / 60:.0f} 分钟（{est / 3600:.1f} 小时）')
    if a.mode == 'latest':
        log('  说明：只重画每只股票的最新快照——CPA / MW 的实盘路径只读它')
    else:
        log('  说明：重画全部交易日，供历史回填使用（很慢，可中断续跑）')
        if a.start != '2014-01-01' or a.end:
            log('  [WARN] 本次区间非完整区间，完成后会按该区间登记进度；')
            log('     下次跑完整区间需加 --reset（或只重跑未登记的股票）')
    log('═' * 74)
    if a.plan:
        log('（--plan 模式，不执行，也不建表、不写库）')
        c.close()
        return 0
    if not n_stock:
        log('无待处理项')
        c.close()
        return 0

    # 真正要跑了才建进度表（保证 --plan 路径完全只读）
    ensure_progress_table(c)

    t0 = time.time()
    ok = fail = skipped = 0
    errs = []
    saved_total = 0
    i = 0
    tasks_arg = [(code, dates, VERSION, a.mode, a.reset) for code, dates in tasks]
    try:
        with mp.Pool(processes=workers, maxtasksperchild=20) as pool:
            for i, (code, dn, saved, err) in enumerate(
                    pool.imap_unordered(worker, tasks_arg, chunksize=1), 1):
                if err == 'SKIP':
                    skipped += 1
                elif err:
                    fail += 1
                    errs.append(f'{code}: {err}')
                    log(f'  [FAIL] {code}  {err}')
                else:
                    ok += 1
                    saved_total += saved
                if i % 100 == 0 or i == n_stock:
                    el = time.time() - t0
                    rate = i / el if el else 0
                    eta = (n_stock - i) / rate if rate else 0
                    log(f'  进度 {i}/{n_stock} ({i / n_stock * 100:.1f}%)  '
                        f'ok={ok} 跳过={skipped} 失败={fail}  '
                        f'{rate:.2f} 只/秒  ETA {eta / 60:.0f} 分钟')
    except KeyboardInterrupt:
        # Ctrl+C：已完成的进度都已登记，直接重跑就能续；
        # 走到下面照样打印汇总并落盘失败清单。
        log('')
        log(f'[WARN] 收到中断信号（已处理 {i}/{n_stock}）。'
            f'已完成的进度都已登记，直接重跑本脚本即可续跑。')

    el = time.time() - t0
    log('─' * 74)
    log(f'完成：成功 {ok:,} / 跳过 {skipped:,} / 失败 {fail:,}｜'
        f'写入 {saved_total:,} 个日期点｜耗时 {el / 60:.1f} 分钟')
    if errs:
        p = os.path.join(ROOT, 'logs', 'recalc_chanlun_bi_errors.txt')
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write('\n'.join(errs))
        log(f'  失败清单（{len(errs)} 条）→ {p}')
        log('  重跑本脚本会自动重试未登记进度的股票')
    c.close()
    # 有失败就给非 0 退出码，便于定时任务/编排器感知
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
