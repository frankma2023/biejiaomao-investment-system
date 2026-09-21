#!/usr/bin/env python3
"""
scripts/fix_lxr_fc_ipo_gap.py — 补全北交所新股上市初期缺失的 lxr_fc_*

问题
----
理杏仁 `lxr_fc_rights`（理杏仁前复权）对**上市未满约 2~3 周**的股票不返回数据。
实测：920158 长江能科 IPO 2025-10-16，同期 `ex_rights` 返回 12 行（10-16 ~ 10-31），
`lxr_fc_rights` 返回 0 行；首个有值日为 2025-11-03。
受影响：15 只 920xxx 北交所新股，共 168 行。

可否回填：可以，且是精确的，不是估算
--------------------------------
前复权价满足 P_adj(t) = P_raw(t) × k(t)，其中 k(t) 仅在**除权除息日**跳变，
其余时间恒定。因此只要确认缺口区间内没有除权事件，就有：

    k(缺口内任意日) = k(缺口后首个有值日的同一段常数)

实现步骤：
  1. 取该股首个有 lxr_fc 的交易日之后 20 个交易日，算 k = lxr_fc_close / ex_close 的中位数
     （用中位数而非首日值：实测首日存在约 0.5% 的 Warm-up 偏差，920158 首日 k=0.7033 而稳定值 0.6999）
  2. 用分红 API（/company/dividend）确认在「缺口首日 ~ 首个有值日」之间没有已实施的除权除息日
  3. 通过校验才回填 lxr_fc_{open,high,low,close} = ex_* × k

校验案例（920158）：2026-05-18 除权，转增 0.4 股 + 派现 0.35 元。
  除权前收 17.70 → 理论因子 (17.70-0.35)/(17.70×1.4) = 0.70016，观测 0.69993，吻合。

用法
----
    python scripts/fix_lxr_fc_ipo_gap.py            # 干跑，只报告可回填项与 k 值
    python scripts/fix_lxr_fc_ipo_gap.py --apply    # 执行回填
"""

import sqlite3
import sys
import os
import statistics

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, 'scripts')
from common import api_post, DB_PATH  # noqa: E402

APPLY = '--apply' in sys.argv
SEG_DAYS = 20          # 用首个有值日之后的 N 个交易日估算 k
TOL = 1e-9

c = sqlite3.connect(str(DB_PATH), timeout=300)
c.row_factory = sqlite3.Row

codes = [r['stock_code'] for r in c.execute("""
    SELECT DISTINCT stock_code FROM daily_kline
    WHERE date >= '2016-01-01' AND ex_close IS NOT NULL AND lxr_fc_close IS NULL
    ORDER BY stock_code""")]
print(f'受影响股票 {len(codes)} 只')
print(f'{"代码":<8}{"缺行":>5}{"缺口区间":>26}{"首个有值日":>13}{"k(中位)":>10}  判定')
print('-' * 88)

safe, skipped = [], []
for code in codes:
    gap = c.execute("""SELECT MIN(date) a, MAX(date) b, COUNT(*) n FROM daily_kline
                       WHERE stock_code=? AND date>='2016-01-01'
                         AND ex_close IS NOT NULL AND lxr_fc_close IS NULL""",
                    (code,)).fetchone()
    seg = c.execute("""SELECT date, ex_close, lxr_fc_close FROM daily_kline
                       WHERE stock_code=? AND lxr_fc_close IS NOT NULL
                       ORDER BY date LIMIT 60""", (code,)).fetchall()
    if not seg:
        print(f'{code:<8}{gap["n"]:>5}{"":>26}{"":>13}{"":>10}  [WARN] 无任何 lxr_fc，跳过')
        skipped.append(code)
        continue
    first = seg[0]['date']

    # 校验：缺口首日 ~ 首个有值日之间不得有已实施的除权除息日
    # （k(t) 仅在除权日跳变，这段无事件则 k 恒定，可用后面的 k 值外推）
    try:
        div = api_post('/company/dividend',
                       {'stockCode': code, 'startDate': '2017-01-01', 'endDate': '2026-09-11'},
                       timeout=120)
    except Exception as e:
        print(f'{code:<8}{gap["n"]:>5}  分红接口失败：{e}')
        skipped.append(code)
        continue
    rows = div if isinstance(div, list) else (div.get('data') if isinstance(div, dict) else [])
    ex_dates = sorted((r.get('exDate') or '')[:10] for r in rows
                      if r.get('exDate') and r.get('status') == 'implemented')

    in_gap = [d for d in ex_dates if gap['a'] < d <= first]
    next_ex = min([d for d in ex_dates if d > first], default='9999-12-31')
    # 估计窗口截断到下个除权日之前，避免跨段致中位数失真
    seg_rows = [r for r in seg if r['date'] < next_ex]
    ks = [r['lxr_fc_close'] / r['ex_close'] for r in seg_rows if r['ex_close']]
    k0 = statistics.median(ks) if ks else float('nan')
    ok = (not in_gap) and len(ks) >= 5
    span = f'{gap["a"]} ~ {gap["b"]}'
    note = '✅ 可回填' if ok else f'⚠ 缺口内有除权 {in_gap[:3]} 或样本不足({len(ks)})'
    print(f'{code:<8}{gap["n"]:>5}{span:>26}{first:>13}{k0:>10.6f}  {note}  ')
    if ok:
        safe.append((code, k0))
    else:
        skipped.append(code)

print(f'\n可回填 {len(safe)} 只 / 跳过 {len(skipped)} 只')
if not APPLY:
    print('（干跑模式，加 --apply 执行）')
    c.close()
    sys.exit(0)

total = 0
for code, k0 in safe:
    cur = c.execute("""UPDATE daily_kline
                       SET lxr_fc_open=ex_open*?, lxr_fc_high=ex_high*?,
                           lxr_fc_low=ex_low*?, lxr_fc_close=ex_close*?
                       WHERE stock_code=? AND date>='2016-01-01'
                         AND ex_close IS NOT NULL AND lxr_fc_close IS NULL""",
                    (k0, k0, k0, k0, code))
    total += cur.rowcount
c.commit()
print(f'\n[OK] 已回填 {total:,} 行')
r = c.execute("""SELECT COUNT(*) n FROM daily_kline WHERE date>='2016-01-01'
                 AND ex_close IS NOT NULL AND lxr_fc_close IS NULL""").fetchone()
print(f'剩余 ex 有值但 lxr_fc 为空：{r["n"]:,} 行')
c.close()
