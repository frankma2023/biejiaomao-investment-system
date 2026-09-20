"""
周K线 CPA 判定引擎 v2 —— ISO 聚合真周K → 日线状态机（WEEKLY_CFG 参数集）→ 独立周线表

与旧版的区别（交接文档 §6.3 三个死罪全修）：
1. 周K聚合改用 chanlun_weekly.iso_aggregate（ISO 口径，代表日=该周最后交易日），
   废弃 dow==0 分组（周一停牌股整周错位）。
2. 笔顶从 chanlun_weekly_bi_json 按扫描周取**当时快照**（防未来），
   废弃借日线 chanlun_bi_json 最新快照（尺度错配 + 未来函数）。
3. WEEKLY_CFG 在 run_weekly 入口统一 update 到 daily.CFG（单股路径也生效），
   废弃主进程 update / 单股路径漏 update 的 CFG 暂替 bug。

架构（PRD §5 关键约束 / 交接文档 D4）：
  worker 进程内 daily.CFG.update(WEEKLY_CFG) → daily.run_state_machine 原样复用。
  ⚠ CFG 是模块级全局：同进程内先跑周线再跑日线会互相污染。
    全量回算走多进程（每进程独立副本），单股调试后如需跑日线必须重载模块。

版本：v2.0（2026-09-18，参数表 = 交接文档 §5 WEEKLY_CFG v2）
"""
import os
import sys
import json
import sqlite3
import time
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT, 'src'))
DB_PATH = os.path.join(PROJECT, 'data', 'lixinger.db')

import scanners.cpa_stage as daily
from scanners.chanlun_weekly import iso_aggregate, WEEKLY_ALGO_VERSION

# ═══════════════════════════════════════════════════════════
# 周线参数集（交接文档 §5 WEEKLY_CFG v2 = PRD §4 定稿）
# 键分三类：
#   A) 日线 CFG 已有键，直接覆盖（保留14 + 修订10 的存量键）
#   B) 日线 CFG 新增键（T2 参数化产出），按周线值覆盖
# ═══════════════════════════ docstring 提示 ═══════════════════════════
WEEKLY_CFG = dict(daily.CFG)

WEEKLY_CFG.update({
    # ── §5.1 保留项（14 项）──
    'atr_win': 8, 'atr_win_slow': 20, 'vr_win': 8, 'pctile_win': 52,
    'w_win_min': 2, 'w_win_max': 12, 'w_win_long': 4,
    'cb_window_min': 1, 'cb_window_max': 4,
    'b_win_min': 4, 'b_win_max': 12,
    'r_high_recency': 12,
    'e_nd10_min': 3.0, 'e_d10_pct_min': 0.12, 'e_pctile_min': 90,
    'ftd_win_min': 1, 'ftd_win_max': 3,
    't_in_days': 1,

    # ── §5.2 修订项（10 项，日线值→周线值；inv_n 单位=周）──
    'inv_n': {'②→③': 3, '②→④': 8, '③→④': 8, '④→⑤': 6, '⑤→⑥': 6, '→⑥': 3},
    'w_first_lookback': 4,      # 日线 20
    'b_depth_max': 0.10,        # 日线 0.05
    'e_peak_gain_min': 0.60,    # 日线 0.30
    'cb_tol_atr': 0.15,         # 日线 0.3
    'cb_hold_days': 1,          # 日线 2
    'w_amp_tol': 0.15,          # 日线 0.12
    'ftd_ret_min': 0.05,        # 日线 0.02
    'r_d20_max_pct': -0.18,     # 日线 -0.12
    'r_panic_lookback': 4,      # 日线 20（T2 新键）

    # ── §5.3 硬编码覆盖参数（T2 已在日线引擎参数化，默认值=日线硬编码值）──
    'ftd_min_history': 52,      # 日线 260
    'ftd_low_lookback': 24,     # 日线 120
    'pause_recent_win': 2,      # 日线 5
    'pause_prior_win': 5,       # 日线 20
    'pause_prior_gap': 1,       # 日线 5（=recent_win，两窗相接）
    'pause_vol_min_recent': 1,  # 日线 3
    'pause_vol_min_prior': 2,   # 日线 10
    'pause_shrink_min_recent': 1,  # 日线 3
    'pause_shrink_min_prior': 2,   # 日线 8
    'pause_mid_min_seg': 2,     # 日线 8
    'win_floor': 4,             # 日线 20
    'b_floor_atr': 0.15,        # 日线 0.3
    'bd_win': 3,                # 日线 15
    'bd_amp_max': 0.12,         # 日线 0.08
    'bd_min_points': 3,         # 日线 8
    'support_lookback': 8,      # 日线 40
    'six_dir_lookback': 1,      # 日线 5
    'crossback_low_win': 0,     # 日线 2（周线=当周即可）
    'warn_tops_count': 2,       # 日线 3（周线快照笔少）

    # 日线已有、周线同值（显式列出防漂移；数据完整度阈值周线放宽）
    'data_min_ratio': 0.7,      # 日线 0.8

    # ── ema/ATR 窗口与日线语义对齐（快慢双 EMA、ATR 双口径）──
    'ema_fast': 10, 'ema_slow': 20,   # 周线 EMA 周期与日线同名同窗（单位=周）
})

WEEKLY_TABLE = 'cpa_stage_stock_weekly'
WEEKLY_TRANS_TABLE = 'cpa_stage_stock_weekly_transitions'

WARMUP_WEEKS = 26        # warmup=26 周（交接文档 T1-7）
MIN_WEEKS = WARMUP_WEEKS + 52   # warmup + 分位窗 52（§10 陷阱 3）


# ═══════════════════════════════════════════════════════════
# [1] 表结构
# ═══════════════════════════════════════════════════════════
def ensure_tables(conn):
    """周线 CPA 两表（结构与日线一致，交接文档 T1-2「字段照抄」）"""
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {WEEKLY_TABLE} (
        stock_code TEXT NOT NULL, date TEXT NOT NULL,
        stage TEXT NOT NULL, prior_stage TEXT,
        stage_start_date TEXT, days_in_stage INTEGER,
        structure_support REAL, invalid_level REAL,
        action TEXT, close REAL,
        metrics_json TEXT,
        PRIMARY KEY (stock_code, date))""")
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {WEEKLY_TRANS_TABLE} (
        stock_code TEXT NOT NULL, transition_date TEXT NOT NULL,
        from_stage TEXT, to_stage TEXT,
        trigger_detail_json TEXT,
        invalidated INTEGER DEFAULT 0, invalidated_date TEXT,
        PRIMARY KEY (stock_code, transition_date))""")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_cpa_weekly_date ON {WEEKLY_TABLE}(date)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_cpa_weekly_stage ON {WEEKLY_TABLE}(stage)")
    conn.commit()


# ═══════════════════════════════════════════════════════════
# [2] 数据层（ISO 口径单一真相源：chanlun_weekly.iso_aggregate）
# ═══════════════════════════════ daily ═════
def _load_daily_df_adj(conn, code, min_date='2014-01-01'):
    """读复权日线（与日线 load_klines 同表同口径）→ DataFrame 供 ISO 聚合"""
    import pandas as pd
    rows = conn.execute(
        """SELECT date, raw_open AS open, raw_high AS high, raw_low AS low,
                  raw_close AS close, close AS adj_close, volume, amount
           FROM daily_kline_adj WHERE stock_code=? AND date>=? ORDER BY date""",
        (code, min_date)).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def _apply_adj_factor(df):
    """raw OHLC → 复权（因子 = adj_close/close，当日统一比例；镜像日线 load_klines）"""
    import pandas as pd
    if df.empty:
        return df
    c, a = df['close'], df['adj_close']
    fac = (a / c).where(c > 0, 1.0)
    out = pd.DataFrame({
        'date': df['date'],
        'open': (df['open'] * fac).round(6),
        'high': (df['high'] * fac).round(6),
        'low': (df['low'] * fac).round(6),
        'close': a.where(a.notna() & (a > 0), c),   # adj_close 失效兜底 raw
        'volume': df['volume'],
        'amount': df['amount'],
    })
    return out


def load_weekly_klines(conn, code, min_date='2014-01-01'):
    """复权日线 → ISO 聚合真周K。返回 list[dict]，字段与日线 load_klines 同名同义：
    date(=周代表日)/close(=复权收盘)/adj_close/open_adj/high_adj/low_adj/volume/amount
    """
    import pandas as pd
    df = _load_daily_df_adj(conn, code, min_date)
    if df.empty:
        return []
    df = _apply_adj_factor(df)
    df = df.dropna(subset=['open', 'close', 'high', 'low'])
    if df.empty:
        return []
    weeks = iso_aggregate(df)
    out = []
    for w in weeks:
        out.append({
            'date': w['date'],
            'close': w['close'],           # 已是复权口径
            'adj_close': w['close'],
            'open_adj': w['open'],
            'high_adj': w['high'],
            'low_adj': w['low'],
            'volume': w['volume'],
            'amount': w['amount'],
        })
    return out


def load_weekly_bi_tops_by_date(conn, code, dates):
    """{周代表日: 笔顶列表}——每扫描周取**当时快照**的笔顶（防未来）。

    快照日期=个股自身周代表日（交接文档 D2），周K序列日期同源，天然对齐。
    某周查不到快照（该股当周整周停牌等）→ 用**前一周可见集**兜底（[-5:] 已含在
    _parse_tops 语义内，这里直接复用上一命中周的笔顶列表对象）。
    """
    out = {}
    last_tops = []
    for d in dates:   # dates 升序（run_state_machine 消费顺序）
        row = conn.execute(
            "SELECT bi_json FROM chanlun_weekly_bi_json WHERE stock_code=? AND scan_date=?",
            (code, d)).fetchone()
        if row and row[0]:
            tops = daily._parse_tops(row[0])
            last_tops = tops if tops else last_tops   # 空快照不覆盖（笔刚起步期）
        out[d] = last_tops
    return out


# ═══════════════════ tops 与状态机 ═══════════════════
def run_weekly(code, conn=None, min_date='2014-01-01'):
    """单股周线 CPA：ISO 周K → 日线状态机（WEEKLY_CFG）→ collapse → (daily_rows, trans_rows)

    行组装与日线完全一致（run_state_machine 返回格式直接落库），仅多一步折叠：
    collapse_states(min_days=2) 后按 [(date, stage, orig_stage)] 回填 stage_out，
    orig_stage 塞进 metrics_json.original 供回测（交接文档 T1-5）。
    """
    _ensure_cfg()
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
    try:
        wkl = load_weekly_klines(conn, code, min_date)
        if len(wkl) < MIN_WEEKS:
            return [], [], len(wkl)
        ind = daily.compute_indicators(wkl)
        tops = load_weekly_bi_tops_by_date(conn, code, [k['date'] for k in wkl])
        d_rows, t_rows = daily.run_state_machine(conn, code, wkl, ind, tops, warmup=WARMUP_WEEKS)
        # 数据层折叠（min_days=2 周），折叠返回 [(date, stage, orig_stage)]
        folded = daily.collapse_states(d_rows, min_days=2)
        fold_map = {r[0]: (r[1], r[2]) for r in folded}
        out_rows = []
        for r in d_rows:
            st, orig = fold_map.get(r[1], (r[2], r[2]))
            metrics_json = r[10]
            if orig != st:
                # 折叠改写了阶段：orig（原判据结果）存入 metrics 供回测区分（交接文档 T1-5）
                m = json.loads(r[10]) if r[10] else {}
                m['original'] = orig
                metrics_json = json.dumps(m, ensure_ascii=False)
            # action 按折叠后 stage 重算（⑥a→⑥ 等折叠会改变动作语义）
            m_for_action = json.loads(metrics_json) if metrics_json else {}
            action = daily._action_of(st, m_for_action)
            out_rows.append((r[0], r[1], st, r[3], r[4], r[5], r[6], r[7], action, r[9], metrics_json))
        return out_rows, t_rows, len(wkl)
    finally:
        if own:
            conn.close()


def _ensure_cfg():
    """CFG 污染防护：幂等 update（值相同则无副作用）。

    单进程内先周线后日线会污染日线 CFG（交接文档 D4 ⚠），
    全量回算走多进程天然隔离；此函数保证 run_weekly 重复调用幂等。
    """
    daily.CFG.update(WEEKLY_CFG)


def mark_invalidated_weekly(conn):
    """周线版 mark_invalidated：三处差异——表名 / inv_n(周) / 时间窗按周换算。

    日线用 N×1.45 日历日换算（交易日→日历日）；周线直接 N×7 天
    （周K日期+7N天近似，跨长假误差≤3天可接受，交接文档 T1-6）。
    """
    cfg_saved = {k: daily.CFG[k] for k in WEEKLY_CFG}
    daily.CFG.update(WEEKLY_CFG)
    try:
        def _rank(s):
            s = (s or '').replace('T', '')
            if s.startswith('①'):
                return 1
            if s.startswith('⑥'):
                return 6
            return {'②': 2, '③': 3, '④': 4, '⑤': 5}.get(s, 0)

        q = f"SELECT stock_code, transition_date, from_stage, to_stage FROM {WEEKLY_TRANS_TABLE}"
        rows = conn.execute(q).fetchall()
        n_inv = 0
        for r in rows:
            sc, td, fs, ts = r[0], r[1], r[2], r[3]
            norm_to = ts.replace('T', '')
            if norm_to.startswith('⑥'):
                norm_to = '⑥'
            key = '%s→%s' % ((fs or '').replace('T', ''), norm_to)
            N = daily.CFG['inv_n'].get(key) or daily.CFG['inv_n'].get('→%s' % norm_to)
            if not N:
                continue
            # 周单位：N 周 × 7 天（周K日期+7N天近似）
            limit = (datetime.strptime(td, '%Y-%m-%d') + timedelta(weeks=N)).strftime('%Y-%m-%d')
            rank_to = _rank(ts)
            nxt_rows = conn.execute(
                f"""SELECT transition_date, to_stage FROM {WEEKLY_TRANS_TABLE}
                   WHERE stock_code=? AND transition_date>? AND transition_date<=?
                   ORDER BY transition_date""", (sc, td, limit)).fetchall()
            inv_date = None
            for nr in nxt_rows:
                if _rank(nr[1]) < rank_to:
                    inv_date = nr[0]
                    break
            if inv_date:
                conn.execute(
                    f"""UPDATE {WEEKLY_TRANS_TABLE} SET invalidated=1, invalidated_date=?
                       WHERE stock_code=? AND transition_date=?""", (inv_date, sc, td))
                n_inv += 1
        conn.commit()
        return n_inv
    finally:
        daily.CFG.update(cfg_saved)


# ═══════════════════════════════════════════════════════════
# [3] 回算主流程
# ═══════════════════════════════════════════════════════════
def _weekly_codes(conn, start):
    """股票池：与日线引擎同池同门槛（有足够历史的）"""
    q = """SELECT stock_code, COUNT(*) n FROM daily_kline
           WHERE date>=? GROUP BY stock_code HAVING n>=320"""
    return [r[0] for r in conn.execute(q, (start,))]


def _worker(codes):
    """多进程 worker：独立连接 + 独立 CFG 副本（D4 隔离要求）"""
    import sqlite3 as _sq
    conn = _sq.connect(DB_PATH, timeout=30)
    conn.row_factory = _sq.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    daily_all, trans_all = [], []
    skipped = []
    for code in codes:
        try:
            d, t, nwk = run_weekly(code, conn=conn)
            if nwk < MIN_WEEKS:
                skipped.append(code)
                continue
            daily_all += d
            trans_all += t
        except Exception as e:
            skipped.append('%s(%s)' % (code, str(e)[:40]))
            continue
    conn.close()
    return daily_all, trans_all, skipped


def backfill(start='2016-01-01', end=None, workers=8, purge=True, codes=None):
    """全量回算周线 CPA（多进程）。

    purge=True 清两张周线表（旧引擎 dow==0 bug 的 74 万行脏数据一并清除）。
    end 参数保留签名兼容（周线数据层由 chanlun_weekly_bi_json 决定快照覆盖），
    当前实现按库内最新数据全量重算——增量策略在 T7 daily_update 接入时再定。
    codes=None 时用日线引擎同款池（_weekly_codes）；传入显式列表则用之
    （T4 薄壳用 POOL_SQL 流动性池，与数据层回填同源——无笔顶快照的股票
    算出的 CPA 是半残数据，不应写库，见交接文档 §0.4 / 陷阱 8）。
    """
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_tables(conn)
    if codes is None:
        codes = _weekly_codes(conn, start)
    print('周线 CPA 股票池: %d 只 | 区间 %s~ | %d 进程 | warmup=%d周 min_weeks=%d' % (
        len(codes), start, workers, WARMUP_WEEKS, MIN_WEEKS), flush=True)
    if purge:
        conn.execute(f"DELETE FROM {WEEKLY_TABLE}")
        conn.execute(f"DELETE FROM {WEEKLY_TRANS_TABLE}")
        conn.commit()
        print('已清空旧周线 CPA 表（含旧引擎脏数据）', flush=True)

    n = max(1, len(codes) // (workers * 4))
    chunks = [codes[i:i + n] for i in range(0, len(codes), n)]
    n_daily = n_trans = 0
    all_skipped = []
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_worker, c): i for i, c in enumerate(chunks)}
        for fut in as_completed(futures):
            try:
                d, t, skipped = fut.result()
                all_skipped += skipped
            except Exception as e:
                print('  ! chunk 失败: %s' % str(e)[:80], flush=True)
                continue
            if d:
                conn.executemany(f"INSERT OR REPLACE INTO {WEEKLY_TABLE} VALUES (?,?,?,?,?,?,?,?,?,?,?)", d)
            if t:
                conn.executemany(f"""INSERT OR REPLACE INTO {WEEKLY_TRANS_TABLE}
                    (stock_code, transition_date, from_stage, to_stage, trigger_detail_json, invalidated, invalidated_date)
                    VALUES (?,?,?,?,?,0,NULL)""", t)
            conn.commit()
            n_daily += len(d or [])
            n_trans += len(t or [])
            done += 1
            print('  chunk %d/%d 完成 (周线 %d, 迁移 %d) %.0fs' % (
                done, len(chunks), n_daily, n_trans, time.time() - t0), flush=True)

    print('标记 invalidated（周线版）...', flush=True)
    n_inv = mark_invalidated_weekly(conn)
    conn.close()
    print('周线回算完成: %d 行 | 迁移 %d | 失效标记 %d | 跳过 %d | 耗时 %.0fs' % (
        n_daily, n_trans, n_inv, len(all_skipped), time.time() - t0))
    if all_skipped:
        print('  跳过明细(前10): %s' % ', '.join(str(s) for s in all_skipped[:10]))


if __name__ == '__main__':
    import argparse as _ap
    p = _ap.ArgumentParser(description='CPA 周线引擎 v2 回算（开发期单股调试用）')
    p.add_argument('--code', default=None, help='单股调试：run_weekly 全历史')
    p.add_argument('--start', default='2016-01-01')
    a = p.parse_args()
    if a.code:
        d, t, nwk = run_weekly(a.code)
        print('%s: %d 周 | %d 行 | %d 迁移' % (a.code, nwk, len(d), len(t)))
        for r in d[:3] + d[-3:]:
            print('  ', r[1], r[2], 'days_in=%s' % r[5], 'action=%s' % r[8])
    else:
        backfill(a.start)
