#!/usr/bin/env python3
"""
每日盘后更新脚本 —— 一键串行完成全部日任务

用法：
    python scripts/daily_update.py              # 全量执行
    python scripts/daily_update.py --skip-rs    # 跳过RS计算
    python scripts/daily_update.py --date 2026-05-10  # 指定日期

执行顺序（按依赖关系分层排列）：
  数据拉取层：
    1. 股票基础信息     (fetch_stock_basic)
    2. 指数日K线        (fetch_index_daily_kline)
    3. 指数估值PE/PB    (fetch_index_fundamental)
    4. 通达信ETF补K线   (fetch_tdx_kline)
    5. 个股日K线        (fetch_stock_daily_kline)
  市场环境层：
    6. 指数拥挤度       (index_crowding)
    7. 融资融券         (fetch_margin_daily)
    8. 龙虎榜+大宗      (daily_review)
    9. 大盘健康度       (market_health)
  量化层：
    10. 个股RS强度      (stock_rs)
    11. 指数RS强度      (index_rs)
    12. 行业分组健康    (market_health --sector)
    13. 指数资金流向    (index_capital_flow)
    14. 大盘卖出评分    (market_sell_score)
    15. 大盘扫描快照    (compute_market_snapshot)
  形态引擎层：
    16. 全A形态扫描    (daily_pattern_scan --all)
    17. 口袋支点V2      (pocket_pivot_v2)
  基本面层：
    18. 个股基本面增量  (fetch_fundamental_nonfinancial)
    19. 机构持股(周一)  (fetch_institutional_holdings)
    20. 研报(周一)      (fetch_stock_reports)
    21. 回购(周一)      (fetch_buyback)
  选股评分层：
    22. CANSLIM评分     (batch_canslim_score)
    23. 观察池日更      (observation)
    24. 持仓监控扫描    (monitoring)
    25. 精选·股票       (screener)
    26. 精选·指数       (index_screener)
  缠论层（为MW信号提供bi数据，必须在MW之前执行）：
    27a. 补昨日缠论bi   (chanlun_scan --date 昨天 — 自动检测缺失并补齐)
    27b. 缠论分钟数据   (fetch_tdx_minute)
    27c. 缠论批量扫描   (chanlun_scan --date 今天 --all)
  MW信号+回测层：
    28. 缠论vs欧奈尔回测 (chanlun_backtest_compare)
    29. MW信号扫描      (backfill_mw.py — 并行预加载+哨兵防卡死，0%兜底等同实盘)
  决策层：
    30. 市值快照        (market_cap_snapshot)
    31. 投资决策驾驶舱  (pipeline)

所有步骤串行执行，单步失败继续后续任务。
"""

import subprocess
import sys
import time
import os
import sqlite3
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
# 任务列表（步骤编号 + 目的 + 依赖）
# ═══════════════════════════════════════════════

log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
log(f"🐺 每日盘后更新开始 — {today_str}")
log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

# 步骤 1~4：数据拉取层（无依赖，预先拉齐今天的原始数据）
TASKS = [
    # 1. 股票基础信息
    ("📋 1.股票状态",         [PYTHON_EXE, "scripts/fetch_stock_basic.py"]),
    # 2. 指数日K线 + 估值（理杏仁API）
    ("📊 2.指数日K线",       [PYTHON_EXE, "scripts/fetch_index_daily_kline.py", "--all", "--end", today_str]),
    ("💹 3.指数估值PE/PB",    [PYTHON_EXE, "scripts/fetch_index_fundamental.py", "--incremental", "--end", today_str]),
    # 4. 通达信补K线（ETF + 个股，本地文件读取）
    ("📡 4.通达信ETF+K线",   [PYTHON_EXE, "scripts/fetch_tdx_kline.py"]),
    ("📈 5.个股日K线",       [PYTHON_EXE, "scripts/fetch_stock_daily_kline.py"]),
    # 5.5 中证全收益指数（H00922 红利全收益，回撤买点基准）
    ("🧧 5.5全收益指数",     [PYTHON_EXE, "scripts/fetch_full_return_index.py"]),
    # 5.6 国债收益率（红利温度计股债息差用）
    ("🏦 5.6国债收益率",     [PYTHON_EXE, "scripts/fetch_bond_yield.py"]),
]

# 步骤 6~8：市场环境层（依赖 K 线数据就位）
TASKS.extend([
    # 6. 指数拥挤度（追涨/恐慌信号）
    ("📐 6.指数拥挤度",      [PYTHON_EXE, "src/scanners/index_crowding.py", "--date", today_str]),
    # 7. 融资融券（杠杆资金动向）
    ("🔄 7.融资融券",        [PYTHON_EXE, "scripts/fetch_margin_daily.py"]),
    # 8. 龙虎榜+大宗交易+汇总（游资/机构动向）
    ("📰 8.龙虎榜+大宗",      [PYTHON_EXE, "scripts/daily_review.py", today_str]),
    # 9. 大盘健康度（涨跌家数/AD线/NHNL）
    ("💊 9.大盘健康度",      [PYTHON_EXE, "src/scanners/market_health.py", "--date", today_str]),
])

# 步骤 10~14：个股/行业量化层（依赖 K 线 + 大盘数据）
TASKS.extend([
    # 10. 个股 RS 强度（RPS 计算，约 5min）
    ("💪 10.个股RS强度",     [PYTHON_EXE, "src/scanners/stock_rs.py", "--date", today_str]),
    # 11. 指数 RS 强度（行业强弱排序基础）
    ("📊 11.指数RS强度",     [PYTHON_EXE, "src/scanners/index_rs.py", "--date", today_str]),
    # 12. 行业分组健康分 v3.0（L2+主题 × 强/中/弱）
    ("🔬 12.行业分组健康",   [PYTHON_EXE, "src/scanners/market_health.py", "--date", today_str, "--sector"]),
    # 13. 指数资金活跃度（北向/主力资金）
    ("💰 13.指数资金流向",   [PYTHON_EXE, "src/scanners/index_capital_flow.py", "--date", today_str]),
    # 14. 大盘卖出评分（环境恶化预警）
    ("📉 14.大盘卖出评分",   [PYTHON_EXE, "src/scanners/market_sell_score.py", "--date", today_str]),
    # 15. 大盘扫描快照（6 卡片 + 趋势图）
    ("📸 15.大盘扫描快照",   [PYTHON_EXE, "scripts/compute_market_snapshot.py", "--date", today_str]),
])

# 步骤 16~17：形态引擎层（依赖个股 RS 完成，检测买入/卖出信号）
# 16. 全 A 股形态扫描（MW/基部突破/口袋支点/卖出信号，依赖个股 RS）
TASKS.append(("🔎 16.全A形态扫描", [PYTHON_EXE, "scripts/daily_pattern_scan.py", "--date", today_str, "--all"]))
# 17. 口袋支点 V2（多周期扫描，依赖 MW 结构的 H/L/C）
TASKS.append(("🟠 17.口袋支点V2", [PYTHON_EXE, "src/scanners/pocket_pivot_v2.py", "--date", today_str, "--save"]))

# 步骤 18~19：基本面层（财务/机构数据，周一全量，每日增量）
# 18. 个股基本面增量（季度财报数据）
TASKS.append(("💰 18.个股基本面", [PYTHON_EXE, "scripts/fetch_fundamental_nonfinancial.py", "--incremental", "--workers", "4"]))
if date.today().weekday() == 0:
    # 19. 机构持股（每季更新，周一拉取）
    TASKS.append(("🏦 19.机构持股", [PYTHON_EXE, "scripts/fetch_institutional_holdings.py"]))
else:
    log(f"⏭️  跳过机构持股（非周一，weekday={date.today().weekday()}）")

# 步骤 20~21：研报/回购（每周一）
if date.today().weekday() == 0:
    TASKS.append(("📝 20.研报拉取", [PYTHON_EXE, "scripts/fetch_stock_reports.py"]))
    TASKS.append(("🔄 21.回购数据", [PYTHON_EXE, "scripts/fetch_buyback.py"]))
else:
    log(f"⏭️  跳过研报/回购（非周一）")

# 步骤 22~24：选股评分层（依赖 RS + 基本面 + 形态信号全部就位）
# 22. CAN SLIM 全量评分
TASKS.append(("🎯 22.CANSLIM评分", [PYTHON_EXE, "scripts/batch_canslim_score.py"]))
# 23. 观察池日更（依赖 RS + CAN SLIM，筛选候选标的）
TASKS.append(("🔍 23.观察池日更", [PYTHON_EXE, "src/discipline/observation.py", "--date", today_str]))
# 24. 持仓监控扫描（依赖观察池 + 形态信号 + 大盘环境）
TASKS.append(("📡 24.持仓监控扫描", [PYTHON_EXE, "src/discipline/monitoring.py"]))
# 25. 欧奈尔每日精选·股票
TASKS.append(("📋 25.精选·股票", [PYTHON_EXE, "src/discipline/screener.py", "--date", today_str]))
# 26. 欧奈尔每日精选·指数（依赖指数 K 线 + 指数 RS）
TASKS.append(("📊 26.精选·指数", [PYTHON_EXE, "src/discipline/index_screener.py", "--date", today_str]))

# 步骤 27：缠论层（依赖 K 线数据，为 MW 信号扫描提供笔数据）
# 27a. 自动补填昨日缺失的 bi 数据（防止漏跑一天造成缺口）
_yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d") if not TARGET_DATE else None
if _yesterday:
    _db = sqlite3.connect(os.path.join(PROJECT_DIR, "data", "lixinger.db"))
    _has_kline = _db.execute("SELECT COUNT(*) FROM daily_kline WHERE date=?", (_yesterday,)).fetchone()[0] > 0
    _has_bi = _db.execute("SELECT COUNT(*) FROM chanlun_bi_json WHERE scan_date=?", (_yesterday,)).fetchone()[0] > 0
    _db.close()
    if _has_kline and not _has_bi:
        TASKS.append(("🎋 27a.补昨日缠论bi", [PYTHON_EXE, "src/scanners/chanlun_scan.py", "--date", _yesterday, "--all"]))
        log(f"  ⚠️ 昨日 {_yesterday} 缠论bi缺失，自动补齐")
# 27b. 缠论分钟数据预下载（TDX 通达信本地文件 → 15/60 分钟 K 线）
# 优化后一次登录批量拉取，取代之前的逐只登录登出
TASKS.append(("⏱️ 27b.缠论分钟数据", [PYTHON_EXE, "scripts/fetch_tdx_minute.py"]))
# 27c. 缠论批量扫描（全市场过滤 ST+低量后缓存 bi 数据，供 MW/BO 等下游使用）
# 必须跑在 MW 信号扫描之前，否则 MW 引擎 0% 兜底下会跳过全部股票
TASKS.append(("🎋 27c.缠论批量扫描", [PYTHON_EXE, "src/scanners/chanlun_scan.py", "--date", today_str, "--all"]))

# 步骤 28~29：MW 信号 + 回测（依赖缠论 bi + 个股 RS 就位）
# 28. 缠论 vs 欧奈尔回测对比
TASKS.append(("⚖️ 28.缠论vs欧奈尔回测", [PYTHON_EXE, "src/scanners/chanlun_backtest_compare.py", "--date", today_str, "--filter"]))
# 29. MW 信号扫描（用 backfill_mw.py 替代 mw_signal.py）
# backfill_mw.py 优势：并行 bi 预加载(3线程+重试) + 哨兵防 run_scan 二次预加载卡死 + 进度输出
# 默认 0% 兜底等同实盘，单日约 15~30 秒
TASKS.append(("🔥 29.MW信号扫描", [PYTHON_EXE, "scripts/backfill_mw.py", "--start", today_str, "--end", today_str]))

# 步骤 30~31：投资决策驾驶舱（依赖前序全部步骤，最终产出）
# 30. 市值快照（pysnowball，为管道市值过滤提供数据）
TASKS.append(("💎 30.市值快照", [PYTHON_EXE, "src/cockpit/market_cap_snapshot.py"]))
# 31. 驾驶舱管道（五级硬过滤 → 简报卡 → 五关检查单 → 仓位/止损建议）
TASKS.append(("🚀 31.投资决策驾驶舱", [PYTHON_EXE, "src/cockpit/pipeline.py", "--date", today_str, "--save"]))

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
