# -*- coding: utf-8 -*-
"""
980092 估值指标研究（PE/PB/股息率，2024-09 至今窗口）
====================================================
1. 基本面字段覆盖确认（有无 PS）
2. 估值指标分布/分位特征
3. 估值分位 × 后续收益交叉验证（窗口内）
4. 估值指标自身的"回撤"（股息率从高点回落等）规律
"""
import sqlite3
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
CODE = '980092'


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # 1. 字段确认
    cols = [r['name'] for r in conn.execute("PRAGMA table_info(index_fundamental_daily)")]
    print('index_fundamental_daily cols:', cols)

    # 2. 基本面数据
    rows = conn.execute("""
        SELECT date, pe_ttm, pe_ttm_pct, pb, pb_pct, dyr, dyr_pct
        FROM index_fundamental_daily WHERE stock_code=? ORDER BY date
    """, (CODE,)).fetchall()
    fund = [dict(r) for r in rows]
    print(f'\n基本面数据: {len(fund)} 条 ({fund[0]["date"]} ~ {fund[-1]["date"]})')

    # 3. K线（合并）
    krows = conn.execute("""
        SELECT date, open, close FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' ORDER BY date
    """, (CODE,)).fetchall()
    klines = {r['date']: dict(r) for r in krows}

    # 4. 估值指标统计
    print('\n=== 估值指标统计 ===')
    for f in ['pe_ttm', 'pb', 'dyr']:
        vals = [x[f] for x in fund if x[f] is not None]
        if vals:
            vals_s = sorted(vals)
            n = len(vals_s)
            print(f'{f}: min={vals_s[0]:.3f} p25={vals_s[n//4]:.3f} 中位={vals_s[n//2]:.3f} '
                  f'p75={vals_s[3*n//4]:.3f} max={vals_s[-1]:.3f} | 当前={vals[-1]:.3f}')

    # 5. 分位字段本身统计（数据库已算好分位）
    print('\n=== 分位字段统计（0-1）===')
    for f in ['pe_ttm_pct', 'pb_pct', 'dyr_pct']:
        vals = [x[f] for x in fund if x[f] is not None]
        if vals:
            print(f'{f}: min={min(vals):.2f} 中位={sorted(vals)[len(vals)//2]:.2f} max={max(vals):.2f} | 当前={vals[-1]:.2f}')

    # 6. 交叉验证：估值分位 × 后续 20/60 日收益（需要K线）
    print('\n=== 估值分位 × 后续收益（窗口内 2024-09 起）===')
    # 找到基本面日期在K线中的索引
    dates_k = list(klines.keys())
    idx_map = {d: i for i, d in enumerate(dates_k)}

    # 分组：PE分位低/中/高、PB分位低/中/高、股息率分位低/中/高
    groups = {
        'pe_lo': [], 'pe_mid': [], 'pe_hi': [],
        'pb_lo': [], 'pb_mid': [], 'pb_hi': [],
        'dyr_lo': [], 'dyr_mid': [], 'dyr_hi': [],
    }
    for x in fund:
        d = x['date']
        if d not in idx_map:
            continue
        i = idx_map[d]
        entry = klines[d]['open']
        if not entry or entry <= 0:
            continue
        for w in (20, 60):
            wi = i + w
            if wi >= len(dates_k):
                continue
            ret = (klines[dates_k[wi]]['open'] / entry - 1) * 100
            pe_p = x['pe_ttm_pct'] if x['pe_ttm_pct'] is not None else 0.5
            pb_p = x['pb_pct'] if x['pb_pct'] is not None else 0.5
            dy_p = x['dyr_pct'] if x['dyr_pct'] is not None else 0.5
            g = ('pe_lo' if pe_p < 0.33 else 'pe_mid' if pe_p < 0.66 else 'pe_hi')
            groups[g].append((w, ret))
            g = ('pb_lo' if pb_p < 0.33 else 'pb_mid' if pb_p < 0.66 else 'pb_hi')
            groups[g].append((w, ret))
            g = ('dyr_lo' if dy_p < 0.33 else 'dyr_mid' if dy_p < 0.66 else 'dyr_hi')
            groups[g].append((w, ret))

    for gname in ['pe_lo', 'pe_mid', 'pe_hi', 'pb_lo', 'pb_mid', 'pb_hi', 'dyr_lo', 'dyr_mid', 'dyr_hi']:
        for w in (20, 60):
            rs = [r for (ww, r) in groups[gname] if ww == w]
            if rs:
                wins = sum(1 for r in rs if r > 0)
                print(f'{gname} {w}日: n={len(rs)} 胜率={wins/len(rs)*100:.1f}% 平均={sum(rs)/len(rs):.2f}% 中位={sorted(rs)[len(rs)//2]:.2f}%')

    # 7. 股息率"回撤"：dyr 从滚动高点回落幅度（股息保护衰减）
    print('\n=== 股息率滚动高点回落（2024-09 起）===')
    dyrs = [x['dyr'] for x in fund if x['dyr'] is not None]
    dates_d = [x['date'] for x in fund if x['dyr'] is not None]
    if len(dyrs) >= 60:
        dd_dyr = []
        for i in range(60, len(dyrs)):
            hi = max(dyrs[i-60:i+1])
            dd = (hi - dyrs[i]) / hi * 100
            dd_dyr.append((dates_d[i], dd))
        print(f'股息率60日高点回落: max={max(d for _, d in dd_dyr):.1f}% | 当前={dd_dyr[-1][1]:.1f}%')
        deep = [(d, v) for d, v in dd_dyr if v >= 10]
        print(f'回落≥10% 天数: {len(deep)}/{len(dd_dyr)}')
        if deep:
            print(f'  最近: {deep[-5:]}')

    conn.close()


if __name__ == '__main__':
    main()
