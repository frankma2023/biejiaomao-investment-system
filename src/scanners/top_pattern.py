"""
头部形态检测引擎 v1.1（Top Pattern — 头肩顶/三重顶/双重顶）

检测上升趋势末端出现的经典反转形态：
  - 三重顶（Triple Top）：三个等高顶部 + 两个深谷，可靠性最高
  - 双重顶（Double Top）：两个等高顶部 + 一个深谷，又称"M头"

检测顺序：头肩顶（预留）→ 三重顶 → 双重顶

信号级别（三级）：
  ⚠️  forming          — 形态正在构建，颈线尚未跌破
  🟡  weak_confirmed   — 连续3日收盘价低于颈线
  🔴  strong_confirmed — 单日收盘价跌破颈线 ≥ 3%

核心算法：深谷分隔法（Deep Trough Separation）
  - 峰谷检测粗筛 → 深谷按需动态计算 retrace_pct → 谷两侧窗口内取最高峰配对

用法:
  python -m src.scanners.top_pattern --stock 600519 --date 2026-05-17
"""

import sys, os, argparse, sqlite3, yaml
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "top_pattern",
    "display_name": "头部形态检测（头肩顶/双重顶/三重顶）",
    "category": "sell_signal",
    "version": "1.1",
    "description": "检测头肩顶/双重顶/三重顶头部反转形态：深谷分隔法配对 + 颈线确认",
}


# ══════════════════════════════════════════════════════════
# 参数加载
# ══════════════════════════════════════════════════════════

def load_params() -> Dict:
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "top_pattern.yaml")
    defaults = {
        # 通用
        'peak_detection_window': 5,
        'peak_prominence': 0.03,
        'max_lookback_days': 200,
        'min_lookback_days': 60,
        'peak_window_days': 60,
        # 上升背景
        'uptrend_lookback': 60,
        'uptrend_min_gain': 0.20,
        'uptrend_check_ma50': True,
        'uptrend_ma50_lookback': 10,
        # 头肩顶
        'hs_enabled': False,
        # 双重顶
        'dt_min_retrace': 0.08,
        'dt_max_price_diff': 0.03,
        'dt_min_days_between': 15,
        'dt_neck_break_pct': 0.03,
        'dt_consecutive_days': 3,
        # 三重顶
        'tt_min_retrace': 0.06,
        'tt_max_price_diff': 0.05,
        'tt_min_days_between': 15,
        'tt_min_trough_gap': 15,
        # 信号持久化
        'sig_max_valid_days': 200,
        'sig_decay_weeks': 8,
        'sig_break_market': True,
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        # 平铺映射
        for k, v in cfg.items():
            if isinstance(v, dict):
                for kk, vv in v.items():
                    flat_key = f"{k}_{kk}"
                    if k == 'uptrend':
                        flat_key = f"uptrend_{kk}"
                    elif k == 'head_shoulders':
                        # 三层嵌套：head_shoulders.{daily,weekly}.param
                        if isinstance(vv, dict):
                            defaults[f"hs_{kk}_enabled"] = True
                            for kkk, vvv in vv.items():
                                defaults[f"hs_{kk}_{kkk}"] = vvv
                        else:
                            defaults[f"hs_{kk}"] = vv
                    elif k == 'double_top':
                        flat_key = f"dt_{kk}"
                    elif k == 'triple_top':
                        flat_key = f"tt_{kk}"
                    elif k == 'signal_persistence':
                        flat_key = f"sig_{kk}"
                    else:
                        flat_key = f"{k}_{kk}"
                    defaults[flat_key] = vv
            else:
                defaults[k] = v
    return defaults


# ══════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════

def _sma(arr: List[float], n: int) -> float:
    """简单移动均线"""
    if len(arr) < n:
        return sum(arr) / max(len(arr), 1)
    return sum(arr[-n:]) / n


def _sma_at(arr: List[float], n: int, idx: int) -> float:
    """idx 位置之前 n 周期的 SMA"""
    start = max(0, idx - n + 1)
    vals = arr[start:idx + 1]
    return sum(vals) / max(len(vals), 1)


def _aggr_weekly(daily: List[Dict]) -> List[Dict]:
    """日线 → 周线聚合。周一为每周日期标识。"""
    weeks = {}
    for k in daily:
        d_str = k['date']
        if '-' in d_str:
            dt = datetime.strptime(d_str, '%Y-%m-%d')
        else:
            dt = datetime.strptime(d_str, '%Y%m%d')
        monday = dt - timedelta(days=dt.weekday())
        wk = monday.strftime('%Y-%m-%d')
        if wk not in weeks:
            weeks[wk] = {
                'date': wk, 'open': k['open'], 'high': k['high'],
                'low': k['low'], 'close': k['close'], 'volume': k['volume'],
            }
        else:
            w = weeks[wk]
            w['high'] = max(w['high'], k['high'])
            w['low'] = min(w['low'], k['low'])
            w['close'] = k['close']
            w['volume'] += k['volume']
    return sorted(weeks.values(), key=lambda x: x['date'])


def _parse_date(d) -> datetime:
    """统一日期解析"""
    if isinstance(d, datetime):
        return d
    s = str(d)
    if '-' in s:
        return datetime.strptime(s, '%Y-%m-%d')
    return datetime.strptime(s, '%Y%m%d')


# ══════════════════════════════════════════════════════════
# 峰谷检测（通用，含突出度过滤）
# ══════════════════════════════════════════════════════════

def find_peaks_troughs(
    df: List[Dict],
    window: int = 5,
    prominence: float = 0.03,
) -> Tuple[List[Dict], List[Dict]]:
    """
    识别局部高点和低点，带突出度过滤。

    Args:
        df: 日线数据列表，每项含 high, low, date
        window: 前后比较窗口天数
        prominence: 峰值最小突出度（0.03 = 3%）

    Returns:
        (peaks, troughs) — 各有 idx, price, date 字段
    """
    highs = [r['high'] for r in df]
    lows = [r['low'] for r in df]
    dates = [r['date'] for r in df]
    n = len(df)

    if n < 2 * window + 1:
        return [], []

    # 粗筛：找出所有局部极值点
    raw_peaks = []
    raw_troughs = []
    for i in range(window, n - window):
        w_high = max(highs[i - window:i + window + 1])
        if highs[i] >= w_high:
            raw_peaks.append({'idx': i, 'price': float(highs[i]), 'date': dates[i]})
        w_low = min(lows[i - window:i + window + 1])
        if lows[i] <= w_low:
            raw_troughs.append({'idx': i, 'price': float(lows[i]), 'date': dates[i]})

    if not raw_peaks and not raw_troughs:
        return [], []

    # 突出度过滤：峰必须比相邻谷高出 prominence 比例
    peaks = []
    for p in raw_peaks:
        left_troughs = [t for t in raw_troughs if t['idx'] < p['idx']]
        right_troughs = [t for t in raw_troughs if t['idx'] > p['idx']]
        nearest_price = None
        if left_troughs:
            nearest_price = left_troughs[-1]['price']
        if right_troughs and (nearest_price is None or right_troughs[0]['price'] > nearest_price):
            nearest_price = right_troughs[0]['price']
        if nearest_price is not None and (p['price'] - nearest_price) / nearest_price >= prominence:
            peaks.append(p)

    troughs = []
    for t in raw_troughs:
        left_peaks = [p for p in raw_peaks if p['idx'] < t['idx']]
        right_peaks = [p for p in raw_peaks if p['idx'] > t['idx']]
        nearest_price = None
        if left_peaks:
            nearest_price = left_peaks[-1]['price']
        if right_peaks and (nearest_price is None or right_peaks[0]['price'] < nearest_price):
            nearest_price = right_peaks[0]['price']
        if nearest_price is not None and (nearest_price - t['price']) / nearest_price >= prominence:
            troughs.append(t)

    return peaks, troughs


# ══════════════════════════════════════════════════════════
# 窗口内最高峰查找
# ══════════════════════════════════════════════════════════

def find_highest_peak_before(
    peaks: List[Dict], trough_idx: int, lookback_days: int = 60
) -> Optional[Dict]:
    """谷前窗口内取最高峰"""
    candidates = [p for p in peaks
                  if p['idx'] < trough_idx
                  and (trough_idx - p['idx']) <= lookback_days]
    return max(candidates, key=lambda x: x['price']) if candidates else None


def find_highest_peak_after(
    peaks: List[Dict], trough_idx: int, lookahead_days: int = 60
) -> Optional[Dict]:
    """谷后窗口内取最高峰"""
    candidates = [p for p in peaks
                  if p['idx'] > trough_idx
                  and (p['idx'] - trough_idx) <= lookahead_days]
    return max(candidates, key=lambda x: x['price']) if candidates else None


def find_highest_peak_between(
    peaks: List[Dict], left_idx: int, right_idx: int
) -> Optional[Dict]:
    """两谷之间最高峰"""
    candidates = [p for p in peaks if left_idx < p['idx'] < right_idx]
    return max(candidates, key=lambda x: x['price']) if candidates else None


# ══════════════════════════════════════════════════════════
# 上升背景检查（仅左顶）
# ══════════════════════════════════════════════════════════

def _has_uptrend_background(
    peak_idx: int,
    highs: List[float],
    lows: List[float],
    closes: List[float],
    dates: List,
    params: Dict,
) -> bool:
    """
    检查左顶的上升背景（仅用于左顶）。
    条件（满足任一即返回 True）：
      1. 方向性涨幅验证：60天内 low→high 方向性涨幅 ≥ 20%
      2. MA50 方向验证：近10天内曾向上
    """
    lookback = params['uptrend_lookback']
    min_gain = params['uptrend_min_gain']
    check_ma50 = params['uptrend_check_ma50']
    ma50_lookback = params['uptrend_ma50_lookback']

    start_idx = max(0, peak_idx - lookback)
    window_highs = highs[start_idx:peak_idx + 1]
    window_lows = lows[start_idx:peak_idx + 1]
    window_dates = dates[start_idx:peak_idx + 1]

    # === 条件1：方向性涨幅验证 ===
    high_val = float(max(window_highs))
    low_val = float(min(window_lows))
    # 找到高点和低点在窗口中的位置
    high_pos = window_highs.index(high_val)
    low_pos = window_lows.index(low_val)

    # 关键：高点必须在低点之后
    if high_pos > low_pos:
        directed_gain = (high_val - low_val) / low_val
        if directed_gain >= min_gain:
            return True

    # === 条件2：MA50 方向验证 ===
    if check_ma50 and peak_idx >= max(ma50_lookback, 50):
        ma50_now = _sma_at(closes, 50, peak_idx)
        ma50_before = _sma_at(closes, 50, peak_idx - ma50_lookback)
        if ma50_now > ma50_before:
            return True

    return False


# ══════════════════════════════════════════════════════════
# 确认判断
# ══════════════════════════════════════════════════════════

def _judge_confirmation(
    close_series: List[float],
    neckline: float,
    right_peak_price: float,
    as_of_idx: int,
    params: Dict,
) -> Tuple[str, Optional[str]]:
    """
    判断确认状态及信号级别。

    Returns:
        (status, signal_level)
        status: 'strong_confirmed' | 'weak_confirmed' | 'forming' | 'failed' | 'decayed' | 'dormant'
        signal_level: 'strong_sell' | 'moderate_sell' | 'warning' | None
    """
    neck_break_pct = params['dt_neck_break_pct']
    consecutive_days = params['dt_consecutive_days']
    max_valid_days = params['sig_max_valid_days']
    decay_weeks = params['sig_decay_weeks']

    current_close = close_series[as_of_idx]

    # 形态失败：价格突破右顶创新高
    if current_close > right_peak_price:
        return 'failed', None

    # 强确认：单日收盘跌破颈线 ≥ 3%
    if current_close <= neckline * (1 - neck_break_pct):
        return 'strong_confirmed', 'strong_sell'

    # 弱确认：连续N天收盘价低于颈线
    if as_of_idx >= consecutive_days - 1:
        last_n = close_series[as_of_idx - consecutive_days + 1:as_of_idx + 1]
        if all(c < neckline for c in last_n):
            return 'weak_confirmed', 'moderate_sell'

    # 正在形成：价格仍在颈线上方
    if current_close > neckline:
        return 'forming', 'warning'

    # 观察中：价格在颈线附近但未跌破
    return 'dormant', 'warning'


# ══════════════════════════════════════════════════════════
# 双重顶检测（深谷分隔法）
# ══════════════════════════════════════════════════════════

def _detect_double_top(
    peaks: List[Dict],
    troughs: List[Dict],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    dates: List,
    as_of_idx: int,
    params: Dict,
    stock_code: str = '',
) -> Optional[Dict]:
    """
    深谷分隔法检测双重顶。
    从后向前遍历深谷，在谷两侧窗口内取最高峰配对。
    """
    peak_window = params['peak_window_days']
    min_retrace = params['dt_min_retrace']
    max_price_diff = params['dt_max_price_diff']
    min_days_between = params['dt_min_days_between']

    # 从后向前遍历谷（优先最新形态）
    for trough in reversed(troughs):
        # 谷必须在 as_of_idx 之前
        if trough['idx'] >= as_of_idx:
            continue

        # === 按需动态计算 retrace_pct ===
        # 用该谷左侧 window 内的最高峰作为参照
        left_peak_candidate = find_highest_peak_before(
            peaks, trough['idx'], peak_window
        )
        if not left_peak_candidate:
            continue

        retrace_pct = (left_peak_candidate['price'] - trough['price']) / left_peak_candidate['price']
        if retrace_pct < min_retrace:
            continue

        # 在谷前窗口内找最高峰（左顶）
        left_peak = find_highest_peak_before(peaks, trough['idx'], peak_window)
        # 在谷后窗口内找最高峰（右顶）
        right_peak = find_highest_peak_after(peaks, trough['idx'], peak_window)

        if not (left_peak and right_peak):
            continue
        # 右顶必须在 as_of_idx 之前
        if right_peak['idx'] >= as_of_idx:
            continue

        # 验证左顶上升背景（右顶不验）
        if not _has_uptrend_background(
            left_peak['idx'], highs, lows, closes, dates, params
        ):
            continue

        # 验证两顶价格相近（≤ max_price_diff）
        price_diff = abs(right_peak['price'] - left_peak['price']) / left_peak['price']
        if price_diff > max_price_diff:
            continue

        # 验证两顶间隔 ≥ min_days_between
        d_left = _parse_date(left_peak['date'])
        d_right = _parse_date(right_peak['date'])
        if (d_right - d_left).days < min_days_between:
            continue

        # 颈线 = 深谷最低价
        neckline = trough['price']

        # 判断确认状态
        status, signal_level = _judge_confirmation(
            closes, neckline, right_peak['price'], as_of_idx, params
        )

        return {
            'type': 'bearish',
            'pattern': 'double_top',
            'left_peak': left_peak,
            'right_peak': right_peak,
            'trough': trough,
            'neckline': neckline,
            'retrace_pct': round(retrace_pct * 100, 1),
            'price_diff_pct': round(price_diff * 100,1),
            'status': status,
            'signal_level': signal_level,
            'stock_code': stock_code,
            'signal_date': str(dates[as_of_idx]),
            'type': 'bearish',
        }

    return None


# ══════════════════════════════════════════════════════════
# 三重顶检测
# ══════════════════════════════════════════════════════════

def _detect_triple_top(
    peaks: List[Dict],
    troughs: List[Dict],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    dates: List,
    as_of_idx: int,
    params: Dict,
    stock_code: str = '',
) -> Optional[Dict]:
    """
    三重顶检测：两个深谷 + 三个峰。
    """
    peak_window = params['peak_window_days']
    min_retrace = params['tt_min_retrace']
    max_price_diff = params['tt_max_price_diff']
    min_days_between = params['tt_min_days_between']
    min_trough_gap = params['tt_min_trough_gap']

    # 从后向前收集两个有效深谷
    valid_troughs = []
    for trough in reversed(troughs):
        if trough['idx'] >= as_of_idx:
            continue

        # 按需动态计算 retrace_pct
        left_candidate = find_highest_peak_before(peaks, trough['idx'], peak_window)
        if not left_candidate:
            continue

        retrace_pct = (left_candidate['price'] - trough['price']) / left_candidate['price']
        if retrace_pct >= min_retrace:
            valid_troughs.append(trough)
            if len(valid_troughs) >= 2:
                break

    if len(valid_troughs) < 2:
        return None

    # valid_troughs[0] = 右谷（较近），valid_troughs[1] = 左谷（较远）
    trough_right = valid_troughs[0]
    trough_left = valid_troughs[1]

    # 验证两谷间距 ≥ min_trough_gap
    d_tl = _parse_date(trough_left['date'])
    d_tr = _parse_date(trough_right['date'])
    if (d_tr - d_tl).days < min_trough_gap:
        return None

    # 找三个峰
    left_peak = find_highest_peak_before(peaks, trough_left['idx'], peak_window)
    middle_peak = find_highest_peak_between(peaks, trough_left['idx'], trough_right['idx'])
    right_peak = find_highest_peak_after(peaks, trough_right['idx'], peak_window)

    if not (left_peak and middle_peak and right_peak):
        return None
    if right_peak['idx'] >= as_of_idx:
        return None

    # 验证左顶上升背景（中顶、右顶不验）
    if not _has_uptrend_background(
        left_peak['idx'], highs, lows, closes, dates, params
    ):
        return None

    # 验证三个峰价格相近（≤ max_price_diff）
    peak_prices = [left_peak['price'], middle_peak['price'], right_peak['price']]
    max_p = max(peak_prices)
    min_p = min(peak_prices)
    price_diff = (max_p - min_p) / max_p
    if price_diff > max_price_diff:
        return None

    # 验证顶之间间隔
    d_lp = _parse_date(left_peak['date'])
    d_mp = _parse_date(middle_peak['date'])
    d_rp = _parse_date(right_peak['date'])
    if (d_mp - d_lp).days < min_days_between or (d_rp - d_mp).days < min_days_between:
        return None

    # 颈线 = 两个深谷的最高价（修正 2026-05-18）
    neckline = max(trough_left['price'], trough_right['price'])

    # 判断确认状态（用右顶判断失败）
    status, signal_level = _judge_confirmation(
        closes, neckline, right_peak['price'], as_of_idx, params
    )

    return {
        'type': 'bearish',
        'pattern': 'triple_top',
        'left_peak': left_peak,
        'middle_peak': middle_peak,
        'right_peak': right_peak,
        'trough_left': trough_left,
        'trough_right': trough_right,
        'neckline': neckline,
        'price_diff_pct': round(price_diff * 100, 1),
        'status': status,
        'signal_level': signal_level,
        'stock_code': stock_code,
        'signal_date': str(dates[as_of_idx]),
    }


# ══════════════════════════════════════════════════════════
# 头肩顶（预留）
# ══════════════════════════════════════════════════════════

def _detect_head_shoulders(
    peaks, troughs, highs, lows, closes, volumes, dates, as_of_idx, params, stock_code=''
) -> Optional[Dict]:
    """
    头肩顶检测（2026-05-18 实现）。
    
    深谷分隔法增强版：从后向前找2个有效深谷 → 配对三峰 → 验证头肩关系。
    """
    peak_window = params['peak_window_days']
    
    # 获取头肩顶参数（根据 freq 自动选 daily/weekly）
    freq = params.get('_freq', 'D')
    hs_key = 'weekly' if freq == 'W' else 'daily'
    min_retrace = params.get(f'hs_{hs_key}_min_retracement', 0.10)
    min_distance = params.get(f'hs_{hs_key}_min_distance', 5)
    max_shoulder_diff = params.get(f'hs_{hs_key}_max_shoulder_diff', 0.05)
    head_above_ratio = params.get(f'hs_{hs_key}_head_above_ratio', 0.03)
    vol_shrink_ratio = params.get(f'hs_{hs_key}_volume_shrink_ratio', 0.80)
    vol_window = params.get(f'hs_{hs_key}_volume_window', 5)
    shoulder_range = params.get(f'hs_{hs_key}_shoulder_day_range', 5)
    
    # 从后向前收集两个有效深谷
    valid_troughs = []
    for trough in reversed(troughs):
        if trough['idx'] >= as_of_idx:
            continue
        
        left_candidate = find_highest_peak_before(peaks, trough['idx'], peak_window)
        if not left_candidate:
            continue
        
        retrace_pct = (left_candidate['price'] - trough['price']) / left_candidate['price']
        if retrace_pct >= min_retrace:
            valid_troughs.append(trough)
            if len(valid_troughs) >= 2:
                break
    
    if len(valid_troughs) < 2:
        return None
    
    # 谷2（较近）= 头部回调低点，谷1（较远）= 左肩回调低点
    trough2 = valid_troughs[0]
    trough1 = valid_troughs[1]
    
    # 找三峰
    left_shoulder = find_highest_peak_before(peaks, trough1['idx'], peak_window)
    head = find_highest_peak_between(peaks, trough1['idx'], trough2['idx'])
    right_shoulder = find_highest_peak_after(peaks, trough2['idx'], shoulder_range)
    
    if not (left_shoulder and head and right_shoulder):
        return None
    if right_shoulder['idx'] >= as_of_idx:
        return None
    
    # 验证：头 > 两肩 × (1 + head_above_ratio)
    head_min_left = left_shoulder['price'] * (1 + head_above_ratio)
    head_min_right = right_shoulder['price'] * (1 + head_above_ratio)
    if head['price'] <= head_min_left or head['price'] <= head_min_right:
        return None
    
    # 验证：左右肩价差 ≤ max_shoulder_diff
    higher_shoulder = max(left_shoulder['price'], right_shoulder['price'])
    lower_shoulder = min(left_shoulder['price'], right_shoulder['price'])
    shoulder_diff = (higher_shoulder - lower_shoulder) / higher_shoulder
    if shoulder_diff > max_shoulder_diff:
        return None
    
    # 验证：左肩上升背景
    if not _has_uptrend_background(
        left_shoulder['idx'], highs, lows, closes, dates, params
    ):
        return None
    
    # 验证：成交量萎缩（右肩均量 < 左肩均量 × vol_shrink_ratio）
    n = len(volumes)
    vw = vol_window
    ls_start = max(0, left_shoulder['idx'] - vw)
    ls_end = min(n - 1, left_shoulder['idx'] + vw)
    ls_vols = volumes[ls_start:ls_end + 1]
    left_vol_ma = sum(ls_vols) / max(len(ls_vols), 1)
    rs_start = max(0, right_shoulder['idx'] - vw)
    rs_end = min(n - 1, right_shoulder['idx'] + vw)
    rs_vols = volumes[rs_start:rs_end + 1]
    right_vol_ma = sum(rs_vols) / max(len(rs_vols), 1)
    
    vol_ratio = right_vol_ma / left_vol_ma if left_vol_ma > 0 else 1.0
    if vol_ratio > vol_shrink_ratio:
        return None
    
    # 颈线 = 两个谷的最高价（头部回调低点通常更高）
    neckline = max(trough1['price'], trough2['price'])
    
    # 判断确认状态
    status, signal_level = _judge_confirmation(
        closes, neckline, right_shoulder['price'], as_of_idx, params
    )
    
    return {
        'type': 'bearish',
        'pattern': 'head_shoulders',
        'left_peak': left_shoulder,
        'right_peak': right_shoulder,
        'head_peak': head,
        'trough_left': trough1,
        'trough_right': trough2,
        'neckline': neckline,
        'vol_ratio': round(vol_ratio, 3),
        'shoulder_diff_pct': round(shoulder_diff * 100, 1),
        'price_diff_pct': round((head['price'] - lower_shoulder) / lower_shoulder * 100, 1),
        'status': status,
        'signal_level': signal_level,
        'stock_code': stock_code,
        'signal_date': str(dates[as_of_idx]),
    }


# ══════════════════════════════════════════════════════════
# 主检测入口
# ══════════════════════════════════════════════════════════

def detect(
    daily: List[Dict],
    params: Optional[Dict] = None,
    stock_code: str = '',
    freq: str = 'D',
) -> List[Dict]:
    """
    检测头部形态（头肩顶/三重顶/双重顶）。

    Args:
        daily: 日线数据列表，每项含 date, open, high, low, close, volume
        params: 参数字典，None 时自动加载
        stock_code: 股票代码
        freq: 'D' 日线 / 'W' 周线

    Returns:
        信号列表，每项含 pattern, status, signal_level, neckline 等
    """
    if params is None:
        params = load_params()
    params['_freq'] = freq

    # 周线重采样
    if freq == 'W':
        daily = _aggr_weekly(daily)

    n = len(daily)
    min_days = params['min_lookback_days']
    if n < min_days:
        return []

    highs = [r['high'] for r in daily]
    lows = [r['low'] for r in daily]
    closes = [r['close'] for r in daily]
    volumes = [r['volume'] for r in daily]
    dates = [r['date'] for r in daily]

    # 1. 峰谷检测（优先缠论 bi，降级窗口法）
    peaks, troughs = [], []
    bi_used = False

    if stock_code:
        try:
            from .chanlun_structure import get_bi_list, get_bi_peaks, get_bi_troughs
            bi_list = get_bi_list(stock_code)
            if bi_list:
                bi_peaks = get_bi_peaks(bi_list)
                bi_troughs = get_bi_troughs(bi_list)
                date_to_idx = {d: i for i, d in enumerate(dates)}

                for p in bi_peaks:
                    idx = date_to_idx.get(p['date'])
                    if idx is not None and idx < n:
                        peaks.append({'idx': idx, 'price': p['price'], 'date': p['date']})
                for t in bi_troughs:
                    idx = date_to_idx.get(t['date'])
                    if idx is not None and idx < n:
                        troughs.append({'idx': idx, 'price': t['price'], 'date': t['date']})

                if len(peaks) >= 2:
                    bi_used = True
        except Exception:
            pass  # 降级到窗口法

    if not bi_used:
        peaks, troughs = find_peaks_troughs(
            daily,
            window=params['peak_detection_window'],
            prominence=params['peak_prominence'],
        )
    if len(peaks) < 2:
        return []

    as_of_idx = n - 1

    # 2. 头肩顶检测（优先级最高）
    result = _detect_head_shoulders(
        peaks, troughs, highs, lows, closes, volumes, dates,
        as_of_idx, params, stock_code
    )
    if result:
        return [result]

    # 3. 三重顶检测
    result = _detect_triple_top(
        peaks, troughs, highs, lows, closes, dates,
        as_of_idx, params, stock_code
    )
    if result:
        return [result]

    # 4. 双重顶检测
    result = _detect_double_top(
        peaks, troughs, highs, lows, closes, dates,
        as_of_idx, params, stock_code
    )
    if result:
        return [result]

    return []


# ══════════════════════════════════════════════════════════
# 综合检测（给 server API 用）
# ══════════════════════════════════════════════════════════

def detect_all(
    daily: List[Dict],
    params: Optional[Dict] = None,
    freq: str = 'D',
    stock_code: str = '',
) -> Dict:
    """
    综合检测，返回所有信号 + 峰谷数据供调试。

    Returns:
        {
            'daily': [...],
            'signals': [...],
            'peaks': [...],
            'troughs': [...],
            'stock_code': str,
        }
    """
    if params is None:
        params = load_params()

    signals = detect(daily, params, stock_code, freq)

    # 峰谷数据（与 detect() 保持一致：优先 bi，降级窗口法）
    peaks, troughs = [], []
    if stock_code:
        try:
            from .chanlun_structure import get_bi_list, get_bi_peaks, get_bi_troughs
            bi_list = get_bi_list(stock_code)
            if bi_list:
                dates = [r['date'] for r in daily]
                date_to_idx = {d: i for i, d in enumerate(dates)}
                n = len(daily)
                for p in get_bi_peaks(bi_list):
                    idx = date_to_idx.get(p['date'])
                    if idx is not None and idx < n:
                        peaks.append({'idx': idx, 'price': p['price'], 'date': p['date']})
                for t in get_bi_troughs(bi_list):
                    idx = date_to_idx.get(t['date'])
                    if idx is not None and idx < n:
                        troughs.append({'idx': idx, 'price': t['price'], 'date': t['date']})
        except Exception:
            pass

    if not peaks:
        peaks, troughs = find_peaks_troughs(
            daily,
            window=params['peak_detection_window'],
            prominence=params['peak_prominence'],
        )

    return {
        'daily': daily,
        'signals': signals,
        'peaks': peaks,
        'troughs': troughs,
        'stock_code': stock_code,
    }


# ══════════════════════════════════════════════════════════
# 诊断信息（给 /api/top-pattern/diag 用）
# ══════════════════════════════════════════════════════════

def get_diag(daily: List[Dict], params: Optional[Dict] = None, stock_code: str = '') -> Dict:
    """返回检测诊断信息，含峰谷列表和匹配条件"""
    if params is None:
        params = load_params()

    n = len(daily)
    highs = [r['high'] for r in daily]
    lows = [r['low'] for r in daily]
    closes = [r['close'] for r in daily]
    dates = [r['date'] for r in daily]

    peaks, troughs = find_peaks_troughs(
        daily,
        window=params['peak_detection_window'],
        prominence=params['peak_prominence'],
    )

    result = detect_all(daily, params, stock_code)

    diag_info = {
        'date': dates[-1] if dates else '',
        'stock': stock_code,
        'total_kline': n,
        'peaks_count': len(peaks),
        'troughs_count': len(troughs),
        'signals_count': len(result['signals']),
        'peaks': [{'date': p['date'], 'price': p['price']} for p in peaks[-10:]],
        'troughs': [{'date': t['date'], 'price': t['price']} for t in troughs[-10:]],
    }

    if result['signals']:
        s = result['signals'][0]
        diag_info.update({
            'pattern': s['pattern'],
            'status': s['status'],
            'signal_level': s['signal_level'],
            'neckline': s['neckline'],
        })

    return diag_info


# ══════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='头部形态检测')
    parser.add_argument('--stock', type=str, default='600519')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    klines = conn.execute("""SELECT date, open, high, low, close, volume FROM daily_kline
        WHERE stock_code=? AND date<=? AND date>=date(?, '-600 days')
        ORDER BY date""", (args.stock, args.date, args.date)).fetchall()
    conn.close()

    if len(klines) < 60:
        print(f"K线不足: {len(klines)} 条 (需要 ≥ 60)")
        sys.exit(1)

    daily = [dict(r) for r in klines]
    result = detect_all(daily, stock_code=args.stock)

    print(f"🔍 {args.stock} @ {args.date}")
    print(f"   峰: {len(result['peaks'])}  谷: {len(result['troughs'])}")
    for s in result['signals']:
        icon = {'strong_confirmed': '🔴', 'weak_confirmed': '🟡', 'forming': '⚠️'}.get(s['status'], '❓')
        print(f"   {icon} {s['pattern']} | {s['status']} | 颈线={s['neckline']:.2f}")

    if not result['signals']:
        print(f"   无有效头部形态")
