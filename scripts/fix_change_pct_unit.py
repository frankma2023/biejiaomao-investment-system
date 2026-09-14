#!/usr/bin/env python3
"""
scripts/fix_change_pct_unit.py — change_pct 单位统一为小数

背景
----
daily_kline.change_pct 绝大多数行是**小数量纲**（0.0123 = 1.23%），
但有 95 只股票的历史段存成了**百分数量纲**（19.17 表示 19.17%），共 228,886 行。
清单见 analysis/change_pct_unit_manifest.csv（由 build_change_pct_manifest.py 生成）。

方向：统一为小数。依据：
  1. 2016 年起 99.44%、2016 年前 98.15% 已是小数，改动量最小
  2. 下游代码普遍按小数理解并有注释写死，例如
     src/scanners/watchlist_report.py:131「change_pct 库内为比率(0.0036=+0.36%)」
     web/discipline/reports/watchlist-report.js:91 展示时 ×100
     scripts/audit_close_integrity.py:27 用 ABS(close/pc-1-change_pct) 同量纲相减
  3. change_pct 的语义是「除权调整后收益率」，与 close/prev_close-1 同量纲

为什么不能靠数值范围识别
----------------------
两套单位在 |change_pct| < 0.31 完全重叠（cp=0.2 可能是 20%，也可能是 0.2%）。
本脚本用**相邻收盘价反算真值**做判定，并把判断结果与 manifest 的区间互相印证。

做法
----
对每只受影响股票：
  1. 用 close/LAG(close) 归类每一行（小数 / 百分数 / 其他）
     「其他」是除权日：change_pct 是除权调整后收益，与原始环比本就不等，属正常
  2. 取百分数行的日期跨度为待改区间；**校验该区间内没有小数形态的行**
     实测 000018 / 600652 区间内小数行数为 0，全部行（含除权日）都是百分数形态
  3. 区间干净 → 整段 /100（含除权日行，它们同样是百分数形态）
     区间混有小数列 → 退化为逐行 /100，只改被判定为百分数的行，并单独报告
  4. 备份原值到 analysis/_change_pct_backup.csv

用法
----
    python scripts/fix_change_pct_unit.py            # 干跑：只报告每只股票的处理方式与行数
    python scripts/fix_change_pct_unit.py --apply    # 执行（先写备份）
"""

import csv
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, 'scripts')
from common import DB_PATH  # noqa: E402

APPLY = '--apply' in sys.argv
MANIFEST = 'analysis/change_pct_unit_manifest.csv'
BACKUP = 'analysis/_change_pct_backup.csv'
FLAT_TOL = 0.005           # |真实涨跌| <= 0.5% 的行不参与形态判定（无法区分单位）

c = sqlite3.connect(str(DB_PATH), timeout=600)
c.row_factory = sqlite3.Row

codes = [r['stock_code'] for r in csv.DictReader(open(MANIFEST, encoding='utf-8'))]
print(f'manifest 载入 {len(codes)} 只股票\n')
print(f'{"代码":<8}{"百分数行":>8}{"区间":>26}{"区间内行数":>10}{"内含小数":>9}  处理方式')
print('-' * 92)

plan = []          # (code, mode, seg0, seg1)
for code in codes:
    rows = c.execute("""
        WITH t AS (SELECT date, close, change_pct,
                   LAG(close) OVER (ORDER BY date) pc
                   FROM daily_kline WHERE stock_code=?)
        SELECT date, close, pc, change_pct FROM t
        WHERE change_pct IS NOT NULL AND pc > 0 ORDER BY date""", (code,)).fetchall()
    pct_dates, dec_dates = [], []
    for r in rows:
        true = r['close'] / r['pc'] - 1
        if abs(true) <= FLAT_TOL:
            continue
        tol = max(0.0006, abs(true) * 0.03)
        cp = r['change_pct']
        if abs(cp - true) <= tol:
            dec_dates.append(r['date'])
        elif abs(cp / 100 - true) <= tol:
            pct_dates.append(r['date'])
    if not pct_dates:
        continue
    s0 = min(pct_dates)

    # 结构性断点：找到第一段「密集小数」（30 天内 >=5 行），其之前才是百分数段。
    # 依据：95 只的百分数形态均终止于 2016-09-30 前（与 close 口径接缝同日），
    # 只有 2 只 ETF 例外（段内无小数，会自然落到 last_P）。
    from datetime import date as _d, timedelta as _td

    def _dt(x):
        y, m, dd = map(int, x.split('-'))
        return _d(y, m, dd)

    dec_sorted = sorted(dec_dates)
    cut = None
    for i, d1 in enumerate(dec_sorted):
        if d1 <= s0:
            continue
        cnt = sum(1 for d in dec_sorted[i:] if _dt(d) <= _dt(d1) + _td(days=30))
        if cnt >= 5:
            cut = d1
            break
    s1 = max((d for d in pct_dates if cut is None or d < cut), default=max(pct_dates))
    n_in = c.execute("SELECT COUNT(*) n FROM daily_kline WHERE stock_code=? "
                     "AND date>=? AND date<=? AND change_pct IS NOT NULL",
                     (code, s0, s1)).fetchone()['n']
    d_in_seg = sorted(d for d in dec_dates if s0 <= d <= s1)
    mode = 'range' if len(d_in_seg) <= 2 else 'row'
    plan.append((code, mode, s0, s1, d_in_seg))
    note = '整段 /100' if mode == 'range' else '逐行 /100'
    if cut:
        note += f'（断点 {cut}）'
    if d_in_seg:
        note += f' 排除 {len(d_in_seg)} 行'
    print(f'{code:<8}{len(pct_dates):>8}{f"{s0} ~ {s1}":>26}{n_in:>10}{len(d_in_seg):>9}  {note}')

n_range = sum(1 for p in plan if p[1] == 'range')
print(f'\n共 {len(plan)} 只：整段处理 {n_range} 只，逐行处理 {len(plan) - n_range} 只')

if not APPLY:
    print('（干跑模式，加 --apply 执行）')
    c.close()
    sys.exit(0)

# ── 备份 ──
os.makedirs('analysis', exist_ok=True)
n_backup = 0
with open(BACKUP, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['stock_code', 'date', 'change_pct_old'])
    for code, mode, s0, s1, _d_in in plan:
        for r in c.execute("SELECT date, change_pct FROM daily_kline "
                           "WHERE stock_code=? AND date>=? AND date<=? "
                           "AND change_pct IS NOT NULL ORDER BY date", (code, s0, s1)):
            w.writerow([code, r['date'], r['change_pct']])
            n_backup += 1
print(f'📦 已备份 {n_backup:,} 行原值 → {BACKUP}')

# ── 执行 ──
total = 0
for code, mode, s0, s1, d_in in plan:
    excl, params = '', [code, s0, s1]
    if d_in:
        excl = ' AND date NOT IN (%s)' % ','.join('?' * len(d_in))
        params += d_in
    if mode == 'range':
        cur = c.execute("UPDATE daily_kline SET change_pct = change_pct / 100.0 "
                        "WHERE stock_code=? AND date>=? AND date<=? "
                        "AND change_pct IS NOT NULL" + excl, params)
        total += cur.rowcount
    else:
        rows = c.execute("""
            WITH t AS (SELECT rowid AS rid, date, close, change_pct,
                       LAG(close) OVER (ORDER BY date) pc
                       FROM daily_kline WHERE stock_code=?)
            SELECT rid, change_pct, close / pc - 1 AS true_ret FROM t
            WHERE change_pct IS NOT NULL AND pc > 0 AND date>=? AND date<=?""",
            (code, s0, s1)).fetchall()
        rids = []
        for r in rows:
            t = r['true_ret']
            if abs(t) <= FLAT_TOL:
                continue
            tol = max(0.0006, abs(t) * 0.03)
            if abs(r['change_pct'] / 100 - t) <= tol and abs(r['change_pct'] - t) > tol:
                rids.append(r['rid'])
        for i in range(0, len(rids), 5000):
            chunk = rids[i:i + 5000]
            ph = ','.join('?' * len(chunk))
            cur = c.execute(f"UPDATE daily_kline SET change_pct = change_pct / 100.0 "
                            f"WHERE rowid IN ({ph})", chunk)
            total += cur.rowcount
c.commit()
print(f'✅ 已修改 {total:,} 行')

# ── 复核：重跑分类，确认不再有百分数行 ──
left = 0
for code in codes:
    rows = c.execute("""
        WITH t AS (SELECT date, close, change_pct, LAG(close) OVER (ORDER BY date) pc
                   FROM daily_kline WHERE stock_code=?)
        SELECT change_pct, close / pc - 1 AS true_ret FROM t
        WHERE change_pct IS NOT NULL AND pc > 0""", (code,)).fetchall()
    for r in rows:
        t = r['true_ret']
        if abs(t) <= FLAT_TOL:
            continue
        tol = max(0.0006, abs(t) * 0.03)
        if (abs(r['change_pct'] / 100 - t) <= tol) and (abs(r['change_pct'] - t) > tol):
            left += 1
print(f'复核：剩余百分数形态行 {left:,}')
c.close()
