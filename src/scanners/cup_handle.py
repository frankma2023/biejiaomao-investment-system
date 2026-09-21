"""
杯柄形态（Cup with Handle）识别引擎 v1.0

O'Neil 经典中轴买点。形态切割法定位前高（与碟形基部 v3.0 一致）。
对齐 product/杯柄形态突破检测引擎_产品需求书.md v1.0

检测流程:
  周线: 形态切割法找前高+杯底
  日线: 杯身验证 → 柄部检测 → 突破验证 → 假突破排除 → RS验证
"""

import sys, os, argparse, sqlite3, yaml, math
from datetime import datetime, date as dt_date
from typing import Optional, Dict, List, Tuple

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "cup_handle",
    "display_name": "杯柄形态",
    "category": "pattern",
    "version": "1.0",
    "description": "识别欧奈尔经典杯柄形态突破买点，形态切割法定位前高"
}


def load_params():
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "cup_handle.yaml")
    defaults = {
        'lookback': 120, 'prior_high_mode': 'cutting', 'min_prior_advance': 0.30,
        'cup_min_age': 35, 'cup_max_age': 325,
        'min_descent_days': 10, 'min_ascent_days': 10, 'min_market_cap': 0,
        'cut_pct': 0.20, 'cut_check_A_pct': 0.02, 'local_extreme_window': 13,
        'cup_drawdown_min': 0.12, 'cup_drawdown_max': 0.33,
        'cup_bottom_check': True, 'cup_bottom_flatness': 0.08,
        'cup_recovery': 0.90,
        'ascent_descent_check': True, 'ascent_descent_ratio': 0.50,
        'vol_bottom_max': 0.60, 'vol_contraction': 0.65,
        'handle_required': True, 'handle_min_days': 5, 'handle_max_days': 30,
        'handle_max_drawdown': 0.12, 'handle_position_ratio': 0.50,
        'handle_vol_ratio': 0.50,
        'breakout_buffer': 0.01, 'breakout_vol_ratio': 1.4,
        'require_green': True, 'close_position_min': 0.50,
        'fake_breakout_lookback': 5,
        'rs_required': False, 'rs_threshold': 80,
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        defaults.update(cfg.get('cup_handle', {}))
    return defaults


# ─── 工具函数 ──────────────────────────────────────────

def _sma(arr: List[float], n: int) -> float:
    if len(arr) < n: return sum(arr) / max(len(arr), 1)
    return sum(arr[-n:]) / n

def _sma_before(arr: List[float], n: int, idx: int) -> float:
    start = max(0, idx - n)
    vals = arr[start:idx]
    return sum(vals) / max(len(vals), 1)

def _linear_slope(y: List[float]) -> float:
    n = len(y)
    if n < 2: return 0
    xs = list(range(n))
    xm = (n - 1) / 2; ym = sum(y) / n
    num = sum((xs[i] - xm) * (y[i] - ym) for i in range(n))
    den = sum((xs[i] - xm) ** 2 for i in range(n))
    return num / den if den else 0

def _slice(daily: List[Dict], start: str, end: str) -> List[Dict]:
    return [k for k in daily if start <= k['date'] <= end]

def _weekly_returns(closes: List[float]) -> List[float]:
    if len(closes) < 6: return []
    return [(closes[i] - closes[i-5]) / closes[i-5]
            for i in range(5, len(closes), 5) if closes[i-5] > 0]

def _aggregate_weekly(daily: List[Dict]) -> List[Dict]:
    """日K线聚合为周K线"""
    if not daily: return []
    result, cur_wk, wk = [], None, None
    for row in daily:
        d = row['date']
        if isinstance(d, str):
            dt = datetime.strptime(d, '%Y-%m-%d').date()
        else:
            dt = d
        key = (dt.isocalendar()[0], dt.isocalendar()[1])
        if key != cur_wk:
            if wk: result.append(wk)
            cur_wk = key
            wk = {'date': dt.strftime('%Y-%m-%d'), 'open': row['open'], 'high': row['high'],
                  'low': row['low'], 'close': row['close'], 'volume': row['volume']}
        else:
            wk['high'] = max(wk['high'], row['high'])
            wk['low'] = min(wk['low'], row['low'])
            wk['close'] = row['close']
            wk['volume'] += row['volume']
            wk['date'] = dt.strftime('%Y-%m-%d')
    if wk: result.append(wk)
    return result

def _is_local_high(values: List[float], idx: int, window: int = 13) -> bool:
    """判断 idx 是否在 ±window 范围内为局部高点"""
    lo, hi = max(0, idx - window), min(len(values) - 1, idx + window)
    peak = values[idx]
    for i in range(lo, hi + 1):
        if values[i] > peak: return False
    return True


# ─── 形态切割法：定位前高和杯底（周线） ──────────────────

def _find_prior_high_and_bottom_cutting(
    weekly: List[Dict], daily: List[Dict], t_date: str, params: Dict
) -> Optional[Dict]:
    """
    形态切割法定位前高和杯底（周线）。
    步骤: 找杯底 → 逆向爬坡 → 遇局部高点执行三切割 → 确定前高

    切割A: candidate ≤ 检测日×(1+cut_check_A_pct)，太高=旧周期
    切割B: 从candidate前方找≥cut_pct大波动，找到=确认前高
    切割C: 回调∈[cup_drawdown_min, cup_drawdown_max]
    """
    cup_min_age = params['cup_min_age']
    cup_max_age = params['cup_max_age']
    min_descent = params['min_descent_days']
    min_ascent = params['min_ascent_days']
    dd_min = params['cup_drawdown_min']
    dd_max = params['cup_drawdown_max']
    cut_pct = params['cut_pct']
    cut_A = params['cut_check_A_pct']
    local_window = params.get('local_extreme_window', 13)
    
    # 找到检测日在周线中的位置
    t_week_idx = len(weekly) - 1
    for wi, w in enumerate(weekly):
        if w['date'] >= t_date:
            t_week_idx = wi
            break
    
    weekly_closes = [w['close'] for w in weekly]
    weekly_dates = [w['date'] for w in weekly]
    
    max_scan_w = min(t_week_idx, cup_max_age // 5)  # 约65周
    min_scan_w = max(2, cup_min_age // 5)            # 约7周
    
    # 1. 找杯底: [t-max_scan, t-min_scan] 内最低周收盘
    bottom_price = float('inf')
    bottom_week_idx = None
    search_start = max(0, t_week_idx - max_scan_w)
    search_end = t_week_idx - min_scan_w
    
    for i in range(search_start, search_end + 1):
        if i >= 0 and i < len(weekly_closes):
            if weekly_closes[i] < bottom_price:
                bottom_price = weekly_closes[i]
                bottom_week_idx = i
    
    if bottom_week_idx is None or bottom_price <= 0:
        return None
    
    # 碟底到t涨幅不能太大
    t_close = daily[-1]['close']  # 近似
    if (t_close - bottom_price) / bottom_price > dd_max:
        return None
    
    # 2. 从杯底逆向爬坡找前高
    candidate_price = bottom_price
    candidate_idx = bottom_week_idx
    prior_high = None
    prior_high_idx = None
    prior_high_date = None
    
    max_back = max(bottom_week_idx - 52, 0)  # 最多回溯52周
    
    for i in range(bottom_week_idx - 1, max_back - 1, -1):
        week_close = weekly_closes[i]
        
        # a) 更新candidate
        if week_close > candidate_price:
            candidate_price = week_close
            candidate_idx = i
        
        # b) 遇到局部高点 → 三切割
        if _is_local_high(weekly_closes, i, local_window):
            # 切割A
            if candidate_price > t_close * (1 + cut_A):
                continue
            
            # 切割B: 从candidate前方找≥cut_pct大波动
            found_move = False
            for j in range(candidate_idx - 1, max(candidate_idx - 52, 0) - 1, -1):
                jc = weekly_closes[j]
                if min(jc, candidate_price) > 0:
                    move = abs(jc - candidate_price) / min(jc, candidate_price)
                    if move >= cut_pct:
                        found_move = True
                        break
            
            if not found_move:
                continue
            
            # 切割C: 回调深度
            if candidate_price > 0:
                drawdown = (candidate_price - bottom_price) / candidate_price
                if dd_min <= drawdown <= dd_max:
                    prior_high = candidate_price
                    prior_high_idx = candidate_idx
                    prior_high_date = weekly_dates[candidate_idx]
                    break
        
        # 兜底：回溯到底
        if i <= max_back + 1:
            if prior_high is None and candidate_price > bottom_price:
                drawdown = (candidate_price - bottom_price) / candidate_price
                if dd_min <= drawdown <= dd_max:
                    prior_high = candidate_price
                    prior_high_idx = candidate_idx
                    prior_high_date = weekly_dates[candidate_idx]
    
    if prior_high is None:
        return None
    
    # 距离校验
    descent_weeks = bottom_week_idx - prior_high_idx
    if descent_weeks < min_descent // 5:
        return None
    
    ascent_weeks = t_week_idx - bottom_week_idx
    if ascent_weeks < min_ascent // 5:
        return None
    
    # 日线索引映射
    bottom_date = weekly_dates[bottom_week_idx]
    t_row = daily[-1]
    
    # 在日线中找到杯底和前高的精确位置
    bottom_daily_idx = next((i for i, k in enumerate(daily)
        if k['date'] >= bottom_date), len(daily) - 1)
    prior_daily_idx = next((i for i, k in enumerate(daily)
        if k['date'] >= prior_high_date), 0)
    
    return {
        'prior_high': prior_high, 'prior_idx': prior_daily_idx,
        'prior_high_date': prior_high_date,
        'bottom': bottom_price, 'bottom_idx': bottom_daily_idx,
        'bottom_date': bottom_date,
        'drawdown': (prior_high - bottom_price) / prior_high if prior_high > 0 else 0,
        'descent_days': bottom_daily_idx - prior_daily_idx,
        'cup_weeks': t_week_idx - prior_high_idx,
    }


# ─── simple 模式：前高定位（备用）─────────────────────

def _find_prior_high_and_bottom_simple(
    weekly: List[Dict], daily: List[Dict], t_date: str, params: Dict
) -> Optional[Dict]:
    """直接取 lookback 内最高收盘价作为前高（快速模式）"""
    cup_min_age = params['cup_min_age']
    cup_max_age = params['cup_max_age']
    dd_min = params['cup_drawdown_min']
    dd_max = params['cup_drawdown_max']
    
    t_week_idx = len(weekly) - 1
    for wi, w in enumerate(weekly):
        if w['date'] >= t_date: t_week_idx = wi; break
    
    max_scan_w = min(t_week_idx, cup_max_age // 5)
    min_scan_w = max(2, cup_min_age // 5)
    search_start = max(0, t_week_idx - max_scan_w)
    search_end = t_week_idx - min_scan_w
    
    # 取 lookback 内最高收盘
    prior_high = 0; prior_week_idx = None
    for i in range(search_start, search_end + 1):
        if weekly[i]['close'] > prior_high:
            prior_high = weekly[i]['close']; prior_week_idx = i
    if prior_week_idx is None: return None
    
    # 在前高之后找最低
    bottom_price = float('inf'); bottom_week_idx = None
    for i in range(prior_week_idx + 1, t_week_idx + 1):
        if weekly[i]['close'] < bottom_price:
            bottom_price = weekly[i]['close']; bottom_week_idx = i
    if bottom_week_idx is None: return None
    
    dd = (prior_high - bottom_price) / prior_high if prior_high > 0 else 0
    if not (dd_min <= dd <= dd_max): return None
    
    return {
        'prior_high': prior_high,
        'prior_idx': next((i for i, k in enumerate(daily) if k['date'] >= weekly[prior_week_idx]['date']), 0),
        'prior_high_date': weekly[prior_week_idx]['date'],
        'bottom': bottom_price,
        'bottom_idx': next((i for i, k in enumerate(daily) if k['date'] >= weekly[bottom_week_idx]['date']), len(daily)-1),
        'bottom_date': weekly[bottom_week_idx]['date'],
        'drawdown': dd,
        'descent_days': (bottom_week_idx - prior_week_idx) * 5,
        'cup_weeks': t_week_idx - prior_week_idx,
    }

# ─── 3.1 前置上涨验证 ────────────────────────────────

def _check_prior_advance(
    daily: List[Dict], prior_high_date: str, prior_high: float, params: Dict
) -> float:
    """返回前置涨幅%，<min_prior_advance 则排除"""
    lookback = params.get('prior_advance_lookback', params['lookback'])
    min_adv = params['min_prior_advance']
    ph_idx = next((i for i, k in enumerate(daily) if k['date'] >= prior_high_date), None)
    if ph_idx is None or ph_idx < 10: return 0
    low = min(k['close'] for k in daily[max(0, ph_idx - lookback):ph_idx])
    return (prior_high - low) / low if low > 0 else 0


# ─── 3.2.2 杯身回升验证 ──────────────────────────────

def _check_recovery(daily: List[Dict], cup: Dict, t_idx: int, params: Dict) -> bool:
    prior_high = cup['prior_high']
    bottom_idx = cup['bottom_idx']
    recovery = params['cup_recovery']
    
    max_after = max(k['close'] for k in daily[bottom_idx:t_idx + 1])
    if max_after < prior_high * recovery:
        return False
    
    ascent = daily[bottom_idx:t_idx + 1]
    asc_closes = [k['close'] for k in ascent]
    if _linear_slope(asc_closes) <= 0:
        return False
    
    for r in _weekly_returns(asc_closes):
        if r > 0.20: return False
    
    if params.get('ascent_descent_check', True):
        d_days = cup['descent_days']
        a_days = t_idx - bottom_idx
        if d_days > 0 and a_days < d_days * params['ascent_descent_ratio']:
            return False
    
    return True


# ─── 3.2.3 成交量验证 ────────────────────────────────

def _check_volume(daily: List[Dict], cup: Dict, t_idx: int, params: Dict):
    bottom_idx = cup['bottom_idx']; prior_idx = cup['prior_idx']
    vb_max = params['vol_bottom_max']; vc_max = params['vol_contraction']
    
    sma50v = _sma_before([k['volume'] for k in daily], 50, t_idx)
    if sma50v <= 0: return None
    
    # 3.1 杯底量萎缩
    b_vols = [daily[i]['volume'] for i in range(max(0, bottom_idx - 5), min(len(daily), bottom_idx + 6))]
    if b_vols:
        avg_b = sum(b_vols) / len(b_vols)
        if avg_b / sma50v > vb_max: return None
    vb_r = avg_b / sma50v if b_vols and sma50v > 0 else 1
    
    # 3.2 下行缩量
    descent = daily[prior_idx:bottom_idx + 1]
    if len(descent) >= 4:
        mid = len(descent) // 2
        fv = sum(k['volume'] for k in descent[:mid]) / mid
        sv = sum(k['volume'] for k in descent[mid:]) / (len(descent) - mid)
        if fv > 0 and sv / fv > vc_max: return None
        vc = sv / fv if fv > 0 else 1
    else:
        vc = 1
    
    # 3.3 回升放量
    ascent = daily[bottom_idx:t_idx + 1]
    d2 = daily[prior_idx + len(descent)//2:bottom_idx + 1]
    aa = sum(k['volume'] for k in ascent) / max(len(ascent), 1)
    d2a = sum(k['volume'] for k in d2) / max(len(d2), 1)
    if d2a > 0 and aa < d2a: return None
    
    return (vb_r, vc)


# ─── 3.3 柄部检测 ────────────────────────────────────

def _find_handle(daily: List[Dict], cup: Dict, t_idx: int, params: Dict) -> Optional[Dict]:
    prior_high = cup['prior_high']; bottom = cup['bottom']
    bottom_idx = cup['bottom_idx']
    h_min, h_max = params['handle_min_days'], params['handle_max_days']
    h_dd_max = params['handle_max_drawdown']
    h_pos = params['handle_position_ratio']
    h_vol_r = params['handle_vol_ratio']
    
    ascent = daily[bottom_idx:t_idx + 1]
    cup_mouth_price = max(k['close'] for k in ascent)
    if cup_mouth_price < prior_high * params['cup_recovery']: return None
    
    cup_mouth_idx = None
    for i in range(len(ascent) - 1, -1, -1):
        if ascent[i]['close'] >= cup_mouth_price:
            cup_mouth_idx = bottom_idx + i; break
    if cup_mouth_idx is None or t_idx - cup_mouth_idx < h_min: return None
    
    handle_seg = daily[cup_mouth_idx:t_idx + 1]
    if len(handle_seg) < h_min or len(handle_seg) > h_max: return None
    
    handle_high = cup_mouth_price
    handle_low = min(k['close'] for k in handle_seg)
    h_dd = (handle_high - handle_low) / handle_high
    if h_dd > h_dd_max: return None
    
    if handle_low < bottom + (prior_high - bottom) * h_pos: return None
    
    h_avg_v = sum(k['volume'] for k in handle_seg) / len(handle_seg)
    cup_body = daily[cup['prior_idx']:t_idx + 1]
    cup_avg_v = sum(k['volume'] for k in cup_body) / max(len(cup_body), 1)
    if cup_avg_v > 0 and h_avg_v / cup_avg_v > h_vol_r: return None
    
    if _linear_slope([k['close'] for k in handle_seg]) > 0.001: return None
    
    sma50c = _sma([k['close'] for k in daily[:t_idx+1]], 50)
    if sma50c > 0 and handle_low < sma50c: return None
    
    handle_low_idx = cup_mouth_idx + next(i for i, k in enumerate(handle_seg) if k['close'] <= handle_low * 1.001)
    
    return {
        'handle_high_price': handle_high, 'handle_low_price': handle_low,
        'handle_high_date': daily[cup_mouth_idx]['date'],
        'handle_low_date': daily[handle_low_idx]['date'],
        'handle_drawdown': h_dd, 'handle_days': len(handle_seg),
        'handle_vol_ratio': h_avg_v / cup_avg_v if cup_avg_v > 0 else 0,
    }


# ─── 3.4 突破验证 ────────────────────────────────────

def _check_breakout(daily: List[Dict], t_idx: int, cup: Dict,
                    handle: Optional[Dict], params: Dict) -> Optional[float]:
    t = daily[t_idx]; tc = t['close']; tv = t['volume']
    buf = params['breakout_buffer']; vr = params['breakout_vol_ratio']
    
    buy_pt = (handle['handle_high_price'] if handle else cup['prior_high']) + buf
    if tc < buy_pt: return None
    
    sma50v = _sma_before([k['volume'] for k in daily], 50, t_idx)
    if sma50v <= 0 or tv < sma50v * vr: return None
    
    if params.get('require_green', True) and tc <= t['open']: return None
    
    if t['high'] > t['low']:
        if (tc - t['low']) / (t['high'] - t['low']) < params['close_position_min']:
            return None
    
    sma50c = _sma([k['close'] for k in daily[:t_idx+1]], 50)
    if tc <= sma50c: return None
    if t_idx >= 150:
        sma150c = _sma([k['close'] for k in daily[:t_idx+1]], 150)
        if tc <= sma150c: return None
    
    return buy_pt


# ─── 3.5 假突破排除 ──────────────────────────────────

def _check_false_breakout(daily: List[Dict], t_idx: int, handle: Optional[Dict], params: Dict) -> bool:
    """返回True=假突破"""
    t = daily[t_idx]; lb = params['fake_breakout_lookback']
    sma50v = _sma_before([k['volume'] for k in daily], 50, t_idx)
    if t['volume'] < sma50v: return True
    
    for i in range(max(0, t_idx - lb), t_idx):
        r = daily[i]
        if r['close'] < r['open'] and r['volume'] > sma50v * 1.3: return True
    
    if handle:
        h_vol_ratio = handle.get('handle_vol_ratio', 0)
        if h_vol_ratio > params['handle_vol_ratio']:
            return True
    return False


# ─── 主检测 ──────────────────────────────────────────

def detect(
    daily: List[Dict],
    params: Optional[Dict] = None,
    market_cap: Optional[float] = None,
    rs_info: Optional[Dict] = None,
) -> List[Dict]:
    if params is None: params = load_params()
    n = len(daily)
    if n < params['lookback'] + 50: return []
    if market_cap and params['min_market_cap'] > 0 and market_cap < params['min_market_cap']:
        return []
    
    weekly = _aggregate_weekly(daily)
    signals = []
    
    for t_idx in range(params['lookback'] + 50, n):
        t_row = daily[t_idx]; t_date = t_row['date']
        
        # 0.2 SMA50趋势
        sma50c = _sma([k['close'] for k in daily[:t_idx+1]], 50)
        if t_row['close'] <= sma50c: continue
        
        # ── 找前高+杯底 ──
        mode = params.get('prior_high_mode', 'cutting')
        if mode == 'simple':
            cup = _find_prior_high_and_bottom_simple(weekly, daily[:t_idx+1], t_date, params)
        else:
            cup = _find_prior_high_and_bottom_cutting(weekly, daily[:t_idx+1], t_date, params)
        if cup is None: continue
        
        # ── 0.1 前置上涨 ──
        pa = _check_prior_advance(daily, cup['prior_high_date'], cup['prior_high'], params)
        if pa < params['min_prior_advance']: continue
        
        # ── 1.4 杯底平坦性 ──
        if params.get('cup_bottom_check', True):
            b_idx = cup['bottom_idx']
            b_start = max(0, b_idx - 5)
            b_end = min(len(daily), b_idx + 6)
            if b_start < b_end:
                b_zone = daily[b_start:b_end]
                z_hi = max(k['close'] for k in b_zone)
                z_lo = min(k['close'] for k in b_zone)
                if z_hi > 0 and (z_hi - z_lo) / z_hi > params['cup_bottom_flatness']:
                    continue  # 杯底太尖，排除
        
        # ── 杯身回升 ──
        if not _check_recovery(daily, cup, t_idx, params): continue
        
        # ── 成交量 ──
        vr = _check_volume(daily, cup, t_idx, params)
        if vr is None: continue
        vb_ratio, vc_val = vr
        
        # ── 柄部 ──
        handle = _find_handle(daily, cup, t_idx, params)
        if params.get('handle_required', True) and handle is None: continue
        
        # ── 突破 ──
        buy_pt = _check_breakout(daily, t_idx, cup, handle, params)
        if buy_pt is None: continue
        
        # ── 假突破 ──
        if _check_false_breakout(daily, t_idx, handle, params): continue
        
        # ── RS ──
        if params.get('rs_required', False) and rs_info:
            if not (rs_info.get('rs_20',0) >= params['rs_threshold'] or
                    rs_info.get('rs_60',0) >= params['rs_threshold'] or
                    rs_info.get('rs_250',0) >= params['rs_threshold']):
                continue
        
        # ── 输出 ──
        sma50v = _sma_before([k['volume'] for k in daily], 50, t_idx)
        has_h = handle is not None
        signal = {
            'signal_date': t_date,
            'pattern_type': 'cup_with_handle' if has_h else 'cup_no_handle',
            'prior_high_date': cup['prior_high_date'],
            'prior_high_price': round(float(cup['prior_high']), 2),
            'bottom_date': cup['bottom_date'],
            'bottom_price': round(float(cup['bottom']), 2),
            'drawdown_pct': round(cup['drawdown'] * 100, 1),
            'descent_days': cup['descent_days'],
            'ascent_days': t_idx - cup['bottom_idx'],
            'handle_high_date': handle['handle_high_date'] if has_h else None,
            'handle_high_price': round(float(handle['handle_high_price']), 2) if has_h else None,
            'handle_low_date': handle['handle_low_date'] if has_h else None,
            'handle_low_price': round(float(handle['handle_low_price']), 2) if has_h else None,
            'handle_drawdown_pct': round(handle['handle_drawdown'] * 100, 1) if has_h else None,
            'handle_vol_ratio': round(float(handle.get('handle_vol_ratio',0)), 3) if has_h else None,
            'buy_point': round(float(buy_pt), 2),
            'breakout_close': round(float(t_row['close']), 2),
            'breakout_vol_ratio': round(t_row['volume'] / sma50v, 2) if sma50v > 0 else 0,
            'breakout_chg_pct': round((t_row['close'] - t_row['open']) / t_row['open'] * 100, 2) if t_row['open'] > 0 else 0,
            'close_position': round((t_row['close'] - t_row['low']) / (t_row['high'] - t_row['low']), 2) if t_row['high'] > t_row['low'] else 1,
            'prior_advance_pct': round(pa * 100, 1),
            'close_vs_sma50': round(t_row['close'] / sma50c, 3) if sma50c > 0 else 0,
            'market_cap': round(float(market_cap), 1) if market_cap else None,
        }
        signals.append(signal)
    
    return signals


# ─── CLI ─────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='杯柄形态识别')
    parser.add_argument('--stock', type=str, default='600519')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    args = parser.parse_args()
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    klines = conn.execute("""
        SELECT date, open, high, low, close, volume FROM daily_kline_adj
        WHERE stock_code=? AND date<=? AND date>=date(?,'-600 days')
        ORDER BY date
    """, (args.stock, args.date, args.date)).fetchall()
    conn.close()
    
    if len(klines) < 170: print(f"K线不足: {len(klines)}"); sys.exit(1)
    
    daily = [dict(r) for r in klines]
    params = load_params()
    sigs = detect(daily, params)
    
    print(f"{args.stock} @ {args.date}")
    print(f"   杯柄形态突破信号: {len(sigs)}")
    for s in sigs:
        print(f"   {s['signal_date']} {s['pattern_type']} 买点={s['buy_point']}")
        print(f"      前高={s['prior_high_price']}({s['prior_high_date']}) 杯底={s['bottom_price']}({s['bottom_date']})")
        print(f"      回调={s['drawdown_pct']}% 下行={s['descent_days']}d 回升={s['ascent_days']}d")
        if s['handle_high_price']:
            print(f"      柄高={s['handle_high_price']} 柄低={s['handle_low_price']} 回撤={s['handle_drawdown_pct']}%")
