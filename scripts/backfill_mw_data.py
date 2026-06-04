#!/usr/bin/env python3
"""
MW信号数据回填脚本

补齐 2026-01-01 至今的关键数据，使 MW 信号引擎的历史扫描能正常评分。
按依赖链逐日串行执行。已存在数据的日期自动跳过。

用法：
    python scripts/backfill_mw_data.py                    # 从 2026-01-01 到今天
    python scripts/backfill_mw_data.py --start 2026-03-01 # 从指定日期开始
    python scripts/backfill_mw_data.py --step 3 --date 2026-04-21  # 仅跑步骤3的某一天

执行顺序（同一日期内）：
  1. 指数RS → 2. 个股RS → 3. 形态扫描（已内置ST/市值/成交额过滤）→ 4. CANSLIM → 5. 观察池
  6. 缠论全量缓存（只需要今天跑一次）

形态扫描过滤规则（在 daily_pattern_scan.py --all 模式中）：
  - 排除 ST / *ST
  - 市值 ≥ 50 亿（close × 总股本，从 stock_equity_change 取）
  - 当日成交额 ≥ 5000 万
"""

import subprocess, sys, os, sqlite3, time, threading
from datetime import datetime, date, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_DIR)

PYTHON_EXE = r"C:\Program Files\Python312\python.exe"
if not os.path.exists(PYTHON_EXE):
    PYTHON_EXE = sys.executable

START_DATE = "2026-01-01"
SINGLE_STEP = 0
SINGLE_DATE = None

for i, arg in enumerate(sys.argv):
    if arg == "--start" and i + 1 < len(sys.argv):
        START_DATE = sys.argv[i + 1]
    if arg == "--step" and i + 1 < len(sys.argv):
        SINGLE_STEP = int(sys.argv[i + 1])
    if arg == "--date" and i + 1 < len(sys.argv):
        SINGLE_DATE = sys.argv[i + 1]

TODAY = date.today().strftime("%Y-%m-%d")

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run_cmd_live(label, cmd, timeout=3600):
    """运行子进程并实时输出 stdout/stderr，不再缓存到内存直到结束"""
    t0 = time.time()
    log(f"  ⏳ {label} — 启动...")

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            bufsize=1
        )

        # 收集最后几行用于摘要
        last_lines = []

        def read_output():
            nonlocal last_lines
            for line in iter(proc.stdout.readline, ""):
                line_stripped = line.rstrip()
                if line_stripped:
                    if len(last_lines) >= 3:
                        last_lines.pop(0)
                    last_lines.append(line_stripped)
                    # 子进程输出加上缩进前缀，便于区分父子输出
                    print(f"    │ {line_stripped}", flush=True)
            proc.stdout.close()

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()

        try:
            returncode = proc.wait(timeout=timeout)
            reader.join(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            elapsed = time.time() - t0
            log(f"  ❌ {label}: 超时 ({elapsed:.0f}s)")
            return False

        elapsed = time.time() - t0

        if returncode == 0:
            summary = last_lines[-1][:80] if last_lines else "(无输出)"
            log(f"  ✅ {label} ({elapsed:.0f}s) ─ {summary}")
            return True
        else:
            err = last_lines[-1][:100] if last_lines else f"exit={returncode}"
            log(f"  ❌ {label} ({elapsed:.0f}s): {err}")
            return False

    except FileNotFoundError:
        log(f"  ❌ {label}: 找不到命令 {cmd[0]}")
        return False
    except Exception as e:
        log(f"  ❌ {label}: {e}")
        return False


def trading_dates(start, end):
    """获取 start~end 之间的交易日"""
    conn = sqlite3.connect(os.path.join(PROJECT_DIR, "data", "lixinger.db"))
    rows = conn.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date >= ? AND date <= ? ORDER BY date LIMIT 500",
        (start, end)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def step_exists(step_id, ds):
    """检查某步骤在某日期是否已有数据"""
    conn = sqlite3.connect(os.path.join(PROJECT_DIR, "data", "lixinger.db"))
    exists = False
    if step_id == 1:  # index_rs
        r = conn.execute("SELECT COUNT(*) FROM index_rs_daily WHERE date=?", (ds,)).fetchone()
        exists = r[0] > 0
    elif step_id == 2:  # stock_rs
        r = conn.execute("SELECT COUNT(*) FROM stock_rs_daily WHERE date=?", (ds,)).fetchone()
        exists = r[0] > 100
    elif step_id == 3:  # pattern_scan
        r = conn.execute("SELECT COUNT(*) FROM pattern_scan_signals WHERE date=?", (ds,)).fetchone()
        exists = r[0] > 0
    elif step_id == 4:  # CANSLIM (用观察池做 proxy 检查)
        r = conn.execute("SELECT COUNT(*) FROM discipline_observation_pool WHERE date=?", (ds,)).fetchone()
        exists = r[0] > 0
    elif step_id == 5:  # observation pool
        r = conn.execute("SELECT COUNT(*) FROM discipline_observation_pool WHERE date=?", (ds,)).fetchone()
        exists = r[0] > 0
    conn.close()
    return exists


STEPS = [
    {"id": 1, "name": "指数RS",      "cmd": ["src/scanners/index_rs.py", "--date"]},
    {"id": 2, "name": "个股RS",      "cmd": ["src/scanners/stock_rs.py", "--date"]},
    {"id": 3, "name": "形态扫描",    "cmd": ["scripts/daily_pattern_scan.py", "--all", "--date"]},
    {"id": 4, "name": "CANSLIM评分", "cmd": ["scripts/batch_canslim_score.py", "--date"]},
    {"id": 5, "name": "观察池",      "cmd": ["src/discipline/observation.py", "--date"]},
]

# ═══════════════════════════════════════════════
# 执行
# ═══════════════════════════════════════════════

dates = trading_dates(START_DATE, TODAY)
log(f"交易日列表: {len(dates)} 天 ({START_DATE} → {TODAY})")
log("=" * 60)
log(f"🐺 MW数据回填 — {len(dates)} 个交易日")
log(f"   过滤: ST·*ST排除 | 市值≥50亿 | 成交额≥5000万")
log("=" * 60)

total_start = time.time()
total_ok = 0
total_skip = 0
total_fail = 0

for di, ds in enumerate(dates):
    if SINGLE_DATE and ds != SINGLE_DATE:
        continue

    # 每天开始时打印一条进度线
    log(f"📅 {ds}  ({di+1}/{len(dates)})")
    day_t0 = time.time()
    day_ok = 0

    for step in STEPS:
        if SINGLE_STEP and step["id"] != SINGLE_STEP:
            continue

        if step_exists(step["id"], ds):
            total_skip += 1
            # 跳过也打一行，让进度完全透明
            if step["id"] == 1:
                log(f"  ⏭ 步骤{step['id']} {step['name']}: 已有数据，跳过")
                # 不逐个打印跳过，减少噪音
            continue

        cmd = [PYTHON_EXE] + step["cmd"] + [ds]
        ok = run_cmd_live(f"步骤{step['id']} {step['name']}", cmd)
        if ok:
            day_ok += 1
            total_ok += 1
        else:
            total_fail += 1
            if step["id"] == 2:  # 个股RS失败则后续无法跑
                log(f"  ⚠ {ds} 个股RS失败，跳过该日后续步骤")
                break

    day_elapsed = time.time() - day_t0
    if day_elapsed > 5:
        log(f"  ⏱ {ds} 当日耗时 {day_elapsed:.0f}s")

    # 每 5 天打印汇总
    if (di + 1) % 5 == 0:
        elapsed = time.time() - total_start
        eta = elapsed / (di + 1) * (len(dates) - di - 1) if di + 1 < len(dates) else 0
        log(f"  📊 [{di+1}/{len(dates)}] 总耗时{elapsed/60:.0f}min | 执行{total_ok} 跳过{total_skip} 失败{total_fail} | 预计剩余{eta/60:.0f}min")

# ═══════════════════════════════════════════════
# 步骤6：缠论全量缓存（只需今天跑一次）
# ═══════════════════════════════════════════════

if SINGLE_STEP == 0 or SINGLE_STEP == 6:
    log(f"\n{'─' * 50}")
    log("步骤6: 🎋 缠论全量缓存（给 MW 引擎 H/L 检测加速）")
    cmd = [PYTHON_EXE, "src/scanners/chanlun_scan.py", "--date", TODAY, "--all"]
    if not SINGLE_DATE:
        ok = run_cmd_live("缠论全量缓存", cmd, timeout=43200)
        if ok: total_ok += 1
        else: total_fail += 1

total_elapsed = time.time() - total_start
log(f"\n{'=' * 60}")
log(f"🐺 回填完成: {total_elapsed/60:.0f}min | 执行{total_ok} 跳过{total_skip} 失败{total_fail}")
log(f"{'=' * 60}")
