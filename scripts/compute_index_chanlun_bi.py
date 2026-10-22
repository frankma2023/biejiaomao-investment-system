#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为指数计算缠论笔（bi），存入独立表 index_chanlun_bi。

为什么不复用 src/scanners/chanlun.py：
  该模块 89KB，深度依赖 czsc 库与个股数据源，改造风险高。
  这里用标准缠论定义自实现，判定逻辑明确可验证：

  1. 包含关系处理（合并 K 线）
     向上处理：高 = max(高)，低 = max(低)
     向下处理：高 = min(高)，低 = min(低)
  2. 分型：合并后 K 线
     顶分型：中间 K 的高 > 左右两 K 的高
     底分型：中间 K 的低 < 左右两 K 的低
  3. 笔：顶→底 或 底→顶，中间至少 1 根独立 K 线（即端点相隔 ≥4 根合并 K）
     方向必须交替

输出字段：code, start_date, end_date, direction(up/down), start_price, end_price,
         high, low, length(合并K数), power(涨跌%), slope

用法：
    python scripts/compute_index_chanlun_bi.py --code 980092
    python scripts/compute_index_chanlun_bi.py --code 980092 --save
    python scripts/compute_index_chanlun_bi.py --all --save
"""
import argparse
import sqlite3
import sys
from datetime import datetime

DB = 'data/lixinger.db'
MIN_BARS = 4      # 笔的最小跨度（合并 K 线数）


def merge_klines(rows):
    """处理包含关系。rows: [(date, high, low)] → [(date, high, low, idx_orig)]"""
    merged = []
    for d, h, l in rows:
        if not merged:
            merged.append([d, h, l, d])
            continue
        ph, pl = merged[-1][1], merged[-1][2]
        # 判断包含
        if (h <= ph and l >= pl) or (h >= ph and l <= pl):
            trend_up = len(merged) < 2 or merged[-1][1] > merged[-2][1]
            if trend_up:
                nh, nl = max(h, ph), max(l, pl)
            else:
                nh, nl = min(h, ph), min(l, pl)
            merged[-1][1], merged[-1][2] = nh, nl
            merged[-1][3] = d      # 保留最新日期
        else:
            merged.append([d, h, l, d])
    return merged


def find_fractals(merged):
    """返回 [(idx, 'top'|'bot', date, price)]"""
    out = []
    for i in range(1, len(merged) - 1):
        _, h0, l0, _ = merged[i - 1]
        _, h1, l1, _ = merged[i]
        _, h2, l2, _ = merged[i + 1]
        if h1 > h0 and h1 > h2:
            out.append((i, 'top', merged[i][0], h1))
        elif l1 < l0 and l1 < l2:
            out.append((i, 'bot', merged[i][0], l1))
    return out


def build_bis(fracts):
    """把分型连成笔：方向交替 + 端点间隔 ≥ MIN_BARS。"""
    if not fracts:
        return []
    bis = []
    last = fracts[0]
    for f in fracts[1:]:
        if f[1] == last[1]:
            # 同向分型：保留更极端的那个
            if (f[1] == 'top' and f[3] > last[3]) or (f[1] == 'bot' and f[3] < last[3]):
                last = f
            continue
        if f[0] - last[0] < MIN_BARS:
            continue
        direction = 'up' if last[1] == 'bot' else 'down'
        sp, ep = last[3], f[3]
        bis.append({
            'start_date': last[2], 'end_date': f[2],
            'direction': direction,
            'start_price': sp, 'end_price': ep,
            'high': max(sp, ep), 'low': min(sp, ep),
            'length': f[0] - last[0],
            'power': (ep / sp - 1) * 100 if sp else 0,
            'slope': (ep - sp) / (f[0] - last[0]) if f[0] > last[0] else 0,
        })
        last = f
    return bis


def compute(code, con):
    rows = con.execute("""
        SELECT date, high, low FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal'
          AND high IS NOT NULL AND low IS NOT NULL AND close>0
        ORDER BY date
    """, (code,)).fetchall()
    if len(rows) < 60:
        return None
    merged = merge_klines([(r[0], r[1], r[2]) for r in rows])
    fracts = find_fractals(merged)
    return build_bis(fracts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--code')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--save', action='store_true')
    a = ap.parse_args()

    con = sqlite3.connect(DB)
    if a.code:
        codes = [a.code]
    elif a.all:
        codes = [r[0] for r in con.execute(
            "SELECT DISTINCT stock_code FROM index_daily_kline "
            "WHERE kline_type='normal' GROUP BY stock_code HAVING COUNT(*)>=200")]
    else:
        print('需 --code 或 --all')
        return 1

    if a.save:
        con.execute("""
            CREATE TABLE IF NOT EXISTS index_chanlun_bi (
                stock_code TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date   TEXT NOT NULL,
                direction  TEXT,
                start_price REAL, end_price REAL,
                high REAL, low REAL,
                length INTEGER, power REAL, slope REAL,
                updated_at TEXT,
                PRIMARY KEY (stock_code, start_date, end_date))
        """)
        con.commit()

    now = datetime.now().isoformat(timespec='seconds')
    total = 0
    for code in codes:
        bis = compute(code, con)
        if not bis:
            continue
        total += len(bis)
        if a.code and not a.save:
            print(f'  {code}: {len(bis)} 笔')
            for b in bis[-6:]:
                print(f"    {b['start_date']} → {b['end_date']}  {b['direction']:>4}  "
                      f"{b['start_price']:>9.2f} → {b['end_price']:>9.2f}  "
                      f"跨度{b['length']:>3}  {b['power']:>+7.2f}%")
        if a.save:
            con.executemany(
                "INSERT OR REPLACE INTO index_chanlun_bi VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?)",
                [(code, b['start_date'], b['end_date'], b['direction'],
                  b['start_price'], b['end_price'], b['high'], b['low'],
                  b['length'], b['power'], b['slope'], now) for b in bis])

    if a.save:
        con.commit()
        print(f'  写入 index_chanlun_bi：{total} 笔 / {len(codes)} 个指数')
    con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
