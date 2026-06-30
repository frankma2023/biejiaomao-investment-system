#!/usr/bin/env python3
"""
每日盘后更新脚本 —— 一键串行完成全部日任务

用法：
    python scripts/daily_update.py              # 全量执行
    python scripts/daily_update.py --skip-rs    # 跳过RS计算
    python scripts/daily_update.py --date 2026-05-10  # 指定日期

执行顺序（按依赖关系排列）：
  1. 股票状态更新       (fetch_stock_basic)
  2. 指数日K线          (fetch_index_daily_kline)
  3. 个股日K线          (fetch_stock_daily_kline)
  4. 个股基本面         (fetch_fundamental_nonfinancial — 周一)
  5. 指数拥挤度         (index_crowding)
  6. 融资融券           (fetch_margin_daily)
  7. 大盘健康度         (market_health)
  8. 大盘卖出评分       (market_sell_score)
  9. 大盘扫描快照       (compute_market_snapshot)
  10. 个股RS+指数RS     (stock_rs + index_rs)
  11. 全A股形态扫描     (daily_pattern_scan — 每日)
  11.5 口袋支点V2扫描    (pocket_pivot_v2 — 每日)
  12. 机构持股拉取      (fetch_institutional_holdings — 周一)
  13. 研报拉取          (fetch_stock_reports — 周一)
  14. 回购数据          (fetch_buyback — 周一)
  15. CAN SLIM评分      (batch_canslim_score — 每日)
  16. 观察池日更        (observation — 每日)
  17. 持仓监控扫描      (monitoring — 每日)
  18. 欧奈尔每日精选·股票 (screener — 每日)
  19. 欧奈尔每日精选·指数 (index_screener — 每日)
  20. 缠论分钟数据预下载 (download_minute_kline — 每日)
  21. 缠论批量扫描      (chanlun_scan — 每日)
  22. 缠论vs欧奈尔回测  (chanlun_backtest_compare — 每日)
  23. MW信号扫描        (mw_signal — 每日，依赖RS/CANSLIM/观察池/缠论缓存)
  24. 投资决策驾驶舱    (cockpit — 每日，依赖前序全部步骤)

步骤 1~3 可并行，但为简单起见串行执行，出错时终止。
"""

import subprocess
import sys
import time
import os
from datetime import datetime, date, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_DIR)

# 固定 Python 解释器（避免 conda 环境下 talib 缺失）
PYTHON_EXE = r"C:\Program Files\Python312\python.exe"
if not os.path.exists(PYTHON_EXE):
    PYTHON_EXE = sys.executable  # 回退

# ── 解析参数 ──
SKIP_RS = "--skip-rs" in sys.argv
TARGET_DATE = None
for i, arg in enumerate(sys.argv):
    if arg == "--date" and i + 1 < len(sys.argv):
        TARGET_DATE = sys.argv[i + 1]

if TARGET_DATE:
    today_str = TARGET_DATE
else:
    today_str = date.today().strftime("%Y-%m-%d")

# ── 日志 ──
LOG_FILE = os.path.join(PROJECT_DIR, "data", "daily_update.log")
start_time = time.time()
tasks = []
failed = []

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def run_task(label, cmd, timeout=3600):
    """执行一个子任务，返回 (label, success, elapsed, output)"""
    log(f"▶ {label}")
    log(f"  CMD: {' '.join(cmd)}")
    t0 = time.time()
    try:
        # 为 Python 脚本显式设置编码
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        elapsed = time.time() - t0
        stdout = r.stdout.strip()
        stderr = r.stderr.strip()
        if r.returncode == 0:
            # 打印最后几行输出
            lines = stdout.split("\n")
            for line in lines[-8:]:
                if line.strip():
                    log(f"    {line.strip()}")
            log(f"  ✅ {label} 完成 ({elapsed:.0f}s)")
            return (label, True, elapsed, stdout)
        else:
            log(f"  ❌ {label} 失败 (exit={r.returncode})")
            for line in stderr.split("\n")[-5:]:
                if line.strip():
                    log(f"    {line.strip()}")
            return (label, False, elapsed, stderr)
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        log(f"  ❌ {label} 超时 ({elapsed:.0f}s)")
        return (label, False, elapsed, "timeout")

# ═══════════════════════════════════════════════
# 任务列表
# ═══════════════════════════════════════════════

log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
log(f"🐺 每日盘后更新开始 — {today_str}")
log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

TASKS = [
    ("📋 股票状态",         [PYTHON_EXE, "scripts/fetch_stock_basic.py"]),
    ("📊 指数日K线",       [PYTHON_EXE, "scripts/fetch_index_daily_kline.py", "--all", "--end", today_str]),
    ("📈 个股日K线",       [PYTHON_EXE, "scripts/fetch_stock_daily_kline.py"]),
    ("📐 指数拥挤度",      [PYTHON_EXE, "src/scanners/index_crowding.py", "--date", today_str]),
    ("🔄 融资融券(新API)", [PYTHON_EXE, "scripts/fetch_margin_daily.py"]),
    ("💊 大盘健康度",      [PYTHON_EXE, "src/scanners/market_health.py", "--date", today_str]),
    ("🔬 行业分组健康分",  [PYTHON_EXE, "src/scanners/market_health.py", "--date", today_str, "--sector"]),
    ("📉 大盘卖出评分",    [PYTHON_EXE, "src/scanners/market_sell_score.py", "--date", today_str]),
    ("📸 大盘扫描快照",    [PYTHON_EXE, "scripts/compute_market_snapshot.py", "--date", today_str]),
]

if not SKIP_RS:
    TASKS.append(("💪 个股RS强度", [PYTHON_EXE, "src/scanners/stock_rs.py", "--date", today_str]))
    TASKS.append(("📊 指数RS强度", [PYTHON_EXE, "src/scanners/index_rs.py", "--date", today_str]))

# 步骤7：全A股形态扫描（依赖个股RS完成，每日执行）
TASKS.append(("🔎 全A股形态扫描", [PYTHON_EXE, "scripts/daily_pattern_scan.py", "--date", today_str, "--all"]))

# 步骤7.5：口袋支点V2扫描（依赖MW结构的H/L/C，每日执行）
TASKS.append(("🟠 口袋支点V2", [PYTHON_EXE, "src/scanners/pocket_pivot_v2.py", "--date", today_str, "--save"]))

# 步骤4.5：个股基本面拉取（每周一执行）
if date.today().weekday() == 0:
    TASKS.append(("💰 个股基本面", [PYTHON_EXE, "scripts/fetch_fundamental_nonfinancial.py", "--incremental", "--workers", "4"]))
else:
    log(f"⏭️  跳过个股基本面（非周一）")
if date.today().weekday() == 0:
    TASKS.append(("🏦 机构持股拉取", [PYTHON_EXE, "scripts/fetch_institutional_holdings.py"]))
else:
    log(f"⏭️  跳过机构持股（非周一，weekday={date.today().weekday()}）")

# 步骤9：研报拉取（每周一执行）
if date.today().weekday() == 0:
    TASKS.append(("📝 研报拉取", [PYTHON_EXE, "scripts/fetch_stock_reports.py"]))
else:
    log(f"⏭️  跳过研报拉取（非周一）")

# 步骤10：回购数据拉取（每周一执行）
if date.today().weekday() == 0:
    TASKS.append(("🔄 回购数据", [PYTHON_EXE, "scripts/fetch_buyback.py"]))
else:
    log(f"⏭️  跳过回购数据（非周一）")

# 步骤11：全量CAN SLIM评分（每日执行）
TASKS.append(("🎯 CAN SLIM评分", [PYTHON_EXE, "scripts/batch_canslim_score.py"]))

# 步骤12：观察池日更（每日执行，依赖个股RS + CANSLIM）
TASKS.append(("🔍 观察池日更", [PYTHON_EXE, "src/discipline/observation.py", "--date", today_str]))

# 步骤13：持仓监控扫描（每日执行，依赖观察池 + 形态信号 + 大盘环境）
TASKS.append(("📡 持仓监控扫描", [PYTHON_EXE, "src/discipline/monitoring.py"]))

# 步骤14：欧奈尔每日精选·股票（每日执行，依赖观察池 + 形态信号）
TASKS.append(("📋 欧奈尔每日精选·股票", [PYTHON_EXE, "src/discipline/screener.py", "--date", today_str]))

# 步骤15：欧奈尔每日精选·指数（每日执行，依赖指数K线 + 指数RS）
TASKS.append(("📊 欧奈尔每日精选·指数", [PYTHON_EXE, "src/discipline/index_screener.py", "--date", today_str]))

# 步骤16：缠论分钟数据预下载（每日执行，为区间套分析缓存关注股票的分钟K线）
# 优化后一次登录批量拉取，取代之前的逐只登录登出
TASKS.append(("⏱️ 缠论分钟数据预下载", [PYTHON_EXE, "src/scripts/download_minute_kline.py"]))

# 步骤17：缠论批量扫描（每日执行，全市场过滤ST+低量后缓存bi数据，供MW信号等下游使用）
TASKS.append(("🎋 缠论批量扫描", [PYTHON_EXE, "src/scanners/chanlun_scan.py", "--date", today_str, "--all"]))

# 步骤18：缠论 vs 欧奈尔回测对比（每日执行，对当天观察池做三组回测对比）
TASKS.append(("⚖️ 缠论vs欧奈尔回测", [PYTHON_EXE, "src/scanners/chanlun_backtest_compare.py", "--date", today_str, "--filter"]))

# 步骤19：MW信号扫描（每日执行，全市场扫描牛市回调后再启动形态）
TASKS.append(("🔥 MW信号扫描", [PYTHON_EXE, "src/scanners/mw_signal.py", "--date", today_str]))

# 步骤20：投资决策驾驶舱管道（每日执行，依赖前序所有步骤）
# 先建市值快照，再跑管道
TASKS.append(("💎 市值快照", [PYTHON_EXE, "src/cockpit/market_cap_snapshot.py"]))
TASKS.append(("🚀 投资决策驾驶舱", [PYTHON_EXE, "src/cockpit/pipeline.py", "--date", today_str, "--save"]))

for label, cmd in TASKS:
    lbl, ok, elapsed, _ = run_task(label, cmd)
    tasks.append((lbl, ok, elapsed))
    if not ok:
        failed.append(lbl)
        log(f"⚠️  {lbl} 失败，继续执行后续任务")
        # 继续执行，不终止

# ═══════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════

total_elapsed = time.time() - start_time

log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
log(f"🐺 每日盘后更新结束")
log(f"   耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")

passed = [t for t in tasks if t[1]]
for lbl, ok, elapsed in tasks:
    status = "✅" if ok else "❌"
    log(f"   {status}  {lbl} ({elapsed:.0f}s)")

if failed:
    log(f"⚠️  失败任务: {', '.join(failed)}")
else:
    log(f"🎉 全部完成")

log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

if failed:
    sys.exit(1)
