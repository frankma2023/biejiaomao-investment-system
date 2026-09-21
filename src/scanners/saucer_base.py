"""
碟形基部（Saucer Base）识别引擎 v1.0

突破日驱动 + 形态切割法 + 参数即标准。
判断"今天是否为碟形基部突破日"，是则输出信号。

核心算法：
  1. 日K线聚合为周K线（周线主分析）
  2. 形态切割法定位碟底和前高（切割A/B/C三层校验）
  3. 日线精细验证（碟底平坦性、弧线圆润性、成交量）
  4. 突破信号确认 + 虚假突破排除
  5. 柄部检测（可选）

参考：碟形基部识别引擎_产品需求书_v3.md
"""

import sys, os, argparse, sqlite3, yaml, math
from datetime import datetime, date as dt_date, timedelta
from typing import Optional, Dict, List, Tuple

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "saucer_base",
    "display_name": "碟形基部",
    "category": "pattern",
    "version": "1.0",
    "description": "识别欧奈尔碟形基部（Saucer Base）突破信号，形态切割法定位前高，日线精细验证"
}

# ─── 参数加载 ───────────────────────────────────────────

def load_params():
    """从 YAML 加载参数，缺失时使用默认值"""
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "saucer_base.yaml")
    defaults = {
        'min_prior_advance': 0.30, 'prior_advance_lookback': 120,
        'min_market_cap_strict': 50, 'min_market_cap_warn': 200,
        'min_bottom_age_days': 10, 'bottom_scan_max_days': 120,
        'bottom_to_t_max_chg': 0.20,
        'cut_pct': 0.20, 'cut_check_A_pct': 0.05, 'min_descent_weeks': 3,
        'correction_min': 0.08, 'correction_max': 0.20,
        'bottom_flat_window_days': 5, 'bottom_flatness_max': 0.05,
        'bottom_flat_window_wide': 10, 'bottom_flatness_wide': 0.08,
        'bottom_flat_wide_required': False, 'bottom_slope_max': 0.0005,
        'arc_smoothness_std': 0.008, 'segment_slope_max': 0.004,
        'ascent_symmetry_enabled': True, 'ascent_symmetry_ratio': 1.5,
        'vol_bottom_max': 0.50, 'vol_contraction': 0.60,
        'vol_spike_check': True, 'vol_spike_ratio': 1.2,
        'handle_enabled': True, 'handle_required': False,
        'handle_min_weeks': 1, 'handle_max_drawdown': 0.15,
        'handle_vol_ratio': 0.70,
        'breakout_buffer': 0.01, 'breakout_vol_ratio': 1.3,
        'close_position_min': 0.50,
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        defaults.update(cfg.get('saucer_base', {}))
    return defaults


# ─── 工具函数 ───────────────────────────────────────────

def _aggregate_weekly(daily_data: List[Dict]) -> List[Dict]:
    """日K线聚合为周K线"""
    if not daily_data:
        return []
    result = []
    current_week = None
    week_row = None
    
    for row in daily_data:
        d = row['date']
        if isinstance(d, str):
            dt = datetime.strptime(d, '%Y-%m-%d').date()
        else:
            dt = d
        
        iso = dt.isocalendar()
        week_key = (iso[0], iso[1])  # (year, week_number)
        
        if week_key != current_week:
            if week_row:
                result.append(week_row)
            current_week = week_key
            week_row = {
                'date': dt.strftime('%Y-%m-%d'),
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'volume': row['volume'],
            }
        else:
            week_row['high'] = max(week_row['high'], row['high'])
            week_row['low'] = min(week_row['low'], row['low'])
            week_row['close'] = row['close']  # 最后一天收盘
            week_row['volume'] += row['volume']
            week_row['date'] = dt.strftime('%Y-%m-%d')  # 最后一天
    
    if week_row:
        result.append(week_row)
    return result


def _sma(values: List[float], n: int) -> float:
    """简单移动平均"""
    if len(values) < n:
        return sum(values) / len(values) if values else 0
    return sum(values[-n:]) / n


def _linear_regression(y: List[float]) -> Tuple[float, float]:
    """返回 (斜率slope, 截距intercept)"""
    n = len(y)
    if n < 2:
        return 0, y[0] if y else 0
    xs = list(range(n))
    x_mean = (n - 1) / 2
    y_mean = sum(y) / n
    num = sum((xs[i] - x_mean) * (y[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    if den == 0:
        return 0, y_mean
    slope = num / den
    intercept = y_mean - slope * x_mean
    return slope, intercept


def _is_local_high(values: List[float], idx: int, window: int = 13) -> bool:
    """判断 idx 是否在 ±window 范围内为局部高点"""
    left = max(0, idx - window)
    right = min(len(values) - 1, idx + window)
    peak = values[idx]
    for i in range(left, right + 1):
        if values[i] > peak:
            return False
    return True


# ─── 碟形基部检测 ──────────────────────────────────────

def detect(
    daily_klines: List[Dict],
    params: Optional[Dict] = None,
    market_cap: Optional[float] = None,
) -> List[Dict]:
    """
    检测碟形基部突破信号。

    Args:
        daily_klines: 日K线列表 [{'date','open','high','low','close','volume'}, ...] 升序
        params: 参数字典，None 则从 YAML 加载
        market_cap: 流通市值（亿），用于市值过滤

    Returns:
        信号列表 [{'signal_date':..., 'stock_code':..., 'pattern_type':..., ...}, ...]
        没有信号返回空列表
    """
    if params is None:
        params = load_params()
    
    if len(daily_klines) < 120:
        return []

    # ── 聚合周线 ──
    weekly = _aggregate_weekly(daily_klines)
    weekly_closes = [w['close'] for w in weekly]
    weekly_dates = [w['date'] for w in weekly]

    signals = []

    # ── 遍历每个交易日的后60天寻找突破日 ──
    # 实际上我们检查每日数据，看是否满足突破条件
    # 优化：只检查最近 N 天的突破
    lookback_days = params['bottom_scan_max_days'] + 60
    
    for t_idx in range(len(daily_klines) - 1, -1, -1):
        t_row = daily_klines[t_idx]
        t_close = t_row['close']
        t_date = t_row['date']
        t_open = t_row['open']
        t_high = t_row['high']
        t_low = t_row['low']
        t_volume = t_row['volume']
        
        # ── 趋势确认：close > SMA50 ──
        if t_idx < 50:
            continue
        sma50 = _sma([k['close'] for k in daily_klines[t_idx-49:t_idx+1]], 50)
        if t_close <= sma50:
            continue
        
        # ── 条件5.4：收盘位置在当日区间上50% ──
        if t_high > t_low:
            close_pos = (t_close - t_low) / (t_high - t_low)
            if close_pos < params['close_position_min']:
                continue
        
        # ── 条件5.3：阳线 ──
        if t_close <= t_open:
            continue
        
        # ── 步骤1：形态切割法找碟底和前高（周线） ──
        weekly_t_idx = len(weekly) - 1  # 突破日对应的周
        # 找到检测日所在的周索引
        for wi in range(len(weekly)):
            if weekly[wi]['date'] >= t_date:
                weekly_t_idx = wi
                break
        
        bottom_result = _find_bottom_and_prior_high(
            weekly_closes, weekly_dates, weekly_t_idx, t_close, params
        )
        if bottom_result is None:
            continue
        
        bottom_price, bottom_week_idx, prior_high, prior_high_week_idx, prior_high_date = bottom_result
        
        # ── 前置上涨验证 ──
        if not _check_prior_advance(daily_klines, prior_high_date, prior_high, params):
            continue
        
        # ── 条件5.1：收盘 ≥ 前高 + breakout_buffer ──
        if t_close < prior_high + params['breakout_buffer']:
            continue
        
        # ── 条件5.2：突破日成交量 ≥ 50日均量 × breakout_vol_ratio ──
        sma50_vol = _sma([k['volume'] for k in daily_klines[max(0,t_idx-49):t_idx+1]], 50)
        if t_volume < sma50_vol * params['breakout_vol_ratio']:
            continue
        
        # ── 条件5.5：收盘 > SMA150 ──
        if t_idx >= 150:
            sma150 = _sma([k['close'] for k in daily_klines[t_idx-149:t_idx+1]], 150)
            if t_close <= sma150:
                continue
        
        # ── 市值过滤 ──
        if market_cap is not None and params['min_market_cap_strict'] > 0:
            if market_cap < params['min_market_cap_strict']:
                continue
        
        # ── 步骤2：碟底平坦性验证（日线） ──
        flat_result = _check_bottom_flatness(daily_klines, bottom_price, bottom_week_idx, weekly_dates, params)
        if flat_result is None:
            continue
        bottom_amp_5d, bottom_amp_10d, bottom_quality = flat_result
        
        # ── 步骤3：弧线圆润性验证（日线） ──
        arc_result = _check_arc_smoothness(
            daily_klines, prior_high_date, prior_high, weekly_dates[bottom_week_idx], bottom_price, t_date, t_close, params
        )
        if arc_result is None:
            continue
        arc_std, ascent_std = arc_result
        
        # ── 步骤4：成交量验证（日线） ──
        vol_result = _check_volume(
            daily_klines, prior_high_date, weekly_dates[bottom_week_idx], bottom_price, sma50_vol, params
        )
        if vol_result is None:
            continue
        vol_bottom_ratio, vol_contraction_val = vol_result
        
        # ── 步骤5：虚假突破排除 ──
        if _check_false_breakout(daily_klines, t_idx, t_close, prior_high, sma50_vol):
            continue
        
        # ── 步骤6：柄部检测 ──
        handle_result = None
        if params.get('handle_enabled', True):
            handle_result = _check_handle(
                daily_klines, weekly_closes, weekly_dates, bottom_week_idx, prior_high_week_idx,
                prior_high, bottom_price, t_date, sma50, params
            )

        has_handle = handle_result is not None
        if params.get('handle_required', False) and not has_handle:
            continue
        
        # ── 输出信号 ──
        signal = {
            'signal_date': t_date,
            'pattern_type': 'saucer_with_handle' if has_handle else 'saucer',
            'prior_high_date': prior_high_date,
            'prior_high_price': round(float(prior_high), 2),
            'bottom_date': weekly_dates[bottom_week_idx],
            'bottom_price': round(float(bottom_price), 2),
            'drawdown_pct': round((prior_high - bottom_price) / prior_high * 100, 1),
            'descent_days': _count_trading_days(daily_klines, prior_high_date, weekly_dates[bottom_week_idx]),
            'ascent_days': _count_trading_days(daily_klines, weekly_dates[bottom_week_idx], t_date),
            'bottom_amp_5d': round(bottom_amp_5d * 100, 1),
            'bottom_amp_10d': round(bottom_amp_10d * 100, 1) if bottom_amp_10d is not None else None,
            'bottom_quality': bottom_quality,
            'arc_std': round(arc_std, 5),
            'ascent_std': round(ascent_std, 5) if ascent_std is not None else None,
            'vol_bottom_ratio': round(vol_bottom_ratio, 3),
            'vol_contraction': round(vol_contraction_val, 3),
            'breakout_vol_ratio': round(t_volume / sma50_vol, 2) if sma50_vol > 0 else 0,
            'breakout_chg_pct': round((t_close - t_open) / t_open * 100, 2) if t_open > 0 else 0,
            'buy_point': round(float(prior_high) + params['breakout_buffer'], 2),
            'handle_flag': has_handle,
            'handle_high_price': round(float(handle_result['handle_high_price']), 2) if has_handle else None,
            'prior_advance_pct': round(_get_prior_advance(daily_klines, prior_high_date, prior_high, params), 1),
            'market_cap': round(float(market_cap), 1) if market_cap else None,
            'close_vs_sma50': round(t_close / sma50, 3) if sma50 > 0 else 0,
            'close_vs_sma150': round(t_close / sma150, 3) if t_idx >= 150 and sma150 > 0 else 0,
        }
        signals.append(signal)
    
    return signals


# ─── 形态切割法（核心算法） ──────────────────────────────

def _find_bottom_and_prior_high(
    weekly_closes: List[float],
    weekly_dates: List[str],
    t_week_idx: int,
    t_close: float,
    params: Dict
) -> Optional[Tuple[float, int, float, int, str]]:
    """
    步骤1：形态切割法定位碟底和前高。
    
    Returns: (bottom_price, bottom_week_idx, prior_high_price, prior_high_week_idx, prior_high_date)
    或 None（未找到有效形态）
    """
    min_descent_weeks = params['min_descent_weeks']
    correction_min = params['correction_min']
    correction_max = params['correction_max']
    cut_pct = params['cut_pct']
    cut_check_A_pct = params['cut_check_A_pct']
    min_bottom_age_days = params['min_bottom_age_days']
    max_scan_days = params['bottom_scan_max_days']
    bottom_to_t_max_chg = params['bottom_to_t_max_chg']
    local_window = 13  # 局部极值窗口（周）
    
    max_scan_weeks = min(t_week_idx, max_scan_days // 5)
    min_scan_weeks = max(2, min_bottom_age_days // 5)
    
    # 1.1 定位碟底：在 [t-max_scan_weeks, t-min_scan_weeks] 范围内找最低周收盘价
    bottom_price = float('inf')
    bottom_idx = None
    
    search_start = max(0, t_week_idx - max_scan_weeks)
    search_end = t_week_idx - min_scan_weeks
    
    for i in range(search_start, search_end + 1):
        if i >= 0 and i < len(weekly_closes):
            if weekly_closes[i] < bottom_price:
                bottom_price = weekly_closes[i]
                bottom_idx = i
    
    if bottom_idx is None or bottom_price <= 0:
        return None
    
    # 碟底到t涨幅不能太大（排除V形反转）
    if (t_close - bottom_price) / bottom_price > bottom_to_t_max_chg:
        return None
    
    # 1.2 从碟底逆向爬坡找前高
    candidate_price = bottom_price
    candidate_idx = bottom_idx
    prior_high_price = None
    prior_high_idx = None
    prior_high_date = None
    
    for i in range(bottom_idx - 1, max(bottom_idx - 52, -1), -1):
        week_close = weekly_closes[i]
        
        # a) 更新candidate为沿途最高收盘价
        if week_close > candidate_price:
            candidate_price = week_close
            candidate_idx = i
        
        # b) 遇到局部高点时执行三切割
        if _is_local_high(weekly_closes, i, local_window):
            # 切割A：candidate不能比突破日高太多
            if candidate_price > t_close * (1 + cut_check_A_pct):
                continue  # 太高，属于另一个形态周期
            
            # 切割B：从candidate向前找≥cut_pct的大波动
            found_large_move = False
            for j in range(candidate_idx - 1, max(candidate_idx - 52, -1), -1):
                j_close = weekly_closes[j]
                if min(j_close, candidate_price) > 0:
                    move = abs(j_close - candidate_price) / min(j_close, candidate_price)
                    if move >= cut_pct:
                        found_large_move = True
                        break
            
            if not found_large_move:
                continue  # 前面没有大波动，candidate可能不是真正的前高
            
            # 切割C：回调深度在[correction_min, correction_max]
            if candidate_price > 0:
                drawdown = (candidate_price - bottom_price) / candidate_price
                if correction_min <= drawdown <= correction_max:
                    prior_high_price = candidate_price
                    prior_high_idx = candidate_idx
                    prior_high_date = weekly_dates[candidate_idx]
                    break
                else:
                    # 回调深度不符合碟形范围，继续回溯
                    continue
        
        # 兜底：回溯到底还没找到 → 取目前遇到的最高点
        if i <= 1:
            if prior_high_price is None and candidate_price > bottom_price:
                drawdown = (candidate_price - bottom_price) / candidate_price
                if correction_min <= drawdown <= correction_max:
                    prior_high_price = candidate_price
                    prior_high_idx = candidate_idx
                    prior_high_date = weekly_dates[candidate_idx]
    
    if prior_high_price is None:
        return None
    
    # 前高到碟底距离检查
    descent_weeks = bottom_idx - prior_high_idx
    if descent_weeks < min_descent_weeks:
        return None
    
    return (bottom_price, bottom_idx, prior_high_price, prior_high_idx, prior_high_date)


# ─── 前置上涨验证 ──────────────────────────────────────

def _check_prior_advance(
    daily_klines: List[Dict],
    prior_high_date: str,
    prior_high: float,
    params: Dict
) -> bool:
    """验证前置上涨：前高之前存在 ≥30% 的涨幅"""
    lookback = params['prior_advance_lookback']
    min_advance = params['min_prior_advance']
    
    # 找到前高在日线中的位置
    prior_idx = None
    for idx, row in enumerate(daily_klines):
        if row['date'] >= prior_high_date:
            prior_idx = idx
            break
    
    if prior_idx is None or prior_idx < lookback:
        return False
    
    # 在前高之前的lookback天内找最低点
    low_price = float('inf')
    for i in range(max(0, prior_idx - lookback), prior_idx):
        if daily_klines[i]['close'] < low_price:
            low_price = daily_klines[i]['close']
    
    if low_price <= 0:
        return False
    
    advance = (prior_high - low_price) / low_price
    return advance >= min_advance


def _get_prior_advance(
    daily_klines: List[Dict],
    prior_high_date: str,
    prior_high: float,
    params: Dict
) -> float:
    """计算前置涨幅百分比"""
    lookback = params['prior_advance_lookback']
    prior_idx = None
    for idx, row in enumerate(daily_klines):
        if row['date'] >= prior_high_date:
            prior_idx = idx
            break
    if prior_idx is None or prior_idx < 10:
        return 0.0
    
    low_price = float('inf')
    for i in range(max(0, prior_idx - lookback), prior_idx):
        if daily_klines[i]['close'] < low_price:
            low_price = daily_klines[i]['close']
    
    if low_price <= 0:
        return 0.0
    return (prior_high - low_price) / low_price * 100


# ─── 碟底平坦性验证 ────────────────────────────────────

def _check_bottom_flatness(
    daily_klines: List[Dict],
    bottom_price: float,
    bottom_week_idx: int,
    weekly_dates: List[str],
    params: Dict
) -> Optional[Tuple[float, float, str]]:
    """
    步骤2：碟底平坦性验证（双层窗口）。
    
    Returns: (bottom_amp_5d, bottom_amp_10d, bottom_quality)
    None 表示必要条件不满足
    """
    window_5 = params['bottom_flat_window_days']
    flatness_max = params['bottom_flatness_max']
    window_10 = params['bottom_flat_window_wide']
    flatness_wide = params['bottom_flatness_wide']
    slope_max = params['bottom_slope_max']
    wide_required = params['bottom_flat_wide_required']
    
    bottom_date = weekly_dates[bottom_week_idx]
    # 找到碟底在日线中的索引
    bottom_daily_idx = None
    for idx, row in enumerate(daily_klines):
        if row['date'] >= bottom_date:
            bottom_daily_idx = idx
            break
    
    if bottom_daily_idx is None:
        return None
    
    # 主检测（±5天，共11天）
    start_5 = max(0, bottom_daily_idx - window_5)
    end_5 = min(len(daily_klines) - 1, bottom_daily_idx + window_5)
    interval_5 = daily_klines[start_5:end_5 + 1]
    
    if len(interval_5) < 3:
        return None
    
    closes_5 = [r['close'] for r in interval_5]
    low_5 = min(closes_5)
    high_5 = max(closes_5)
    amp_5 = (high_5 - low_5) / low_5 if low_5 > 0 else 1.0
    
    # 线性回归斜率
    slope, _ = _linear_regression(closes_5)
    
    if amp_5 > flatness_max or abs(slope) > slope_max:
        return None  # 必要条件不满足
    
    # 扩展窗口（±10天，共21天）
    start_10 = max(0, bottom_daily_idx - window_10)
    end_10 = min(len(daily_klines) - 1, bottom_daily_idx + window_10)
    interval_10 = daily_klines[start_10:end_10 + 1]
    closes_10 = [r['close'] for r in interval_10]
    low_10 = min(closes_10)
    high_10 = max(closes_10)
    amp_10 = (high_10 - low_10) / low_10 if low_10 > 0 else 1.0
    
    quality = 'normal'
    if amp_10 > flatness_wide:
        if wide_required:
            return None  # 扩展区间升级为必要条件
        quality = 'shallow'
    
    return (amp_5, amp_10, quality)


# ─── 弧线圆润性验证 ────────────────────────────────────

def _check_arc_smoothness(
    daily_klines: List[Dict],
    prior_high_date: str,
    prior_high: float,
    bottom_date: str,
    bottom_price: float,
    t_date: str,
    t_close: float,
    params: Dict
) -> Optional[Tuple[float, Optional[float]]]:
    """
    步骤3：弧线圆润性验证。
    
    3.1-3.4: 下行段分三段验证
    3.5: 上行段对称性验证（可选）
    
    Returns: (arc_std, ascent_std) or None
    """
    arc_std_max = params['arc_smoothness_std']
    seg_slope_max = params['segment_slope_max']
    sym_enabled = params['ascent_symmetry_enabled']
    sym_ratio = params['ascent_symmetry_ratio']
    
    # 下行段：[前高 → 碟底]
    descent = _get_segment(daily_klines, prior_high_date, bottom_date)
    if len(descent) < 6:
        return None
    
    closes_desc = [r['close'] for r in descent]
    
    # 3.4: 下行段日收益标准差
    returns = []
    for i in range(1, len(closes_desc)):
        if closes_desc[i-1] > 0:
            ret = (closes_desc[i] - closes_desc[i-1]) / closes_desc[i-1]
            returns.append(ret)
    
    if not returns:
        return None
    
    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
    arc_std = math.sqrt(variance)
    
    if arc_std > arc_std_max:
        return None
    
    # 3.1-3.3: 按时间均分三段
    n = len(closes_desc)
    seg_len = n // 3
    if seg_len == 0:
        return None
    
    seg_A = closes_desc[:seg_len]
    seg_M = closes_desc[seg_len:2 * seg_len]
    seg_B = closes_desc[2 * seg_len:]
    
    def avg_daily_slope(seg: List[float]) -> float:
        if len(seg) < 2:
            return 0
        return (seg[-1] - seg[0]) / seg[0] / len(seg) if seg[0] > 0 else 0
    
    slope_A = abs(avg_daily_slope(seg_A))
    slope_M = abs(avg_daily_slope(seg_M))
    slope_B = abs(avg_daily_slope(seg_B))
    
    # 3.2: 每段日均跌幅 ≤ seg_slope_max
    if slope_A > seg_slope_max or slope_M > seg_slope_max or slope_B > seg_slope_max:
        return None
    
    # 3.3: 三段递减 A ≥ M ≥ B
    if not (slope_A >= slope_M >= slope_B):
        # 允许小幅误差（5% tolerance）
        if slope_M > slope_A * 1.05 or slope_B > slope_M * 1.05:
            return None
    
    # 3.5: 上行段对称性（可选）
    ascent_std = None
    if sym_enabled:
        ascent = _get_segment(daily_klines, bottom_date, t_date)
        if len(ascent) >= 6:
            closes_asc = [r['close'] for r in ascent]
            asc_returns = []
            for i in range(1, len(closes_asc)):
                if closes_asc[i-1] > 0:
                    asc_returns.append((closes_asc[i] - closes_asc[i-1]) / closes_asc[i-1])
            if asc_returns:
                mean_a = sum(asc_returns) / len(asc_returns)
                var_a = sum((r - mean_a) ** 2 for r in asc_returns) / len(asc_returns)
                ascent_std = math.sqrt(var_a)
                if ascent_std > arc_std * sym_ratio:
                    return None
    
    return (arc_std, ascent_std)


# ─── 成交量验证 ────────────────────────────────────────

def _check_volume(
    daily_klines: List[Dict],
    prior_high_date: str,
    bottom_date: str,
    bottom_price: float,
    sma50_vol: float,
    params: Dict
) -> Optional[Tuple[float, float]]:
    """
    步骤4：成交量验证。
    
    Returns: (vol_bottom_ratio, vol_contraction_val) or None
    """
    vol_bottom_max = params['vol_bottom_max']
    vol_contraction_max = params['vol_contraction']
    vol_spike_check = params['vol_spike_check']
    vol_spike_ratio = params['vol_spike_ratio']
    
    # 碟底区间 ±5天
    bottom_idx = None
    for idx, row in enumerate(daily_klines):
        if row['date'] >= bottom_date:
            bottom_idx = idx
            break
    
    if bottom_idx is None:
        return None
    
    start_b = max(0, bottom_idx - 5)
    end_b = min(len(daily_klines) - 1, bottom_idx + 5)
    bottom_vols = [daily_klines[i]['volume'] for i in range(start_b, end_b + 1)]
    avg_bottom_vol = sum(bottom_vols) / len(bottom_vols) if bottom_vols else 0
    
    vol_bottom_ratio = avg_bottom_vol / sma50_vol if sma50_vol > 0 else 1.0
    if vol_bottom_ratio > vol_bottom_max:
        return None
    
    # 成交量萎缩：下行后半/前半
    descent = _get_segment(daily_klines, prior_high_date, bottom_date)
    if len(descent) >= 4:
        mid = len(descent) // 2
        first_half_vol = sum(r['volume'] for r in descent[:mid]) / max(mid, 1)
        second_half_vol = sum(r['volume'] for r in descent[mid:]) / max(len(descent) - mid, 1)
        
        vol_contraction_val = second_half_vol / first_half_vol if first_half_vol > 0 else 1.0
        if vol_contraction_val > vol_contraction_max:
            return None
    else:
        vol_contraction_val = 1.0
    
    # 底部异常放量检查（可关）
    if vol_spike_check:
        for v in bottom_vols:
            if v > sma50_vol * vol_spike_ratio:
                return None
    
    return (vol_bottom_ratio, vol_contraction_val)


# ─── 虚假突破排除 ──────────────────────────────────────

def _check_false_breakout(
    daily_klines: List[Dict],
    t_idx: int,
    t_close: float,
    prior_high: float,
    sma50_vol: float
) -> bool:
    """
    步骤5：虚假突破排除。
    返回 True 表示信号作废。
    """
    # 检测日成交量 < 50日均量
    if daily_klines[t_idx]['volume'] < sma50_vol:
        return True
    
    # 检测日前5天有放量下跌
    for i in range(max(0, t_idx - 5), t_idx):
        row = daily_klines[i]
        if row['close'] < row['open'] and row['volume'] > sma50_vol * 1.3:
            return True
    
    return False


# ─── 柄部检测 ──────────────────────────────────────────

def _check_handle(
    daily_klines: List[Dict],
    weekly_closes: List[float],
    weekly_dates: List[str],
    bottom_week_idx: int,
    prior_high_week_idx: int,
    prior_high: float,
    bottom_price: float,
    t_date: str,
    sma50: float,
    params: Dict
) -> Optional[Dict]:
    """
    步骤6：柄部检测。
    
    条件 H1-H6。
    柄部是回升到前高附近后的小幅回调。
    
    Returns: dict with handle info or None
    """
    handle_max_dd = params['handle_max_drawdown']
    handle_min_weeks = params['handle_min_weeks']
    handle_vol_ratio = params['handle_vol_ratio']
    
    # H1: 在回升段定位柄部高点 — 回升段中最后一次收盘 ≥ 前高 × 0.90
    # 回升段：碟底→检测日
    rebound_closes = weekly_closes[bottom_week_idx:]
    rebound_dates = weekly_dates[bottom_week_idx:]
    
    handle_high_price = None
    handle_high_idx = None
    
    for i in range(len(rebound_closes) - 2, -1, -1):  # 从倒数第二个开始（不算突破日本身）
        if rebound_closes[i] >= prior_high * 0.90:
            handle_high_price = rebound_closes[i]
            handle_high_idx = i
            break
    
    if handle_high_price is None or handle_high_idx < 2:
        return None
    
    # H2: 柄部区间 — 从柄部高点到突破日之间的最低点
    handle_zone = rebound_closes[handle_high_idx:]
    handle_low_price = min(handle_zone)
    handle_low_idx_relative = handle_zone.index(handle_low_price)
    
    # 柄部时长
    handle_days = len(handle_zone) * 5  # 约略
    if handle_days < handle_min_weeks * 5 or handle_days > 30:
        return None
    
    # 柄部回撤
    handle_dd = (handle_high_price - handle_low_price) / handle_high_price if handle_high_price > 0 else 1.0
    if handle_dd > handle_max_dd:
        return None
    
    # H3: 柄部低点在上半部
    if handle_low_price < bottom_price + 0.5 * (prior_high - bottom_price):
        return None
    
    # H4: 柄部量缩（用日线）
    # 找到柄部对应的日线区间
    handle_week_date = rebound_dates[handle_high_idx] if handle_high_idx < len(rebound_dates) else None
    if handle_week_date is None:
        return None
    
    handle_daily = _get_segment(daily_klines, handle_week_date, t_date)
    if len(handle_daily) < 3:
        return None
    
    h_avg_vol = sum(r['volume'] for r in handle_daily) / len(handle_daily)
    # 基部整体日均量（前高→突破日）
    base_daily = _get_segment(daily_klines, weekly_dates[prior_high_week_idx], t_date)
    b_avg_vol = sum(r['volume'] for r in base_daily) / len(base_daily) if base_daily else h_avg_vol
    
    if h_avg_vol > b_avg_vol * handle_vol_ratio:
        return None
    
    # H5: 柄部区间回归斜率 ≤ 0（向下或水平）
    h_closes = [r['close'] for r in handle_daily]
    slope, _ = _linear_regression(h_closes)
    if slope > 0.001:  # 微小正斜率容忍
        return None
    
    # H6: 柄部低点 ≥ SMA50
    if handle_low_price < sma50:
        return None
    
    return {
        'handle_high_price': handle_high_price,
        'handle_low_price': handle_low_price,
        'handle_drawdown': handle_dd,
    }


# ─── 辅助函数 ──────────────────────────────────────────

def _get_segment(daily_klines: List[Dict], start_date: str, end_date: str) -> List[Dict]:
    """获取日期区间的日K线"""
    segment = []
    for row in daily_klines:
        if row['date'] >= start_date and row['date'] <= end_date:
            segment.append(row)
    return segment


def _count_trading_days(daily_klines: List[Dict], start_date: str, end_date: str) -> int:
    """计算区间内交易日数"""
    count = 0
    for row in daily_klines:
        if row['date'] >= start_date and row['date'] <= end_date:
            count += 1
    return count


# ─── 批量扫描 ──────────────────────────────────────────

def scan_batch(
    date_str: str,
    pool: str = 'all',
    params: Optional[Dict] = None
) -> List[Dict]:
    """
    批量扫描指定日期全市场碟形基部突破。
    
    Args:
        date_str: 扫描日期 'YYYY-MM-DD'
        pool: 'hs300'/'zz500'/'all'
        params: 参数字典
    """
    if params is None:
        params = load_params()
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 获取候选股票列表
    if pool == 'hs300':
        # 从 index_constituents 获取
        stocks = conn.execute("""
            SELECT DISTINCT stock_code FROM index_constituents 
            WHERE index_code='000300' AND date <= ? AND date >= date(?, '-30 days')
        """, (date_str, date_str)).fetchall()
    elif pool == 'zz500':
        stocks = conn.execute("""
            SELECT DISTINCT stock_code FROM index_constituents 
            WHERE index_code='000905' AND date <= ? AND date >= date(?, '-30 days')
        """, (date_str, date_str)).fetchall()
    else:
        stocks = conn.execute("""
            SELECT DISTINCT stock_code FROM daily_kline_adj
            WHERE date = ? AND volume > 0
        """, (date_str,)).fetchall()
    
    results = []
    total = len(stocks)
    
    for i, row in enumerate(stocks):
        code = row['stock_code']
        
        # 排除ST
        if 'ST' in code:
            continue
        
        # 获取K线数据（回溯400天）
        klines = conn.execute("""
            SELECT date, open, high, low, close, volume
            FROM daily_kline_adj
            WHERE stock_code = ? AND date <= ? AND date >= date(?, '-400 days')
            ORDER BY date
        """, (code, date_str, date_str)).fetchall()
        
        if len(klines) < 120:
            continue
        
        daily = [dict(r) for r in klines]
        
        # 获取市值
        mkt_row = conn.execute("""
            SELECT value FROM fundamental_indicator
            WHERE stock_code = ? AND metric_code = 'mc' AND date <= ?
            ORDER BY date DESC LIMIT 1
        """, (code, date_str)).fetchone()
        market_cap = float(mkt_row['value']) / 1e8 if mkt_row else None
        
        # 检测
        try:
            sigs = detect(daily, params, market_cap)
            for s in sigs:
                s['stock_code'] = code
                results.append(s)
        except Exception as e:
            continue
        
        if (i + 1) % 100 == 0:
            print(f"  扫描进度: {i+1}/{total}")
    
    conn.close()
    return results


# ─── CLI ────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='碟形基部识别引擎')
    parser.add_argument('--stock', type=str, help='单股票检测，如 600519')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'), help='检测日期')
    parser.add_argument('--scan', action='store_true', help='批量扫描模式')
    parser.add_argument('--pool', type=str, default='all', help='扫描池: hs300/zz500/all')
    parser.add_argument('--output', type=str, help='输出JSON文件路径')
    
    args = parser.parse_args()
    params = load_params()
    
    if args.scan:
        print(f"开始批量扫描碟形基部: {args.date} (池={args.pool})")
        results = scan_batch(args.date, args.pool, params)
        print(f"[OK] 扫描完成: 找到 {len(results)} 个碟形基部突破信号")
        
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2, default=str)
            print(f"结果已保存至: {args.output}")
        else:
            for s in results[:20]:
                print(f"  {s['stock_code']} {s['pattern_type']} 回调{s['drawdown_pct']}% 买点{s['buy_point']}")
    elif args.stock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        
        klines = conn.execute("""
            SELECT date, open, high, low, close, volume
            FROM daily_kline_adj
            WHERE stock_code = ? AND date <= ? AND date >= date(?, '-400 days')
            ORDER BY date
        """, (args.stock, args.date, args.date)).fetchall()
        
        if len(klines) < 120:
            print(f"[WARN] K线数据不足: {len(klines)} 条")
            sys.exit(1)
        
        daily = [dict(r) for r in klines]
        sigs = detect(daily, params)
        
        print(f"{args.stock} @ {args.date}")
        print(f"   碟形基部突破信号: {len(sigs)} 个")
        for s in sigs:
            print(f"   {s['signal_date']} 类型={s['pattern_type']} 回调={s['drawdown_pct']}%")
            print(f"      前高={s['prior_high_price']}({s['prior_high_date']}) 碟底={s['bottom_price']}({s['bottom_date']})")
            print(f"      买点={s['buy_point']} 量比={s['breakout_vol_ratio']}")
            print(f"      碟底振幅={s['bottom_amp_5d']}%/10d={s['bottom_amp_10d']} 质量={s['bottom_quality']}")
        
        conn.close()
