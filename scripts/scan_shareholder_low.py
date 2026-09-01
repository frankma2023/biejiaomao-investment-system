# -*- coding: utf-8 -*-
"""
scripts/scan_shareholder_low.py — 全市场股东人数创新低扫描（筹码集中度）
========================================================================
逻辑：
- 每只股票（历史报告期 ≥3 期）计算最新股东人数 vs 历史最低
- 创新低：最新 ≤ 历史最低 × 1.05（接近/创新低，与 stock-valuation 页面 5% 容差一致）
- 加分：连续减少期数（筹码持续集中）、较上期减少幅度
- 排除：ST/退市（名称含 ST/*ST）、历史 <3 期

输出：shareholder_low_scan 表（扫描日期快照）+ 控制台 Top 清单
用法：python scripts/scan_shareholder_low.py [--limit 100]
"""
import sys, os, io, sqlite3, argparse
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'lixinger.db')
TODAY = datetime.now().strftime('%Y-%m-%d')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    # 全市场股东人数（每只股票全部历史）
    rows = db.execute("""SELECT stock_code, date, total FROM shareholders_num_daily
        ORDER BY stock_code, date""").fetchall()
    by_code = {}
    for r in rows:
        by_code.setdefault(r['stock_code'], []).append((r['date'], r['total']))
    print(f'扫描 {len(by_code)} 只...')

    results = []
    for code, seq in by_code.items():
        vals = [t for _, t in seq if t is not None]
        if len(vals) < 3:
            continue  # 历史不足 3 期
        dates = [d for d, _ in seq]
        cur = vals[-1]
        hist_min = min(vals)
        # 创新低/接近新低（5% 容差）
        if cur > hist_min * 1.05:
            continue
        # 连续减少期数（从最新往前数）
        streak = 0
        for i in range(len(vals) - 1, 0, -1):
            if vals[i] < vals[i - 1]:
                streak += 1
            else:
                break
        # 较上期减少幅度
        prev = vals[-2]
        chg = (cur / prev - 1) * 100 if prev else 0
        # 名称（排除 ST）
        nm = db.execute("SELECT stock_name FROM watchlist WHERE stock_code=? LIMIT 1", (code,)).fetchone()
        name = nm['stock_name'] if nm else None
        if not name:
            nm2 = db.execute("SELECT name FROM stock_basic WHERE stock_code=? LIMIT 1", (code,)).fetchone()
            name = nm2['name'] if nm2 else code
        if name and ('ST' in name.upper() or '退' in name):
            continue
        results.append({
            'code': code, 'name': name, 'date': dates[-1], 'total': cur,
            'hist_min': hist_min, 'hist_min_date': dates[vals.index(hist_min)],
            'streak': streak, 'chg': round(chg, 1),
            'ratio': round(cur / hist_min, 3),
        })

    # 排序：创新低程度 + 连续减少期数
    results.sort(key=lambda x: (x['streak'] >= 2, -x['streak'], x['ratio']), reverse=True)
    print(f'命中 {len(results)} 只（历史≥3期，最新≤历史最低×1.05）:')
    print(f"{'代码':<8}{'名称':<10}{'最新':<10}{'较上期':<8}{'连续减':<6}{'历史最低':<10}日期")
    for x in results[:args.limit or 30]:
        print(f"{x['code']:<8}{x['name']:<10}{x['total']:<10}{x['chg']:+.1f}%{x['streak']:<6}{x['hist_min']:<10}{x['hist_min_date']}")

    # 落表
    db.execute("""CREATE TABLE IF NOT EXISTS shareholder_low_scan (
        scan_date TEXT, stock_code TEXT, stock_name TEXT, report_date TEXT,
        total INTEGER, hist_min INTEGER, hist_min_date TEXT,
        streak INTEGER, chg REAL, ratio REAL,
        PRIMARY KEY (scan_date, stock_code))""")
    db.executemany("""INSERT OR REPLACE INTO shareholder_low_scan
        (scan_date, stock_code, stock_name, report_date, total, hist_min, hist_min_date, streak, chg, ratio)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [(TODAY, x['code'], x['name'], x['date'], x['total'], x['hist_min'],
          x['hist_min_date'], x['streak'], x['chg'], x['ratio']) for x in results])
    db.commit()
    db.close()
    print(f'\n已写入 shareholder_low_scan（{TODAY}，{len(results)} 只）')


if __name__ == '__main__':
    main()
