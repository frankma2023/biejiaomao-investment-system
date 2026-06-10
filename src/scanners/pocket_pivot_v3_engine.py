"""
口袋支点 V3 识别引擎 — 供 pattern-scan 实时使用

基于 pocket_pivot_v2.py 的检测逻辑，包装为标准引擎接口。
与独立扫描器 pocket_pivot_v2.py 共用 evaluate_stock 核心逻辑。

用法：engine_registry 自动发现后在 pattern-scan 显示
"""
import sys, os, sqlite3, json
from datetime import datetime

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SRC_DIR))
if SRC_DIR not in sys.path: sys.path.insert(0, SRC_DIR)

DB_PATH = os.path.join(PROJECT_ROOT, "data", "lixinger.db")

ENGINE_META = {
    "name": "pocket_pivot_v3",
    "display_name": "口袋支点V3",
    "category": "breakout",
    "version": "3.0",
    "description": "集成MW缠论H/L/C结构的口袋支点检测，三种类型(base/continuation/10ma_bounce)"
}


def sma(values, n):
    clean = [v for v in values if v is not None]
    if len(clean) < n: return None
    return sum(clean[-n:]) / n


def _get_structure(code, klines, db):
    """获取 H/L/C 结构：直接使用 chanlun_structure 共享层，不依赖 MW 信号表"""
    dates = [k['date'] for k in klines]
    n = len(klines)

    try:
        from chanlun_structure import get_bi_list, get_bi_peaks
        bi_list = get_bi_list(code)
        if bi_list:
            peaks = get_bi_peaks(bi_list)
            if peaks:
                # 在最近200根K线中找最高bi峰
                lookback = min(200, n - 10)
                cutoff = n - lookback
                recent = [p for p in peaks if p['date'] >= dates[cutoff] and p['date'] in dates]
                if not recent:
                    recent = peaks[-3:]
                h_peak = None
                for p in reversed(recent):
                    if p['date'] in dates:
                        h_idx = dates.index(p['date'])
                        if h_idx < n - 3:
                            h_peak = p
                            break
                if h_peak:
                    l_idx = h_idx + 1
                    l_price = klines[l_idx]['close']
                    for i in range(h_idx + 1, n):
                        if klines[i]['close'] < l_price:
                            l_price = klines[i]['close']
                            l_idx = i
                    return {
                        'h_date': h_peak['date'], 'h_price': h_peak['price'],
                        'l_date': dates[l_idx], 'l_price': l_price,
                        'c_start': dates[l_idx], 'c_end': dates[-1],
                        'b1_date': None,
                        'decline_pct': round((h_peak['price'] - l_price) / h_peak['price'] * 100, 2),
                    }
    except Exception:
        pass

    return None


def _evaluate(klines, idx, structure, rps20, rps250):
    """复刻 pocket_pivot_v2.evaluate_stock 的核心逻辑"""
    n = len(klines)
    today = klines[idx]
    o, h, l, c, v = today['open'], today['high'], today['low'], today['close'], today['volume']
    if c <= 0 or v <= 0: return None

    s = structure
    l_date = s.get('l_date')
    c_start_date = s.get('c_start', '')
    b1_date = s.get('b1_date')
    if not l_date: return None

    dates = [k['date'] for k in klines]

    # 基础趋势
    closes = [k['close'] for k in klines[:idx+1]]
    sma10 = sma(closes, 10); sma60 = sma(closes, 60)
    if sma10 is None or sma60 is None: return None
    if not (c > sma60 and c > sma10): return None

    # 延伸
    pct_ma10 = (c - sma10) / sma10 * 100
    if pct_ma10 > 25: return None

    # 距L天数
    l_idx = dates.index(l_date) if l_date in dates else -1
    days_from_l = idx - l_idx if l_idx >= 0 else 999
    if days_from_l < 3: return None

    # 类型判断
    in_c_zone = False
    c_end_date = s.get('c_end', '')
    if c_start_date and l_idx >= 0:
        c_end_idx = dates.index(c_end_date) if c_end_date in dates else idx
        in_c_zone = (l_idx <= idx <= min(c_end_idx + 5, n-1))

    in_p_zone = False
    if b1_date and b1_date in dates:
        b1_idx = dates.index(b1_date)
        days_after_b1 = idx - b1_idx
        in_p_zone = (3 <= days_after_b1 <= 15) and days_from_l > 15

    is_10ma_bounce = (l <= sma10 * 1.02 and c > klines[idx-1]['close'] if idx > 0 else False)

    # 量价
    gain_pct = (c - klines[idx-1]['close']) / klines[idx-1]['close'] * 100 if idx > 0 else 0
    if gain_pct < 3: return None
    if h - l <= 0: return None
    close_pos = (c - l) / (h - l)
    if close_pos < 0.50: return None
    if c <= o: return None

    # 量 > 前10天最大下跌量
    down_vols = []
    for i in range(max(0, idx-10), idx):
        if klines[i]['close'] < klines[i-1]['close']:
            down_vols.append(klines[i]['volume'])
    if down_vols and v <= max(down_vols): return None

    # 突破
    prev_highs = [klines[i]['high'] for i in range(max(0, idx-10), idx)]
    if prev_highs and h < max(prev_highs): return None

    # RS（有数据时才检查，无数据时放行）
    if rps20 is not None and rps250 is not None:
        if not (rps20 >= 80 or rps250 >= 80): return None

    # 确定类型
    pivot_type = None
    b1_overlap = False
    if in_c_zone and c > sma10:
        pivot_type = 'base'
        if b1_date and klines[idx]['date'] == b1_date:
            b1_overlap = True
    elif in_p_zone and is_10ma_bounce:
        pivot_type = 'continuation'
    elif is_10ma_bounce and c > sma60 and sma10 > sma60:
        pivot_type = '10ma_bounce'
    else:
        return None

    return {
        'type': 'bullish',
        'date': klines[idx]['date'],
        'pivot_type': pivot_type,
        'b1_overlap': b1_overlap,
        'gain_pct': round(gain_pct, 2),
        'close_position': round(close_pos, 2),
        'details': {
            'signal_type': f'PP-{pivot_type}' + ('+B1' if b1_overlap else ''),
            'h_date': s.get('h_date'), 'l_date': s.get('l_date'),
            'c_days': days_from_l, 'base_depth': s.get('decline_pct'),
            'rps_20': rps20, 'rps_250': rps250,
        }
    }


def detect(klines, params=None):
    """引擎入口：扫描 K 线中的每一天，返回口袋支点信号列表"""
    code = None
    for k in klines:
        if k.get('stock_code'):
            code = k['stock_code']
            break
    if not code: return []

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    # RS 数据
    rps20_map = {}
    rps250_map = {}
    try:
        rs_rows = db.execute(
            "SELECT date, rps_20, rps_250 FROM stock_rs_daily WHERE stock_code=?",
            (code,)
        ).fetchall()
        for r in rs_rows:
            rps20_map[r['date']] = r['rps_20']
            rps250_map[r['date']] = r['rps_250']
    except sqlite3.OperationalError:
        pass

    # H/L/C 结构
    structure = _get_structure(code, klines, db)
    db.close()
    if not structure: return []

    # 扫描每一天
    signals = []
    for idx in range(len(klines)):
        day = klines[idx]['date']
        if klines[idx].get('close') is None or klines[idx].get('volume') is None: continue
        rps20 = rps20_map.get(day)
        rps250 = rps250_map.get(day)
        result = _evaluate(klines, idx, structure, rps20, rps250)
        if result:
            signals.append(result)

    return signals
