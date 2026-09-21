#!/usr/bin/env python3
"""
scripts/migrate_price_caliber.py — 价格口径迁移器（分步、可干跑、可回滚）

设计原则
-------
1. **不碰 SQL 结构，只换数据源**。建一张视图 `daily_kline_adj`，把复权价以
   `open/high/low/close` 的列名暴露出来，并额外暴露 `raw_*` 四个原始价列。
   于是迁移动作退化为「把 `FROM daily_kline` 换成 `FROM daily_kline_adj`」，
   以及少数几处真正的逻辑改动（COALESCE 兜底、反推函数）。
2. **server.py 的形态 API 用 `table = 'index_daily_kline' if mode == 'index' else 'daily_kline'`
   在个股/指数间切换**，所以那里只需把 else 分支的表名换成视图名，一行一处，
   指数路径完全不受影响。
3. 每条规则都必须能校验命中数，数量不符就中止，不做“猜着改”。
4. 默认干跑，`--apply` 才写盘，写盘前把原文件备份到
   `analysis/_code_migration_backup/<时间戳>/`。
5. 幂等：规则已应用过的文件会报告「已迁移，跳过」。

用法
----
    python scripts/migrate_price_caliber.py --status          # 看各步状态
    python scripts/migrate_price_caliber.py --step 0          # 建视图（干跑）
    python scripts/migrate_price_caliber.py --step 0 --apply  # 建视图
    python scripts/migrate_price_caliber.py --step 1          # 看 diff
    python scripts/migrate_price_caliber.py --step 1 --apply  # 应用
    python scripts/migrate_price_caliber.py --scan            # 扫出剩余候选点
    python scripts/migrate_price_caliber.py --verify-view     # 校验视图可用
"""

import argparse
import csv
import datetime
import difflib
import os
import re
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from common import DB_PATH  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(ROOT)

VIEW = 'daily_kline_adj'
BACKUP_ROOT = 'analysis/_code_migration_backup'

VIEW_SQL = f"""
CREATE VIEW IF NOT EXISTS {VIEW} AS
SELECT stock_code,
       date,
       COALESCE(lxr_fc_open,  ex_open)  AS open,
       COALESCE(lxr_fc_high,  ex_high)  AS high,
       COALESCE(lxr_fc_low,   ex_low)   AS low,
       COALESCE(lxr_fc_close, ex_close) AS close,
       ex_open  AS raw_open,
       ex_high  AS raw_high,
       ex_low   AS raw_low,
       ex_close AS raw_close,
       volume,
       amount,
       change_pct,
       turnover_rate
FROM daily_kline
"""
# 说明：`lxr_fc_*` 只覆盖 2016-01-01 起，2016 前为 NULL。
#   COALESCE 回退到 ex_*（原始价），与这些代码历史上拿到的值一致，不引入新的口径错误；
#   代价是 2016-01-01 存在一个「原始价 → 复权价」的尺度接缝（现有行为也是如此，未恶化）。
#   彻底修法：补下 2016 前的 lxr_fc（约 6,266 只 × 4 窗口 ≈ 2.5 万次调用）。

# ══════════════════════════════════════════════════════════════════
# 规则表
#   step  步骤号
#   file  相对路径
#   find  原文本（必须精确，含缩进）
#   repl  新文本
#   expect 期望命中次数
#   note  说明
# ══════════════════════════════════════════════════════════════════
RULES = [
    # ── Step 1：RPS 双引擎 ──────────────────────────────────
    dict(step=1, file='src/scanners/stock_rs.py', expect=1,
         note='RPS 全市场加载：COALESCE(adj_close,close) → 视图的复权 close',
         find="        SELECT stock_code, date, COALESCE(adj_close, close) as adj_close, amount\n"
              "        FROM daily_kline\n",
         repl="        SELECT stock_code, date, close as adj_close, amount\n"
              "        FROM daily_kline_adj\n"),
    dict(step=1, file='src/detectors/stock_rs.py', expect=1,
         note='旧版 RPS：close 保留原始价（raw_close），adj_close 换成复权价',
         find="        rows = db.execute(f'''SELECT stock_code, date, close, adj_close, amount\n"
              "            FROM daily_kline WHERE stock_code IN ({ph})\n",
         repl="        rows = db.execute(f'''SELECT stock_code, date, raw_close AS close, "
              "close AS adj_close, amount\n"
              "            FROM daily_kline_adj WHERE stock_code IN ({ph})\n"),

    # ── Step 2：pattern-scan 主接口 + 观察池日报 + 删两个反推调用 ──
    dict(step=2, file='src/server.py', expect=1,
         note='pattern-scan：个股分支改用视图',
         find="    table = 'index_daily_kline' if is_index else 'daily_kline'\n"
              "    kf = \"AND kline_type='normal'\" if is_index else ''\n"
              "\n"
              "    # 获取足够的历史K线（至少2年）\n"
              "    # 个股优先用前复权价（adj_*），NULL 时退回不复权\n"
              "    chg_col = 'change' if is_index else 'change_pct'\n"
              "    if is_index:\n"
              "        ohlc = \"open, high, low, close\"\n"
              "    else:\n"
              "        ohlc = \"COALESCE(adj_open, open) as open, COALESCE(adj_high, high) as high, "
              "COALESCE(adj_low, low) as low, COALESCE(adj_close, close) as close\"\n",
         repl="    table = 'index_daily_kline' if is_index else 'daily_kline_adj'\n"
              "    kf = \"AND kline_type='normal'\" if is_index else ''\n"
              "\n"
              "    # 获取足够的历史K线（至少2年）\n"
              "    # 个股走 daily_kline_adj 视图：open/high/low/close 已是理杏仁前复权价\n"
              "    chg_col = 'change' if is_index else 'change_pct'\n"
              "    ohlc = \"open, high, low, close\"\n"),
    dict(step=2, file='src/server.py', expect=1,
         note='pattern-scan：删掉 change_pct 反推兜底（视图已是真复权价）',
         find="    # ── 前复权：用 change_pct 逆向推算（adj_*/complex_factor 大量缺失后的兜底方案）──\n"
              "    if not is_index:\n"
              "        _ensure_adj_prices(klines_full)\n",
         repl="    # 价格口径：个股走 daily_kline_adj 视图，已是理杏仁前复权价，无需再兜底\n"),
    dict(step=2, file='src/scanners/watchlist_report.py', expect=1,
         note='观察池日报：改用视图 + 删除反推兜底',
         find="        SELECT date, COALESCE(adj_open, open) as open, COALESCE(adj_high, high) as high,\n"
              "               COALESCE(adj_low, low) as low, COALESCE(adj_close, close) as close,\n"
              "               volume, amount, change_pct\n"
              "        FROM daily_kline WHERE stock_code=? AND date<=?\n",
         repl="        SELECT date, open, high, low, close, volume, amount, change_pct\n"
              "        FROM daily_kline_adj WHERE stock_code=? AND date<=?\n"),
    dict(step=2, file='src/scanners/watchlist_report.py', expect=1,
         note='观察池日报：删除 _ensure_adj_prices 调用',
         find="    # 前复权兜底（complex_factor 大量 NULL）\n"
              "    from src.server import _ensure_adj_prices\n"
              "    _ensure_adj_prices(klines)\n",
         repl="    # 价格口径：已由 daily_kline_adj 视图提供理杏仁前复权价，无需兜底\n"),
    # ── Step 3：server.py 其余形态 API 的 table 分支 ────────────
    dict(step=3, file='src/server.py', expect=16,
         note='16 个形态 API（主路径+diag）：个股分支改用复权视图，指数分支不动',
         find="    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'\n",
         repl="    table = 'index_daily_kline' if mode == 'index' else 'daily_kline_adj'\n"),
    dict(step=3, file='src/server.py', expect=1,
         note='flat-base：字面 daily_kline → 视图（该接口无 index 分支）',
         find='    rows = db.execute(f"""SELECT date, open, high, low, close, volume, amount FROM daily_kline\n'
              '        WHERE stock_code=? AND date>=date(?,?) AND date<=? ORDER BY date""",\n'
              '        (stock_code, start, extra, end)).fetchall()\n',
         repl='    rows = db.execute(f"""SELECT date, open, high, low, close, volume, amount FROM daily_kline_adj\n'
              '        WHERE stock_code=? AND date>=date(?,?) AND date<=? ORDER BY date""",\n'
              '        (stock_code, start, extra, end)).fetchall()\n'),
    dict(step=3, file='src/server.py', expect=1,
         note='saucer-base：字面 daily_kline → 视图',
         find='            SELECT date, open, high, low, close, volume\n'
              '            FROM daily_kline\n'
              "            WHERE stock_code = ? AND date <= ? AND date >= date(?, '-400 days')\n",
         repl='            SELECT date, open, high, low, close, volume\n'
              '            FROM daily_kline_adj\n'
              "            WHERE stock_code = ? AND date <= ? AND date >= date(?, '-400 days')\n"),
    dict(step=3, file='src/server.py', expect=1,
         note='shareholder-low：近 61 日算 chg20/chg60，跨期收益率改用复权价',
         find='SELECT date, close FROM daily_kline WHERE stock_code=?',
         repl='SELECT date, close FROM daily_kline_adj WHERE stock_code=?'),
    dict(step=3, file='src/server.py', expect=1,
         note='index-constituents：成分股 1/5/10/20 日涨跌幅，跨期比率改用复权价',
         find=('        (SELECT close FROM daily_kline WHERE stock_code=ic.stock_code AND date<=? '
               'ORDER BY date DESC LIMIT 1) as close,\n'
               '        (SELECT close FROM daily_kline WHERE stock_code=ic.stock_code AND date<=(SELECT date FROM daily_kline '
               'WHERE stock_code=ic.stock_code AND date<=? ORDER BY date DESC LIMIT 1 OFFSET 1) ORDER BY date DESC LIMIT 1) as prev_close,\n'
               '        (SELECT close FROM daily_kline WHERE stock_code=ic.stock_code AND date<=(SELECT date FROM daily_kline '
               'WHERE stock_code=ic.stock_code AND date<=? ORDER BY date DESC LIMIT 1 OFFSET 4) ORDER BY date DESC LIMIT 1) as close_5d_ago,\n'
               '        (SELECT close FROM daily_kline WHERE stock_code=ic.stock_code AND date<=(SELECT date FROM daily_kline '
               'WHERE stock_code=ic.stock_code AND date<=? ORDER BY date DESC LIMIT 1 OFFSET 9) ORDER BY date DESC LIMIT 1) as close_10d_ago,\n'
               '        (SELECT close FROM daily_kline WHERE stock_code=ic.stock_code AND date<=(SELECT date FROM daily_kline '
               'WHERE stock_code=ic.stock_code AND date<=? ORDER BY date DESC LIMIT 1 OFFSET 19) ORDER BY date DESC LIMIT 1) as close_20d_ago\n'),
         repl=('        (SELECT close FROM daily_kline_adj WHERE stock_code=ic.stock_code AND date<=? '
               'ORDER BY date DESC LIMIT 1) as close,\n'
               '        (SELECT close FROM daily_kline_adj WHERE stock_code=ic.stock_code AND date<=(SELECT date FROM daily_kline_adj '
               'WHERE stock_code=ic.stock_code AND date<=? ORDER BY date DESC LIMIT 1 OFFSET 1) ORDER BY date DESC LIMIT 1) as prev_close,\n'
               '        (SELECT close FROM daily_kline_adj WHERE stock_code=ic.stock_code AND date<=(SELECT date FROM daily_kline_adj '
               'WHERE stock_code=ic.stock_code AND date<=? ORDER BY date DESC LIMIT 1 OFFSET 4) ORDER BY date DESC LIMIT 1) as close_5d_ago,\n'
               '        (SELECT close FROM daily_kline_adj WHERE stock_code=ic.stock_code AND date<=(SELECT date FROM daily_kline_adj '
               'WHERE stock_code=ic.stock_code AND date<=? ORDER BY date DESC LIMIT 1 OFFSET 9) ORDER BY date DESC LIMIT 1) as close_10d_ago,\n'
               '        (SELECT close FROM daily_kline_adj WHERE stock_code=ic.stock_code AND date<=(SELECT date FROM daily_kline_adj '
               'WHERE stock_code=ic.stock_code AND date<=? ORDER BY date DESC LIMIT 1 OFFSET 19) ORDER BY date DESC LIMIT 1) as close_20d_ago\n')),

    # ── Step 4：持仓监控与驾驶舱 ──────────────────────────
    # 不改的（真实价场景）：monitoring._get_latest_close（最新价快照）
    #   briefing:170/324/316/335（市值、入出场参考价）、monitoring:187（只取 volume）
    dict(step=4, file='src/discipline/monitoring.py', expect=1,
         note='持仓监控：MA50 + 连续5日下跌（除权日会误报破位）',
         find='SELECT close, date FROM daily_kline\n',
         repl='SELECT close, date FROM daily_kline_adj\n'),
    dict(step=4, file='src/discipline/screener.py', expect=1,
         note='精选筛选：400 根 K 线供技术形态层打分',
         find='FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 400',
         repl='FROM daily_kline_adj WHERE stock_code=? ORDER BY date DESC LIMIT 400'),
    dict(step=4, file='src/cockpit/briefing.py', expect=1,
         note='驾驶舱简报：近5日涨跌',
         find='"SELECT close FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 6"',
         repl='"SELECT close FROM daily_kline_adj WHERE stock_code=? ORDER BY date DESC LIMIT 6"'),
    # ⚠️ briefing 的 MA10 暂不改：它参与 calculate_stop_loss(st, entry_price, ..., ma10)，
    #    而 entry_price / h_price / l_price / b2_low 全是**原始价**尺度（后者来自 mw_signal_daily）。
    #    只改 ma10 会让同一次止损计算混用两套尺度，有分红时算出偏低的止损位。
    #    待 Step 5 把 mw_signal 的价格也改成复权后，两者一起改。
    dict(step=4, file='src/cockpit/sentiment.py', expect=1,
         note='舆情摘要：近5日涨跌',
         find='"SELECT close FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 6"',
         repl='"SELECT close FROM daily_kline_adj WHERE stock_code=? ORDER BY date DESC LIMIT 6"'),
    dict(step=4, file='src/cockpit/oneil_deep.py', expect=1,
         note='欧奈尔深度：MA5-250 与偏离度',
         find='SELECT close FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 250',
         repl='SELECT close FROM daily_kline_adj WHERE stock_code=? ORDER BY date DESC LIMIT 250'),
    dict(step=4, file='src/cockpit/oneil_deep.py', expect=1,
         note='欧奈尔深度：近20日K线展示',
         find='SELECT date, open, high, low, close, volume FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 20',
         repl='SELECT date, open, high, low, close, volume FROM daily_kline_adj WHERE stock_code=? ORDER BY date DESC LIMIT 20'),

    # ── Step 5：形态引擎群（scanners）──────────────────────
    # 统一改为 FROM daily_kline_adj（视图里 open/high/low/close 已是理杏仁前复权价）
    # 说明：少数只取 volume / stock_code / MAX(date) 的行也一并改——这些列在视图里
    #       取值完全相同，改了无副作用，换来「一个文件一条规则」的简洁。
    dict(step=5, file='src/scanners/base_detector.py', expect=1,
         note='基部检测', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/box_breakdown.py', expect=1,
         note='跌破箱体：K线读取', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/box_breakout.py', expect=1,
         note='放量箱体突破：K线读取', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/canslim_score.py', expect=5,
         note='CANSLIM：N分(52周新高)/L分(21日超额)/形态引擎',
         find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/cdl_engine.py', expect=1,
         note='TA-Lib K线形态（CLI调试）', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/chanlun_backtest_compare.py', expect=1,
         note='缠论vs欧奈尔回测对比', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/chanlun_scan.py', expect=3,
         note='缠论批量扫描', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/climax_top.py', expect=3,
         note='高潮见顶', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/cup_handle.py', expect=1,
         note='杯柄形态', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/double_bottom.py', expect=1,
         note='双重底', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/flat_base.py', expect=1,
         note='平台整理基部', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/market_health.py', expect=13,
         note='大盘环境：涨跌家数/新高新低/MA50占比/放量突破/行业加权',
         find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/market_sell_score.py', expect=6,
         note='大盘卖出评分：龙头股回落/低价高价股涨幅',
         find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/mw_signal.py', expect=5,
         note='MW 信号引擎：H前高/L低点/C区间/B1B2（核心）',
         find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/pattern_structure.py', expect=1,
         note='形态结构：MA5-60 + H/L/C/B1B2', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/pocket_pivot.py', expect=1,
         note='口袋支点 V1', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/pocket_pivot_v2.py', expect=2,
         note='口袋支点 V2', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/railroad_tracks.py', expect=1,
         note='铁轨线', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/recommend.py', expect=1,
         note='推荐（CLI调试）', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/saucer_base.py', expect=3,
         note='碟形基部', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/talib_engine.py', expect=1,
         note='TA-Lib 指标（CLI调试）', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/top_pattern.py', expect=1,
         note='头部形态', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/volume_divergence.py', expect=1,
         note='量价背离', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/volume_stall.py', expect=1,
         note='放量滞涨', find='FROM daily_kline', repl='FROM daily_kline_adj'),
    dict(step=5, file='src/scanners/watchlist_report.py', expect=1,
         note='观察池日报：scan_date 取值', find='FROM daily_kline', repl='FROM daily_kline_adj'),

    # cpa_stage 特殊：原查询取 close + adj_close 算复权因子。改用视图后，
    # 把 close 指向 raw_close（保持「真实价」语义）、adj_close 指向视图的 close（复权价），
    # 于是 fac = 复权/原始 = 真实复权因子，输出与原语义一致但数据正确。
    dict(step=5, file='src/scanners/cpa_stage.py', expect=1,
         note='CPA 阶段判定：close→raw_close，adj_close→复权 close',
         find='"""SELECT date, open, high, low, close, adj_close, volume, amount\n'
              '           FROM daily_kline WHERE stock_code=? AND date>=? ORDER BY date""",',
         repl='"""SELECT date, raw_open AS open, raw_high AS high, raw_low AS low,\n'
              '                  raw_close AS close, close AS adj_close, volume, amount\n'
              '           FROM daily_kline_adj WHERE stock_code=? AND date>=? ORDER BY date""",'),

    # chanlun 用 table 变量在「指数优先、否则个股」间切：只改个股分支
    dict(step=5, file='src/scanners/chanlun.py', expect=4,
         note='缠论：table 变量的个股分支（双引号）',
         find='"daily_kline"', repl='"daily_kline_adj"'),
    dict(step=5, file='src/scanners/chanlun.py', expect=2,
         note='缠论：table 变量的个股分支（单引号）',
         find="'daily_kline'", repl="'daily_kline_adj'"),

    # 三个 CLI 脚本：同样用 args.mode 切指数/个股，只改 else 分支
    dict(step=5, file='src/scanners/base_breakout.py', expect=1,
         note='基部突破：else 分支改视图，指数分支不动',
         find="else 'daily_kline'", repl="else 'daily_kline_adj'"),
    dict(step=5, file='src/scanners/base_breakout_v2.py', expect=1,
         note='基部突破V2：else 分支改视图',
         find="else 'daily_kline'", repl="else 'daily_kline_adj'"),
    dict(step=5, file='src/scanners/breakout_failure.py', expect=1,
         note='突破失败：else 分支改视图',
         find="else 'daily_kline'", repl="else 'daily_kline_adj'"),

    # 删掉 change_pct 反推前复权的旧实现（数据已是真复权价，反推是重复劳动）
    dict(step=5, file='src/scanners/box_breakout.py', expect=1,
         note='box_breakout：删 _adj_prices 调用',
         find='    # 前复权（若调用方已复权则 change_pct 反推结果 ≈ 原值，幂等）\n'
              '    daily = _adj_prices([dict(k) for k in daily])\n',
         repl='    # 价格口径：调用方传入的已是复权价（daily_kline_adj 视图），无需再反推\n'),
    dict(step=5, file='src/scanners/box_breakdown.py', expect=1,
         note='box_breakdown：删 _adj_prices 调用',
         find='    daily = _adj_prices([dict(k) for k in daily])\n',
         repl='    # 价格口径：调用方传入的已是复权价（daily_kline_adj 视图），无需再反推\n'),

    # ── Step 6：回测 ──────────────────────────────────────
    # 注：mw_backtest.py:515 取 B2 日收盘价用于算市值（close × 总股本）→ 真实价场景，保持不改
    dict(step=6, file='src/analytics/mw_backtest.py', expect=1,
         note='MW 回测：B2 后 forward return 的 price_cache',
         find='SELECT date, close FROM daily_kline\n            WHERE stock_code=? AND date >= ?',
         repl='SELECT date, close FROM daily_kline_adj\n            WHERE stock_code=? AND date >= ?'),
    dict(step=6, file='src/analytics/mw_backtest.py', expect=1,
         note='MW 回测：随机基准与组合模拟取价',
         find='SELECT date, close FROM daily_kline WHERE stock_code=? AND date >= ? ORDER BY date',
         repl='SELECT date, close FROM daily_kline_adj WHERE stock_code=? AND date >= ? ORDER BY date'),
    dict(step=6, file='src/discipline/trades_api.py', expect=1,
         note='精选回测：信号后 5/10/20 日收益',
         find='SELECT date, close FROM daily_kline\n                WHERE stock_code = ? AND date >= ? ORDER BY date',
         repl='SELECT date, close FROM daily_kline_adj\n                WHERE stock_code = ? AND date >= ? ORDER BY date'),
    dict(step=6, file='src/discipline/trades_api.py', expect=2,
         note='_lookup_kline：实时引擎群入参与理想买点',
         find='FROM daily_kline WHERE stock_code = ? ORDER BY date DESC LIMIT ?',
         repl='FROM daily_kline_adj WHERE stock_code = ? ORDER BY date DESC LIMIT ?'),

    # ── Step 7：阶段 4 —— scripts 层的回测/指数脚本 ──────────
    # 这些脚本多数同时需要「原始价」与「复权价」，用别名把视图的两套列映射回原列名。
    dict(step=7, file='scripts/backtest_signals.py', expect=1,
         note='全信号回测框架：K线加载（原始 OHLC + 复权 OHLC 都要）',
         find='SELECT stock_code, date, open, high, low, close, volume, amount,\n                   adj_close, adj_open, adj_high, adj_low\n            FROM daily_kline',
         repl='SELECT stock_code, date, raw_open AS open, raw_high AS high, raw_low AS low,\n                   raw_close AS close, volume, amount,\n                   close AS adj_close, open AS adj_open, high AS adj_high, low AS adj_low\n            FROM daily_kline_adj'),
    dict(step=7, file='scripts/backtest_signals.py', expect=1,
         note='全信号回测框架：交易日历',
         find='SELECT DISTINCT date FROM daily_kline', repl='SELECT DISTINCT date FROM daily_kline_adj'),
    dict(step=7, file='scripts/mw_full_backtest_v2.py', expect=1,
         note='MW 回测：交易日历',
         find='SELECT DISTINCT date FROM daily_kline', repl='SELECT DISTINCT date FROM daily_kline_adj'),
    dict(step=7, file='scripts/mw_full_backtest_v2.py', expect=1,
         note='MW 回测：T+3 取价（需要复权 OHLC）',
         find='SELECT stock_code, date, adj_open, adj_close FROM daily_kline',
         repl='SELECT stock_code, date, open AS adj_open, close AS adj_close FROM daily_kline_adj'),
    dict(step=7, file='scripts/build_microcap_index.py', expect=1,
         note='微盘股指数：交易日列表',
         find='SELECT DISTINCT date FROM daily_kline WHERE date >= ? ORDER BY date',
         repl='SELECT DISTINCT date FROM daily_kline_adj WHERE date >= ? ORDER BY date'),
    dict(step=7, file='scripts/build_microcap_index.py', expect=1,
         note='微盘股指数：恢复增量状态（原始 close + 复权 adj_close）',
         find='SELECT stock_code, close, adj_close FROM daily_kline WHERE date=?',
         repl='SELECT stock_code, raw_close AS close, close AS adj_close FROM daily_kline_adj WHERE date=?'),
    dict(step=7, file='scripts/build_microcap_index.py', expect=1,
         note='微盘股指数：停牌股回溯基准复权价',
         find='SELECT adj_close, close FROM daily_kline WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1',
         repl='SELECT close AS adj_close, raw_close AS close FROM daily_kline_adj WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1'),
    dict(step=7, file='scripts/build_microcap_index.py', expect=1,
         note='微盘股指数：流式逐日取价（原始 close + 复权 adj_close）',
         find='SELECT stock_code, date, close, adj_close FROM daily_kline WHERE date >= ? ',
         repl='SELECT stock_code, date, raw_close AS close, close AS adj_close FROM daily_kline_adj WHERE date >= ? '),
    dict(step=7, file='scripts/hanako_loader.py', expect=1,
         note='skill 复用入口：K线加载（原始 OHLC + adj_close）',
         find='SELECT stock_code, date,\n                       open, high, low, close, volume, amount,\n                       adj_close\n                FROM daily_kline',
         repl='SELECT stock_code, date,\n                       raw_open AS open, raw_high AS high, raw_low AS low,\n                       raw_close AS close, volume, amount,\n                       close AS adj_close\n                FROM daily_kline_adj'),
    dict(step=7, file='scripts/backtest_bb_pp2_replay.py', expect=1,
         note='BB/PP 回放：K线加载（去掉 COALESCE 兜底，视图已是复权价）',
         find='SELECT date, COALESCE(adj_open, open) as open,\n        COALESCE(adj_high, high) as high, COALESCE(adj_low, low) as low,\n        COALESCE(adj_close, close) as close, volume, amount, change_pct\n        FROM daily_kline',
         repl='SELECT date, open, high, low, close, volume, amount, change_pct\n        FROM daily_kline_adj'),
    dict(step=7, file='scripts/backtest_bb_pp2_replay.py', expect=2,
         note='BB/PP 回放：股票列表',
         find='SELECT DISTINCT stock_code FROM daily_kline WHERE date>=? AND date<=?',
         repl='SELECT DISTINCT stock_code FROM daily_kline_adj WHERE date>=? AND date<=?'),
    dict(step=7, file='scripts/backtest_bb_pp2_replay.py', expect=1,
         note='BB/PP 回放：交易日列表',
         find='SELECT DISTINCT date FROM daily_kline WHERE date>=? AND date<=? ORDER BY date',
         repl='SELECT DISTINCT date FROM daily_kline_adj WHERE date>=? AND date<=? ORDER BY date'),
    dict(step=7, file='scripts/backtest_bb_pp2_replay.py', expect=1,
         note='BB/PP 回放：复权收盘价映射',
         find='SELECT date, close, change_pct FROM daily_kline',
         repl='SELECT date, close, change_pct FROM daily_kline_adj'),
    dict(step=7, file='scripts/replay_watchlist_rules.py', expect=1,
         note='观察池规则回放：复权收盘价',
         find='SELECT date, COALESCE(adj_close, close) as close FROM daily_kline',
         repl='SELECT date, close FROM daily_kline_adj'),
    dict(step=7, file='scripts/scan_bb_pp2_confluence.py', expect=1,
         note='BB/PP 共振扫描：K线加载（去 COALESCE）',
         find='SELECT date, COALESCE(adj_open, open) as open, COALESCE(adj_high, high) as high,\n        COALESCE(adj_low, low) as low, COALESCE(adj_close, close) as close, volume, amount, change_pct\n        FROM daily_kline',
         repl='SELECT date, open, high, low, close, volume, amount, change_pct\n        FROM daily_kline_adj'),
    dict(step=7, file='scripts/scan_bb_pp2_confluence.py', expect=1,
         note='BB/PP 共振扫描：股票列表',
         find='SELECT DISTINCT stock_code FROM daily_kline WHERE date >= ? AND date <= ?',
         repl='SELECT DISTINCT stock_code FROM daily_kline_adj WHERE date >= ? AND date <= ?'),
]

STEPS = {
    0: '建 daily_kline_adj 视图（数据库操作，不改代码）',
    1: 'RPS 双引擎（scanners/stock_rs.py + detectors/stock_rs.py）',
    2: 'pattern-scan 主接口 + 观察池日报 + 删反推调用',
    3: 'server.py 其余形态 API（16 处 table 分支 + 4 处字面表名）',
    4: '持仓监控与驾驶舱（monitoring/screener/briefing/sentiment/oneil_deep）',5: '形态引擎群（scanners 30 个文件）+ 删 box_breakout._adj_prices',
    6: '回测（analytics/mw_backtest.py / discipline/trades_api.py / chanlun_backtest_compare.py）',
    7: '阶段4：scripts 层的回测/指数脚本（backtest_signals / mw_full_backtest_v2 / microcap / hanako_loader / bb_pp2 系列）',
}


# ══════════════════════════════════════════════════════════════════
def step0(apply):
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='view' AND name=?", (VIEW,))
    exists = cur.fetchone() is not None
    print(f'  视图 {VIEW} 现状：{"已存在" if exists else "不存在"}')
    if exists:
        print('  已存在，将先删除再按当前定义重建（视图无数据，不影响表）')
    print('  --- 待执行 SQL ---')
    print(VIEW_SQL.strip())
    if apply:
        conn.executescript(f'DROP VIEW IF EXISTS {VIEW};' + VIEW_SQL)
        conn.commit()
        print(f'  [OK] 已重建视图 {VIEW}')
    else:
        print('  （干跑，加 --apply 执行）')
    if exists or apply:
        n = conn.execute(f"SELECT COUNT(*) FROM {VIEW}").fetchone()[0]
        tot = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
        nnull = conn.execute(f"SELECT COUNT(*) FROM {VIEW} WHERE close IS NULL").fetchone()[0]
        for col in ('open', 'close', 'raw_close'):
            v = conn.execute(f"SELECT {col} FROM {VIEW} WHERE stock_code='600309' "
                             f"ORDER BY date DESC LIMIT 1").fetchone()
            print(f'  校验 600309 最新 {col} = {v[0] if v else None}')
        print(f'  视图行数 {n:,} / 表行数 {tot:,}  {"" if n == tot else "不一致"}')
        print(f'  close 为空的行 {nnull:,}  {"全覆盖" if nnull == 0 else "[WARN] 仍有空值"}')
    conn.close()
    return 0


def load_rules(step):
    return [r for r in RULES if r['step'] == step]


def apply_step(step, apply, show_diff=True):
    rules = load_rules(step)
    if not rules:
        print(f'  [WARN] Step {step} 尚未录入规则。用 --scan 查看候选点，或等规则补齐。')
        print(f'     步骤说明：{STEPS.get(step, "?")}')
        return 2

    by_file = {}
    for r in rules:
        by_file.setdefault(r['file'], []).append(r)

    total_ok = total_skip = total_abort = 0
    pending = {}          # file -> new_text
    print(f'  Step {step}：{STEPS.get(step, "")}')
    print(f'  规则 {len(rules)} 条，涉及 {len(by_file)} 个文件')
    print('-' * 78)

    for f, rs in by_file.items():
        path = os.path.join(ROOT, f)
        if not os.path.exists(path):
            print(f'  {f}  文件不存在')
            total_abort += len(rs)
            continue
        text = open(path, encoding='utf-8').read()
        orig = text
        for r in rs:
            n_find = text.count(r['find'])
            n_repl = text.count(r['repl'])
            # ⚠️ find 可能是 repl 的前缀（如 FROM daily_kline → FROM daily_kline_adj），
            #    这时已迁移的部分也会被 n_find 统计到，且直接 replace 会写成 _adj_adj。
            #    做法：用占位符把「已迁移的」先扣离，只对真正的未迁移部分做替换。
            prefix_case = (r['find'] in r['repl'] and r['find'] != r['repl'])
            if prefix_case:
                ph = '\x00MIGRATED\x00'
                guarded = text.replace(r['repl'], ph)
                n_eff = guarded.count(r['find'])
                n_done = n_repl
            else:
                guarded, n_eff, n_done = text, n_find, n_repl

            if n_eff == r['expect']:
                if prefix_case:
                    text = guarded.replace(r['find'], r['repl']).replace(ph, r['repl'])
                else:
                    text = text.replace(r['find'], r['repl'])
                print(f'  {f}  命中 {n_eff}/{r["expect"]}  {r["note"]}')
                total_ok += 1
            elif n_eff == 0 and n_done >= 1:
                print(f'  · {f}  已迁移，跳过  {r["note"]}')
                total_skip += 1
            else:
                print(f'  {f}  待迁移 {n_eff} 处（原始 {n_find} / 已迁移 {n_done}），'
                      f'期望 {r["expect"]} 处 → 中止本条  {r["note"]}')
                total_abort += 1
        if text != orig:
            pending[f] = text

    print('-' * 78)
    print(f'  命中 {total_ok} / 已迁移 {total_skip} / 中止 {total_abort}')

    if pending and show_diff:
        print()
        for f, new in pending.items():
            old = open(os.path.join(ROOT, f), encoding='utf-8').read().splitlines(keepends=True)
            newl = new.splitlines(keepends=True)
            d = list(difflib.unified_diff(old, newl, fromfile=f'原 {f}', tofile=f'新 {f}', n=2))
            print(''.join(d[:80]))

    if not apply:
        print('\n  （干跑模式，加 --apply 才会写盘）')
        return 0

    if total_abort:
        print('\n  存在中止项，为安全起见不写盘。请先核对规则。')
        return 3

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    bdir = os.path.join(ROOT, BACKUP_ROOT, ts)
    os.makedirs(bdir, exist_ok=True)
    for f, new in pending.items():
        dst = os.path.join(bdir, f.replace('/', '__'))
        shutil.copy2(os.path.join(ROOT, f), dst)
        open(os.path.join(ROOT, f), 'w', encoding='utf-8').write(new)
        print(f'  {f}  ← 原文件已备份到 {os.path.relpath(dst, ROOT)}')
    print(f'\n  [OK] Step {step} 已应用 {len(pending)} 个文件，备份目录 {os.path.relpath(bdir, ROOT)}')
    print('     建议：python -c "import py_compile,glob,sys; [py_compile.compile(p,doraise=True) for p in glob.glob(\'src/**/*.py\',recursive=True)]"')
    return 0


# ══════════════════════════════════════════════════════════════════
def scan():
    """扫出所有读 daily_kline 价格列的候选点，标注风险"""
    pats = [
        ('OHLC4', re.compile(r'\bopen\s*,\s*high\s*,\s*low\s*,\s*close\b')),
        ('COALESCE_adj', re.compile(r'COALESCE\s*\(\s*adj_\w+\s*,')),
        ('adj_col', re.compile(r'\badj_(open|high|low|close)\b')),
        ('close_only', re.compile(r"SELECT\s+[\w,\s]*\bclose\b\s*(,|FROM|$)")),
        ('table_var', re.compile(r"table = 'index_daily_kline' if")),
        ('from_dk', re.compile(r'FROM\s+daily_kline\b')),
        ('from_var', re.compile(r'FROM\s+\{table\}')),
        ('ensure_adj', re.compile(r'_ensure_adj_prices|_adj_prices\(')),
    ]
    rows = []
    for base in ('src', 'scripts'):
        for dp, dn, fn in os.walk(os.path.join(ROOT, base)):
            dn[:] = [d for d in dn if d not in ('__pycache__', 'backup')]
            for f in fn:
                if not f.endswith('.py') or f.startswith(('_diag', '_probe', '_check')):
                    continue
                p = os.path.join(dp, f)
                rel = os.path.relpath(p, ROOT).replace('\\', '/')
                if rel.startswith('scripts/migrate_price_caliber'):
                    continue
                try:
                    lines = open(p, encoding='utf-8', errors='ignore').read().split('\n')
                except OSError:
                    continue
                for i, ln in enumerate(lines, 1):
                    if 'daily_kline_adj' in ln:      # 已迁移的行不计入候选
                        continue
                    hits = [n for n, rx in pats if rx.search(ln)]
                    if not hits:
                        continue
                    risky = 'table_var' in hits or 'from_var' in hits
                    rows.append({'file': rel, 'line': i, 'tags': '|'.join(hits),
                                 'risky_index_mixed': 'Y' if risky else '',
                                 'text': ln.strip()[:130]})
    out = 'analysis/price_caliber_candidates.csv'
    os.makedirs('analysis', exist_ok=True)
    with open(out, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    from collections import Counter
    c = Counter()
    for r in rows:
        for t in r['tags'].split('|'):
            c[t] += 1
    print(f'候选点 {len(rows)} 条 → {out}')
    for k, v in c.most_common():
        print(f'   {k:16s} {v:>5}')
    print(f'\n  其中个股/指数共用 table 变量（需按 mode 分支处理）：'
          f'{sum(1 for r in rows if r["risky_index_mixed"]):,} 处')
    return 0


def status():
    print('迁移步骤与规则状态')
    print('=' * 78)
    for s, desc in STEPS.items():
        n = len(load_rules(s))
        mark = f'规则 {n} 条' if n else '规则待补'
        print(f'  Step {s}  {desc}')
        print(f'           {mark}')
    print()
    print('说明：Step 0 是数据库操作，Step 7 的重算脚本另附。')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--step', type=int)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--scan', action='store_true')
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--verify-view', action='store_true')
    ap.add_argument('--no-diff', action='store_true')
    a = ap.parse_args()

    if a.status:
        return status()
    if a.scan:
        return scan()
    if a.verify_view:
        return step0(a.apply)
    if a.step is None:
        ap.print_help()
        return status()
    if a.step == 0:
        return step0(a.apply)
    return apply_step(a.step, a.apply, show_diff=not a.no_diff)


if __name__ == '__main__':
    sys.exit(main())
