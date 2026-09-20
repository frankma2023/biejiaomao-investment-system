# -*- coding: utf-8 -*-
"""周线 CPA 增量模式（T7 幂等要求，v3 判据）：

判据：数据层周线笔快照领先于 CPA 落库行（快照 max(scan_date) > CPA max(date)，含 CPA 无行）→ 重算该股。
快照缺失（数据层未回填到该股）→ 跳过，等 35a 回填后下一天自然补上。
与 daily_update 步骤 35a 的产出严格衔接，天然幂等：重算后两者对齐，下次跳过。

⚠ 首次运行或表里有旧引擎/无快照时期的脏数据时，必须先跑全量 purge：
  python scripts/backfill_cpa_weekly.py --workers 8
（v1 判据时期曾写过 238 万行无笔顶证据的半残数据 + 旧引擎 dow==0 口径残留，
  日期键不同 REPLACE 覆盖不掉，只有 purge 能清。）

上游：步骤 35a（backfill_chanlun_weekly.py --incremental）
"""
import sys, os

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT, 'src'))

import sqlite3

import scanners.cpa_stage_weekly as wk


def stocks_needing_update(conn):
    """返回 (需要重算的股票列表, 总池大小)。"""
    # 快照最新周（周线笔表仅数千行）
    snap = {}
    for code, wmax in conn.execute(
            "SELECT stock_code, MAX(scan_date) FROM chanlun_weekly_bi_json GROUP BY stock_code"):
        snap[code] = wmax
    if not snap:
        return [], 0
    # CPA 最新行（PK(stock_code,date) 索引序聚合，快）
    cpa_max = {}
    for code, dmax in conn.execute(
            "SELECT stock_code, MAX(date) FROM %s GROUP BY stock_code" % wk.WEEKLY_TABLE):
        cpa_max[code] = dmax
    need = []
    for code, wmax in snap.items():
        cmax = cpa_max.get(code)
        if cmax is None or cmax < wmax:
            need.append(code)
    total = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM daily_kline").fetchone()[0]
    return sorted(need), total


def incremental(workers=8):
    """增量入口：把 stocks_needing_update 选出的股票交给 worker 管道。"""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    import time as _t
    t0 = _t.time()
    conn = sqlite3.connect(wk.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    wk.ensure_tables(conn)
    need, total = stocks_needing_update(conn)
    print('周线CPA增量: 总池 %d | 有新完整周需重算 %d | 其余快照已是最新（幂等跳过）' % (total, len(need)), flush=True)
    if not need:
        conn.close()
        print('无需更新，0s 退出（幂等验证通过）')
        return
    n_daily = n_trans = 0
    n = max(1, len(need) // (workers * 4))
    chunks = [need[i:i + n] for i in range(0, len(need), n)]
    skipped_all = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(wk._worker, c): i for i, c in enumerate(chunks)}
        for fut in as_completed(futures):
            try:
                d, t, skipped = fut.result()
                skipped_all += skipped
            except Exception as e:
                print('  ! chunk 失败: %s' % str(e)[:80], flush=True)
                continue
            if d:
                conn.executemany("INSERT OR REPLACE INTO %s VALUES (?,?,?,?,?,?,?,?,?,?,?)" % wk.WEEKLY_TABLE, d)
            if t:
                conn.executemany("""INSERT OR REPLACE INTO %s
                    (stock_code, transition_date, from_stage, to_stage, trigger_detail_json, invalidated, invalidated_date)
                    VALUES (?,?,?,?,?,0,NULL)""" % wk.WEEKLY_TRANS_TABLE, t)
            conn.commit()
            n_daily += len(d or [])
            n_trans += len(t or [])
    print('标记 invalidated（周线版）...', flush=True)
    n_inv = wk.mark_invalidated_weekly(conn)
    conn.close()
    print('周线CPA增量完成: %d 行 | 迁移 %d | 失效 %d | 跳过 %d | 耗时 %.0fs' % (
        n_daily, n_trans, n_inv, len(skipped_all), _t.time() - t0))


if __name__ == '__main__':
    incremental()
