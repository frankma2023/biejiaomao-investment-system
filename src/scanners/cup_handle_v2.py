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
    'min_prior_advance', 'min_descent_bars', 'cup_min_age', 'cup_max_age',
    'mouth_lock_pullback', 'min_ascent_bars',
    'depth_min', 'depth_max', 'mouth_vs_high_max',
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

        # V3: 下行 K 线数
        bars = d1.get('length')
        if not (isinstance(bars, (int, float)) and bars >= params['min_descent_bars']):
            continue

        out.append({
            'p0': float(p0), 'p1': float(p1),
            't0_idx': date_idx[t0], 't1_idx': date_idx[t1],
            'bars': int(bars),
        })
    return out


def _evaluate(daily: List[Dict], ctx: Dict, d1: Dict, t_idx: int,
              p2: float, t2_idx: int, params: Dict) -> Optional[Dict]:
    """
    在检测日 t_idx 上，对给定 D1 与杯口 (p2, t2_idx) 执行形态校验与分类。

    Args:
        daily: 日K列表。
        ctx: 预计算上下文（均线数组等）。
        d1: D1 候选。
        t_idx: 检测日索引。
        p2: 杯口价（= max(close[t1 .. t_idx-1])）。
        t2_idx: 杯口所在索引。
        params: 参数字典。

    Returns:
        记录 dict（含 record_type）或 None。
    """
    closes = ctx['closes']
    p1 = d1['p1']
    t1_idx = d1['t1_idx']

    # V4: 杯底距检测日的交易日区间
    age = t_idx - t1_idx
    if not (params['cup_min_age'] <= age <= params['cup_max_age']):
        return None

    # V6: 杯口/前高
    if p2 > d1['p0'] * params['mouth_vs_high_max']:
        return None

    # 杯底→杯口 的上升段长度
    if t2_idx - t1_idx < params['min_ascent_bars']:
        return None

    # V5: 杯身深度
    depth = (p2 - p1) / p2
    if not (params['depth_min'] <= depth <= params['depth_max']):
        return None

    # 柄部区间 = (t2_idx, t_idx-1]
    if t_idx - 1 < t2_idx:
        return None
    handle = closes[t2_idx + 1: t_idx]           # 不含杯口当日，不含检测日
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
    handle_days = t_idx - 1 - t2_idx
    if handle_days > params['handle_days_max']:
        return None

    # V10: 柄低须在杯身上半部
    if p3 < p1 + (p2 - p1) * params['handle_position_ratio']:
        return None

    # V11: 柄部期间不得出现放量长阴
    vols = ctx['volumes']
    vol_ma = ctx['vol_ma']
    red_ratio = params['handle_red_vol_ratio']
    for k in range(t2_idx + 1, t_idx):
        ma = vol_ma[k]
        if ma and ma > 0 and closes[k] < daily[k]['open'] and vols[k] > ma * red_ratio:
            return None

    # ── 分类 ──────────────────────────────────────────
    t = daily[t_idx]
    buy_point = p2 + params['buy_point_buffer']
    t2_date = daily[t2_idx]['date']
    p3_idx = t2_idx + 1 + handle.index(p3)

    base = _build_record(daily, ctx, d1, t_idx, p2, t2_idx, p3, p3_idx,
                         handle, depth, hdd, handle_days, buy_point, params)

    # S1: 价格突破
    if t['close'] > buy_point:
        if _is_signal(daily, ctx, t_idx, t2_idx, p2, buy_point, params):
            base['record_type'] = 'SIGNAL'
            base['breakout_close'] = round(t['close'], 3)
            ma20v = ctx['vol_ma'][t_idx]
            base['breakout_vol_ratio'] = round(t['volume'] / ma20v, 3) if ma20v else None
            return base
        return None

    # W1/W2/W3: 候选
    if t_idx - t2_idx > params['mouth_to_signal_max']:
        return None
    if t['close'] < p1 * (1 - params['cup_invalidate_tolerance']):
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
                  buy_point: float, params: Dict) -> Dict:
    """
    组装输出记录（PRD §5）。

    Args:
        daily: 日K列表。
        ctx: 预计算上下文。
        d1: D1 候选。
        t_idx: 检测日索引。
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

    Returns:
        记录 dict。
    """
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
        vals = [closes[k] / ma[k] for k in range(t2_idx + 1, t_idx)
                if ma[k] and ma[k] > 0]
        hd_min[w] = min(vals) if vals else None

    # 巫毒日：柄部量能低于均量阈值的天数
    ma_v = ctx['vol_ma']
    voodoo = sum(1 for k in range(t2_idx + 1, t_idx)
                 if ma_v[k] and ma_v[k] > 0
                 and ctx['volumes'][k] < ma_v[k] * params['voodoo_vol_ratio'])

    return {
        'record_type': None,
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
        'mouth_to_date_days': t_idx - t2_idx,
        'bottom_amp': round(bottom_amp, 4) if bottom_amp is not None else None,
        'hd_min_ma10': round(hd_min[10], 4) if hd_min[10] is not None else None,
        'hd_min_ma20': round(hd_min[20], 4) if hd_min[20] is not None else None,
        'hd_min_ma50': round(hd_min[50], 4) if hd_min[50] is not None else None,
        'ma10_held': bool(hd_min[10] is not None and hd_min[10] >= 1.0),
        'voodoo_days': voodoo,
        'prior_advance_pct': round((p0 / (d1['p1'] or 1) - 1) * 100, 2),
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
           diagnose: bool = False):
    """
    单次无状态扫描，输出 CANDIDATE 与 SIGNAL 两类记录。

    对每个检测日 T，结构点全部由 [.., T-1] 的数据拟合；T 当日只用于突破判定。
    因此同一份数据重复运行结果完全一致（幂等），无跨日状态。

    Args:
        daily: 日K列表，按日期升序，元素含 date/open/high/low/close/volume。
        params: 参数字典；None 时从配置文件加载。
        market_cap: 流通市值（亿），用于 min_market_cap 过滤。
        bi_list: 缠论笔列表；None 时按 stock_code 从库中加载。
        stock_code: 股票代码，用于加载笔数据。
        diagnose: True 时返回 (records, 漏斗统计)。

    Returns:
        记录列表；diagnose=True 时返回 (记录列表, 统计字典)。
    """
    if params is None:
        params = load_params()

    stats = {'points': 0, 'no_d1': 0, 'v_fail': 0, 'passed_v': 0,
             'SIGNAL': 0, 'CANDIDATE': 0, 'other': 0}

    n = len(daily)
    need = params['cup_max_age'] + params['min_descent_bars'] + 60
    if n < need:
        return ([], stats) if diagnose else []

    cap = daily[0].get('stock_code') or stock_code
    if params['min_market_cap'] > 0 and market_cap and market_cap < params['min_market_cap']:
        return ([], stats) if diagnose else []

    if bi_list is None and cap:
        bi_list = _load_bi(cap)
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

    records = []
    seen = set()          # (date, t2_idx, t1_idx) 去重：一只股票同一天只出一条记录
    for d1 in d1s:
        t1 = d1['t1_idx']
        lo = t1 + params['cup_min_age']
        hi = min(t1 + params['cup_max_age'], n - 1)
        if lo > hi:
            continue
        # 初始化 running_max 覆盖 [t1, lo-1]
        seg = closes[t1:lo]
        if not seg:
            continue
        p2 = max(seg)
        t2 = t1 + seg.index(p2)

        for t_idx in range(lo, hi + 1):
            stats['points'] += 1
            rec = _evaluate(daily, ctx, d1, t_idx, p2, t2, params)
            if rec is not None:
                stats['passed_v'] += 1
                stats[rec['record_type']] = stats.get(rec['record_type'], 0) + 1
                key = (rec['date'], t2, t1)
                if key not in seen:
                    seen.add(key)
                    records.append(rec)
            else:
                stats['v_fail'] += 1
            # 为下一轮纳入 closes[t_idx]
            if closes[t_idx] > p2:
                p2 = closes[t_idx]
                t2 = t_idx

    records.sort(key=lambda r: r['date'])
    return (records, stats) if diagnose else records


# ─── CLI ──────────────────────────────────────────────

def _load_daily(conn, stock_code: str, end_date: str, days: int) -> List[Dict]:
    """读取指定股票截至 end_date 的日K（前复权分红再投口径）。"""
    rows = conn.execute(
        "SELECT date, open, high, low, close, volume FROM daily_kline_adj "
        "WHERE stock_code=? AND date<=? AND date>=date(?, ?) ORDER BY date",
        (stock_code, end_date, end_date, f'-{days} days')).fetchall()
    return [dict(r) for r in rows if r['close'] is not None]


def main():
    ap = argparse.ArgumentParser(description='杯柄形态放量突破检测 V2')
    ap.add_argument('--stock', default='600519')
    ap.add_argument('--date', default=None, help='截止日期，默认今天')
    ap.add_argument('--days', type=int, default=2500, help='回溯日历天数')
    ap.add_argument('--diagnose', action='store_true', help='输出逐层漏斗')
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
    records, stats = detect(daily, params, stock_code=args.stock, diagnose=True)

    print(f"{args.stock} {name['name'] if name else ''} @ {end}   K线 {len(daily)} 根")
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
