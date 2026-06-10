"""
基部突破识别引擎 V2 — 缠论H/L/C驱动

与 V1 的关键区别:
  1. H/L 定位: 缠论笔顶/笔底替代局部窗口法
  2. C 区间: 缠论定义（L后振幅<10%的横盘段）
  3. BO 单信号: 取代 V1 的自研基部检测
  4. 参数对齐欧奈尔经典规则 (深度8~40%, MA10>MA20, 量比≥1.5x)

用法:
  python base_breakout_v2.py --stock 600519 --date 2026-06-05
"""

import sys, os, json, argparse, sqlite3, yaml
from datetime import datetime, timedelta
from typing import Optional, Dict, List

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.dirname(os.path.abspath(__file__))  # src/scanners/
PARENT_SRC = os.path.dirname(SRC_DIR)  # src/
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, PARENT_SRC)  # 确保 from scanners.chanlun import 可解析
sys.path.insert(0, SRC_DIR)   # 确保 from chanlun import 可解析
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "base_breakout_v2",
    "display_name": "基部突破V2",
    "category": "breakout",
    "version": "2.0",
    "description": "缠论H/L/C驱动的通用基部突破检测，覆盖杯柄/碟形/双重底/平底/高紧旗等形态"
}

_chanlun_cache = {}


def load_params():
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "base_breakout_v2.yaml")
    defaults = {
        'drawdown_min': 0.08,        # 最小调整深度 8%
        'drawdown_max': 0.40,        # 最大调整深度 40%
        'min_c_days': 10,            # C 区间最少交易日（多周期扫描放宽至2周）
        'c_amp_max': 0.15,           # C 区间最大振幅
        'bo_gain_min': 0.03,         # 突破日最低涨幅 3%
        'bo_vol_ratio': 1.5,         # 突破日量 vs 20日均量
        'bo_close_pos_min': 0.50,    # 收盘在日内位置
        'require_ma60': True,         # 要求收盘 > MA60（欧奈尔50日均线，A股用60日）
        'rs_threshold': 80,          # RS 最低阈值
        'max_extension_pct': 0.30,    # 最大延伸比例（距MA10不超过30%）
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        defaults.update(cfg.get('base_breakout', {}))
    return defaults


def sma(values, n):
    clean = [v for v in values if v is not None]
    if len(clean) < n: return None
    return sum(clean[-n:]) / n


def _get_all_hlc_structures(klines, code):
    """从缠论 bi 峰谷提取所有历史 H-L-C 结构（多周期，不依赖 MW 信号表）"""
    n = len(klines)
    dates = [k['date'] for k in klines]
    structures = []

    from scanners.chanlun_structure import get_bi_list, get_bi_peaks
    bi_list = get_bi_list(code)
    if not bi_list:
        return []
    peaks = get_bi_peaks(bi_list)
    if len(peaks) < 2:
        return []

    for i in range(len(peaks) - 1):
        h_peak = peaks[i]
        if h_peak['date'] not in dates:
            continue
        h_idx = dates.index(h_peak['date'])
        if h_idx >= n - 20:
            break

        l_idx = h_idx + 1
        l_price = klines[l_idx]['close']
        for j in range(h_idx + 1, min(h_idx + 120, n)):
            if klines[j]['close'] < l_price:
                l_price = klines[j]['close']
                l_idx = j

        c_start_idx = l_idx
        c_end_idx = n - 1
        if i + 1 < len(peaks) and peaks[i + 1]['date'] in dates:
            c_end_idx = min(c_end_idx, dates.index(peaks[i + 1]['date']) - 1)

        decline_pct = round((h_peak['price'] - l_price) / h_peak['price'] * 100, 2)

        structures.append({
            'h_date': h_peak['date'], 'h_price': h_peak['price'], 'h_idx': h_idx,
            'l_date': dates[l_idx], 'l_price': l_price, 'l_idx': l_idx,
            'c_start_idx': c_start_idx, 'c_end_idx': c_end_idx,
            'c_start_date': dates[l_idx], 'c_end_date': dates[c_end_idx],
            'decline_pct': decline_pct
        })

    return structures


def detect(daily, params=None):
    """
    检测基部突破信号（缠论 H/L/C 结构驱动）。

    Args:
        daily: list[dict], OHLCV K线数据
        params: dict or None

    Returns:
        list[dict]: 信号列表
    """
    if params is None: params = load_params()
    
    # 从 K 线数据或 params 中提取 stock_code
    code = params.get('stock_code', '')
    if not code:
        for k in daily:
            if k.get('stock_code'):
                code = k['stock_code']
                break
    
    n = len(daily)
    if n < 120: return []
    
    # 获取所有历史 H/L/C 结构
    structures = _get_all_hlc_structures(daily, code)
    if not structures: return []
    
    # 参数提取
    dd_min = params.get('drawdown_min', 0.08)
    dd_max = params.get('drawdown_max', 0.40)
    min_c_days = params.get('min_c_days', 20)
    c_amp_max = params.get('c_amp_max', 0.15)
    bo_gain = params.get('bo_gain_min', 0.03)
    bo_vol = params.get('bo_vol_ratio', 1.5)
    bo_pos = params.get('bo_close_pos_min', 0.50)
    require_ma60 = params.get('require_ma60', True)
    rs_min = params.get('rs_threshold', 80)
    max_ext = params.get('max_extension_pct', 0.30)
    
    signals = []
    seen_dates = set()

    for hlc in structures:
        decline = hlc['decline_pct']
        if decline < dd_min * 100 or decline > dd_max * 100: continue
        c_days = hlc['c_end_idx'] - hlc['c_start_idx'] + 1
        if c_days < min_c_days: continue
        check_days = min(min_c_days * 2, c_days)
        c_closes = [daily[i]['close'] for i in range(hlc['c_start_idx'], hlc['c_start_idx'] + check_days)]
        if c_closes:
            c_amp = (max(c_closes) - min(c_closes)) / min(c_closes) if min(c_closes) > 0 else 999
            if c_amp > c_amp_max: continue
        else:
            c_amp = 0

        for t_idx in range(hlc['c_start_idx'], hlc['c_end_idx'] + 1):
            if t_idx == 0: continue
            k = daily[t_idx]
            if k['date'] in seen_dates: continue
            c, v, o, h, l = k['close'], k['volume'], k['open'], k['high'], k['low']
            if c <= 0 or v <= 0: continue
            gain = (c - daily[t_idx-1]['close']) / daily[t_idx-1]['close']
            if gain < bo_gain: continue
            vol_20 = [daily[j]['volume'] for j in range(max(0, t_idx-20), t_idx)]
            avg20 = sum(vol_20) / len(vol_20) if vol_20 else 0
            if avg20 <= 0 or v < avg20 * bo_vol: continue
            if h > l: pos = (c - l) / (h - l)
            else: pos = 0
            if pos < bo_pos: continue
            closes_all = [d['close'] for d in daily[:t_idx+1]]
            ma10 = sma(closes_all, 10); ma20 = sma(closes_all, 20); ma60 = sma(closes_all, 60)
            if ma10 and c <= ma10: continue
            if ma20 and c <= ma20: continue
            if require_ma60 and ma60 and c <= ma60: continue
            if ma10 and c > ma10 * (1 + max_ext): continue
            c_max = max(daily[i]['close'] for i in range(hlc['c_start_idx'], t_idx))
            if c <= c_max: continue
            prev_highs = [daily[j]['high'] for j in range(max(0, t_idx-10), t_idx)]
            if prev_highs and h < max(prev_highs): continue
            rps20 = k.get('rps_20', 0) or k.get('rps20', 0) or 0
            rps250 = k.get('rps_250', 0) or k.get('rps250', 0) or 0
            if rps20 > 0 and rps250 > 0:
                if rps20 < rs_min and rps250 < rs_min: continue
            seen_dates.add(k['date'])
            signals.append({
                'signal_date': k['date'],
                'prior_high_date': hlc['h_date'], 'prior_high_price': round(hlc['h_price'], 2),
                'trough_date': hlc['l_date'], 'trough_price': round(hlc['l_price'], 2),
                'drawdown_pct': hlc['decline_pct'], 'base_days': c_days,
                'c_amplitude': round(c_amp * 100, 1),
                'breakout_close': round(c, 2), 'breakout_gain_pct': round(gain * 100, 2),
                'breakout_vol_ratio': round(v / avg20, 2) if avg20 > 0 else 0,
                'close_position': round(pos, 2),
                'ma10': round(ma10, 2) if ma10 else None,
                'ma20': round(ma20, 2) if ma20 else None,
                'ma60': round(ma60, 2) if ma60 else None,
                'ma10_cross_ma20': (ma10 or 0) > (ma20 or 0),
                'rps_20': rps20 if rps20 > 0 else None,
                'rps_250': rps250 if rps250 > 0 else None,
                'buy_point': round(c_max + 0.01, 2),
            })
    
    return signals


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='基部突破检测 V2')
    parser.add_argument('--stock', type=str, default='600519')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--mode', type=str, default='stock')
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    table = 'index_daily_kline' if args.mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if args.mode == 'index' else ''
    rows = conn.execute(f"""
        SELECT date, open, high, low, close, volume, amount FROM {table}
        WHERE stock_code=? {kf} AND date<=? AND date>=date(?, '-500 days')
        ORDER BY date
    """, (args.stock, args.date, args.date)).fetchall()
    conn.close()

    if len(rows) < 120:
        print(f"K线不足: {len(rows)} 条 (需要 >= 120)")
        sys.exit(1)

    daily = [dict(r) for r in rows]
    params = load_params()
    params['stock_code'] = args.stock
    signals = detect(daily, params)

    print(f"🔍 {args.stock} @ {args.date}")
    print(f"   信号数: {len(signals)}")
    for s in signals:
        print(f"   📅 {s['signal_date']} | 前高¥{s['prior_high_price']} → 低¥{s['trough_price']} "
              f"({s['drawdown_pct']}%) | 基部{s['base_days']}天")
        print(f"      突破¥{s['breakout_close']} (+{s['breakout_gain_pct']}%) "
              f"量比{s['breakout_vol_ratio']}x | MA10={s['ma10']} MA20={s['ma20']}")
