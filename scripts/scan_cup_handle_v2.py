#!/usr/bin/env python3
"""杯柄形态 V2 每日全市场落库。

站在扫描日当天看：K线截断到当日、缠论笔取当日快照、只写当日新出现的记录。
实盘当时判断不出就是判断不出——后续K线变化导致笔重划，不允许改写已发布的记录，
因此写入用 INSERT OR IGNORE（该行为是历史产出，只增不改；确需重算用 --force）。

写入 cup_handle_v2_daily 的两类记录：
    SIGNAL    当日放量突破（可交易信号）
    CANDIDATE 结构已完成、买点已定、尚未突破（供下游筛选/观察）

引擎按「每个结构每类只输出一次」去重，候选输出在首次成为候选那天，
故每日候选量约为个位数，可直接作为观察清单消费。

用法：
    python scripts/scan_cup_handle_v2.py [--date YYYY-MM-DD] [--force]
"""
import argparse
import os
import sqlite3
import sys
import time

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))

from scanners import cup_handle_v2 as ch  # noqa: E402

DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')

DDL = """
CREATE TABLE IF NOT EXISTS cup_handle_v2_daily (
    date                TEXT NOT NULL,
    stock_code          TEXT NOT NULL,
    stock_name          TEXT,
    record_type         TEXT NOT NULL,
    prior_high_date     TEXT, prior_high_price  REAL,
    bottom_date         TEXT, bottom_price      REAL,
    mouth_date          TEXT, mouth_price       REAL,
    handle_low_date     TEXT, handle_low_price  REAL,
    buy_point           REAL,
    target_price        REAL,
    stop_price          REAL,
    suggested_max_hold  INTEGER,
    depth_pct           REAL,
    mouth_vs_high       REAL,
    handle_dd_pct       REAL,
    handle_days         INTEGER,
    mouth_to_date_days  INTEGER,
    prior_advance_pct   REAL,
    breakout_close      REAL,
    breakout_vol_ratio  REAL,
    hd_min_ma10         REAL,
    ma10_held           INTEGER,
    bottom_amp          REAL,
    voodoo_days         INTEGER,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    -- 同一只股票同一天可能有两个杯底不同、杯口相同的结构同时突破，
    -- 故唯一键须含 bottom_date，否则 INSERT OR REPLACE 会静默吃掉一条。
    UNIQUE(date, stock_code, record_type, bottom_date, mouth_date)
);
CREATE INDEX IF NOT EXISTS idx_cup_v2_date ON cup_handle_v2_daily(date);
CREATE INDEX IF NOT EXISTS idx_cup_v2_code ON cup_handle_v2_daily(stock_code);
"""


def _row(rec, name):
    """把引擎记录展平为表行。"""
    d = rec['details']
    return (
        rec['date'], rec['stock_code'], name, rec['record_type'],
        rec['prior_high_date'], rec['prior_high_price'],
        rec['bottom_date'], rec['bottom_price'],
        rec['mouth_date'], rec['mouth_price'],
        rec['handle_low_date'], rec['handle_low_price'],
        rec['buy_point'], d['target_price'], d['stop_price'], d['suggested_max_hold'],
        rec['depth_pct'], rec['mouth_vs_high'], rec['handle_dd_pct'], rec['handle_days'],
        rec['mouth_to_date_days'], rec['prior_advance_pct'],
        rec['breakout_close'], rec['breakout_vol_ratio'],
        rec['hd_min_ma10'], 1 if rec['ma10_held'] else 0,
        rec['bottom_amp'], rec['voodoo_days'],
    )


COLS = ('date,stock_code,stock_name,record_type,'
        'prior_high_date,prior_high_price,bottom_date,bottom_price,'
        'mouth_date,mouth_price,handle_low_date,handle_low_price,'
        'buy_point,target_price,stop_price,suggested_max_hold,'
        'depth_pct,mouth_vs_high,handle_dd_pct,handle_days,'
        'mouth_to_date_days,prior_advance_pct,'
        'breakout_close,breakout_vol_ratio,hd_min_ma10,ma10_held,'
        'bottom_amp,voodoo_days')


def main():
    ap = argparse.ArgumentParser(description='杯柄形态 V2 每日全市场落库')
    ap.add_argument('--date', default=None,
                    help='扫描日；默认取K线最新交易日。非交易日会回退到之前最近的交易日')
    ap.add_argument('--limit', type=int, default=0, help='调试用：只扫前 N 只')
    ap.add_argument('--force', action='store_true',
                    help='重算该日：先删除当日已发布记录再写入（默认历史产出只增不改）')
    args = ap.parse_args()

    params = ch.load_params()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)

    # 站在 target 这一天看：K线截断到当日，笔取当日快照，只写当日新出现的记录。
    want = args.date or conn.execute(
        "SELECT MAX(date) FROM daily_kline_adj").fetchone()[0]
    row = conn.execute("SELECT MAX(date) FROM daily_kline_adj WHERE date<=?",
                       (want,)).fetchone()
    target = row[0] if row else None
    if not target:
        print(f'[FAIL] {want} 之前没有任何K线，无法确定扫描日')
        return
    snap = conn.execute("SELECT MAX(scan_date) FROM chanlun_bi_json WHERE scan_date<=?",
                        (target,)).fetchone()[0]
    print(f'扫描日 {target}（请求 {want}）　笔快照 {snap}　'
          f'{"重算当日" if args.force else "只增不改"}')

    if args.force:
        # 必须整日清空：引擎口径变化后，旧记录的唯一键可能与新记录不同，
        # 只靠 INSERT OR REPLACE 会留下删不掉的陈旧行。
        n = conn.execute("DELETE FROM cup_handle_v2_daily WHERE date=?", (target,)).rowcount
        conn.commit()
        print(f'  已删除 {target} 的旧记录 {n} 条')

    codes = [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_basic WHERE stock_code GLOB '[036][0-9][0-9][0-9][0-9][0-9]' "
        "ORDER BY stock_code")]
    if args.limit:
        codes = codes[:args.limit]
    names = {r['stock_code']: r['name'] for r in conn.execute(
        "SELECT stock_code, name FROM stock_basic")}

    verb = 'INSERT OR REPLACE' if args.force else 'INSERT OR IGNORE'
    sql = f"{verb} INTO cup_handle_v2_daily ({COLS}) VALUES ({','.join(['?'] * 28)})"

    t0 = time.time()
    n_sig = n_cand = n_skip = n_lag = 0
    for i, code in enumerate(codes, 1):
        daily = ch._load_daily(conn, code, target, 2500)
        if len(daily) < 400:
            continue
        # 停牌股最后一根K线可能早于 target：那它当天没有可判定的行情，跳过。
        if daily[-1]['date'] != target:
            n_lag += 1
            continue
        recs = ch.detect(daily, params, stock_code=code, as_of=target,
                         record_types=('SIGNAL', 'CANDIDATE'))
        for r in recs:
            cur = conn.execute(sql, _row(r, names.get(code, '')))
            if cur.rowcount == 0:
                n_skip += 1
                continue
            if r['record_type'] == 'SIGNAL':
                n_sig += 1
            else:
                n_cand += 1
        if i % 800 == 0:
            print(f'  ...{i:,}/{len(codes):,}  SIGNAL {n_sig}  CANDIDATE {n_cand}  '
                  f'({time.time()-t0:.0f}s)', flush=True)
    conn.commit()
    conn.close()
    print(f'{target}: 扫描 {len(codes):,} 只（停牌跳过 {n_lag}）   '
          f'新写入 SIGNAL {n_sig} / CANDIDATE {n_cand}   '
          f'已存在跳过 {n_skip}   耗时 {time.time()-t0:.0f}s')


if __name__ == '__main__':
    main()
