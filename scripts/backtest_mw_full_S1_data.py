"""
MW 信号全面回测 · Step 1: SQL 产出 forward returns + 全量因子矩阵
"""
import sqlite3, json, os, numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
OUT_DIR = 'D:/hanako/investment-system/config/strategy'
os.makedirs(OUT_DIR, exist_ok=True)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
t0 = datetime.now()

print("=" * 70)
print("MW 全面回测 · Step 1: 构建数据表")
print("=" * 70)

# ── 1.1 前向收益率临时表 ──
print("\n[1.1] 创建前向收益临时表...", end=' ', flush=True)
conn.execute("DROP TABLE IF EXISTS _mw_returns")
conn.execute("""
    CREATE TEMP TABLE _mw_returns AS
    WITH klined AS (
        SELECT stock_code, date, open, high, low, close, volume, amount,
               ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date) - 1 as rn
        FROM daily_kline WHERE date >= '2016-01-01' AND date <= '2026-10-31'
    )
    SELECT s.stock_code, s.b1_date, s.b2_date, s.tech_score, s.decline_pct,
           s.h_rs250, s.is_plus, s.score, s.confidence,
           s.b1_return_pct, s.b1_vol_ratio,
           s.score_h, s.score_d, s.score_i1, s.score_i2, s.score_sig,
           s.ind_rs20, s.ind_rs250, s.c_amount_avg, s.h_pre_rise_pct,
           -- B1次日开盘（可执行入场价）
           k1.open as entry_open,
           -- B1日收盘（仅对比）
           k0.close as b1_close,
           -- forward returns: B1次日开盘 → 持有 N 日
           k5.close / k1.open - 1 as ret_b1_5d,
           k10.close / k1.open - 1 as ret_b1_10d,
           k20.close / k1.open - 1 as ret_b1_20d,
           k40.close / k1.open - 1 as ret_b1_40d,
           k60.close / k1.open - 1 as ret_b1_60d,
           -- B1收盘入场（不可执行）
           k5.close / k0.close - 1 as ret_b1c_5d,
           k10.close / k0.close - 1 as ret_b1c_10d,
           k20.close / k0.close - 1 as ret_b1c_20d,
           -- K线特征（B1日附近）
           k0.rn as b1_rn,
           k1.volume as next_vol
    FROM mw_signal_daily s
    JOIN klined k0 ON k0.stock_code = s.stock_code AND k0.date = s.b1_date
    JOIN klined k1 ON k1.stock_code = s.stock_code AND k1.rn = k0.rn + 1
    LEFT JOIN klined k5 ON k5.stock_code = s.stock_code AND k5.rn = k0.rn + 1 + 5
    LEFT JOIN klined k10 ON k10.stock_code = s.stock_code AND k10.rn = k0.rn + 1 + 10
    LEFT JOIN klined k20 ON k20.stock_code = s.stock_code AND k20.rn = k0.rn + 1 + 20
    LEFT JOIN klined k40 ON k40.stock_code = s.stock_code AND k40.rn = k0.rn + 1 + 40
    LEFT JOIN klined k60 ON k60.stock_code = s.stock_code AND k60.rn = k0.rn + 1 + 60
    WHERE s.b1_date >= '2016-01-01' AND s.b1_date != '_sentinel_' AND k1.open > 0
""")
n = conn.execute("SELECT COUNT(*) FROM _mw_returns").fetchone()[0]
print(f"{n:,} 条 ({(datetime.now()-t0).total_seconds():.0f}s)")

# ── 1.2 加入额外因子：乖离率、市值、市场状态 ──
print("[1.2] 补充因子...", end=' ', flush=True)

# 乖离率 = (close - MA20) / MA20 * 100
conn.execute("DROP TABLE IF EXISTS _mw_deviation")
conn.execute("""
    CREATE TEMP TABLE _mw_deviation AS
    WITH k AS (
        SELECT stock_code, date, close,
               AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as ma20,
               AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as ma50
        FROM daily_kline WHERE date >= '2015-11-01' AND date <= '2026-07-31'
    )
    SELECT stock_code, date,
           (close - ma20) / ma20 * 100 as deviation_ma20,
           (close - ma50) / ma50 * 100 as deviation_ma50
    FROM k WHERE ma20 IS NOT NULL
""")
n2 = conn.execute("SELECT COUNT(*) FROM _mw_deviation").fetchone()[0]
print(f"乖离率 {n2:,} 行", end=' ', flush=True)

# 市值（从 stock_equity_change 取最新流通市值）
# 简化：用 c_amount_avg 反推（已有横盘期日均成交额）

# ── 1.3 合并到一张宽表 ──
print("\n[1.3] 构建宽表...", end=' ', flush=True)
conn.execute("DROP TABLE IF EXISTS _mw_wide")
conn.execute("""
    CREATE TEMP TABLE _mw_wide AS
    SELECT r.*,
           d.deviation_ma20, d.deviation_ma50,
           CASE WHEN r.b2_date IS NOT NULL AND r.b2_date != '' THEN 1 ELSE 0 END as has_b2,
           -- 乖离率分组
           CASE WHEN d.deviation_ma20 >= 10 THEN '高乖离≥10%'
                WHEN d.deviation_ma20 >= 0 THEN '正乖离0~10%'
                ELSE '负乖离' END as dev_ma20_group,
           -- 回调深度分组
           CASE WHEN r.decline_pct >= 35 THEN '深调≥35%'
                WHEN r.decline_pct >= 25 THEN '中调25~35%'
                WHEN r.decline_pct >= 15 THEN '浅调15~25%'
                ELSE '微调<15%' END as decline_group,
           -- 关注分分层
           CASE WHEN r.tech_score >= 80 THEN '极高≥80'
                WHEN r.tech_score >= 65 THEN '高65~79'
                WHEN r.tech_score >= 50 THEN '关注50~64'
                WHEN r.tech_score >= 35 THEN '一般35~49'
                ELSE '低<35' END as attention_tier,
           -- B1涨幅分组  
           CASE WHEN r.b1_return_pct >= 5 THEN '强B1≥5%'
                WHEN r.b1_return_pct >= 2 THEN '标准B1 2~5%'
                ELSE '弱B1<2%' END as b1_strength,
           -- 前高RS分组
           CASE WHEN r.h_rs250 >= 90 THEN 'RS≥90'
                WHEN r.h_rs250 >= 80 THEN 'RS80~89'
                WHEN r.h_rs250 >= 70 THEN 'RS70~79'
                ELSE 'RS<70' END as h_rs_group,
           -- 行业RS分组
           CASE WHEN r.ind_rs20 >= 90 THEN '行业RS≥90'
                WHEN r.ind_rs20 >= 80 THEN '行业RS80~89'
                WHEN r.ind_rs20 IS NOT NULL THEN '行业RS<80'
                ELSE '无行业RS' END as ind_rs_group
    FROM _mw_returns r
    LEFT JOIN _mw_deviation d ON d.stock_code = r.stock_code AND d.date = r.b1_date
""")
n3 = conn.execute("SELECT COUNT(*) FROM _mw_wide").fetchone()[0]
print(f"{n3:,} 条")

# ── 1.4 市场环境分类 ──
print("[1.4] 市场环境分类...", end=' ', flush=True)
conn.execute("DROP TABLE IF EXISTS _mw_market")
# 用中证全指 000985 的 60 日涨跌幅判断牛熊
conn.execute("""
    CREATE TEMP TABLE _mw_market AS
    WITH idx_returns AS (
        SELECT date, close,
               close / LAG(close, 60) OVER (ORDER BY date) - 1 as ret_60d
        FROM daily_kline WHERE stock_code = '000985' AND date >= '2015-09-01'
    )
    SELECT date,
           CASE WHEN ret_60d >= 0.15 THEN '牛市'
                WHEN ret_60d <= -0.15 THEN '熊市'
                ELSE '震荡市' END as market_regime,
           ret_60d
    FROM idx_returns WHERE ret_60d IS NOT NULL
""")
n4 = conn.execute("SELECT COUNT(*) FROM _mw_market").fetchone()[0]
print(f"{n4} 个交易日")

# ── 1.5 导出宽表 ──
print("\n[1.5] 导出数据...", end=' ', flush=True)
rows = conn.execute("""
    SELECT w.*, m.market_regime, m.ret_60d as market_ret_60d
    FROM _mw_wide w
    LEFT JOIN _mw_market m ON m.date = w.b1_date
""").fetchall()

data = [dict(r) for r in rows]
with open(os.path.join(OUT_DIR, 'mw_backtest_wide.json'), 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, default=str)
print(f"{len(data):,} 条 → mw_backtest_wide.json")

elapsed = (datetime.now() - t0).total_seconds()
print(f"\nStep 1 完成 · 耗时 {elapsed:.0f}s")
conn.close()
