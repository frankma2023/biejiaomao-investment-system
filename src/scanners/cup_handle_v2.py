"""
杯柄形态放量突破检测引擎 v2.0

对齐 docs/product/杯柄形态突破检测引擎_产品需求书_v2.md

v1（cup_handle.py）在数学上不可能产出信号：其买点取自包含检测日自身的窗口最大值，
而突破判定又要求检测日收盘超过该值。本引擎的原则是**形态拟合与突破触发彻底分离**：
所有结构点只使用检测日**之前**的数据。

设计要点：
  1. 只使用一根缠论笔（向下笔 D1）定位 前高 P0 与 杯底 P1；
     杯口 P2 与柄部全部由量化规则给出，规避笔的确认滞后。
  2. **单次无状态扫描**：给定检测日 T，输出唯一确定，可幂等重跑。无跨日状态。
  3. 同时输出两类记录：
       SIGNAL    —— 当日放量突破（可交易信号）
       CANDIDATE —— 结构已完成、买点已定、尚未突破（供次日挂单，拿到 A 口径入场价）

用法：
    python src/scanners/cup_handle_v2.py --stock 600519 --date 2026-09-18
    python src/scanners/cup_handle_v2.py --stock 600519 --diagnose
"""

import argparse
import os
import sqlite3
import sys
from typing import Dict, List, Optional

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "cup_handle_v2",
    "display_name": "杯柄形态V2",
    "category": "pattern",
    "version": "2.0",
    "description": "缠论笔定位前高/杯底 + 量化杯口 + 放量突破触发；输出候选与信号两类记录",
}

CONFIG_PATH = os.path.join(PROJECT_DIR, "config", "market", "cup_handle_v2.yaml")

# 全部必需参数。缺任意一项即报错——禁止代码内兜底（PRD §4.1 / §8.2）
REQUIRED_PARAMS = (
    'min_prior_advance', 'advance_origin_tolerance', 'min_descent_bars', 'cup_min_age', 'cup_max_age',
    'mouth_lock_pullback', 'rim_gap_window', 'rim_gap_max', 'min_ascent_bars',
    'depth_min', 'depth_max', 'mouth_vs_high_max', 'mouth_span_max',
    'require_breakout_confirm',
    'handle_dd_min', 'handle_dd_max', 'handle_days_max', 'mouth_to_signal_max',
    'handle_position_ratio', 'handle_red_vol_ratio',
    'buy_point_buffer', 'breakout_vol_ratio', 'vol_ma_window',
    'close_position_min', 'require_green', 'require_above_ma50', 'ma_trend_window',
    'cup_invalidate_tolerance',
    'suggested_tp', 'suggested_sl', 'suggested_max_hold',
    'ma_support_ref', 'voodoo_vol_ratio', 'bottom_amp_window', 'bottom_amp_min',
    'min_market_cap', 'speed_rule_days', 'speed_rule_gain',
)


def load_params(path: str = CONFIG_PATH) -> Dict:
    """
    读取并校验引擎参数。

    参数全部来自 YAML，代码内不保留任何默认值：配置缺失或键缺失即抛错，
    避免 v1 那种「参数写死在代码里、调整无效」的情况。

    Args:
        path: 配置文件路径。

    Returns:
        参数字典（已剔除顶层键 cup_handle_v2）。

    Raises:
        FileNotFoundError: 配置文件不存在。
        KeyError: 缺少必需参数。
        ValueError: 参数几何自检未通过。
    """
    import yaml

    if not os.path.exists(path):
        raise FileNotFoundError(f"杯柄V2 配置缺失: {path} —— 引擎拒绝使用硬编码默认值")

    with open(path, encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}
    if 'cup_handle_v2' not in raw:
        raise KeyError(f"{path} 缺少顶层键 cup_handle_v2")
    params = dict(raw['cup_handle_v2'])

    missing = [k for k in REQUIRED_PARAMS if k not in params]
    if missing:
        raise KeyError(f"{path} 缺少必需参数: {', '.join(missing)}")

    _validate_geometry(params)
    return params


def _validate_geometry(p: Dict) -> None:
    """
    参数几何自检（PRD §4.2）。

    v1 的参数组合（handle_position_ratio=0.50 + handle_max_drawdown=0.12 +
    cup_recovery=0.90）在浅杯下无法同时满足，但加载期毫无提示，导致引擎
    静默跑出 0 个信号。此类互斥必须在启动时就报错。

    Args:
        p: 参数字典。

    Raises:
        ValueError: 存在互斥或越界的参数组合。
    """
    errs = []
    if not p['cup_min_age'] < p['cup_max_age']:
        errs.append("cup_min_age 必须小于 cup_max_age")
    if not p['depth_min'] < p['depth_max']:
        errs.append("depth_min 必须小于 depth_max")
    if not p['handle_dd_min'] < p['handle_dd_max']:
        errs.append("handle_dd_min 必须小于 handle_dd_max")
    if p['handle_dd_max'] > p['depth_max']:
        errs.append("handle_dd_max 不应大于 depth_max（柄部不可能比杯身还深）")
    if p['mouth_lock_pullback'] > p['handle_dd_max']:
        errs.append("mouth_lock_pullback 不应大于 handle_dd_max（否则杯口确认晚于柄部成立）")
    if not 0 < p['mouth_lock_pullback'] < 1:
        errs.append("mouth_lock_pullback 必须在 (0, 1) 内")
    if not 0 < p['rim_gap_max'] < 1:
        errs.append("rim_gap_max 必须在 (0, 1) 内")
    if p['mouth_vs_high_max'] > 1:
        errs.append("mouth_vs_high_max 不应大于 1（前高必须高于杯口）")
    if p['mouth_span_max'] < 1:
        errs.append("mouth_span_max 至少为 1")
    if not 0 <= p['advance_origin_tolerance'] < 1:
        errs.append("advance_origin_tolerance 必须在 [0, 1) 内")
    if p['rim_gap_window'] < 1:
        errs.append("rim_gap_window 至少为 1")
    if p['handle_position_ratio'] < 0:
        errs.append("handle_position_ratio 不应为负")
    if p['handle_position_ratio'] >= 1:
        errs.append("handle_position_ratio 应小于 1（柄低不可能高过杯口）")
    if p['min_ascent_bars'] < 1:
        errs.append("min_ascent_bars 至少为 1")
    if p['bottom_amp_min'] < 0:
        errs.append("bottom_amp_min 不应为负")
    if p['suggested_sl'] <= 0 or p['suggested_tp'] <= 0:
        errs.append("suggested_tp / suggested_sl 必须为正")
    if errs:
        raise ValueError("杯柄V2 参数几何自检失败：\n  - " + "\n  - ".join(errs))


# ─── 工具 ─────────────────────────────────────────────

def _d10(s: Optional[str]) -> Optional[str]:
    """'2023-09-13 00:00:00' -> '2023-09-13'"""
    return s[:10] if s else None


def _rolling_mean(arr: List[float], window: int) -> List[Optional[float]]:
    """
    滚动均值，前 window-1 项为 None。

    Args:
        arr: 数值序列。
        window: 窗口长度。

    Returns:
        与 arr 等长的列表，元素为窗口均值或 None。
    """
    out: List[Optional[float]] = [None] * len(arr)
    s = 0.0
    for i, v in enumerate(arr):
        s += v
        if i >= window:
            s -= arr[i - window]
        if i >= window - 1:
            out[i] = s / window
    return out


def _load_bi(stock_code: str, target_date: Optional[str] = None) -> List[Dict]:
    """
    取该股的缠论笔序列（最新可用快照）。

    max_age_days 放宽到 30：笔快照的更新频率低于日K，用默认的 5 天会让绝大多数
    股票回落到「无笔」分支，并刷出大量过期告警。笔只用于定位数月前的前高/杯底，
    30 天的快照陈旧度不影响结论。

    Args:
        stock_code: 股票代码。
        target_date: 快照日期上限；None 表示取最新。

    Returns:
        笔列表，每项含 sdt/edt/direction/high/low/length 等。
    """
    try:
        from scanners.chanlun_structure import get_bi_list
    except ImportError:
        from src.scanners.chanlun_structure import get_bi_list
    try:
        return get_bi_list(stock_code, max_age_days=30, target_date=target_date) or []
    except Exception as e:
        # 笔数据缺失不应中断全市场扫描，但必须可见
        print(f"[cup_handle_v2] {stock_code} 缠论笔加载失败: {e}")
        return []


# ─── 结构拟合 ─────────────────────────────────────────

def _mark_gaps(closes: List[float], window: int, max_gap: float) -> List[bool]:
    """
    标记「跳涨日」：收盘高出前 window 日最高收盘 max_gap 以上。

    跳涨日意味着价格突破了一段既有区间，因此不能充当杯口——杯口是杯子右侧的顶，
    突破它才是买点；让突破日自己当杯口，突破就永远成立（PRD §2.5）。

    Args:
        closes: 收盘价序列。
        window: 回看窗口（交易日）。
        max_gap: 允许高出前高的最大比例。

    Returns:
        与 closes 等长的布尔列表，True 表示该日为跳涨日。
    """
    n = len(closes)
    out = [False] * n
    for i in range(1, n):
        prev = closes[max(0, i - window):i]
        if prev and closes[i] > max(prev) * (1 + max_gap):
            out[i] = True
    return out


def _build_d1_candidates(bi: List[Dict], date_idx: Dict[str, int], params: Dict) -> List[Dict]:
    """
    从笔序列中筛出可作为「前高 → 杯底」的向下笔 D1（PRD §2.3）。

    条件：方向向下、日期可映射、前置上涨达标（V2）、K 线数达标（V3）。

    注意：**不检查「前高高于前一笔高点」**。缠论相邻笔共享转折点，向下笔的高点
    恒等于前一笔向上的高点，该条件恒假。真正约束「杯口不得偏离前高」的是
    _evaluate 中的 V6（P2/P0 ≤ mouth_vs_high_max），该阈值已标定。

    Args:
        bi: 缠论笔列表。
        date_idx: 日期 -> 日K索引。
        params: 参数字典。

    Returns:
        候选列表，每项含 p0/p1/t0_idx/t1_idx/bars。
    """
    out = []
    for i in range(1, len(bi)):
        d1, prev = bi[i], bi[i - 1]
        if d1.get('direction') != '向下':
            continue

        t0, t1 = _d10(d1.get('sdt')), _d10(d1.get('edt'))
        if t0 not in date_idx or t1 not in date_idx:
            continue

        p0, p1 = d1.get('high'), d1.get('low')
        if not (isinstance(p0, (int, float)) and isinstance(p1, (int, float)) and p0 > p1 > 0):
            continue

        # V2: 前置上涨（相对再前一笔的低点）
        prev_low = prev.get('low')
        if not (isinstance(prev_low, (int, float)) and prev_low > 0):
            continue
        if (p0 - prev_low) / prev_low < params['min_prior_advance']:
            continue

        # V13: 杯底不得跌破前置上涨的起点。
        # 杯柄本质是上涨过程中的调整；跌破起点意味着这波上涨被完全回吐，
        # 结构上已不是「上升趋势中的整理」，而是趋势转折。
        # 603903: 起点 11.01(06-22)，杯底 10.28(07-30)，跌破 6.6% → 否决。
        # 留 advance_origin_tolerance 容差：相邻两笔共享转折点，报价噪声会让
        # 杯底「低于起点 0.012 元」这种浮点级差异出现（003030 实测），
        # 严格 > 比较会把它误杀，反而留下 W 形的浅读数。
        if p1 <= prev_low * (1 - params['advance_origin_tolerance']):
            continue

        # V3: 下行 K 线数
        bars = d1.get('length')
        if not (isinstance(bars, (int, float)) and bars >= params['min_descent_bars']):
            continue

        out.append({
            'p0': float(p0), 'p1': float(p1), 'prev_low': float(prev_low),
            't0_idx': date_idx[t0], 't1_idx': date_idx[t1],
            'bars': int(bars),
        })
    return out


def _evaluate(daily: List[Dict], ctx: Dict, d1: Dict, t_idx: int,
              p2: float, t2_idx: int, params: Dict,
              p2_prev: Optional[float] = None,
              t2_prev: Optional[int] = None) -> Optional[Dict]:
    """
    在检测日 t_idx 上，对给定 D1 与杯口执行形态校验与分类。

    Args:
        daily: 日K列表。
        ctx: 预计算上下文（均线数组等）。
        d1: D1 候选。
        t_idx: 检测日（= 记录的输出日期）。
        p2: 杯口价，= max(close[t1 .. t_idx-1])。
        t2_idx: 杯口所在索引。
        params: 参数字典。
        p2_prev: 杯口价在 t_idx-2 时的取值；仅 require_breakout_confirm 时使用。
        t2_prev: 对应的杯口索引。

    Returns:
        记录 dict（含 record_type）或 None。

    突破确认（PRD §3.2 S7）：SIGNAL 始终发在**突破日**（轻仓买入点，能吃到
    杯口价）；次日站稳杯口之上时，detect() 额外补一条 CONFIRM 记录（加仓点）。
    两者并存，不互相压制——见 §7.5。
    """
    closes = ctx['closes']
    p1 = d1['p1']
    t1_idx = d1['t1_idx']
    bar = t_idx

    # V4: 杯底距检测日的交易日区间
    age = t_idx - t1_idx
    if not (params['cup_min_age'] <= age <= params['cup_max_age']):
        return None

    # V6: 前高必须高于杯口（杯口是杯子右侧的顶，高于前高说明已经在突破途中）
    if p2 > d1['p0'] * params['mouth_vs_high_max']:
        return None

    # V14: 前高→杯口 的时长上限。调整拖太久就不是上涨过程中的整理
    if t2_idx - d1['t0_idx'] > params['mouth_span_max']:
        return None

    # 杯底→杯口 的上升段长度
    if t2_idx - t1_idx < params['min_ascent_bars']:
        return None

    # V5: 杯身深度
    depth = (p2 - p1) / p2
    if not (params['depth_min'] <= depth <= params['depth_max']):
        return None

    # 柄部区间 = (t2_idx, bar-1]，不含杯口当日、不含突破日
    if bar - 1 < t2_idx:
        return None
    handle = closes[t2_idx + 1: bar]
    if not handle:
        return None
    p3 = min(handle)

    # V7/B1: 杯口已回落确认
    if p3 > p2 * (1 - params['mouth_lock_pullback']):
        return None

    # V8: 柄部回撤
    hdd = (p2 - p3) / p2
    if not (params['handle_dd_min'] <= hdd <= params['handle_dd_max']):
        return None

    # V9: 柄部时长
    handle_days = bar - 1 - t2_idx
    if handle_days > params['handle_days_max']:
        return None

    # V10: 柄低须在杯身上半部
    if p3 < p1 + (p2 - p1) * params['handle_position_ratio']:
        return None

    # V11: 柄部期间不得出现放量长阴
    vols = ctx['volumes']
    vol_ma = ctx['vol_ma']
    red_ratio = params['handle_red_vol_ratio']
    for k in range(t2_idx + 1, bar):
        ma = vol_ma[k]
        if ma and ma > 0 and closes[k] < daily[k]['open'] and vols[k] > ma * red_ratio:
            return None

    # ── 分类 ──────────────────────────────────────────
    buy_point = p2 + params['buy_point_buffer']
    p3_idx = t2_idx + 1 + handle.index(p3)
    base = _build_record(daily, ctx, d1, t_idx, p2, t2_idx, p3, p3_idx,
                         handle, depth, hdd, handle_days, buy_point, params,
                         bar_idx=bar)

    # S1~S6：突破日放量收上买点
    if daily[bar]['close'] > buy_point:
        if _is_signal(daily, ctx, bar, t2_idx, p2, buy_point, params):
            base['record_type'] = 'SIGNAL'
            base['breakout_close'] = round(daily[bar]['close'], 3)
            ma20v = ctx['vol_ma'][bar]
            base['breakout_vol_ratio'] = (
                round(daily[bar]['volume'] / ma20v, 3) if ma20v else None)
            return base
        return None

    # W1/W2/W3: 候选
    if bar - t2_idx > params['mouth_to_signal_max']:
        return None
    if daily[t_idx]['close'] < p1 * (1 - params['cup_invalidate_tolerance']):
        return None
    base['record_type'] = 'CANDIDATE'
    return base


def _is_signal(daily: List[Dict], ctx: Dict, t_idx: int, t2_idx: int,
               p2: float, buy_point: float, params: Dict) -> bool:
    """
    SIGNAL 判定（PRD §3.2 S2~S6，S1 由调用方判定）。

    Args:
        daily: 日K列表。
        ctx: 预计算上下文。
        t_idx: 检测日索引。
        t2_idx: 杯口索引。
        p2: 杯口价。
        buy_point: 买点价。
        params: 参数字典。

    Returns:
        是否构成信号。
    """
    t = daily[t_idx]

    # S5: 杯口→突破 的间隔（最强判据）
    if t_idx - t2_idx > params['mouth_to_signal_max']:
        return False

    # S2: 放量
    ma_v = ctx['vol_ma'][t_idx]
    if not ma_v or ma_v <= 0 or t['volume'] < ma_v * params['breakout_vol_ratio']:
        return False

    # S3: 阳线
    if params['require_green'] and t['close'] <= t['open']:
        return False

    # S4: 收盘位置
    rng = t['high'] - t['low']
    if rng > 0 and (t['close'] - t['low']) / rng < params['close_position_min']:
        return False

    # S6: 趋势确认
    if params['require_above_ma50']:
        ma = ctx['ma_trend'][t_idx]
        if not ma or t['close'] <= ma:
            return False
    return True


def _build_record(daily: List[Dict], ctx: Dict, d1: Dict, t_idx: int, p2: float,
                  t2_idx: int, p3: float, p3_idx: int, handle: List[float],
                  depth: float, hdd: float, handle_days: int,
                  buy_point: float, params: Dict,
                  bar_idx: Optional[int] = None) -> Dict:
    """
    组装输出记录（PRD §5）。

    Args:
        daily: 日K列表。
        ctx: 预计算上下文。
        d1: D1 候选。
        t_idx: 记录的输出日期索引（突破确认开启时 = 突破日 + 1）。
        p2: 杯口价。
        t2_idx: 杯口索引。
        p3: 柄低价。
        p3_idx: 柄低索引。
        handle: 柄部收盘序列。
        depth: 杯身深度。
        hdd: 柄部回撤。
        handle_days: 柄部交易日数。
        buy_point: 买点价。
        params: 参数字典。
        bar_idx: 突破日索引；None 时等于 t_idx。

    Returns:
        记录 dict。
    """
    bar = t_idx if bar_idx is None else bar_idx
    closes = ctx['closes']
    t = daily[t_idx]
    p0, p1 = d1['p0'], d1['p1']
    win = params['bottom_amp_window']
    t1_idx = d1['t1_idx']

    # 杯底 ±N 日振幅（仅记录，不判定）
    z = closes[max(0, t1_idx - win): t1_idx + win + 1]
    bottom_amp = (max(z) - min(z)) / min(z) if z and min(z) > 0 else None

    # 柄部对均线的最深侵入
    hd_min = {}
    for w in (10, 20, 50):
        ma = ctx['ma'][w]
        vals = [closes[k] / ma[k] for k in range(t2_idx + 1, bar)
                if ma[k] and ma[k] > 0]
        hd_min[w] = min(vals) if vals else None

    # 巫毒日：柄部量能低于均量阈值的天数
    ma_v = ctx['vol_ma']
    voodoo = sum(1 for k in range(t2_idx + 1, bar)
                 if ma_v[k] and ma_v[k] > 0
                 and ctx['volumes'][k] < ma_v[k] * params['voodoo_vol_ratio'])

    return {
        'record_type': None,
        # pattern-scan 等前端消费的字段
        'type': 'bullish',
        'details': {
            'description': (f"杯口 {p2:.2f} → 买点 {buy_point:.2f} | "
                            f"深 {depth * 100:.1f}% 柄 {hdd * 100:.1f}% "
                            f"{bar - t2_idx}日"),
            'prior_high': round(p0, 3),
            'prior_high_date': daily[d1['t0_idx']]['date'],
            'bottom': round(p1, 3),
            'bottom_date': daily[t1_idx]['date'],
            'mouth': round(p2, 3),
            'mouth_date': daily[t2_idx]['date'],
            'handle_low': round(p3, 3),
            'handle_low_date': daily[p3_idx]['date'],
            'buy_point': round(buy_point, 3),
            'target_price': round(buy_point * (1 + params['suggested_tp']), 3),
            'stop_price': round(buy_point * (1 - params['suggested_sl']), 3),
            'suggested_max_hold': params['suggested_max_hold'],
        },
        'stock_code': t.get('stock_code'),
        'date': t['date'],
        'prior_high_date': daily[d1['t0_idx']]['date'],
        'prior_high_price': round(p0, 3),
        'bottom_date': daily[t1_idx]['date'],
        'bottom_price': round(p1, 3),
        'mouth_date': daily[t2_idx]['date'],
        'mouth_price': round(p2, 3),
        'handle_low_date': daily[p3_idx]['date'],
        'handle_low_price': round(p3, 3),
        # ── 几何特征（仅记录）──
        'depth_pct': round(depth * 100, 2),
        'depth_from_high_pct': round((p0 - p1) / p0 * 100, 2),
        'mouth_vs_high': round(p2 / p0, 4),
        'handle_dd_pct': round(hdd * 100, 2),
        'handle_days': handle_days,
        'mouth_to_date_days': bar - t2_idx,
        'bottom_amp': round(bottom_amp, 4) if bottom_amp is not None else None,
        'hd_min_ma10': round(hd_min[10], 4) if hd_min[10] is not None else None,
        'hd_min_ma20': round(hd_min[20], 4) if hd_min[20] is not None else None,
        'hd_min_ma50': round(hd_min[50], 4) if hd_min[50] is not None else None,
        'ma10_held': bool(hd_min[10] is not None and hd_min[10] >= 1.0),
        'voodoo_days': voodoo,
        # 前置上涨 = 前高相对【再前一笔的低点】的涨幅。不能写成 (p0/p1-1)——
        # 那是「前高到杯底的跌幅」，与杯身深度同源，曾在此处误用。
        'prior_advance_pct': round((p0 / d1['prev_low'] - 1) * 100, 2),
        'advance_origin_price': round(d1['prev_low'], 3),
        # ── 交易字段 ──
        'buy_point': round(buy_point, 3),
        'entry_rule': 'buy_stop_at_pivot',
        'suggested_tp': params['suggested_tp'],
        'suggested_sl': params['suggested_sl'],
        'suggested_max_hold': params['suggested_max_hold'],
        'requires_active_exit': True,
        'breakout_close': None,
        'breakout_vol_ratio': None,
    }


# ─── 主检测 ────────────────────────────────────────────

def detect(daily: List[Dict], params: Optional[Dict] = None,
           market_cap: Optional[float] = None,
           bi_list: Optional[List[Dict]] = None,
           stock_code: Optional[str] = None,
           record_types=('SIGNAL',),
           as_of: Optional[str] = None,
           diagnose: bool = False):
    """
    单次无状态扫描，产出 CANDIDATE / SIGNAL 两类记录。

    对每个检测日 T，结构点全部由 [.., T-1] 的数据拟合；T 当日只用于突破判定。
    因此同一份数据重复运行结果完全一致（幂等），无跨日状态。

    Args:
        daily: 日K列表，按日期升序，元素含 date/open/high/low/close/volume。
        params: 参数字典；None 时从配置文件加载。
        market_cap: 流通市值（亿），用于 min_market_cap 过滤。
        bi_list: 缠论笔列表；None 时按 stock_code 取「daily 最后一根K线当日」的笔快照。
            调用方若自行传入，须保证该笔序列只用了不晚于最后一根K线的数据；
            chanlun_bi_json 是每股 50 笔的滚动窗口且端点会随后续K线调整，
            一次性用最新快照扫全history会把未来信息带进笔的划分。
        stock_code: 股票代码，用于加载笔数据。
        record_types: 需要返回的记录类型。默认只返回 SIGNAL——CANDIDATE 会在
            同一结构的整个跟踪窗口内逐日重复出现，全量返回会淹没前端图表与下游
            清单；需要时显式传入 ('SIGNAL','CANDIDATE')。
            每个结构（杯底日 + 杯口日 唯一标识）每类记录只输出一次：
            CANDIDATE 输出在「首次成为候选」那天。
        as_of: 「当天视角」日期。传入后 daily 先截断到 ≤ as_of，笔快照取 ≤ as_of 的
            最后一个（chanlun_bi_json 是每股 50 笔的滚动窗口且端点会随后续K线调整，
            用最新快照评估历史K线会把未来信息带进笔的划分），返回值只保留
            date == as_of 的记录。逐日扫描必须传它。
        diagnose: True 时返回 (records, 漏斗统计)。

    Returns:
        记录列表；diagnose=True 时返回 (记录列表, 统计字典)。
    """
    if params is None:
        params = load_params()

    stats = {'points': 0, 'no_d1': 0, 'v_fail': 0, 'passed_v': 0,
             'SIGNAL': 0, 'CANDIDATE': 0, 'other': 0}

    # as_of：只站在 as_of 这一天看。K线截断到当日，笔快照随之取当日快照，
    # 返回的记录也只保留当日新出现的那一条。实盘当时判断不出，就永远判断不出，
    # 不允许后续K线改写历史产出。
    if as_of is not None:
        daily = [k for k in daily if k['date'] <= as_of]
        if not daily:
            return ([], stats) if diagnose else []

    n = len(daily)
    need = params['cup_max_age'] + params['min_descent_bars'] + 60
    if n < need:
        return ([], stats) if diagnose else []

    cap = daily[0].get('stock_code') or stock_code
    if params['min_market_cap'] > 0 and market_cap and market_cap < params['min_market_cap']:
        return ([], stats) if diagnose else []

    if bi_list is None and cap:
        # chanlun_bi_json 是每股 50 笔的滚动窗口，且笔的端点会随后续K线继续调整：
        # 实测 000007 的 2024-09-19 快照与 2026-09-22 快照，50 笔中仅 26 笔完全一致。
        # 因此必须取检测日当天的快照；用最新快照评估历史K线等于让笔的划分偷看未来数据。
        bi_list = _load_bi(cap, daily[-1]['date'])
    if not bi_list or len(bi_list) < 2:
        return ([], stats) if diagnose else []

    daily = [dict(k) for k in daily]
    for k in daily:
        k.setdefault('stock_code', cap)

    date_idx = {k['date']: i for i, k in enumerate(daily)}
    closes = [k['close'] for k in daily]
    volumes = [k['volume'] for k in daily]
    ctx = {
        'closes': closes, 'volumes': volumes,
        'vol_ma': _rolling_mean(volumes, params['vol_ma_window']),
        'ma_trend': _rolling_mean(closes, params['ma_trend_window']),
        'ma': {w: _rolling_mean(closes, w) for w in (10, 20, 50)},
    }

    d1s = _build_d1_candidates(bi_list, date_idx, params)
    if not d1s:
        return ([], stats) if diagnose else []

    # 一个杯子只有一个杯底：同一杯口下每类记录只保留最深的那条。
    # 多根向下笔会指向同一个杯口——27.1 → 19.35 → 25.25 → 19.92 → 26.51 的 W 形底，
    # 两根向下笔的杯口都是 26.51（003030）；年线级别的新低也会取代一年前的老基部
    # （002479：4.819 与 4.16 共享同一杯口）。不按 (杯底, 杯口) 而按 (杯口, 类型) 去重，
    # 正是为了让「同一只杯子」只留一条。
    #
    # 去重放在 V 校验之后：更深的杯底若因深度超限被否，说明那个基部本就不是杯柄，
    # 但更浅的读数可能对应另一个成立的前高→杯口区间，不该被连带否掉。
    # 深者优先而非先到先得：同一根 D1 在连续检测日上的 bottom_price 相同，
    # 严格小于号保证 CANDIDATE 仍落在「首次成为候选」那天。
    best = {}           # (record_type, t2) -> rec
    is_gap = _mark_gaps(closes, params['rim_gap_window'], params['rim_gap_max'])
    for d1 in d1s:
        t1 = d1['t1_idx']
        lo = t1 + params['cup_min_age']
        hi = min(t1 + params['cup_max_age'], n - 1)
        if lo > hi:
            continue
        # 杯口从杯底当天起逐日推进，且只用「不是跳涨日」的收盘来抬高杯口。
        p2 = closes[t1]
        t2 = t1

        for t_idx in range(t1, hi + 1):
            if t_idx >= lo:
                stats['points'] += 1
                rec = _evaluate(daily, ctx, d1, t_idx, p2, t2, params)
                if rec is None:
                    stats['v_fail'] += 1
                else:
                    stats['passed_v'] += 1
                    rt = rec['record_type']
                    stats[rt] = stats.get(rt, 0) + 1
                    key = (rt, t2)
                    prev = best.get(key)
                    if prev is None or rec['bottom_price'] < prev['bottom_price']:
                        best[key] = rec
            # 杯口只能被「走到」，不能被「跳上去」（PRD §2.5）。
            #
            # 原来的 p2 = max(close[t1 .. t_idx-1]) 会让任何一根收盘都成为新杯口。
            # 603903 就是被这一点毁掉的：真杯口是 08-18 的 14.76，之后 15 个交易日
            # 在 13.55~14.55 之间横盘；09-09 收 15.12 是【突破这次横盘的尝试】，
            # 但引擎把 15.12 记成了新杯口，于是 09-10 跌回 14.61（跌破真杯口）
            # 被重新解读成「柄部正常回落」，09-22 的二次上攻成了新突破。
            #
            # 跳涨日 = 收盘高出前 rim_gap_window 日最高收盘 rim_gap_max 以上，
            # 即「突破了一段既有区间」。此类日不能充当杯口——杯口是右侧的顶，
            # 突破它才叫买点；让突破日自己当杯口，突破就永远成立了。
            # 判据只用 <= t_idx-1 的数据，因果性与幂等不受影响。
            if closes[t_idx] > p2 and not is_gap[t_idx]:
                p2 = closes[t_idx]
                t2 = t_idx

    records = list(best.values())

    # 加仓点（PRD §3.2 S7，用户规则 5）：突破日发 SIGNAL（轻仓、吃杯口价），
    # 次日收盘仍站在杯口之上时补一条 CONFIRM（加仓）。两者并存互不压制。
    # 判据只用 <= 突破日+1 的数据，记录日期即确认日，因果性与幂等不受影响。
    if params['require_breakout_confirm']:
        di = {k['date']: i for i, k in enumerate(daily)}
        extra = []
        for rec in records:
            if rec['record_type'] != 'SIGNAL':
                continue
            i = di.get(rec['date'])
            if i is None or i + 1 >= n:
                continue
            if closes[i + 1] > rec['mouth_price']:
                c = dict(rec)
                c['record_type'] = 'CONFIRM'
                c['date'] = daily[i + 1]['date']
                c['details'] = dict(rec['details'])
                c['details']['description'] = (
                    '加仓：突破日 %s 后次日收 %.2f 仍站稳杯口 %.2f | %s'
                    % (rec['date'], closes[i + 1], rec['mouth_price'],
                       rec['details']['description']))
                extra.append(c)
        records.extend(extra)

    records.sort(key=lambda r: r['date'])
    if as_of is not None:
        records = [r for r in records if r['date'] == as_of]
    if record_types is not None:
        keep = set(record_types)
        records = [r for r in records if r['record_type'] in keep]
    return (records, stats) if diagnose else records


# ─── CLI ──────────────────────────────────────────────

def _load_daily(conn, stock_code: str, end_date: str, days: int) -> List[Dict]:
    """读取指定股票截至 end_date 的日K（前复权分红再投口径）。"""
    rows = conn.execute(
        "SELECT date, open, high, low, close, volume FROM daily_kline_adj "
        "WHERE stock_code=? AND date<=? AND date>=date(?, ?) ORDER BY date",
        (stock_code, end_date, end_date, f'-{days} days')).fetchall()
    return [dict(r) for r in rows if r['close'] is not None]


def _snapshot_date(stock_code: str, as_of: str) -> str:
    """该股在 as_of 视角下实际用到的笔快照日期，便于核对口径。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT MAX(scan_date) FROM chanlun_bi_json WHERE stock_code=? AND scan_date<=?",
            (stock_code, as_of)).fetchone()
        conn.close()
        return row[0] if row and row[0] else '无'
    except sqlite3.Error:
        return '未知'


def main():
    ap = argparse.ArgumentParser(description='杯柄形态放量突破检测 V2')
    ap.add_argument('--stock', default='600519')
    ap.add_argument('--date', default=None, help='截止日期，默认今天')
    ap.add_argument('--days', type=int, default=2500, help='回溯日历天数')
    ap.add_argument('--diagnose', action='store_true', help='输出逐层漏斗')
    ap.add_argument('--all-dates', action='store_true',
                    help='列出历史全部记录（默认只给 --date 当天的当天视角）')
    args = ap.parse_args()

    from datetime import date as _d
    end = args.date or _d.today().strftime('%Y-%m-%d')

    params = load_params()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    daily = _load_daily(conn, args.stock, end, args.days)
    name = conn.execute("SELECT name FROM stock_basic WHERE stock_code=?",
                        (args.stock,)).fetchone()
    conn.close()

    if not daily:
        print(f"{args.stock} 无K线数据"); return
    as_of = None if args.all_dates else daily[-1]['date']
    records, stats = detect(daily, params, stock_code=args.stock, as_of=as_of,
                            record_types=('SIGNAL', 'CONFIRM', 'CANDIDATE'), diagnose=True)

    print(f"{args.stock} {name['name'] if name else ''} @ {daily[-1]['date']}   "
          f"K线 {len(daily)} 根   笔快照 {_snapshot_date(args.stock, daily[-1]['date'])}"
          f"{'   [全历史]' if as_of is None else '   [当天视角]'}")
    if args.diagnose:
        print("\n=== 漏斗 ===")
        for k, v in stats.items():
            print(f"  {k:<12} {v:,}")
    sig = [r for r in records if r['record_type'] == 'SIGNAL']
    cand = [r for r in records if r['record_type'] == 'CANDIDATE']
    print(f"\nSIGNAL {len(sig)} 条   CANDIDATE {len(cand)} 条")
    for r in records[-12:]:
        print(f"  [{r['record_type']:<9}] {r['date']}  买点={r['buy_point']:<8} "
              f"前高={r['prior_high_price']}({r['prior_high_date']}) "
              f"杯底={r['bottom_price']}({r['bottom_date']}) "
              f"杯口={r['mouth_price']}({r['mouth_date']}) "
              f"柄低={r['handle_low_price']}  深={r['depth_pct']}% "
              f"柄回撤={r['handle_dd_pct']}% 杯口→今={r['mouth_to_date_days']}d")


if __name__ == '__main__':
    main()
