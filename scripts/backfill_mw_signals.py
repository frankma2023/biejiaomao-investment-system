#!/usr/bin/env python3
"""
MW信号回填补丁 — 在依赖数据已就绪的基础上，逐日运行MW信号扫描（支持多进程并行）

前置条件：backfill_mw_data.py 的步骤1~6已执行（RS/CANSLIM/观察池/缠论缓存已就绪）

用法：
    python scripts/backfill_mw_signals.py                      # 全量，4进程并行
    python scripts/backfill_mw_signals.py --workers 6          # 6进程并行
    python scripts/backfill_mw_signals.py --start 2026-04-01   # 从指定日期
    python scripts/backfill_mw_signals.py --date 2026-04-14    # 单日
    python scripts/backfill_mw_signals.py --force              # 强制重扫(忽略已有数据)
"""

import subprocess, sys, os, sqlite3, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_DIR)

PYTHON_EXE = r"C:\Program Files\Python312\python.exe"
if not os.path.exists(PYTHON_EXE):
    PYTHON_EXE = sys.executable

START_DATE = "2026-01-01"
SINGLE_DATE = None
WORKERS = 4
FORCE = False

for i, arg in enumerate(sys.argv):
    if arg == "--start" and i + 1 < len(sys.argv):
        START_DATE = sys.argv[i + 1]
    if arg == "--date" and i + 1 < len(sys.argv):
        SINGLE_DATE = sys.argv[i + 1]
    if arg == "--workers" and i + 1 < len(sys.argv):
        WORKERS = int(sys.argv[i + 1])
    if arg == "--force":
        FORCE = True

TODAY = date.today().strftime("%Y-%m-%d")

# 确保 SQLite WAL 模式 + 合理超时（多进程写入兼容）
DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")
_conn = sqlite3.connect(DB_PATH)
_conn.execute("PRAGMA journal_mode=WAL")
_conn.execute("PRAGMA busy_timeout=30000")
_conn.execute("PRAGMA synchronous=NORMAL")
_conn.close()

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def run_one_date(ds):
    """运行单个日期的 MW 扫描，返回 (ds, success, elapsed, output_tail)"""
    t0 = time.time()
    cmd = [PYTHON_EXE, "src/scanners/mw_signal.py", "--date", ds]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env
        )
        stdout, _ = proc.communicate(timeout=7200)
        elapsed = time.time() - t0
        returncode = proc.returncode

        # 提取关键输出行
        lines = [l.strip() for l in stdout.split("\n") if l.strip()]
        tail = lines[-3:] if lines else []

        return (ds, returncode == 0, elapsed, tail)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return (ds, False, time.time() - t0, ["超时"])
    except Exception as e:
        return (ds, False, time.time() - t0, [str(e)])

def trading_dates(start, end):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date >= ? AND date <= ? ORDER BY date LIMIT 500",
        (start, end)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]

def mw_exists(ds):
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b2_date=?", (ds,)).fetchone()
    conn.close()
    return r[0] > 0

dates = trading_dates(START_DATE, TODAY)

todo = []
for ds in dates:
    if SINGLE_DATE and ds != SINGLE_DATE:
        continue
    if not FORCE and mw_exists(ds):
        continue
    todo.append(ds)

log(f"交易日: {len(dates)} 天, 已扫描: {len(dates)-len(todo)} 天, 待扫描: {len(todo)} 天")
if FORCE:
    log("⚠ --force 模式：忽略已有数据，全部重扫")
if not todo:
    log("全部完成！")
    sys.exit(0)

log("=" * 60)
log(f"🐺 MW信号回填 — {len(todo)} 个交易日, {WORKERS} 进程并行")
log("=" * 60)

total_start = time.time()
ok = 0
fail = 0
completed = 0

# 单日期或单进程 → 串行
if WORKERS <= 1 or SINGLE_DATE:
    log("  模式: 串行")
    for di, ds in enumerate(todo):
        log(f"📅 {ds}  ({di+1}/{len(todo)})")
        ds_result = run_one_date(ds)
        ds_name, ds_ok, ds_elapsed, ds_tail = ds_result

        for line in ds_tail:
            log(f"    │ {line}")
        if ds_ok:
            ok += 1
            log(f"  ✅ {ds} 完成 ({ds_elapsed:.0f}s)")
        else:
            fail += 1
            log(f"  ❌ {ds} 失败 ({ds_elapsed:.0f}s)")

        elapsed = time.time() - total_start
        remaining = len(todo) - di - 1
        if di > 0 and remaining > 0:
            avg = elapsed / (di + 1)
            eta = avg * remaining
            speed = avg / 60
            log(f"  ⏱ 进度 {di+1}/{len(todo)} | 总 {elapsed/60:.0f}min | 单日 {speed:.0f}min | 预计剩余 {eta/60:.0f}min")
else:
    log(f"  模式: {WORKERS} 进程并行")
    # 并行提交所有任务
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(run_one_date, ds): ds for ds in todo}

        for future in as_completed(futures):
            ds = futures[future]
            try:
                ds_name, ds_ok, ds_elapsed, ds_tail = future.result()
            except Exception as e:
                ds_ok = False
                ds_elapsed = 0
                ds_tail = [str(e)]

            completed += 1
            if ds_ok:
                ok += 1
                log(f"✅ [{completed}/{len(todo)}] {ds} ({ds_elapsed:.0f}s)")
            else:
                fail += 1
                err = ds_tail[-1][:80] if ds_tail else "未知错误"
                log(f"❌ [{completed}/{len(todo)}] {ds} ({ds_elapsed:.0f}s): {err}")

            # 每5天或每完成一批打印汇总
            if completed % max(1, WORKERS) == 0:
                elapsed = time.time() - total_start
                remaining = len(todo) - completed
                avg = elapsed / completed if completed > 0 else 0
                eta = avg * remaining
                log(f"  📊 进度 {completed}/{len(todo)} | 总 {elapsed/60:.0f}min | 完成{ok} 失败{fail} | 预计剩余 {eta/60:.0f}min")

total_elapsed = time.time() - total_start
log(f"\n{'=' * 60}")
log(f"🐺 完成: {total_elapsed/60:.0f}min | 成功{ok} 失败{fail}")
log(f"{'=' * 60}")
