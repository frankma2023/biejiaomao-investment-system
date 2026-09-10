# -*- coding: utf-8 -*-
"""
CPA 阶段判定引擎 v1.1
════════════════════════════════════════════════════════════
PRD: docs/product/CPA阶段判定引擎_产品需求书.md

六阶段状态机（Oliver Kell Cycle of Price Action）：
  ① Reversal Extension → ② Wedge Pop → ③ EMA Crossback
  → ④ Base 'n Break → ⑤ Exhaustion Extension → ⑥ Wedge Drop（⑥a/b/c）→ 循环

核心语义：**阶段 = 由事件触发的区间**（②③④ 是事件，⑤⑥① 是状态/段），
        任何时刻每只股票都属于某个区间，无空档。

本文件结构：
  [1] 配置（所有阈值集中于此，便于回测校准）
  [2] 指标层（EMA/ATR/VR/分位/斜率）
  [3] 数据层（复权 K 线、缠论笔顶、建表）
  [4] 判据层（六阶段）
  [5] 状态机（迁移 + 优先级 + 过渡态 + invalidated）
  [6] 回算主流程（全量/增量）+ 数据层折叠

用法：
  python src/scanners/cpa_stage.py --start 2016-01-01 --workers 8   # 全量回算
  python src/scanners/cpa_stage.py --incremental                     # 每日增量
"""
import os, sys, json, sqlite3
from datetime import datetime, timedelta

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT, 'data', 'lixinger.db')

# ═══════════════════════════════════════════════════════════
# [1] 配置（阈值全部集中，待回测校准的项已标注）
# ═══════════════════════════════════════════════════════════
CFG = {
    # ── 通用 ──
    'vr_win': 20,              # 量比窗口
    'atr_win': 20,             # ATR 窗口
    'atr_win_slow': 60,        # ATR 慢窗（待校准：防垂直拉升钝化）
    'pctile_win': 250,         # 分位窗口
    'ema_fast': 10,
    'ema_slow': 20,
    'slope_lag': 3,            # EMA 斜率比较滞后

    # ── 停顿区识别通用 ──
    'pause_amp_max': 0.30,     # 停顿区振幅上限（防单边行情误判）
    'pause_mid_gap': 0.10,     # 前后半段中枢差异上限（无单边方向）
    'data_min_ratio': 0.8,     # 窗口数据完整度下限

    # ── 阶段① Reversal Extension ──
    'r_drawdown_min': 0.30,    # 自 250 日高点回撤 ≥30%
    'r_nd20_min': 3.0,         # N_D20 ≥3（ATR 归一口径）
    'r_d20_max_pct': -0.12,    # D20 ≤-12%（辅助口径）
    'r_panic_vr': 1.8,         # 恐慌量 VR ≥1.8
    'r_strong_gain': 0.40,     # 前置：250 日内最大涨幅 ≥40%
    'r_high_recency': 120,     # 前置：距 250 日高点 ≤120 交易日
    'r_h_rps250': 70,          # 前置：高点时 RPS250 ≥70
    'r_lower_shadow_ratio': 2.0,   # 长下影：下影 ≥ 实体×2
    'r_lower_shadow_atr': 0.5,     # 且 下影 ≥ ATR20×0.5

    # ── 阶段② Wedge Pop ──
    'w_win_min': 3, 'w_win_max': 40,      # 收缩窗口 3-40 日
    'w_win_long': 10,                      # 长窗口门槛（笔可用）
    'w_amp_tol': 0.12,                     # 区间振幅 ≤12%
    'w_amp_shrink': 0.6,                   # 短窗振幅收缩比
    'w_vol_dry': 0.7,                      # 量能干涸比
    'w_breakout_buf': 1.005,               # 突破缓冲 +0.5%
    'w_breakout_vr': 1.5,                  # 突破量比
    'w_first_lookback': 20,                # "首次站上"回看 20 日

    # ── 阶段③ EMA Crossback ──
    'cb_window_min': 5, 'cb_window_max': 15,   # 距 ② 的窗口
    'cb_tol_atr': 0.3,                          # 容差 0.3×ATR20
    'cb_vol_max': 0.9,                          # 缩量 VR ≤0.9
    'cb_hold_days': 2,                           # ≥2 日收盘在均线上

    # ── 阶段④ Base 'n Break ──
    'b_win_min': 10, 'b_win_max': 40,      # 箱体时长
    'b_depth_ratio': 0.5,                  # 深度 ≤ 前段涨幅 50%
    'b_prior_gain_min': 0.20,              # 前置涨幅 ≥20%（待校准）
    'b_breakout_buf': 1.005,
    'b_breakout_vr': 1.5,
    'b_touch_band': 0.005,                 # 触碰判定带宽 ±0.5%

    # ── 阶段⑤ Exhaustion Extension ──
    'e_peak_gain_min': 0.30,               # 前置：自 ② 峰值涨幅 ≥30%
    'e_nd10_min': 4.0,                     # N_D10 ≥4
    'e_d10_pct_min': 0.16,                 # 或 D10 ≥16%
    'e_pctile_min': 95,                    # 且 250 日分位 ≥95%

    # ── 阶段⑥ Wedge Drop ──
    'd_vr_score': 1.3,                     # VR 评分分档（非门槛）
    'd_resume_days': 0,                    # ⑥b→②/③ 复活：站上 EMA20 即触发

    # ── FTD 快速通道（欧奈尔追盘日，个股适配待验）──
    'ftd_min_dd': 0.25,                    # 深回撤 ≥25%
    'ftd_win_min': 4, 'ftd_win_max': 15,   # 反转窗口 4-15 日（补丁四：7 日封顶是洞）
    'ftd_ret_min': 0.02,                   # 涨幅 ≥2%
    'ftd_vr_min': 1.3,                     # 放量

    # ── 非对称阈值 + 过渡态（抖动治理，2026-09-10 讨论）──
    't_in_band': 0.3,      # 进⑥b：close < EMA20 - 0.3×ATR（防守快确认，宁可误报不可漏报）
    't_in_days': 2,        # 进⑥b 确认天数
    't_out_band': 0.5,     # 出⑥b：close > EMA20 + 0.5×ATR（进攻慢确认，宁可慢不可假）
    't_out_win': 3,        # 出⑥b 观察窗口
    't_out_need': 2,       # 出⑥b 需窗口内 N 日达标（3中2）
    # 过渡态：[-0.3, +0.5]×ATR 区间 = 判据本来就没答案的地带（独立标签）

    # ── invalidated N 值（待校准）──
    'inv_n': {'②→③': 15, '②→④': 40, '③→④': 40, '④→⑤': 30, '⑤→⑥': 30, '→⑥': 12},
}

# 动作方向固定枚举
ACTIONS = {
    '①': '观察', '①a': '观察', '①b': '观察',
    '②': '入场', '②T': '观察',           # ②T = 过渡态（模糊区，不参与）
    '③': '入场', '④': '加仓',
    '⑤': '保护利润', '⑥a': '清仓',
    '⑥b': '观察', '⑥bT': '观察', '⑥c': '观察', '⑥cT': '观察',
}


# ═══════════════════════════════════════════════════════════
# [2] 指标层（纯函数）
# ═══════════════════════════════════════════════════════════
def ema_series(values, n):
    """指数移动平均（首值取第一个有效值）"""
    out = [None] * len(values)
    a = 2.0 / (n + 1)
    prev = None
    for i, v in enumerate(values):
        if v is None:
            out[i] = prev
            continue
        prev = v if prev is None else v * a + prev * (1 - a)
        out[i] = prev
    return out


def sma_series(values, n):
    out = [None] * len(values)
    for i in range(len(values)):
        if i + 1 < n:
            continue
        seg = [x for x in values[i + 1 - n:i + 1] if x is not None]
        if len(seg) == n:
            out[i] = sum(seg) / n
    return out


def atr_series(highs, lows, n):
    """ATR（用复权 high/low 的均值幅度近似：high-low 的 n 日均值）"""
    out = [None] * len(highs)
    for i in range(len(highs)):
        if i + 1 < n:
            continue
        s = 0.0
        ok = True
        for j in range(i + 1 - n, i + 1):
            h, l = highs[j], lows[j]
            if h is None or l is None:
                ok = False
                break
            s += (h - l)
        if ok:
            out[i] = s / n
    return out


def vr_series(volumes, n):
    """量比 = 当日量 / n 日均量（不含当日还是含？——用含当日的前 n 日均量，实盘可算）"""
    out = [None] * len(volumes)
    for i in range(len(volumes)):
        if i + 1 < n:
            continue
        seg = [v for v in volumes[i + 1 - n:i + 1] if v is not None]
        if len(seg) == n and sum(seg) > 0:
            out[i] = volumes[i] / (sum(seg) / n) if volumes[i] is not None else None
    return out


def pctile_of(series, i, win, value):
    """value 在 series[i-win+1..i] 中的百分位（0-100）"""
    if value is None:
        return None
    seg = [x for x in series[max(0, i + 1 - win):i + 1] if x is not None]
    if len(seg) < 20:
        return None
    below = sum(1 for x in seg if x <= value)
    return below / len(seg) * 100


def slope_up(ema, i, lag):
    """EMA 斜率向上：ema[i] > ema[i-lag]"""
    if i - lag < 0 or ema[i] is None or ema[i - lag] is None:
        return None
    return ema[i] > ema[i - lag]


def max_gain_in(klines, i_from, i_to):
    """区间最大涨幅（低点到后续高点的最大涨幅）——用于前置闸门与峰值涨幅"""
    if i_from >= i_to:
        return None
    lo = None
    best = 0.0
    for j in range(i_from, i_to + 1):
        p = klines[j].get('close')
        if p is None:
            continue
        if lo is None or p < lo:
            lo = p
        if lo and lo > 0:
            best = max(best, p / lo - 1)
    return best


def drawdown_from_high(klines, i, win=250):
    """自近 win 日最高收盘的回撤（正数） + 高点索引"""
    s = max(0, i + 1 - win)
    hi_idx, hi = None, None
    for j in range(s, i + 1):
        p = klines[j].get('close')
        if p is None:
            continue
        if hi is None or p > hi:
            hi, hi_idx = p, j
    if hi is None or hi <= 0:
        return None, None
    cur = klines[i].get('close')
    return (1 - cur / hi) if cur else None, hi_idx


# ═══════════════════════════════════════════════════════════
# [3] 数据层
# ═══════════════════════════════════════════════════════════
def load_klines(conn, code, min_date='2014-01-01'):
    """
    加载复权 K 线。返回 list[dict]，每项含：
      date, close(真实), adj_close, low_adj, high_adj, open_adj, volume, amount
    复权因子 = adj_close / close（当日统一比例）
    """
    rows = conn.execute(
        """SELECT date, open, high, low, close, adj_close, volume, amount
           FROM daily_kline WHERE stock_code=? AND date>=? ORDER BY date""",
        (code, min_date)).fetchall()
    out = []
    for r in rows:
        c, a = r['close'], r['adj_close']
        fac = (a / c) if (c and a and c > 0) else 1.0
        out.append({
            'date': r['date'],
            'close': c,
            'adj_close': a if a else c,
            'open_adj': (r['open'] * fac) if r['open'] else None,
            'high_adj': (r['high'] * fac) if r['high'] else None,
            'low_adj': (r['low'] * fac) if r['low'] else None,
            'volume': r['volume'],
            'amount': r['amount'],
        })
    return out


def load_bi_tops(conn, code):
    """
    加载缠论笔顶序列（用于阶段② 收缩收敛 / 阶段⑥ 高点不抬高）。
    约定（同 MW 引擎）：每笔有 direction('向上'/'向下')、sdt、high、low。
      笔顶 = direction='向下' 的笔（从顶开始向下）→ sdt 为顶部日期、high 为顶部价。
    返回 [{'date':..., 'price':...}, ...]（按日期升序）
    """
    row = conn.execute(
        """SELECT bi_json FROM chanlun_bi_json WHERE stock_code=?
           ORDER BY scan_date DESC LIMIT 1""", (code,)).fetchone()
    if not row or not row[0]:
        return []
    try:
        bi = json.loads(row[0])
    except Exception:
        return []
    tops = []
    for b in bi:
        if b.get('direction') == '向下':
            d = (b.get('sdt') or '')[:10]
            h = b.get('high')
            if d and h:
                tops.append({'date': d, 'price': h})
    tops.sort(key=lambda x: x['date'])
    return tops


def load_bi_by_date(conn, code, date):
    """加载指定日期的笔数据（预留：严格当日快照防未来函数——回填路径当前用最新快照 load_bi_tops）"""
    row = conn.execute(
        """SELECT bi_json FROM chanlun_bi_json WHERE stock_code=? AND scan_date=?""",
        (code, date)).fetchone()
    if not row or not row[0]:
        return []
    try:
        return json.loads(row[0])
    except Exception:
        return []


def load_rps250(conn, code, date, win=250):
    """RPS250（预留：阶段① 前置闸门 c 条件用；当前实现用"250日最大涨幅"替代）"""
    try:
        row = conn.execute(
            "SELECT rps_250 FROM stock_rs_daily WHERE stock_code=? AND date=?",
            (code, date)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS cpa_stage_daily (
        stock_code TEXT NOT NULL, date TEXT NOT NULL,
        stage TEXT NOT NULL, prior_stage TEXT,
        stage_start_date TEXT, days_in_stage INTEGER,
        structure_support REAL, invalid_level REAL,
        action TEXT, close REAL,
        metrics_json TEXT,
        PRIMARY KEY (stock_code, date))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS cpa_stage_transitions (
        stock_code TEXT NOT NULL, transition_date TEXT NOT NULL,
        from_stage TEXT, to_stage TEXT,
        trigger_detail_json TEXT,
        invalidated INTEGER DEFAULT 0, invalidated_date TEXT,
        PRIMARY KEY (stock_code, transition_date))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cpa_daily_date ON cpa_stage_daily(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cpa_daily_stage ON cpa_stage_daily(stage)")
    conn.commit()


# ═══════════════════════════════════════════════════════
# [4] 判据层（六阶段）
# ═══════════════════════════════════════════════════════
def compute_indicators(kl):
    """预计算所有指标序列（一次算完，供逐日推进使用）"""
    closes = [k['adj_close'] for k in kl]
    highs = [k['high_adj'] for k in kl]
    lows = [k['low_adj'] for k in kl]
    vols = [k['volume'] for k in kl]
    ema10 = ema_series(closes, 10)
    ema20 = ema_series(closes, 20)
    atr20 = atr_series(highs, lows, 20)
    return {
        'closes': closes, 'highs': highs, 'lows': lows, 'vols': vols,
        'ema10': ema10, 'ema20': ema20, 'atr20': atr20,
        'atr60': atr_series(highs, lows, 60),
        'vr': vr_series(vols, 20),
        'nd10': [(c - e) / a if (e and a and a > 0) else None
                 for c, e, a in zip(closes, ema10, atr20)],
        'nd20': [(c - e) / a if (e and a and a > 0) else None
                 for c, e, a in zip(closes, ema20, atr20)],
        'd10': [(c / e - 1) if (e and e > 0) else None
                for c, e in zip(closes, ema10)],
    }


def find_pause_zone(ind, i, win_min, win_max, amp_tol, vol_dry):
    """找以 i 为突破日的【收缩/停顿区】（阶段②/④ 共用）
    返回 dict(high, low, start_idx, end_idx, win_len, vol_dry_ok, amp_ok, shrink_ok) 或 None

    修正（测试驱动）：加【结构合理性】约束——停顿区必须是"无明显方向的窄幅震荡"，
    否则单边大涨区间（amp 可达 100%+）会被误判为停顿区（因 shrink_ok 单独通过）。
    """
    best = None
    for w in range(win_max, win_min - 1, -1):   # 从长到短找（优先长窗口）
        s = i - w
        if s < 20:
            continue
        seg_high = [x for x in ind['highs'][s:i] if x is not None]
        seg_low = [x for x in ind['lows'][s:i] if x is not None]
        seg_c = [x for x in ind['closes'][s:i] if x is not None]
        if len(seg_high) < w * CFG['data_min_ratio'] or len(seg_low) < w * CFG['data_min_ratio']:
            continue
        zh, zl = max(seg_high), min(seg_low)
        if zl <= 0:
            continue
        amp = (zh - zl) / zl
        # ── 结构合理性（必要）：前后半段价格中枢差异 ≤10%（无单边方向）──
        if len(seg_c) >= 8:
            mid = len(seg_c) // 2
            avg1 = sum(seg_c[:mid]) / mid
            avg2 = sum(seg_c[mid:]) / (len(seg_c) - mid)
            if avg1 <= 0 or abs(avg2 / avg1 - 1) > CFG['pause_mid_gap']:
                continue          # 单边行情，不是停顿
        # ── 振幅上限（必要，防单边/大涨区间误判）──
        if amp > CFG['pause_amp_max']:
            continue
        # 量能干涸：突破前 3-5 日均量 vs 之前 20 日均量
        v_recent = [x for x in ind['vols'][max(0, i - 5):i] if x]
        v_prior = [x for x in ind['vols'][max(0, i - 25):max(0, i - 5)] if x]
        dry_ok = False
        if len(v_recent) >= 3 and len(v_prior) >= 10:
            dry_ok = (sum(v_recent) / len(v_recent)) <= (sum(v_prior) / len(v_prior)) * vol_dry
        # 振幅收缩（短窗口）：突破前 3-5 日均振幅 vs 更早 10-20 日
        a_recent, a_prior = [], []
        for j in range(max(0, i - 5), i):
            h, l = ind['highs'][j], ind['lows'][j]
            if h and l and l > 0:
                a_recent.append((h - l) / l)
        for j in range(max(0, i - 25), max(0, i - 5)):
            h, l = ind['highs'][j], ind['lows'][j]
            if h and l and l > 0:
                a_prior.append((h - l) / l)
        shrink_ok = False
        if len(a_recent) >= 3 and len(a_prior) >= 8:
            shrink_ok = (sum(a_recent) / len(a_recent)) <= (sum(a_prior) / len(a_prior)) * CFG['w_amp_shrink']
        amp_ok = amp <= amp_tol
        if dry_ok or amp_ok or shrink_ok:
            cand = {'high': zh, 'low': zl, 'start_idx': s, 'end_idx': i - 1,
                    'win_len': w, 'vol_dry_ok': dry_ok, 'amp_ok': amp_ok,
                    'shrink_ok': shrink_ok, 'amp': amp}
            # 优先条件更充分者，其次更长窗口
            score = (dry_ok + amp_ok + shrink_ok, w)
            if best is None or score > best[0]:
                best = (score, cand)
    return best[1] if best else None


def judge_reversal(ind, kl, i, tops):
    """阶段① 判据。返回 {'a':bool, 'b':bool, 'detail':{...}}"""
    c = ind['closes'][i]
    if c is None or ind['ema20'][i] is None or not ind['atr20'][i]:
        return {'a': False, 'b': False, 'detail': {}}
    dd, hi_idx = drawdown_from_high(kl, i, CFG['pctile_win'])
    if dd is None or hi_idx is None:
        return {'a': False, 'b': False, 'detail': {}}
    # 前置闸门：强势股
    gate_gain = max_gain_in(kl, max(0, hi_idx - 250), hi_idx)
    gate_recent = (i - hi_idx) <= CFG['r_high_recency']
    gate_a = (gate_gain is not None and gate_gain >= CFG['r_strong_gain'])
    if not gate_a:
        return {'a': False, 'b': False, 'detail': {'gate': 'strong_gain_fail', 'gain': gate_gain}}
    # ①a
    nd20 = ind['nd20'][i]
    d20 = (c / ind['ema20'][i] - 1) if ind['ema20'][i] else None
    deep = dd >= CFG['r_drawdown_min']
    ext = (nd20 is not None and -nd20 >= CFG['r_nd20_min']) or (d20 is not None and d20 <= CFG['r_d20_max_pct'])
    # 恐慌量：近 20 日出现 VR ≥ 门槛的下跌日
    panic = False
    for j in range(max(0, i - 20), i + 1):
        vj, cj = ind['vr'][j], ind['closes'][j]
        cj_prev = ind['closes'][j - 1] if j > 0 else None
        if vj and cj and cj_prev and cj < cj_prev and vj >= CFG['r_panic_vr']:
            panic = True
            break
    a_ok = bool(deep and ext and panic and gate_recent)
    # ①b 衰竭迹象
    b_ok = False
    b_detail = {}
    if a_ok:
        k = kl[i]
        o, h, l, cl = k.get('open_adj'), k.get('high_adj'), k.get('low_adj'), k.get('adj_close')
        if None not in (o, h, l, cl) and (atr := ind['atr20'][i]):
            body = abs(cl - o)
            lower = min(o, cl) - l
            if body > 0 and lower >= body * CFG['r_lower_shadow_ratio'] and lower >= atr * CFG['r_lower_shadow_atr']:
                b_ok, b_detail = True, {'type': 'long_lower_shadow', 'lower': round(lower, 3)}
            # 反转日：low < 前日 low 且 close > 前日 high
            if not b_ok and i > 0:
                pl, ph = kl[i - 1].get('low_adj'), kl[i - 1].get('high_adj')
                if pl and ph and l < pl and cl > ph:
                    b_ok, b_detail = True, {'type': 'reversal_day'}
    return {'a': a_ok, 'b': b_ok,
            'detail': {'drawdown': round(dd, 3), 'nd20': round(nd20, 2) if nd20 else None,
                       'panic': panic, 'gate_gain': round(gate_gain, 3) if gate_gain else None,
                       **b_detail}}


def judge_ftd(ind, kl, i):
    """FTD 快速通道（欧奈尔追盘日，节点：底部反转确认）
    条件：深回撤 ≥25% + 低点后 4-15 日 + 涨幅 ≥2% + VR ≥1.3 + 收盘 > EMA20
    返回 {'hit':bool, 'low_idx':int, 'low':float, 'detail':{...}}

    设计理由（与用户讨论）：FTD 只标记"一个值得跟踪的起点"（证据负担轻）——
    不需要停顿区/多日确认；而普通复苏（出⑥b）需证明"下降趋势已终结"（证据负担重）——
    两个判据服务于两种证据负担，冲突自然消解。
    注：欧奈尔原意 FTD 是指数级信号，个股适用性待验（用户保留态度）。
    """
    if i < 1:
        return {'hit': False}
    c, c1 = ind['closes'][i], ind['closes'][i - 1]
    vr, e20 = ind['vr'][i], ind['ema20'][i]
    if None in (c, c1, vr, e20) or not c1:
        return {'hit': False}
    if not (c / c1 - 1 >= CFG['ftd_ret_min'] and vr >= CFG['ftd_vr_min'] and c > e20):
        return {'hit': False}
    # 低点：i 前 ftd_win_min~ftd_win_max 日内存在"近 120 日最低收盘"的低点 L，且自 250 日高点回撤 ≥25%
    for L in range(i - CFG['ftd_win_max'], i - CFG['ftd_win_min'] + 1):
        if L < 260:
            continue
        s = max(0, L + 1 - 120)
        seg = [kl[j]['adj_close'] for j in range(s, L + 1) if kl[j]['adj_close']]
        if not seg or kl[L]['adj_close'] != min(seg):
            continue
        dd, _ = drawdown_from_high(kl, L, CFG['pctile_win'])
        if dd is None or dd < CFG['ftd_min_dd']:
            continue
        win = i - L
        low = kl[L]['adj_close']
        return {'hit': True, 'low_idx': L, 'low': low,
                'detail': {'path': 'ftd_4_7' if win <= 7 else 'ftd_8_15',
                           'ftd_win': win, 'ret': round(c / c1 - 1, 3),
                           'vr': round(vr, 2), 'low': round(low, 2), 'dd': round(dd, 3),
                           'low_date': kl[L]['date']}}
    return {'hit': False}


def judge_wedge_pop(ind, kl, i, tops):
    """阶段② 事件判据：
    路径 B（快速）：FTD 特征（深回撤后 4-15 日放量 +2% 站上 EMA20）——无停顿区要求
    路径 A（慢速）：收缩/停顿区 + 放量突破 + 首次站上 10/20 EMA
    返回 {'hit':bool, 'path':str, 'pause':{...}|None, 'low':float, 'detail':{...}}
    """
    c, v = ind['closes'][i], ind['vr'][i]
    e10, e20 = ind['ema10'][i], ind['ema20'][i]
    if c is None or e10 is None or e20 is None or v is None:
        return {'hit': False, 'path': None, 'pause': None, 'low': None, 'detail': {}}
    if not (c > e10 and c > e20):
        return {'hit': False, 'path': None, 'pause': None, 'low': None, 'detail': {}}
    # ── 路径 B：FTD 快速通道（无需首次站上/停顿区）──
    ftd = judge_ftd(ind, kl, i)
    if ftd['hit']:
        return {'hit': True, 'path': ftd['detail']['path'], 'pause': None,
                'low': ftd['low'], 'detail': ftd['detail']}
    # ── 路径 A：停顿区慢速通道 ──
    below = 0
    for j in range(max(0, i - CFG['w_first_lookback']), i):
        cj, ej = ind['closes'][j], ind['ema20'][j]
        if cj and ej and cj < ej:
            below += 1
    first_above = below >= CFG['w_first_lookback'] * 0.6
    if not first_above:
        return {'hit': False, 'path': None, 'pause': None, 'low': None, 'detail': {'first_above': False}}
    pause = find_pause_zone(ind, i, CFG['w_win_min'], CFG['w_win_max'],
                            CFG['w_amp_tol'], CFG['w_vol_dry'])
    if not pause:
        return {'hit': False, 'path': None, 'pause': None, 'low': None, 'detail': {'pause': 'none'}}
    if not (c > pause['high'] * CFG['w_breakout_buf'] and v >= CFG['w_breakout_vr']):
        return {'hit': False, 'path': None, 'pause': pause, 'low': None,
                'detail': {'break': False, 'vr': round(v, 2)}}
    bio_conv = None
    if pause['win_len'] >= CFG['w_win_long'] and tops:
        recent = [t for t in tops if t['date'] >= kl[pause['start_idx']]['date']][-3:]
        if len(recent) >= 2:
            bio_conv = all(recent[j + 1]['price'] <= recent[j]['price'] * 1.01 for j in range(len(recent) - 1))
    return {'hit': True, 'path': 'pause', 'pause': pause, 'low': pause['low'],
            'detail': {'path': 'pause', 'vr': round(v, 2), 'amp': round(pause['amp'], 3),
                       'win': pause['win_len'], 'dry': pause['vol_dry_ok'],
                       'shrink': pause['shrink_ok'], 'bi_conv': bio_conv,
                       'breakout': round(c, 2), 'pause_low': round(pause['low'], 2)}}


def judge_crossback(ind, kl, i, ctx):
    """阶段③ 事件判据：② 后 5-15 日内首次回踩 EMA 并守住
    ctx: 状态机上下文 {'w_date_idx':int, 'w_pause':dict, 'w_close':float}
    """
    wi = ctx.get('w_date_idx')
    if wi is None:
        return {'hit': False}
    gap = i - wi
    if not (CFG['cb_window_min'] <= gap <= CFG['cb_window_max']):
        return {'hit': False}
    c, l = ind['closes'][i], ind['lows'][i]
    e10, e20, a20, v = ind['ema10'][i], ind['ema20'][i], ind['atr20'][i], ind['vr'][i]
    if None in (c, l, e10, e20, a20, v):
        return {'hit': False}
    tol = CFG['cb_tol_atr'] * a20
    # 档位：标准档（触 EMA10）
    std = (l <= e10 + tol) and (c > e10)
    deep = (l <= e20 + tol) and (c > e20) and (c < e10)
    if not (std or deep):
        return {'hit': False}
    phase = 'standard' if std else 'deep'
    line = e10 if phase == 'standard' else e20
    # 缩量
    if v > CFG['cb_vol_max']:
        return {'hit': False, 'phase': phase, 'detail': {'vol_fail': round(v, 2)}}
    # 持续守住：回踩期 ≥2 日 close 在该均线上方（含当日与之前几日）
    hold = 0
    for j in range(max(0, i - CFG['cb_hold_days'] * 2), i + 1):
        cj, ej = ind['closes'][j], ind['ema10'][j] if phase == 'standard' else ind['ema20'][j]
        if cj and ej and cj > ej:
            hold += 1
    # 角色反转：EMA10 斜率转正
    slope = slope_up(ind['ema10'], i, CFG['slope_lag'])
    if hold < CFG['cb_hold_days'] or not slope:
        return {'hit': False, 'phase': phase, 'detail': {'hold': hold, 'slope': slope}}
    # 深度上限：回踩 low ≥ 入口结构低点（FTD 入口=FTD 前低点；慢速入口=停顿区低点）
    entry_low = ctx.get('entry_low') or (ctx.get('w_pause') or {}).get('high')
    if entry_low and l < entry_low:
        return {'hit': False, 'phase': phase, 'detail': {'depth_fail': round(l, 2), 'entry_low': round(entry_low, 2)}}
    return {'hit': True, 'phase': phase,
            'detail': {'days_since_②': gap, 'vr': round(v, 2), 'hold': hold,
                       'slope_up': slope, 'stop': round(line, 2)}}


def find_box(ind, kl, i, prior_gain_start_idx):
    """阶段④ 箱体识别（无参数边界 + 四项硬门槛）
    返回 dict(high, low, start_idx, win_len, depth, touch_top, touch_bot) 或 None
    """
    best = None
    for w in range(CFG['b_win_max'], CFG['b_win_min'] - 1, -1):
        s = i - w
        if s < 20:
            continue
        # 箱体边界：收盘价极值（PRD §6：无参数）
        seg_c = [x for x in ind['closes'][s:i] if x is not None]
        if len(seg_c) < w * 0.8:
            continue
        zh, zl = max(seg_c), min(seg_c)
        if zl <= 0:
            continue
        depth = (zh - zl) / zl
        # 均线位置：箱底 ≥ EMA20 - 0.3*ATR20
        e20 = ind['ema20'][i - 1]
        a20 = ind['atr20'][i - 1]
        if e20 is None or a20 is None or zl < e20 - 0.3 * a20:
            continue
        # 量能收缩：后段均量 < 前段均量
        half = w // 2
        v1 = [x for x in ind['vols'][s:s + half] if x]
        v2 = [x for x in ind['vols'][s + half:i] if x]
        if not (v1 and v2):
            continue
        vol_ok = (sum(v2) / len(v2)) < (sum(v1) / len(v1))
        # 深度门槛
        prior_gain = None
        if prior_gain_start_idx is not None and prior_gain_start_idx < s:
            base_close = kl[prior_gain_start_idx].get('close')
            if base_close and base_close > 0:
                prior_gain = zh / base_close - 1
        depth_ok = (prior_gain is not None and depth <= prior_gain * CFG['b_depth_ratio']) or depth <= 0.15
        if not (vol_ok and depth_ok):
            continue
        # 触碰次数（质量评分）
        band = CFG['b_touch_band']
        touch_top = sum(1 for x in seg_c if x >= zh * (1 - band))
        touch_bot = sum(1 for x in seg_c if x <= zl * (1 + band))
        cand = {'high': zh, 'low': zl, 'start_idx': s, 'win_len': w, 'depth': depth,
                'touch_top': touch_top, 'touch_bot': touch_bot, 'prior_gain': prior_gain}
        if best is None or w > best['win_len']:
            best = cand
    return best


def judge_base_break(ind, kl, i, ctx):
    """阶段④ 事件判据：箱体 + 放量突破箱顶"""
    c, v = ind['closes'][i], ind['vr'][i]
    if c is None or v is None:
        return {'hit': False, 'box': None}
    start_idx = ctx.get('entry_idx')
    box = find_box(ind, kl, i, start_idx)
    if not box:
        return {'hit': False, 'box': None}
    # 前置：自最后有效入场点涨幅 ≥20%
    if start_idx is not None:
        base_c = kl[start_idx].get('close')
        if base_c and base_c > 0:
            gain = box['high'] / base_c - 1
            if gain < CFG['b_prior_gain_min']:
                return {'hit': False, 'box': box, 'detail': {'prior_gain': round(gain, 3), 'note': '涨幅不足→边界样本'}}
    if not (c > box['high'] * CFG['b_breakout_buf'] and v >= CFG['b_breakout_vr']):
        return {'hit': False, 'box': box, 'detail': {'vr': round(v, 2)}}
    return {'hit': True, 'box': box,
            'detail': {'box_high': round(box['high'], 2), 'box_low': round(box['low'], 2),
                       'win': box['win_len'], 'depth': round(box['depth'], 3),
                       'touch_top': box['touch_top'], 'vr': round(v, 2)}}


def judge_exhaustion(ind, kl, i, ctx):
    """阶段⑤ 判据：前置峰值涨幅 + 延伸极值（双口径取或 + 分位）"""
    c = ind['closes'][i]
    e10, a20 = ind['ema10'][i], ind['atr20'][i]
    if None in (c, e10, a20) or e10 <= 0 or a20 <= 0:
        return {'hit': False}
    # 前置：自 ② 以来峰值涨幅 ≥30%
    wi = ctx.get('w_date_idx')
    if wi is None:
        return {'hit': False}
    base = kl[wi].get('close')
    if not base or base <= 0:
        return {'hit': False}
    peak = max([x for x in ind['closes'][wi:i + 1] if x] or [base])
    peak_gain = peak / base - 1
    if peak_gain < CFG['e_peak_gain_min']:
        return {'hit': False, 'detail': {'peak_gain': round(peak_gain, 3)}}
    nd10 = (c - e10) / a20
    d10 = c / e10 - 1
    hit_ext = (nd10 >= CFG['e_nd10_min']) or (d10 >= CFG['e_d10_pct_min'])
    pctl = pctile_of(ind['nd10'], i, CFG['pctile_win'], nd10)
    hit_pctl = pctl is not None and pctl >= CFG['e_pctile_min']
    if not (hit_ext and hit_pctl):
        return {'hit': False, 'detail': {'nd10': round(nd10, 2), 'd10': round(d10, 3),
                                          'pctl': pctl, 'peak_gain': round(peak_gain, 3)}}
    vr = ind['vr'][i]
    return {'hit': True, 'detail': {'nd10': round(nd10, 2), 'd10': round(d10, 3),
                                     'pctl': pctl, 'peak_gain': round(peak_gain, 3),
                                     'vr': round(vr, 2) if vr else None,
                                     'vr_class': ('放量' if (vr and vr >= CFG['d_vr_score']) else '缩量')}}


def judge_wedge_drop(ind, kl, i, ctx, structure_support, tops):
    """阶段⑥ 判据：衰竭①② + 破坏③（一票否决）
    返回 {'warn':bool, 'confirm':bool, 'detail':{...}}
    """
    c, e20 = ind['closes'][i], ind['ema20'][i]
    if c is None or e20 is None:
        return {'warn': False, 'confirm': False, 'detail': {}}
    # ① 高点不抬高（笔顶序列）
    hl = None
    if tops:
        recent = [t for t in tops if t['date'] <= kl[i]['date']][-3:]
        if len(recent) >= 2:
            hl = all(recent[j + 1]['price'] <= recent[j]['price'] * 1.01 for j in range(len(recent) - 1))
    # ② EMA10 斜率转负
    slope = slope_up(ind['ema10'], i, CFG['slope_lag'])
    neg = (slope is False)
    warn = bool(hl and neg)
    # ③ 趋势破坏：close < EMA20 且 close < 结构支撑位
    below_ma = c < e20
    below_sup = (structure_support is not None and c < structure_support)
    confirm = bool(below_ma and below_sup and warn)
    vr = ind['vr'][i]
    return {'warn': warn, 'confirm': confirm,
            'detail': {'high_lower': hl, 'slope_neg': neg, 'close': round(c, 2),
                       'ema20': round(e20, 2), 'support': round(structure_support, 2) if structure_support else None,
                       'below_ma': below_ma, 'below_sup': below_sup,
                       'vr': round(vr, 2) if vr else None,
                       'vr_class': ('放量' if (vr and vr >= CFG['d_vr_score']) else '缩量')}}


# ═══════════════════════════════════════════════════════
# [5] 状态机
# ═══════════════════════════════════════════════════════
def init_stage(ind, kl, i):
    """初始状态判定（PRD §9.6 六规则，从高到低）"""
    c = ind['closes'][i]
    e20 = ind['ema20'][i]
    if c is None or e20 is None:
        return '①a', {}
    dd, _ = drawdown_from_high(kl, i, CFG['pctile_win'])
    nd10 = ind['nd10'][i]
    above = c > e20
    if above:
        pctl = pctile_of(ind['nd10'], i, CFG['pctile_win'], nd10) if nd10 is not None else None
        if pctl is not None and pctl >= CFG['e_pctile_min'] and nd10 and nd10 >= CFG['e_nd10_min']:
            return '⑤', {}
        if find_box(ind, kl, i, max(0, i - 120)):
            return '④', {'init_box': True}
        return '②', {'init_flag': True}      # 初值推断（标记）
    if dd is not None and dd >= CFG['r_drawdown_min']:
        return '⑥c', {'init_flag': True}
    return '⑥b', {'init_flag': True}


def max_gain_since(kl, i0, i1):
    """自 i0 以来的峰值涨幅（阶段⑤ 前置用）"""
    b = kl[i0].get('close')
    if not b or b <= 0:
        return None
    peak = max([x for x in (k.get('close') for k in kl[i0:i1 + 1]) if x] or [b])
    return peak / b - 1


def below_ma_confirmed(ind, i, n=2, band_atr=0.3):
    cnt = 0
    for j in range(max(0, i - n + 1), i + 1):
        c, e, a = ind['closes'][j], ind['ema20'][j], ind['atr20'][j]
        if c is not None and e is not None and a and c < e - band_atr * a:
            cnt += 1
    return cnt >= n


def above_ma_confirmed(ind, i, n=3, band_atr=0.5, need=2):
    """n 日窗口内 need 日收盘 > EMA20 + band*ATR20（非对称：出⑥b 用宽带 0.5 + 3中2，进攻慢确认）"""
    cnt = 0
    for j in range(max(0, i - n + 1), i + 1):
        c, e, a = ind['closes'][j], ind['ema20'][j], ind['atr20'][j]
        if c is not None and e is not None and a and c > e + band_atr * a:
            cnt += 1
    return cnt >= need


def in_transition_zone(ind, i, stage):
    """过渡态判定：处于 ②/⑥b/⑥c 且价格在模糊带 [-0.3, +0.5]×ATR 内
    → 判据本来就没答案的地带（回测已证：低收益垃圾时间区）
    """
    if stage not in ('②', '⑥b', '⑥c'):
        return False
    c, e, a = ind['closes'][i], ind['ema20'][i], ind['atr20'][i]
    if None in (c, e, a) or not a:
        return False
    lo = e - CFG['t_in_band'] * a
    hi = e + CFG['t_out_band'] * a
    return lo <= c <= hi


def run_state_machine(conn, code, kl, ind, tops, warmup=260):
    """逐日推进状态机。
    返回 (daily_rows, trans_rows)
      daily_rows: (code, date, stage, prior, start_date, days_in, support, invalid_level, action, close, metrics_json)
      trans_rows: (code, date, from_stage, to_stage, detail_json)

    阶段语义：阶段 = 由事件触发的区间（PRD §9.1），区间内每日输出快照。
    """
    daily, trans = [], []
    n = len(kl)
    if n <= warmup:
        return daily, trans

    stage, ctx = init_stage(ind, kl, warmup - 1)
    ctx = dict(ctx)
    ctx.update({'stage_start_idx': warmup - 1, 'entry_idx': None, 'w_date_idx': None,
                'w_pause': None, 'cb_low': None, 'box': None, 'exh_start_idx': None})
    structure_support = None

    for i in range(warmup, n):
        d = kl[i]['date']
        c = ind['closes'][i]
        e10, e20, a20 = ind['ema10'][i], ind['ema20'][i], ind['atr20'][i]
        if c is None or e10 is None or e20 is None or not a20:
            continue
        prev_stage = stage
        detail = {}

        # ── 结构支撑位维护（只升不降）──
        if ctx.get('cb_low'):
            structure_support = ctx['cb_low']
        elif ctx.get('box'):
            structure_support = ctx['box']['low']
        elif ctx.get('w_pause'):
            structure_support = ctx['w_pause']['low']
        else:
            seg = [x for x in ind['closes'][max(0, i - 40):i + 1] if x]
            structure_support = min(seg) if seg else None

        # ═══ 迁移判定（按状态分支）═══
        if stage in ('①a', '①b'):
            r1 = judge_reversal(ind, kl, i, tops)
            sub = '①b' if r1['b'] else ('①a' if r1['a'] else stage)
            if sub != stage:
                stage = sub; ctx['stage_start_idx'] = i; detail = r1['detail']
            w = judge_wedge_pop(ind, kl, i, tops)
            if w['hit']:
                stage = '②'
                ctx.update({'w_date_idx': i, 'w_pause': w['pause'], 'entry_idx': i,
                            'entry_low': w['low'], 'entry_path': w['path'],
                            'stage_start_idx': i, 'cb_low': None, 'box': None})
                detail = w['detail']

        elif stage == '②':
            # ②失效判据：跌破【入口结构低点】（FTD前低点/停顿区低点）/ 结构支撑 / 连续2日破EMA20
            entry_low = ctx.get('entry_low')
            pl = (ctx.get('w_pause') or {}).get('low')
            fail_low = entry_low or pl or structure_support
            if below_ma_confirmed(ind, i, CFG['t_in_days'], CFG['t_in_band']):
                # n 日跌破 EMA20-band：底部反转区间结束 → ⑥ 判定
                r6 = judge_wedge_drop(ind, kl, i, ctx, structure_support, tops)
                stage = '⑥a' if r6['confirm'] else '⑥b'
                ctx.update({'stage_start_idx': i, 'w_pause': None, 'w_date_idx': None})
                detail = {'reason': '②区间跌破EMA20(n日确认)', **r6['detail']}
            elif fail_low and c < fail_low:
                # 跌破入口结构低点 → 结构失败（V型底部不成立/突破失败）
                stage = '①a'
                ctx.update({'w_pause': None, 'w_date_idx': None, 'entry_low': None, 'stage_start_idx': i})
                detail = {'reason': '②失效（跌破入口结构低点）', 'entry_low': round(fail_low, 2),
                          'entry_path': ctx.get('entry_path')}
            else:
                r5 = judge_exhaustion(ind, kl, i, ctx)
                r4 = judge_base_break(ind, kl, i, ctx)
                r3 = judge_crossback(ind, kl, i, ctx)
                if r5['hit']:
                    stage = '⑤'; ctx['exh_start_idx'] = i; ctx['stage_start_idx'] = i; detail = r5['detail']
                elif r4['hit']:
                    stage = '④'; ctx['box'] = r4['box']; ctx['entry_idx'] = i; ctx['stage_start_idx'] = i; detail = r4['detail']
                elif r3['hit']:
                    stage = '③'; ctx['cb_low'] = kl[i].get('close'); ctx['entry_idx'] = i; ctx['stage_start_idx'] = i; detail = r3['detail']

        elif stage == '③':
            if below_ma_confirmed(ind, i, CFG['t_in_days'], CFG['t_in_band']):
                # n 日跌破 EMA20 → 进 ⑥ 判定（confirm→⑥a；否则⑥b）
                r6 = judge_wedge_drop(ind, kl, i, ctx, structure_support, tops)
                stage = '⑥a' if r6['confirm'] else '⑥b'
                ctx.update({'stage_start_idx': i, 'cb_low': None})
                detail = {'reason': '③区间跌破EMA20(2日确认)', **r6['detail']}
            else:
                cb_low = ctx.get('cb_low')
                if cb_low and c < cb_low:
                    stage = '②'; ctx.update({'cb_low': None, 'stage_start_idx': i})
                    detail = {'reason': '③失效（跌破回踩低点但守住EMA20）→回②区间'}
                else:
                    r5 = judge_exhaustion(ind, kl, i, ctx)
                    r4 = judge_base_break(ind, kl, i, ctx)
                    if r5['hit']:
                        stage = '⑤'; ctx['exh_start_idx'] = i; ctx['stage_start_idx'] = i; detail = r5['detail']
                    elif r4['hit']:
                        stage = '④'; ctx['box'] = r4['box']; ctx['entry_idx'] = i; ctx['stage_start_idx'] = i; detail = r4['detail']

        elif stage == '④':
            box = ctx.get('box')
            if box and c < box['high']:
                # ④ 失效（收盘回到箱内）
                if c < e20:
                    r6 = judge_wedge_drop(ind, kl, i, ctx, structure_support, tops)
                    stage = '⑥a' if r6['confirm'] else '⑥b'
                    ctx['stage_start_idx'] = i; detail = {'reason': '④失效+趋势破坏', **r6['detail']}
                else:
                    stage = '③'; ctx['stage_start_idx'] = i; detail = {'reason': '④失效（回箱内，趋势未破）→回③'}
            else:
                r5 = judge_exhaustion(ind, kl, i, ctx)
                if r5['hit']:
                    stage = '⑤'; ctx['exh_start_idx'] = i; ctx['stage_start_idx'] = i; detail = r5['detail']
                else:
                    # 新箱体（连续加仓）
                    nb = find_box(ind, kl, i, ctx.get('entry_idx'))
                    if nb and c > nb['high'] * CFG['b_breakout_buf'] and (ind['vr'][i] or 0) >= CFG['b_breakout_vr']:
                        ctx['box'] = nb; ctx['entry_idx'] = i; ctx['stage_start_idx'] = i
                        detail = {'reason': '连续箱体突破', 'box_high': round(nb['high'], 2)}

        elif stage == '⑤':
            if c < e10:
                r6 = judge_wedge_drop(ind, kl, i, ctx, structure_support, tops)
                stage = '⑥a' if r6['confirm'] else '⑥b'
                ctx['stage_start_idx'] = i; detail = {'reason': '⑤结束（跌破EMA10）', **r6['detail']}

        elif stage in ('⑥a', '⑥b', '⑥c'):
            dd, _ = drawdown_from_high(kl, i, CFG['pctile_win'])
            # 趋势复活：窗口 t_out_win 内 t_out_need 日站上 EMA20+t_out_band×ATR → 回 ② 区间
            if above_ma_confirmed(ind, i, CFG['t_out_win'], CFG['t_out_band'], CFG['t_out_need']) and stage in ('⑥a', '⑥b', '⑥c'):
                stage = '②'; ctx.update({'stage_start_idx': i, 'w_date_idx': i, 'w_pause': None, 'cb_low': None})
                detail = {'reason': '趋势复活（站上EMA20）→回②区间'}
            else:
                r1 = judge_reversal(ind, kl, i, tops)
                if r1['a']:   # ①a 全条件满足
                    stage = '①b' if r1['b'] else '①a'
                    ctx['stage_start_idx'] = i; ctx['w_pause'] = None; ctx['w_date_idx'] = None
                    detail = {'reason': '①a全条件满足（跌够且衰竭）'}
                elif dd is not None and dd >= CFG['r_drawdown_min']:
                    if stage != '⑥c':
                        stage = '⑥c'; detail = {'reason': '回撤≥30%但衰竭未现'}
                else:
                    if stage == '⑥a':
                        stage = '⑥b'; detail = {'reason': '跌破后延续（回撤<30%）'}

        # ═══ 失效位 ═══
        invalid = None
        if stage in ('①a', '①b'):
            invalid = None
        elif stage == '②' and ctx.get('w_pause'):
            invalid = ctx['w_pause']['low']
        elif stage == '③' and ctx.get('cb_low'):
            invalid = ctx['cb_low']
        elif stage == '④' and ctx.get('box'):
            invalid = ctx['box']['high']
        elif stage == '⑤':
            invalid = e10
        elif stage.startswith('⑥'):
            invalid = e20

        metrics = {'nd10': round(ind['nd10'][i], 2) if ind['nd10'][i] is not None else None,
                   'nd20': round(ind['nd20'][i], 2) if ind['nd20'][i] is not None else None,
                   'vr': round(ind['vr'][i], 2) if ind['vr'][i] else None,
                   'ema10': round(e10, 2), 'ema20': round(e20, 2)}
        if stage == '②' and ctx.get('entry_path'):
            metrics['entry_path'] = ctx['entry_path']   # 溯源字段（补丁一）
        if ctx.get('init_flag'):
            metrics['init_flag'] = True
            ctx.pop('init_flag', None)
        if detail:
            metrics['detail'] = detail

        # 过渡态标签（非对称阈值的自然死区，独立标签不入迁移）
        stage_out = stage
        if in_transition_zone(ind, i, stage):
            stage_out = stage + 'T'
            metrics['transition_zone'] = True

        daily.append((code, d, stage_out, prev_stage if prev_stage != stage else None,
                      kl[ctx['stage_start_idx']]['date'], i - ctx['stage_start_idx'],
                      round(structure_support, 2) if structure_support else None,
                      round(invalid, 2) if invalid else None,
                      ACTIONS.get(stage_out, '观察'), c, json.dumps(metrics, ensure_ascii=False)))
        if stage != prev_stage:
            trans.append((code, d, prev_stage, stage, json.dumps(detail, ensure_ascii=False)))

    return daily, trans


def mark_invalidated(conn, code=None):
    """事后标记 invalidated（PRD §9.5）：迁移后 N 日内出现反向/失效迁移 → invalidated=1"""
    q = "SELECT stock_code, transition_date, from_stage, to_stage FROM cpa_stage_transitions"
    args = ()
    if code:
        q += " WHERE stock_code=?"
        args = (code,)
    rows = conn.execute(q, args).fetchall()
    n_inv = 0
    for r in rows:
        sc, td, fs, ts = r[0], r[1], r[2], r[3]
        key = '%s→%s' % (fs, ts)
        N = CFG['inv_n'].get(key) or CFG['inv_n'].get('→%s' % ts)
        if not N:
            continue
        limit = (datetime.strptime(td, '%Y-%m-%d') + timedelta(days=int(N * 1.5))).strftime('%Y-%m-%d')
        # 反向/失效：N 日内又发生一次迁移（离开该阶段）
        nxt = conn.execute(
            """SELECT MIN(transition_date) FROM cpa_stage_transitions
               WHERE stock_code=? AND transition_date>? AND transition_date<=?""",
            (sc, td, limit)).fetchone()
        if nxt and nxt[0]:
            conn.execute(
                """UPDATE cpa_stage_transitions SET invalidated=1, invalidated_date=?
                   WHERE stock_code=? AND transition_date=?""",
                (nxt[0], sc, td))
            n_inv += 1
    conn.commit()
    return n_inv


def collapse_states(daily_rows, min_days=5):
    """数据层折叠（PRD §9 三层治理第二层）：把短命状态段并入前后主导状态

    用途：回测/信号门禁消费前调用，消除横跳区间噪声；
          折叠区保留 original 标记（不丢信息）。
    返回 [(date, stage, orig_stage), ...]
    """
    if not daily_rows:
        return []
    # 1) 提取连续状态段
    segs = []
    cur = daily_rows[0][2]
    start = 0
    for k in range(1, len(daily_rows)):
        if daily_rows[k][2] != cur:
            segs.append([cur, start, k - 1])
            cur, start = daily_rows[k][2], k
    segs.append([cur, start, len(daily_rows) - 1])
    # 2) 短命段（<min_days）若与前后段同主状态 → 合并
    changed = True
    while changed:
        changed = False
        out = []
        for s in segs:
            dur = s[2] - s[1] + 1
            if dur < min_days and out and len(segs) > 1:
                # 与前段同主状态（去 T 后缀比较）
                if s[0].rstrip('T') == out[-1][0].rstrip('T'):
                    out[-1][2] = s[2]
                    changed = True
                    continue
            out.append(s)
        if changed:
            segs = out
            # 再次合并相邻同状态段
            merged = []
            for s in segs:
                if merged and merged[-1][0] == s[0]:
                    merged[-1][2] = s[2]
                else:
                    merged.append(s)
            segs = merged
    # 3) 展开
    out_rows = []
    for (st, a, b) in segs:
        for k in range(a, b + 1):
            out_rows.append((daily_rows[k][1], st, daily_rows[k][2]))
    return out_rows


# ── [6] 回算主流程 ──
def _stock_codes(conn, start, end=None):
    """回算范围内的股票池（有足够历史的）"""
    q = """SELECT stock_code, COUNT(*) n FROM daily_kline
           WHERE date>=? GROUP BY stock_code HAVING n>=320"""
    args = [start]
    if end:
        q = """SELECT stock_code, COUNT(*) n FROM daily_kline
               WHERE date>=? AND date<=? GROUP BY stock_code HAVING n>=320"""
        args = [start, end]
    return [r[0] for r in conn.execute(q, args)]


def _backfill_worker(codes):
    """多进程 worker：独立连接，处理一批股票（模块级函数才能 pickle）
    返回 (daily_rows, trans_rows, skipped)
    """
    import sqlite3 as _sq
    conn = _sq.connect(DB_PATH, timeout=30)
    conn.row_factory = _sq.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    daily_all, trans_all = [], []
    skipped = []
    for code in codes:
        try:
            kl = load_klines(conn, code, '2014-01-01')
            if len(kl) < 320:
                skipped.append(code)
                continue
            ind = compute_indicators(kl)
            tops = load_bi_tops(conn, code)
            d, t = run_state_machine(conn, code, kl, ind, tops)
            daily_all += d
            trans_all += t
        except Exception as e:
            skipped.append('%s(%s)' % (code, str(e)[:40]))
            continue
    conn.close()
    return daily_all, trans_all, skipped


def backfill(start='2016-01-01', end=None, workers=8, purge=True):
    """全量回算 2016-2026 全市场（多进程）
    purge=True：先清空两表（全量重建）；False：只追加（增量）
    """
    import time as _t
    from concurrent.futures import ProcessPoolExecutor, as_completed
    t0 = _t.time()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_tables(conn)
    codes = _stock_codes(conn, start, end)
    print('股票池: %d 只 | 区间 %s ~ %s | %d 进程' % (len(codes), start, end or '最新', workers), flush=True)
    if purge:
        conn.execute("DELETE FROM cpa_stage_daily")
        conn.execute("DELETE FROM cpa_stage_transitions")
        conn.commit()
    # 分块
    n = max(1, len(codes) // (workers * 4))
    chunks = [codes[i:i + n] for i in range(0, len(codes), n)]
    n_daily = n_trans = 0
    all_skipped = []
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_backfill_worker, c): i for i, c in enumerate(chunks)}
        for fut in as_completed(futures):
            try:
                d, t, skipped = fut.result()
                all_skipped += skipped
            except Exception as e:
                print('  ! chunk 失败: %s' % str(e)[:80]); continue
            if d:
                conn.executemany("INSERT OR REPLACE INTO cpa_stage_daily VALUES (?,?,?,?,?,?,?,?,?,?,?)", d)
            if t:
                conn.executemany("""INSERT OR REPLACE INTO cpa_stage_transitions
                    (stock_code, transition_date, from_stage, to_stage, trigger_detail_json, invalidated, invalidated_date)
                    VALUES (?,?,?,?,?,0,NULL)""", t)
            conn.commit()
            n_daily += len(d or [])
            n_trans += len(t or [])
            done += 1
            print('  chunk %d/%d 完成 (日线 %d, 迁移 %d) %.0fs' % (
                done, len(chunks), n_daily, n_trans, _t.time() - t0), flush=True)
    # invalidated 事后标记
    print('标记 invalidated...', flush=True)
    n_inv = mark_invalidated(conn)
    conn.close()
    print('回算完成: 日线 %d 行 | 迁移 %d 条 | 已失效标记 %d | 跳过 %d 只 | 耗时 %.0fs' % (
        n_daily, n_trans, n_inv, len(all_skipped), _t.time() - t0))
    if all_skipped:
        print('  跳过明细(前10): %s' % ', '.join(str(s) for s in all_skipped[:10]))


def incremental():
    """每日增量：全市场重跑（单股全历史仅 ~0.3s，全市场 ~3min——比上下文恢复更简单可靠）"""
    backfill(start='2016-01-01', workers=8, purge=True)


if __name__ == '__main__':
    import argparse as _ap
    p = _ap.ArgumentParser(description='CPA 阶段判定引擎回算')
    p.add_argument('--start', default='2016-01-01')
    p.add_argument('--end', default=None)
    p.add_argument('--workers', type=int, default=8)
    p.add_argument('--incremental', action='store_true', help='每日增量（等价全量重跑）')
    a = p.parse_args()
    if a.incremental:
        incremental()
    else:
        backfill(a.start, a.end, a.workers)
