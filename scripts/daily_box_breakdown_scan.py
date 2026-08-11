#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日跌破箱体扫描（精简版）
==========================
参考 daily-pattern-scan 模式：对全 A 股跑 box_breakdown 引擎，
输出最近 N 个活跃跌破箱体信号，供人工核对引擎准确性。

用法：
    python scripts/daily_box_breakdown_scan.py            # 扫最新数据，输出最近 3 个
    python scripts/daily_box_breakdown_scan.py --top 5   # 输出最近 5 个
    python scripts/daily_box_breakdown_scan.py --code 603259  # 单股调试
"""

import sys, os, sqlite3, time, argparse, json
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")


def load_klines(code, end_date):
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""SELECT date, open, high, low, close, volume, change_pct
        FROM daily_kline WHERE stock_code=? AND date<=? ORDER BY date""",
        (code, end_date)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def scan_stock(code, end_date):
    """单股跑 box_breakdown，返回活跃信号（signal_level 非空）"""
    try:
        from src.scanners.box_breakdown import detect, load_params
        kl = load_klines(code, end_date)
        if len(kl) < 100:
            return []
        sigs = detect(kl, load_params())
        return [s for s in sigs if s.get('signal_level')]
    except Exception:
        return []


def get_all_a_shares():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    rows = conn.execute(
        "SELECT DISTINCT stock_code FROM daily_kline WHERE date=(SELECT MAX(date) FROM daily_kline)").fetchall()
    conn.close()
    return [r[0] for r in rows if r[0].startswith(('60', '00', '30', '68'))]


def get_name(code):
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
    conn.close()
    return r['name'] if r else code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--top', type=int, default=3, help='输出最近 N 个信号')
    parser.add_argument('--code', type=str, default=None, help='单股调试')
    parser.add_argument('--date', type=str, default=None, help='数据截止日期')
    args = parser.parse_args()

    if args.date:
        end_date = args.date
    else:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        end_date = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
        conn.close()
    print(f'📅 数据截止: {end_date}')

    if args.code:
        # 单股调试
        sigs = scan_stock(args.code, end_date)
        name = get_name(args.code)
        print(f'\n🔍 {args.code} {name}: {len(sigs)} 个活跃跌破信号')
        for s in sigs:
            print(f"   {s['signal_date']} [{s['signal_level']}] 下沿{s['band_bottom']} 收盘{s['close']:.2f} "
                  f"跌破{s.get('drop_pct')}% 最大{s.get('max_drop_pct')}%")
        return

    # 全 A 扫描
    codes = get_all_a_shares()
    print(f'🔎 扫描 {len(codes)} 只 A 股 ...')
    t0 = time.time()
    all_signals = []
    for i, code in enumerate(codes):
        sigs = scan_stock(code, end_date)
        for s in sigs:
            all_signals.append({
                'code': code, 'name': get_name(code),
                'date': s['signal_date'], 'level': s['signal_level'],
                'bottom': s['band_bottom'], 'close': s['close'],
                'drop_pct': s.get('drop_pct'), 'max_drop': s.get('max_drop_pct'),
                'box_start': s['details'].get('box_start_date'),
            })
        if (i + 1) % 1000 == 0:
            print(f'  进度 {i+1}/{len(codes)} ({time.time()-t0:.0f}s)', flush=True)

    # 按触发日期排序，取最近 N 个
    all_signals.sort(key=lambda x: x['date'], reverse=True)
    recent = all_signals[:args.top]

    print(f'\n{"="*95}')
    print(f'📉 最近 {args.top} 个跌破箱体信号（共检出 {len(all_signals)} 个活跃信号 / {len(codes)} 只）')
    print(f'{"="*95}')
    print(f'{"代码":<8}{"名称":<10}{"触发日":<12}{"级别":<14}{"下沿":>8}{"收盘":>8}{"最大跌幅":>9}  箱体起点')
    for s in recent:
        print(f'{s["code"]:<8}{s["name"]:<10}{s["date"]:<12}{s["level"]:<14}'
              f'{s["bottom"]:>8.2f}{s["close"]:>8.2f}{s["max_drop"]:>8.2f}%  {s["box_start"]}')

    # 保存 JSON（供页面/验证）
    out_path = os.path.join(PROJECT_DIR, "analysis", "box_breakdown_daily.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'date': end_date, 'total': len(all_signals), 'recent': recent}, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 已保存 {out_path}（耗时 {time.time()-t0:.0f}s）')


if __name__ == '__main__':
    main()
