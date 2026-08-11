# -*- coding: utf-8 -*-
"""全市场扫描：同日「基部突破 + 口袋支点V2」共振信号 → 未来 10/20/30/60 日收益统计
近3年：信号日 >= 2023-08-01，检测窗口用 2022-01-01 起 K 线
用法：python scripts/scan_bb_pp2_confluence.py [--workers 8] [--limit 200]
"""
import sys, os, sqlite3, time, argparse, json
from multiprocessing import Pool
from datetime import datetime

PROJ = r'D:\hanako\investment-system'
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'src'))
os.chdir(PROJ)

DB = os.path.join(PROJ, 'data', 'lixinger.db')
SIGNAL_START = '2023-08-01'   # 近3年信号
KLINE_START = '2022-01-01'    # 检测窗口 K 线起点（给引擎预热+回溯）
END_DATE = '2026-08-07'

ENGINE_NAMES = ('base_breakout', 'pocket_pivot_v2')


def load_kline(code):
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = db.execute("""SELECT date, COALESCE(adj_open, open) as open, COALESCE(adj_high, high) as high,
        COALESCE(adj_low, low) as low, COALESCE(adj_close, close) as close, volume, amount, change_pct
        FROM daily_kline WHERE stock_code=? AND date>=? AND date<=? ORDER BY date""",
        (code, KLINE_START, END_DATE)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def ensure_adj(kl):
    """复刻 server.py _ensure_adj_prices：change_pct 逆向推算前复权"""
    if not kl:
        return
    n = len(kl)
    kl[n-1]['adj_close'] = kl[n-1]['close']
    for i in range(n-2, -1, -1):
        chg = kl[i+1].get('change_pct')
        kl[i]['adj_close'] = kl[i+1]['adj_close'] / (1 + chg) if chg is not None else kl[i+1]['adj_close']
    for k in kl:
        ratio = k['adj_close'] / k['close'] if k['close'] else 1
        for f in ('open', 'high', 'low'):
            k[f] = k[f] * ratio if k[f] else k[f]
        k['close'] = k['adj_close']


def scan_one(code):
    """扫描单只股票，返回 (code, [bb_signals], [pp2_signals])"""
    from src.scanners.base_breakout import detect as bb_detect, load_params as bb_params
    from src.scanners.pocket_pivot_v2_engine import detect as pp2_detect
    try:
        kl = load_kline(code)
        if len(kl) < 120:
            return (code, [], [])
        for k in kl:
            k['stock_code'] = code
        ensure_adj(kl)
        bb = bb_detect(kl, bb_params())
        bb = bb if isinstance(bb, list) else []
        pp2 = pp2_detect(kl)
        pp2 = pp2 if isinstance(pp2, list) else []
        # 统一字段名，过滤信号日
        def norm(sigs):
            out = set()
            for s in sigs:
                d = s.get('date') or s.get('signal_date')
                if d and d >= SIGNAL_START:
                    out.add(d)
            return out
        return (code, norm(bb), norm(pp2))
    except Exception as e:
        return (code, [], [])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--limit', type=int, default=0, help='只扫描前 N 只（测试用）')
    args = parser.parse_args()

    db = sqlite3.connect(DB)
    codes = [r[0] for r in db.execute(
        "SELECT DISTINCT stock_code FROM daily_kline WHERE date >= ? AND date <= ?",
        (SIGNAL_START, END_DATE)).fetchall()]
    db.close()
    # 排除北交所（8/4 开头）与已退市？
    codes = [c for c in codes if not c.startswith(('8', '4', '9'))]
    if args.limit:
        codes = codes[:args.limit]
    print(f'扫描 {len(codes)} 只股票, workers={args.workers}', flush=True)

    t0 = time.time()
    results = []
    with Pool(args.workers) as pool:
        for i, res in enumerate(pool.imap_unordered(scan_one, codes, chunksize=50)):
            results.append(res)
            if (i + 1) % 500 == 0:
                print(f'  进度 {i+1}/{len(codes)} ({time.time()-t0:.0f}s)', flush=True)
    print(f'扫描完成 {time.time()-t0:.0f}s', flush=True)

    # 合并
    bb_map = {}   # (code, date) -> True
    pp2_map = {}
    for code, bb, pp2 in results:
        for d in bb:
            bb_map[(code, d)] = True
        for d in pp2:
            pp2_map[(code, d)] = True

    # 共振 = 同日双信号
    conflu = sorted(set(bb_map.keys()) & set(pp2_map.keys()))
    print(f'base_breakout 信号: {len(bb_map)} | pocket_pivot_v2 信号: {len(pp2_map)} | 共振: {len(conflu)}', flush=True)

    # 保存中间结果
    with open(os.path.join(PROJ, 'analysis', 'bb_pp2_confluence_signal_dates.json'), 'w', encoding='utf-8') as f:
        json.dump({'confluence': [[c, d] for c, d in conflu]}, f, ensure_ascii=False)
    print('共振日期已保存 analysis/bb_pp2_confluence_signal_dates.json')


if __name__ == '__main__':
    main()
