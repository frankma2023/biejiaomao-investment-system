"""周K线缠论分析引擎 —— 真周线笔（CZSC Freq.W）

与日线 chanlun.py 的关系：
- analyze(freq="W") 是"假周线"：读日线直接打 W 标签（chanlun.py analyze()），
  RawBar 仍是日线尺度，笔依然是日线笔。本模块把日线聚合为真周K再喂 CZSC，
  产出真周线笔，供周K CPA 判定引擎（cpa_stage_weekly.py）消费。

聚合口径（PRD §4.4「同口径」约束）：
- ISO week 分组，group_key = {ISO年}-W{ISO周:02d}，代表日 = 该周最后交易日。
  与 pattern-scan 服务端 _aggregate_klines 同款口径。
- 未用 pandas resample("W-FRI") 做数据源的原因：W-FRI 标签是日历周五，
  长假周（周五休市，如国庆、春节）标签落在非交易日，与引擎周K代表日错位；
  ISO 代表日永远是真实交易日。两条聚合路径在此合一，天然同口径，
  验收 A1 抽查长假周照做（verify_vs_w_fri 辅助函数可对比两种口径）。

防未来函数（PRD §6.2）：
- 增量模式 scan_stock_weekly_all：1 次加载日线 → 合成周K → CZSC 逐根 update，
  每个目标周用「截至该周的笔列表」产出（镜像日线 scan_stock_all n_init=1 范式）。
- 写库为「当时可见」快照，cpa_stage_weekly 按扫描周取数，不用最新快照跑历史。

存储（PRD §5.2）：
- chanlun_weekly_bi_json(stock_code, scan_date, bi_json)：
  PK(stock_code, scan_date)，bi_json = 当时可见的周线笔列表 JSON。
  scan_date = 周代表日（该周最后交易日，非日历周五）。

版本：WEEKLY_ALGO_VERSION = 'czsc101_w'（czsc 1.0.1 + 真周线口径）
"""

import json
import sqlite3
from datetime import datetime, timedelta

import pandas as pd

DB_PATH = r"D:\hanako\investment-system\data\lixinger.db"
WEEKLY_ALGO_VERSION = 'czsc101_w'

# 周线窗口上限（周K根数）。回填 2016-2026 约 550 周 + warmup 26 周，
# 默认 600 周足够；日线读取上限 = 周数 × 7（节假日多的年份一周也只占 7 个日历日，
# 5 个交易日足够，取 7 冗余）。
DEFAULT_MAX_WEEKS = 600


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_tables(conn):
    """建周线笔快照表（结构镜像日线 chanlun_bi_json + algo_version 版本列）"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chanlun_weekly_bi_json (
            stock_code TEXT NOT NULL,
            scan_date TEXT NOT NULL,
            bi_json TEXT,
            algo_version TEXT,
            PRIMARY KEY(stock_code, scan_date)
        )
    """)
    try:
        conn.execute("ALTER TABLE chanlun_weekly_bi_json ADD COLUMN algo_version TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def load_daily_df(code, end_date=None, max_weeks=DEFAULT_MAX_WEEKS):
    """读日线 DataFrame（date/open/high/low/close/volume/amount，升序）

    读取上限按周数换算：max_weeks × 7 个日历日回溯，保证合成出 max_weeks 根周K。
    """
    from czsc import Freq  # noqa: F401  预留：确认 czsc 可导入（失败早暴露）

    conn = _connect()
    try:
        if end_date:
            df = pd.read_sql("""
                SELECT date, open, high, low, close, volume, amount
                FROM daily_kline_adj
                WHERE stock_code = ? AND date <= ? AND date >= date(?, '-%d days')
                ORDER BY date
            """ % (int(max_weeks) * 7), conn, params=(code, end_date, end_date))
        else:
            df = pd.read_sql("""
                SELECT date, open, high, low, close, volume, amount
                FROM daily_kline_adj
                WHERE stock_code = ? AND date >= date('now', '-%d days')
                ORDER BY date
            """ % (int(max_weeks) * 7), conn, params=(code,))
    finally:
        conn.close()
    if df.empty:
        return df
    df = df.sort_values("date").reset_index(drop=True)
    df = df.dropna(subset=["open", "close", "high", "low"])
    return df


def iso_week_key(date_str):
    """YYYY-MM-DD → (ISO年, ISO周号, ISO第几日)"""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    iso = dt.isocalendar()
    return iso[0], iso[1], iso[2]


def iso_aggregate(df):
    """日线 DataFrame → 真周K列表（ISO week 分组）

    返回 list[dict]：
      date     = 该周最后交易日（代表日，真实交易日）
      iso_key  = '{ISO年}-W{ISO周:02d}'
      open/high/low/close/volume/amount = 周聚合 OHLCV
    """
    weeks = []
    cur_key = None
    cur = None
    for row in df.itertuples(index=False):
        y, w, _ = iso_week_key(row.date)
        gk = f"{y}-W{w:02d}"
        if gk != cur_key:
            if cur is not None:
                weeks.append(cur)
            cur = {'date': row.date, 'iso_key': gk,
                   'open': row.open, 'high': row.high, 'low': row.low,
                   'close': row.close,
                   'volume': row.volume or 0, 'amount': row.amount or 0}
            cur_key = gk
        else:
            cur['date'] = row.date          # 代表日 = 该周最后交易日（每根覆盖）
            cur['high'] = max(cur['high'], row.high)
            cur['low'] = min(cur['low'], row.low)
            cur['close'] = row.close
            cur['volume'] = (cur['volume'] or 0) + (row.volume or 0)
            cur['amount'] = (cur['amount'] or 0) + (row.amount or 0)
    if cur is not None:
        weeks.append(cur)
    return weeks


def week_representative_dates(df):
    """日线 DataFrame → 该股票所有有交易的周代表日列表（升序）"""
    return [w['date'] for w in iso_aggregate(df)]


def week_is_complete(rep_date, data_max_date, now=None):
    """代表日所在周是否为完整周

    两个充分条件任一即可：
    1. 数据已越过该周日历周五（friday <= data_max）——周内后续交易日已有数据；
    2. 现实时间已过该周日历周五（now > friday）——本周已收盘。长假提前结束
       （如周五休市）的周也算完整，不丢本周信号。
    周内数据未更新到周五且本周还在进行中（now <= friday）→ 不完整，跳过。
    """
    dt = datetime.strptime(rep_date, '%Y-%m-%d')
    friday = dt + timedelta(days=4 - dt.weekday())
    if now is None:
        now = datetime.now()
    # 日期级比较：周五盘中（now 在 friday 当日白天）不算完整，
    # 收盘后数据入库（data_max=friday）才由条件 1 判完整
    return (friday.strftime('%Y-%m-%d') <= data_max_date
            or now.date() > friday.date())


def to_rawbars(code, weeks):
    """周K dict 列表 → RawBar(freq=W) 列表（dt=代表日）"""
    from czsc import RawBar, Freq
    bars = []
    for w in weeks:
        bars.append(RawBar(
            symbol=code, dt=pd.Timestamp(w['date']), freq=Freq.W,
            open=w['open'], close=w['close'], high=w['high'], low=w['low'],
            vol=w['volume'], amount=w['amount'],
        ))
    return bars


def summarize_weekly(code, scan_date, r):
    """从 analyze_from_czsc 结果提取周线摘要（镜像 chanlun_scan.summarize）"""
    if not r or r.get("error"):
        return None
    bi_list = r.get("bi_list", [])
    latest_bi_dir = latest_bi_power = None
    if bi_list:
        last_bi = bi_list[-1]
        latest_bi_dir = str(last_bi.get("direction", ""))
        latest_bi_power = round(float(last_bi.get("power", 0)), 1)
    return {
        "scan_date": scan_date,
        "stock_code": code,
        "freq": "W",
        "bi_count": r.get("bi_count", 0),
        "zs_count": r.get("zs_count", 0),
        "latest_bi_dir": latest_bi_dir,
        "latest_bi_power": latest_bi_power,
        "kline_count": r.get("kline_count", 0),
        "bi_json": json.dumps(bi_list, ensure_ascii=False),
    }


def scan_stock_weekly_all(code, target_dates, max_weeks=DEFAULT_MAX_WEEKS):
    """单股全历史周线扫描：1 次加载日线 + ISO 聚合周K + CZSC 逐根 update

    Args:
        code: 股票代码
        target_dates: 目标扫描周代表日列表（YYYY-MM-DD，市场日历各周最后交易日）
        max_weeks: 周K窗口上限

    Returns:
        list[tuple]: [(个股周代表日, summary or None)]，升序。
        快照存在**个股自己的周代表日**上（与日线引擎「按自身日期查快照」契约对称）：
        市场目标周 → ISO 周键 → 个股自身聚合 bar。个股周内部分停牌（如周五停牌）
        用其自身最后交易日作代表日，不丢整周；整周停牌 → (市场代表日, None)。
        不完整周（本周还在进行中且数据未越过周五）不产出。
    """
    if not target_dates:
        return []
    from czsc import CZSC
    from scanners.chanlun import analyze_from_czsc

    target_set = set(target_dates)
    max_target = max(target_dates)
    df = load_daily_df(code, end_date=max_target, max_weeks=max_weeks)
    if df.empty:
        return [(d, None) for d in sorted(target_dates)]
    data_max = df['date'].max()

    weeks = iso_aggregate(df)
    if not weeks:
        return [(d, None) for d in sorted(target_dates)]

    # n_init=1：逐根 update，每根都有结果（镜像日线 2026-09-12 修复）
    bars_all = to_rawbars(code, weeks)
    c = CZSC(bars_all[:1], max_bi_num=50)

    # ISO 周键 → 个股自身代表日（市场目标周 → 个股快照映射）
    iso2own = {w['iso_key']: w['date'] for w in weeks}
    scan_map = {}
    for t in target_set:
        y, wk, _ = iso_week_key(t)
        own = iso2own.get(f"{y}-W{wk:02d}")
        if own is not None:
            scan_map[own] = t

    out = []
    seen = set()
    for j, bar in enumerate(bars_all):
        if j >= 1:
            c.update(bar)
        d = str(bar.dt.date())
        if d not in scan_map or d in seen:
            continue
        seen.add(d)
        # 完整周校验：本周还在进行中（现实时间未越过周五）且数据未越过周五 → 跳过
        if not week_is_complete(d, data_max):
            continue
        r = analyze_from_czsc(code, c, freq="W", bars=bars_all[:j + 1])
        out.append((d, summarize_weekly(code, d, r)))
    # 补齐未覆盖的目标（整周停牌/不完整周 → None，对齐市场代表日）
    got = {d for d, _ in out}
    for t in target_dates:
        if t not in got and t not in scan_map.values():
            out.append((t, None))
    out.sort(key=lambda x: x[0])
    return out


def analyze_weekly(code, limit=260, end_date=None):
    """分析单只股票的周线缠论结构（当前视角快照，供 API/调试）

    与日线 analyze() 返回结构一致（bi_list/fx_list/zs_list/...），
    基于真周K（ISO 聚合，dt=代表日）。

    Args:
        code: 股票代码
        limit: 周K数量（默认 260 周 ≈ 5 年）
        end_date: 截止日期（防未来数据泄露）；最后一根部分周不产出（当前视角
            未越过周五时）。end_date=None 时以库内最新数据为准，最后一根周K
            只有在日历周五已过（完整周）才产出。

    Returns:
        dict 或 {'error': ...}
    """
    from czsc import CZSC
    from scanners.chanlun import analyze_from_czsc

    df = load_daily_df(code, end_date=end_date, max_weeks=max(limit, 60))
    if df.empty:
        return {"error": f"无K线数据: {code}"}
    data_max = df['date'].max()
    weeks = iso_aggregate(df)
    if not weeks:
        return {"error": f"无周K数据: {code}"}
    # 丢弃尾部不完整周（本周还在进行中且数据未越过周五）
    while weeks and not week_is_complete(weeks[-1]['date'], data_max):
        weeks.pop()
    if not weeks:
        return {"error": f"无完整周K: {code}"}
    bars = to_rawbars(code, weeks[-limit:])
    czsc_obj = CZSC(bars, max_bi_num=50)
    r = analyze_from_czsc(code, czsc_obj, freq="W", bars=bars)
    r["period_dates"] = [w['date'] for w in weeks[-limit:]]
    return r


def verify_vs_w_fri(code, end_date=None):
    """开发/验收辅助：ISO 聚合 vs pandas W-FRI resample 的口径对比

    返回差异清单。两种口径在 A 股交易日历下应「分组等价」，仅长假周
    （周五休市）存在标签差异：W-FRI 标签=日历周五（可能非交易日），
    ISO 代表日=真实最后交易日。此函数把 W-FRI 标签映射到其周内实际
    最后交易日后逐字段对比。
    """
    from scanners.chanlun import _synthesize_klines

    df = load_daily_df(code, end_date=end_date, max_weeks=DEFAULT_MAX_WEEKS)
    if df.empty:
        return {'error': f"无K线数据: {code}"}
    syn = _synthesize_klines(df, 'W')  # period_date=日历周五
    iso = iso_aggregate(df)
    # W-FRI → 周内实际最后交易日 映射
    df_dates = list(df['date'])
    df_set = set(df_dates)
    fri_map = {}
    for p in syn['period_date']:
        ps = str(p)[:10]
        dt = datetime.strptime(ps, '%Y-%m-%d')
        monday = dt - timedelta(days=dt.weekday())
        # 该周（周一~周五）内实际有交易的最后一天
        rep = None
        for off in range(4, -1, -1):
            cand = (monday + timedelta(days=off)).strftime('%Y-%m-%d')
            if cand in df_set:
                rep = cand
                break
        fri_map[ps] = rep
    iso_by_rep = {w['date']: w for w in iso}
    diffs = []
    matched = 0
    for _, row in syn.iterrows():
        ps = str(row['period_date'])[:10]
        rep = fri_map.get(ps)
        if rep is None or rep not in iso_by_rep:
            continue
        w = iso_by_rep[rep]
        matched += 1
        for f in ('open', 'high', 'low', 'close', 'volume'):
            a, b = float(row[f]), float(w[f])
            if abs(a - b) > max(1e-6, abs(b) * 1e-9):
                diffs.append({'week_rep': rep, 'field': f, 'wfri': a, 'iso': b})
    return {'code': code, 'wfri_weeks': int(len(syn)), 'iso_weeks': len(iso),
            'matched': matched, 'diff_count': len(diffs),
            'diffs': diffs[:20], 'iso_only_weeks': len(iso) - matched}


if __name__ == '__main__':
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else '688432'
    end = sys.argv[2] if len(sys.argv) > 2 else None
    r = analyze_weekly(code, end_date=end)
    if r.get('error'):
        print('ERROR:', r['error'])
        sys.exit(1)
    print(f"{code} 周线分析: 周K数={r['kline_count']} 笔数={r['bi_count']} "
          f"中枢={r['zs_count']} 最新笔={r['bi_list'][-1]['direction'] if r['bi_list'] else '-'}")
    if r['bi_list']:
        for b in r['bi_list'][-5:]:
            print(f"  {b['sdt'][:10]} → {b['edt'][:10]} {b['direction']} "
                  f"H={b['high']:.2f} L={b['low']:.2f}")
    v = verify_vs_w_fri(code)
    print(f"口径核对: W-FRI {v.get('wfri_weeks')} 周 vs ISO {v.get('iso_weeks')} 周, "
          f"匹配 {v.get('matched')}, 差异 {v.get('diff_count')}")
