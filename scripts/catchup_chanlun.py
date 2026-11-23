# -*- coding: utf-8 -*-
"""
scripts/catchup_chanlun.py — 缠论日线扫描的缺口回补（单趟多日）
================================================================
背景：chanlun_scan.py 一次只扫一个日期，每只股票都要重放全历史 K 线。
缺 5 个交易日就要重放 5 遍，实测一天约 30 分钟，5 天要两个半小时。
scan_stock_all(code, dates) 本来就是「一只股票一次重放、产出多个目标日」，
这里直接复用它，把 N 天的缺口合成一趟。

用法：
  python scripts/catchup_chanlun.py                 # 自动检测缺口（最新 scan_date 之后的所有交易日）
  python scripts/catchup_chanlun.py --days 5        # 只回补最近 5 个交易日里的缺口
  python scripts/catchup_chanlun.py --dry-run       # 只打印缺口，不扫
"""
import sys, os, argparse

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT, 'src'))
sys.path.insert(0, PROJECT)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from scanners.chanlun_scan import (  # noqa: E402
    _connect, _ensure_table, get_target_stocks, scan_stock_all,
    generate_html, CHANLUN_ALGO_VERSION,
)

INSERT_SQL = """
    INSERT OR REPLACE INTO chanlun_scan_daily
    (scan_date, stock_code, stock_name, bi_count, zs_count, segment_count,
     latest_bi_dir, latest_bi_power, divergence_count, latest_div_type,
     trade_signal_count, latest_trade_type, latest_trade_side, latest_trade_price,
     resonance_strength, algo_version)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def missing_days(conn, days=None):
    mx = conn.execute("SELECT MAX(scan_date) FROM chanlun_scan_daily").fetchone()[0]
    rows = conn.execute(
        "SELECT DISTINCT date FROM index_daily_kline "
        "WHERE stock_code='000922' AND kline_type='normal' AND date>? ORDER BY date",
        (mx,)).fetchall()
    out = [r[0] for r in rows]
    if days:
        out = out[-days:]
    return mx, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=None, help='只补最近 N 个交易日内的缺口')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    conn = _connect()
    _ensure_table(conn)
    last, dates = missing_days(conn, args.days)
    print(f'库内最新日线扫描: {last}')
    print(f'待回补交易日({len(dates)}): {dates}')
    if not dates:
        print('无缺口')
        return
    if args.dry_run:
        return

    stocks = get_target_stocks(conn, all_market=True)
    print(f'全市场过滤后 {len(stocks)} 只，单趟多日')

    ok = 0
    rows = 0
    for i, (code, name) in enumerate(stocks):
        for d, r in scan_stock_all(code, dates):
            if not r:
                continue
            r['stock_code'] = code
            r['stock_name'] = name
            conn.execute(INSERT_SQL, (
                r['scan_date'], r['stock_code'], r['stock_name'],
                r['bi_count'], r['zs_count'], r['segment_count'],
                r['latest_bi_dir'], r['latest_bi_power'],
                r['divergence_count'], r['latest_div_type'],
                r['trade_signal_count'], r['latest_trade_type'],
                r['latest_trade_side'], r['latest_trade_price'],
                r['resonance_strength'], CHANLUN_ALGO_VERSION,
            ))
            if r.get('bi_json'):
                conn.execute(
                    "INSERT OR REPLACE INTO chanlun_bi_json (stock_code, scan_date, bi_json) VALUES (?,?,?)",
                    (code, r['scan_date'], r['bi_json']))
            rows += 1
        ok += 1
        if (i + 1) % 50 == 0:
            conn.commit()
            print(f'  进度 {i+1}/{len(stocks)}  成功 {ok} 只  写入 {rows} 行')
    conn.commit()

    for d in dates[-2:]:
        p = generate_html(d)
        if p:
            print(f'HTML 快照 {d}: {p}')
    print(f'完成: {ok}/{len(stocks)} 只，写入 {rows} 行')
    conn.close()


if __name__ == '__main__':
    main()
