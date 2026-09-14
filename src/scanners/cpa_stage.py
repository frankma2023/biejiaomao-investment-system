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
    # 前置闸门（2026-09-14 v1.5 重建，PRD §13.2 #4）：只剩「距高点 ≤60 日」一条。
    'r_high_recency': 60,      # 前置：距 250 日高点 ≤60 交易日（原 120；实测单调，60 日 +2.24pp）
    'r_strong_gain': None,     # 已废弃（v1.5）：单独作闸门 −0.02pp，现只在 detail 里记录
    'r_h_rps250': None,        # 已废弃（v1.5）：单独作闸门 −0.04pp，现只在 detail 里记录
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
    # 2026-09-14 校准（PRD §12.2 #13/#14），全量 5,310 个 ④ 事件的 H20 超额：
    #   深度严格单调：≤3% +5.43%/52% ｜ 3~5% −0.07% ｜ 5~8% −0.11% ｜ 8~12% −0.60% ｜ >12% −1.77%
    #   前置涨幅严格递增坏：<20% +2.65%/45% ｜ 20~30% +1.86% ｜ 30~50% −0.20% ｜ 50~100% −1.31% ｜ ≥100% −4.49%/33%
    # → 深度改绝对上限（原相对口径「≤前段涨幅50%」随 #14 一并取消）
    # → 前置涨幅由下界改上界（原 ≥20% 恰好排除了最好的一组）
    'b_win_min': 10, 'b_win_max': 40,      # 箱体时长
    'b_depth_max': 0.05,                   # 箱体深度 ≤5%（绝对，2026-09-14）
    'b_prior_gain_max': 0.30,              # 前置涨幅 ≤30%（上界，2026-09-14 反向）
    'b_vcp_score': False,                  # VCP 收缩度不参与评分（实测无区分力，PRD §12.2 #11）
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
    't_out_band': 0.0,     # 出⑥b 门槛：close > EMA20 + 0×ATR = **纯 EMA20**（2026-09-13 回落）
    # ⚠ 原为 0.5×ATR（2026-09-10 拖动治理时自设），因为没有验证基础，且 ATR 口径本身还在待定（#28），
    #   用户决定先退回传统形式“站上 EMA20”。参数保留在这里：将来若要加回 ATR 带，只改这一个数。
    #   副作用：过渡态模糊带的上界就是这个参数，所以改成 0 后过渡带变为 [-0.3, 0]×ATR。
    # 出⑥b（趋势复活）确认：#30 校准，由 3中2 收紧为 **4中3**（2026-09-13 用户拍定）。
    # 理由：下跌趋势逆转不能因两天反弹就确认（“三阳改三观”）；宁愿谨慎。
    # ⚠ 这是自设参数，实测发现不准还要调。
    't_out_win': 4,        # 出⑥b 观察窗口
    't_out_need': 3,       # 出⑥b 需窗口内 N 日达标（4中3）
    # 过渡态：[-0.3, +0.5]×ATR 区间 = 判据本来就没答案的地带（独立标签）

    # ── invalidated N 值（待校准）──
    'inv_n': {'②→③': 15, '②→④': 40, '③→④': 40, '④→⑤': 30, '⑤→⑥': 30, '→⑥': 12},
}

# 动作方向固定枚举
ACTIONS = {
    '①': '观察', '①a': '观察', '①b': '观察',
    '②': '入场', '②T': '观察',           # ②T = 过渡态（模糊区，不参与）
    '③': '入场', '④': '观察', '持有': '持有',   # ④: 2026-09-14 由「加仓」降级（整体 −0.58%/40% 负期望，PRD §12.2 #11）
    '⑤': '保护利润', '⑥w': '减仓',        # ⑥w = 预警态（衰竭未破位，Spec B6）
    '⑥a': '清仓', '⑥b': '观察', '⑥bT': '观察', '⑥c': '观察', '⑥cT': '观察',
}


def _action_of(stage_out, metrics):
    """动作 = 阶段标签的函数；例外项由回测校准（PRD §12.2）

    · ③ 深档 → 观察（#7）：深档 H20 −0.72%/41%，负期望，不应挂「入场」
    · ④ 的动作已在 ACTIONS 里直接改为「观察」（#11）
    """
    act = ACTIONS.get(stage_out, '观察')
    if stage_out == '③':
        d = (metrics or {}).get('detail') or {}
        if d.get('phase') == 'deep':
            return '观察'
    return act


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
        """SELECT date, raw_open AS open, raw_high AS high, raw_low AS low,
                  raw_close AS close, close AS adj_close, volume, amount
           FROM daily_kline_adj WHERE stock_code=? AND date>=? ORDER BY date""",
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


def _parse_tops(bi_json):
    """从 bi_json 提取笔顶序列（按日期升序）。
    约定（同 MW 引擎）：笔顶 = direction='向下' 的笔（从顶开始向下）→ sdt 为顶部日期、high 为顶部价。
    """
    if not bi_json:
        return []
    try:
        bi = json.loads(bi_json)
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


def load_bi_tops(conn, code):
    """加载**最新快照**的笔顶序列。

    阶段② 收缩收敛 / 阶段⑥ 高点不抬高用。
    ⚠ 只适合「算今天」：拿它跑历史日期是未来函数（见 load_bi_tops_by_date）。
    """
    row = conn.execute(
        """SELECT bi_json FROM chanlun_bi_json WHERE stock_code=?
           ORDER BY scan_date DESC LIMIT 1""", (code,)).fetchone()
    if not row or not row[0]:
        return []
    return _parse_tops(row[0])


def load_bi_tops_by_date(conn, code, dates):
    """返回 {date: 笔顶列表}——每个日期取它**当日快照**里可见的笔顶（防未来函数）。

    为什么需要它：`max_bi_num=50` 封顶后，一份最新快照只保留最近约 25 个笔顶
    （实测覆盖约 5 年）。对 2018 年的某日而言，最新快照里的笔顶**全部在该日之后**。
    回填历史时用最新快照 = 拿未来数据算过去。

    代价：每个日期一次点查 + 一次 JSON 解析（本机约 0.5ms/日）。
    """
    out = {}
    for d in dates:
        if d in out:
            continue
        row = conn.execute(
            "SELECT bi_json FROM chanlun_bi_json WHERE stock_code=? AND scan_date=?",
            (code, d)).fetchone()
        out[d] = _parse_tops(row[0] if row else None)
    return out


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
    atr60 = atr_series(highs, lows, CFG['atr_win_slow'])   # ⑤ 专用慢口径（PRD §12.2 #16）
    return {
        'closes': closes, 'highs': highs, 'lows': lows, 'vols': vols,
        'ema10': ema10, 'ema20': ema20, 'atr20': atr20,
        'atr60': atr60,
        'vr': vr_series(vols, 20),
        'nd10': [(c - e) / a if (e and a and a > 0) else None
                 for c, e, a in zip(closes, ema10, atr20)],
        # ⑤ 用慢口径（2026-09-14）：ATR20 会被延伸行情自己的大阳线抬高 → 分母虚大 → 漏报
        'nd10_slow': [(c - e) / a if (e and a and a > 0) else None
                      for c, e, a in zip(closes, ema10, atr60)],
        'nd20': [(c - e) / a if (e and a and a > 0) else None
                 for c, e, a in zip(closes, ema20, atr20)],
        'd10': [(c / e - 1) if (e and e > 0) else None
                for c, e in zip(closes, ema10)],
    }


def find_pause_zone(ind, i, win_min, win_max, amp_tol, vol_dry, tops=None, kl=None):
    """找以 i 为突破日的【收缩/停顿区】（阶段②/④ 共用）
    返回 dict(...) 或 None

    判据（PRD §4 四种证据满足任一）：
      a. 区间振幅 ≤ amp_tol（长窗口）
      b. 振幅收缩比（短窗口）
      c. 量能干涸
      d. 【笔顶收敛】（长窗口，Spec review B4 补实现）
    约束：短窗口（<w_win_long）必须含量能证据（PRD §4 判据可用性自适应）
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
        # 笔顶收敛（PRD §4 证据之一，Spec B4）：长窗口且笔可用
        bi_conv = None
        if tops and kl and w >= CFG['w_win_long']:
            recent = [t for t in tops if kl[s]['date'] <= t['date'] <= kl[i - 1]['date']][-3:]
            if len(recent) >= 2:
                bi_conv = all(recent[j + 1]['price'] <= recent[j]['price'] * 1.01
                              for j in range(len(recent) - 1))
        # 短窗口必须含量能证据（PRD §4）
        if w < CFG['w_win_long'] and not dry_ok:
            continue
        if dry_ok or amp_ok or shrink_ok or bi_conv:
            cand = {'high': zh, 'low': zl, 'start_idx': s, 'end_idx': i - 1,
                    'win_len': w, 'vol_dry_ok': dry_ok, 'amp_ok': amp_ok,
                    'shrink_ok': shrink_ok, 'bi_conv': bi_conv, 'amp': amp}
            # 优先条件更充分者，其次更长窗口
            score = (dry_ok + amp_ok + shrink_ok + bool(bi_conv), w)
            if best is None or score > best[0]:
                best = (score, cand)
    return best[1] if best else None


def judge_reversal(ind, kl, i, tops, rps_map=None):
    """阶段① 判据。返回 {'a':bool, 'b':bool, 'detail':{...}}
    rps_map: {date: rps_250}（状态机预加载；前置闸门 c 条件用）
    """
    c = ind['closes'][i]
    if c is None or ind['ema20'][i] is None or not ind['atr20'][i]:
        return {'a': False, 'b': False, 'detail': {}}
    dd, hi_idx = drawdown_from_high(kl, i, CFG['pctile_win'])
    if dd is None or hi_idx is None:
        return {'a': False, 'b': False, 'detail': {}}
    # 前置闸门（2026-09-14 v1.5 重建，PRD §13.2 #4）：只剩「距高点 ≤0 日」一条。
    # 实测（600 只 / ①A 候选日 3,529 个，scripts/bt_cpa_04_gate_sweep.py）：
    #   仅 涨幅≥40%      −0.02pp（阈值 20~100% 全在 ±0.25pp 内）
    #   仅 RPS250≥70     −0.04pp（越严越差）
    #   仅 距高点≤120日  +1.18pp；≤60 日 +2.24pp（单调）
    # 原「gain 为必要条件 + (rec or rps)」两段式：gain 与 rps 均无价值，
    # 且 rps 支路在 a_ok 里被 gate_recent 重新锁死，本来就是死代码。
    gate_recent = (i - hi_idx) <= CFG['r_high_recency']
    if not gate_recent:
        return {'a': False, 'b': False,
                'detail': {'gate': 'recency_fail', 'days_since_high': i - hi_idx}}
    # 以下两项不再作门槛，仅记录（供观察与事后回测）
    gate_gain = max_gain_in(kl, max(0, hi_idx - 250), hi_idx)
    rps_high = (rps_map or {}).get(kl[hi_idx]['date'])
    # ①a：深度回撤 + EMA 下方极值（ATR 归一为主，固定百分比为辅）
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
    a_ok = bool(deep and ext and panic)   # gate_recent 已在函数开头作为前置闸门返回（v1.5）
    # ①b 衰竭迹象（任一）：长下影 / 反转日 / 两日确认（Spec W2 补全）
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
            # 两日确认：前一日长下影/反转日 + 今日收盘站上前日高点（Spec W2）
            if not b_ok and i > 0:
                pk = kl[i - 1]
                po, ph2, pl2, pc = pk.get('open_adj'), pk.get('high_adj'), pk.get('low_adj'), pk.get('adj_close')
                atr_prev = ind['atr20'][i - 1]
                if None not in (po, ph2, pl2, pc) and atr_prev:
                    body2 = abs(pc - po)
                    lower2 = min(po, pc) - pl2
                    prev_trace = (body2 > 0 and lower2 >= body2 * CFG['r_lower_shadow_ratio']
                                  and lower2 >= atr_prev * CFG['r_lower_shadow_atr'])
                    if prev_trace and cl > ph2:
                        b_ok, b_detail = True, {'type': 'two_day_confirm'}
    return {'a': a_ok, 'b': b_ok,
            'detail': {'drawdown': round(dd, 3), 'nd20': round(nd20, 2) if nd20 else None,
                       'panic': panic, 'gate_gain': round(gate_gain, 3) if gate_gain else None,
                       'gate_rps250': rps_high, 'days_since_high': i - hi_idx,
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
                            CFG['w_amp_tol'], CFG['w_vol_dry'], tops=tops, kl=kl)
    if not pause:
        return {'hit': False, 'path': None, 'pause': None, 'low': None, 'detail': {'pause': 'none'}}
    if not (c > pause['high'] * CFG['w_breakout_buf'] and v >= CFG['w_breakout_vr']):
        return {'hit': False, 'path': None, 'pause': pause, 'low': None,
                'detail': {'break': False, 'vr': round(v, 2)}}
    bio_conv = None
    if pause['win_len'] >= CFG['w_win_long'] and tops:
        # 上界不能省：只写下界会让 [-3:] 取到全序列最后 3 个笔顶（即未来数据）。
        # 修正前 tops 是「最新快照」，这个缺陷让阶段② 的收缩判据一直在看未来。
        recent = [t for t in tops
                  if kl[pause['start_idx']]['date'] <= t['date'] <= kl[i]['date']][-3:]
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
    # 缩量：VR ≤ cb_vol_max 或 回踩期均量 < 突破日量×0.7（PRD §5 双口径，Spec W3）
    v_win = [x for x in ind['vols'][max(0, i - 2):i + 1] if x]
    v_avg = (sum(v_win) / len(v_win)) if v_win else None
    v_w = ctx.get('w_vol')
    vol_ok = (v <= CFG['cb_vol_max']) or (v_avg is not None and v_w and v_avg < v_w * 0.7)
    if not vol_ok:
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
    # 深度上限（PRD §5：回踩 low ≥ 停顿区最高点）；FTD 入口无停顿区 → 用 FTD 前低点作替代（PRD 待补记）
    pause_high = (ctx.get('w_pause') or {}).get('high')
    depth_limit = pause_high if pause_high else ctx.get('entry_low')
    if depth_limit and l < depth_limit:
        return {'hit': False, 'phase': phase, 'detail': {'depth_fail': round(l, 2), 'depth_limit': round(depth_limit, 2)}}
    return {'hit': True, 'phase': phase,
            'detail': {'days_since_②': gap, 'vr': round(v, 2), 'hold': hold,
                       'slope_up': slope, 'stop': round(line, 2),
                       'phase': phase}}   # 2026-09-14：档位写入 detail，供动作层判定（PRD §12.2 #7）


def find_box(ind, kl, i):
    """阶段④ 箱体识别（无参数边界 + 四项硬门槛）

    2026-09-14：去掉 prior_gain_start_idx 形参——深度门槛改绝对上限后（PRD §12.2 #13），
    箱体筛选不再依赖前置涨幅；前置涨幅的判定搬到了 judge_base_break（上界，PRD §12.2 #14）。
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
        # 深度门槛（2026-09-14，PRD §12.2 #13）：改绝对上限。
        # 原「≤前段涨幅×50% 或 ≤15%」的相对口径随 #14 取消前置涨幅下界而失去基准。
        # 前置涨幅的判定改在 judge_base_break 里做（上界），此处不再需要 prior_gain_start_idx。
        depth_ok = depth <= CFG['b_depth_max']
        if not (vol_ok and depth_ok):
            continue
        # 触碰次数（质量评分）
        band = CFG['b_touch_band']
        touch_top = sum(1 for x in seg_c if x >= zh * (1 - band))
        touch_bot = sum(1 for x in seg_c if x <= zl * (1 + band))
        cand = {'high': zh, 'low': zl, 'start_idx': s, 'win_len': w, 'depth': depth,
                'touch_top': touch_top, 'touch_bot': touch_bot}
        if best is None or w > best['win_len']:
            best = cand
    return best


def judge_base_break(ind, kl, i, ctx):
    """阶段④ 事件判据：箱体 + 放量突破箱顶"""
    c, v = ind['closes'][i], ind['vr'][i]
    if c is None or v is None:
        return {'hit': False, 'box': None}
    start_idx = ctx.get('entry_idx')
    box = find_box(ind, kl, i)
    if not box:
        return {'hit': False, 'box': None}
    # 前置：自最后有效入场点涨幅（2026-09-14 由「≥20% 下界」反向为「≤30% 上界」，PRD §12.2 #14）
    gain = None
    if start_idx is not None:
        base_c = kl[start_idx].get('adj_close')   # 复权口径统一（PRD §2）
        if base_c and base_c > 0:
            gain = box['high'] / base_c - 1
            # 原「≥20%」恰好排除了最好的一组（<20%：+2.65%/45%），纳入了最差的一组（≥100%：−4.49%/33%）
            if gain > CFG['b_prior_gain_max']:
                return {'hit': False, 'box': box, 'detail': {'prior_gain': round(gain, 3),
                                                            'boundary': 'high_prior_gain'}}
    if not (c > box['high'] * CFG['b_breakout_buf'] and v >= CFG['b_breakout_vr']):
        return {'hit': False, 'box': box, 'detail': {'vr': round(v, 2)}}
    return {'hit': True, 'box': box,
            'detail': {'box_high': round(box['high'], 2), 'box_low': round(box['low'], 2),
                       'win': box['win_len'], 'depth': round(box['depth'], 3),
                       'touch_top': box['touch_top'], 'vr': round(v, 2),
                       # 2026-09-14：命中路径也记录前置涨幅（原只在拒绝路径记，
                       # 导致事后无法校验 #14 的上界是否生效，也无法做分组回测）
                       'prior_gain': round(gain, 3) if gain is not None else None}}


def judge_exhaustion(ind, kl, i, ctx):
    """阶段⑤ 判据：前置峰值涨幅 + 延伸极值（双口径取或 + 分位）"""
    c = ind['closes'][i]
    e10, a20 = ind['ema10'][i], ind['atr20'][i]
    nd10_slow = ind['nd10_slow'][i]
    if None in (c, e10, a20, nd10_slow) or e10 <= 0 or a20 <= 0:
        return {'hit': False}
    # 前置：自 ② 以来峰值涨幅 ≥30%
    wi = ctx.get('w_date_idx')
    if wi is None:
        return {'hit': False}
    base = kl[wi].get('adj_close')   # 复权口径统一（PRD §2）
    if not base or base <= 0:
        return {'hit': False}
    # 峰值涨幅 = 区间最大涨幅（PRD §2：低点到后续高点的最大涨幅，非起点收盘到峰值）
    lo = None
    peak_gain = 0.0
    for j in range(wi, i + 1):
        px = ind['closes'][j]
        if px is None:
            continue
        if lo is None or px < lo:
            lo = px
        if lo and lo > 0:
            peak_gain = max(peak_gain, px / lo - 1)
    if peak_gain < CFG['e_peak_gain_min']:
        return {'hit': False, 'detail': {'peak_gain': round(peak_gain, 3)}}
    # 2026-09-14：⑤ 的归一化分母改用 ATR60（PRD §12.2 #16）。
    # ATR20 会被延伸行情自己的大阳线抬高 → 分母虚大 → 归一化偏离被压小 → 漏报。
    # 实测漏判率 ATR20 55.3% → ATR60 28.7%；与百分比口径重叠率 44.7% → 71.3%。
    # ⚠ 只改 ⑤：nd10（ATR20 口径）在 ① 的下影线、③ 的容差上继续使用，不动。
    nd10 = nd10_slow
    d10 = c / e10 - 1
    hit_ext = (nd10 >= CFG['e_nd10_min']) or (d10 >= CFG['e_d10_pct_min'])
    pctl = pctile_of(ind['nd10_slow'], i, CFG['pctile_win'], nd10)
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
    # ③ 趋势破坏：close < EMA20（2026-09-14 起不再要求同时击穿结构支撑位，PRD §12.2 #20）
    # 实测要求 below_sup 反而使后续更强（③全 +0.76% vs 仅破MA −0.02%）——
    # 原 PRD 把结构破坏当作“一票否决”，数据不支持。below_sup 保留为记录项，不作门槛。
    below_ma = c < e20
    below_sup = (structure_support is not None and c < structure_support)
    confirm = bool(below_ma and warn)
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
    # 2026-09-14：⑤ 的判据已换 ATR60 口径（PRD §12.2 #16），初值推断必须同口径，
    # 否则同一只股票在初值路径与主路径上会得到不同的 ⑤ 判定。
    nd10_slow = ind['nd10_slow'][i]
    above = c > e20
    if above:
        pctl = (pctile_of(ind['nd10_slow'], i, CFG['pctile_win'], nd10_slow)
                if nd10_slow is not None else None)
        if pctl is not None and pctl >= CFG['e_pctile_min'] and nd10_slow and nd10_slow >= CFG['e_nd10_min']:
            return '⑤', {}
        if find_box(ind, kl, i):
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


def above_ma_confirmed(ind, i, n=4, band_atr=0.0, need=3):
    """n 日窗口内 need 日收盘 > EMA20 + band*ATR20

    非对称：出⑥b 用 **4中3**（#30 校准，原为 3中2）——进攻慢确认，
    不接受单日/两日脉冲（“三阳改三观”）。
    band_atr 默认 0.0 = 纯 EMA20（2026-09-13 回落，原 0.5×ATR）；默认值与 CFG 一致。
    """
    cnt = 0
    for j in range(max(0, i - n + 1), i + 1):
        c, e, a = ind['closes'][j], ind['ema20'][j], ind['atr20'][j]
        if c is not None and e is not None and a and c > e + band_atr * a:
            cnt += 1
    return cnt >= need


def in_transition_zone(ind, i, stage):
    """过渡态判定：处于 ②/⑥b/⑥c 且价格在模糊带 [-t_in_band, +t_out_band]×ATR 内
    （即“进”与“出”两道门槛之间的地带 = 判据本来就没答案的地带）
    → 回测已证：低收益垃圾时间区。当前 t_out_band=0，模糊带是 [-0.3, 0]×ATR。
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
    # RPS250 预加载（前置闸门 c 条件用）
    rps_map = {}
    if conn is not None:
        try:
            for _r in conn.execute("SELECT date, rps_250 FROM stock_rs_daily WHERE stock_code=?", (code,)):
                if _r[1] is not None:
                    rps_map[_r[0]] = _r[1]
        except Exception:
            pass
    # MW B1/B2 对照预加载（PRD §9.10 交叉验证，独立计算不作条件）
    b1_dates, b2_dates = set(), set()
    if conn is not None:
        try:
            for _b in conn.execute("SELECT b1_date, b2_date FROM mw_signal_daily WHERE stock_code=?", (code,)):
                if _b[0]:
                    b1_dates.add(_b[0])
                if _b[1]:
                    b2_dates.add(_b[1])
        except Exception:
            pass

    stage, ctx = init_stage(ind, kl, warmup - 1)
    ctx = dict(ctx)
    ctx.update({'stage_start_idx': warmup - 1, 'entry_idx': None, 'w_date_idx': None,
                'w_pause': None, 'cb_low': None, 'box': None, 'exh_start_idx': None})
    structure_support = None
    # tops 可能是 {date: 笔顶列表}（回填路径，防未来函数）或 list（最新快照语义）。
    # 在这里一元化：循环每轮把 tops 重绑成「当日可见的笔顶」，下游 7 个判据调用点不动。
    _tops_src = tops

    for i in range(warmup, n):
        d = kl[i]['date']
        tops = _tops_src.get(d, []) if isinstance(_tops_src, dict) else _tops_src
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
            r1 = judge_reversal(ind, kl, i, tops, rps_map=rps_map)
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
                    stage = '③'
                    _lows = [x for x in ind['lows'][max(0, i - 2):i + 1] if x]
                    ctx['cb_low'] = min(_lows) if _lows else l   # 回踩低点用 low（Spec W5）
                    ctx['w_vol'] = ind['vols'][ctx['w_date_idx']] if ctx.get('w_date_idx') is not None else None
                    ctx['entry_idx'] = i; ctx['stage_start_idx'] = i; detail = r3['detail']

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
                    nb = find_box(ind, kl, i)
                    # 2026-09-14：与 judge_base_break 一致施加前置涨幅上界（PRD §12.2 #14）。
                    # 此路径原本没有前置涨幅门槛（旧版只在 find_box 的深度相对口径里间接用到），
                    # 属遗留不一致，借本次统一。
                    _ok_gain = True
                    _ei = ctx.get('entry_idx')
                    if nb and _ei is not None:
                        _bc = kl[_ei].get('adj_close')
                        if _bc and _bc > 0:
                            _ok_gain = (nb['high'] / _bc - 1) <= CFG['b_prior_gain_max']
                    if (nb and _ok_gain and c > nb['high'] * CFG['b_breakout_buf']
                            and (ind['vr'][i] or 0) >= CFG['b_breakout_vr']):
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
                stage = '②'
                ctx.update({'stage_start_idx': i, 'w_date_idx': i, 'w_pause': None, 'cb_low': None,
                            'entry_low': None, 'entry_path': 'revive'})   # 清理旧入口上下文（Spec W13）
                detail = {'reason': '趋势复活（站上EMA20）→回②区间'}
            else:
                # ⑥b/⑥c 升级确认（Spec W12）：三条件齐 → ⑥a（清仓）
                if stage in ('⑥b', '⑥c'):
                    r6 = judge_wedge_drop(ind, kl, i, ctx, structure_support, tops)
                    if r6['confirm']:
                        stage = '⑥a'; detail = {'reason': '⑥b/c 升级确认（三条件齐）', **r6['detail']}
                r1 = judge_reversal(ind, kl, i, tops, rps_map=rps_map)
                if r1['a']:   # ①a 全条件满足
                    stage = '①b' if r1['b'] else '①a'
                    ctx['stage_start_idx'] = i; ctx['w_pause'] = None; ctx['w_date_idx'] = None
                    detail = {'reason': '①a全条件满足（跌够且衰竭）',
                              **(r1.get('detail') or {})}   # 2026-09-14：原写法丢掉了判据的 detail，
                                                            # 导致闸门/回撤/恐慌量字段不入库、无法事后核验
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

        # nd10 = ATR20 口径（①③ 用）；nd10_slow = ATR60 口径（⑤ 用）。
        # 2026-09-14：⑤ 改 ATR60 后两者必须都记录，否则页面显示的口径与判据不一致。
        metrics = {'nd10': round(ind['nd10'][i], 2) if ind['nd10'][i] is not None else None,
                   'nd10_slow': round(ind['nd10_slow'][i], 2) if ind['nd10_slow'][i] is not None else None,
                   'nd20': round(ind['nd20'][i], 2) if ind['nd20'][i] is not None else None,
                   'vr': round(ind['vr'][i], 2) if ind['vr'][i] else None,
                   'ema10': round(e10, 2), 'ema20': round(e20, 2)}
        if stage == '②' and ctx.get('entry_path'):
            metrics['entry_path'] = ctx['entry_path']   # 溯源字段（补丁一）
        # 交叉验证（独立计算，不作条件；Spec W10）
        if stage in ('②', '③'):
            _win = [kl[j]['date'] for j in range(max(0, i - 2), min(n, i + 3))]
            metrics['b1_overlap'] = any(d in b1_dates for d in _win)
            metrics['b2_overlap'] = any(d in b2_dates for d in _win)
        if detail.get('boundary') == 'low_prior_gain':
            metrics['boundary_sample'] = 'low_prior_gain'   # ④ 边界样本独立标记（Spec W6）
        if ctx.get('init_flag'):
            metrics['init_flag'] = True
            ctx.pop('init_flag', None)
        # ⑥ 方向分档（2026-09-14 v1.5，PRD §13.2 #31）：近 5 日涨跌。
        # 实测方向胜率差 6pp / 中位差 1.55pp，而回撤分档不单调（<-50% 组反而最好）。
        # 故 ⑥ 的展示层按方向分（破位后·反弹 / 破位后·续跌），回撤降为次要标注。
        if stage.startswith('⑥'):
            _j5 = i - 5
            if _j5 >= 0 and ind['closes'][_j5] and c:
                _d5 = c / ind['closes'][_j5] - 1
                metrics['six_dir'] = 'rebound' if _d5 > 0 else 'decline'
                metrics['six_dir_pct'] = round(_d5, 4)
            _dd6, _ = drawdown_from_high(kl, i, CFG['pctile_win'])
            if _dd6 is not None:
                metrics['six_dd'] = round(_dd6, 4)
        if detail:
            metrics['detail'] = detail

        # 预警态（PRD §8.4）：衰竭迹象（高点不抬高 + EMA10 斜率转负）出现但未破位 → 减仓
        warn_now = False
        if stage in ('②', '③', '④', '⑤'):
            _tp = [t for t in tops if t['date'] <= kl[i]['date']][-3:]
            _hl = len(_tp) >= 2 and all(_tp[j + 1]['price'] <= _tp[j]['price'] * 1.01 for j in range(len(_tp) - 1))
            _sl = slope_up(ind['ema10'], i, CFG['slope_lag'])
            warn_now = bool(_hl and _sl is False)
        # 过渡态 / 预警态标签（不改内部状态机，仅输出层）
        stage_out = stage
        if warn_now:
            stage_out = '⑥w'
            metrics['warn_from'] = stage
        elif in_transition_zone(ind, i, stage):
            stage_out = stage + 'T'
            metrics['transition_zone'] = True

        daily.append((code, d, stage_out, prev_stage if prev_stage != stage else None,
                      kl[ctx['stage_start_idx']]['date'], i - ctx['stage_start_idx'],
                      round(structure_support, 2) if structure_support else None,
                      round(invalid, 2) if invalid else None,
                      _action_of(stage_out, metrics), c, json.dumps(metrics, ensure_ascii=False)))
        if stage != prev_stage:
            trans.append((code, d, prev_stage, stage, json.dumps(detail, ensure_ascii=False)))

    return daily, trans


def mark_invalidated(conn, code=None):
    """事后标记 invalidated（PRD §9.5）：迁移后 N 日内出现【回退型】迁移 → invalidated=1

    修复（Spec review B2）：
      1. 阶段名归一化（⑥a/⑥b/⑥c → ⑥）后再查 N 值——原实现键不匹配导致⑥永不标记
      2. 去掉 int(N*1.5) 的无依据窗口放大（直接用 N 个交易日 ≈ N*1.45 日历日）
      3. 只把"回退型"迁移（stage_rank 变小）算失效——正向推进（②→③→④）不是失效
    """
    def _rank(s):
        s = (s or '').replace('T', '')
        if s.startswith('①'):
            return 1
        if s.startswith('⑥'):
            return 6
        return {'②': 2, '③': 3, '④': 4, '⑤': 5}.get(s, 0)

    q = "SELECT stock_code, transition_date, from_stage, to_stage FROM cpa_stage_transitions"
    args = ()
    if code:
        q += " WHERE stock_code=?"
        args = (code,)
    rows = conn.execute(q, args).fetchall()
    n_inv = 0
    for r in rows:
        sc, td, fs, ts = r[0], r[1], r[2], r[3]
        norm_to = ts.replace('T', '')
        if norm_to.startswith('⑥'):
            norm_to = '⑥'
        key = '%s→%s' % ((fs or '').replace('T', ''), norm_to)
        N = CFG['inv_n'].get(key) or CFG['inv_n'].get('→%s' % norm_to)
        if not N:
            continue
        limit = (datetime.strptime(td, '%Y-%m-%d') + timedelta(days=int(N * 1.45))).strftime('%Y-%m-%d')
        rank_to = _rank(ts)
        # 找出 N 日窗口内的后续迁移，判断是否有回退
        nxt_rows = conn.execute(
            """SELECT transition_date, to_stage FROM cpa_stage_transitions
               WHERE stock_code=? AND transition_date>? AND transition_date<=?
               ORDER BY transition_date""", (sc, td, limit)).fetchall()
        inv_date = None
        for nr in nxt_rows:
            if _rank(nr[1]) < rank_to:
                inv_date = nr[0]
                break
        if inv_date:
            conn.execute(
                """UPDATE cpa_stage_transitions SET invalidated=1, invalidated_date=?
                   WHERE stock_code=? AND transition_date=?""", (inv_date, sc, td))
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
            # 按日期取笔顶（防未来函数）：每个日期用它自己那天的快照，
            # 而不是一份最新快照跑 2016-2026 全历史（见 PRD §9.16）
            tops = load_bi_tops_by_date(conn, code, [k['date'] for k in kl])
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
