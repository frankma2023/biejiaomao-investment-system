"""
口袋支点 V3 识别引擎 — 供 pattern-scan 实时使用

基于 pocket_pivot_v2.py 的检测逻辑，包装为标准引擎接口。
v3.2: 多周期扫描，遍历所有历史 bi 峰谷对，捕获各周期口袋支点
"""
import sys, os, sqlite3
from datetime import datetime

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SRC_DIR))
if SRC_DIR not in sys.path: sys.path.insert(0, SRC_DIR)

DB_PATH = os.path.join(PROJECT_ROOT, "data", "lixinger.db")

ENGINE_META = {
    "name": "pocket_pivot_v3",
    "display_name": "口袋支点V3",
    "category": "breakout",
    "version": "3.2",
    "description": "多周期缠论H/L/C口袋支点检测，base/continuation/10ma_bounce"
}


def sma(values, n):
    clean = [v for v in values if v is not None]
    if len(clean) < n: return None
    return sum(clean[-n:]) / n


def _get_all_structures(code, klines):
    """从缠论 bi 峰谷提取所有历史 H-L-C 结构（多周期）"""
    dates = [k['date'] for k in klines]
    n = len(klines)
    structures = []

    try:
        from chanlun_structure import get_bi_list, get_bi_peaks
        bi_list = get_bi_list(code)
        if not bi_list:
            return []
        peaks = get_bi_peaks(bi_list)
        if len(peaks) < 2:
            return []

        # 遍历所有峰，为每个峰构建 H-L-C 结构
        for i in range(len(peaks) - 1):
            h_peak = peaks[i]
            if h_peak['date'] not in dates:
                continue
            h_idx = dates.index(h_peak['date'])
            if h_idx >= n - 3:
                continue  # H 太接近末尾

            # 找 H 之后的最低收盘价作为 L
            l_idx = h_idx + 1
            l_price = klines[l_idx]['close']
            for j in range(h_idx + 1, min(h_idx + 120, n)):
                if klines[j]['close'] < l_price:
                    l_price = klines[j]['close']
                    l_idx = j

            # C 区：L 之后到 H 之后 120 天或下一个峰的起点
            c_end = min(l_idx + 60, n - 1)
            if i + 1 < len(peaks) and peaks[i + 1]['date'] in dates:
                next_h_idx = dates.index(peaks[i + 1]['date'])
                c_end = min(c_end, next_h_idx - 1)

            decline_pct = round((h_peak['price'] - l_price) / h_peak['price'] * 100, 2)

            structures.append({
                'h_date': h_peak['date'], 'h_price': h_peak['price'],
                'l_date': dates[l_idx], 'l_price': l_price,
                'c_start': dates[l_idx], 'c_end': dates[c_end],
                'b1_date': None,
                'decline_pct': decline_pct,
            })
    except Exception:
        pass

    return structures


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

    # 确保扫描日在 C 区内
    l_idx_s = dates.index(l_date) if l_date in dates else -1
    c_end_date = s.get('c_end', '')
    c_end_idx = dates.index(c_end_date) if c_end_date in dates else idx
    if l_idx_s < 0: return None
    if not (l_idx_s <= idx <= min(c_end_idx + 5, n - 1)):
        return None  # 不在 C 区，跳过

    # 基础趋势
    closes = [k['close'] for k in klines[:idx+1]]
    sma10 = sma(closes, 10); sma60 = sma(closes, 60)
    if sma10 is None or sma60 is None: return None
    if not (c > sma60 and c > sma10): return None

    # 延伸
    pct_ma10 = (c - sma10) / sma10 * 100
    if pct_ma10 > 25: return None

    # 距L天数
    days_from_l = idx - l_idx_s
    if days_from_l < 3: return None

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

    # RS
    if rps20 is not None and rps250 is not None:
        if not (rps20 >= 80 or rps250 >= 80): return None

    # 确定类型
    pivot_type = None
    b1_overlap = False
    if c > sma10:
        pivot_type = 'base'
        if b1_date and klines[idx]['date'] == b1_date:
            b1_overlap = True
    elif l <= sma10 * 1.02 and c > klines[idx-1]['close']:
        if c > sma60 and sma10 > sma60:
            pivot_type = '10ma_bounce'
        else:
            pivot_type = 'continuation'
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
    """多周期扫描所有历史 bi 峰谷对中的口袋支点"""
    code = None
    for k in klines:
        if k.get('stock_code'):
            code = k['stock_code']
            break
    if not code: return []

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    rps20_map, rps250_map = {}, {}
    try:
        for r in db.execute("SELECT date, rps_20, rps_250 FROM stock_rs_daily WHERE stock_code=?", (code,)):
            rps20_map[r['date']] = r['rps_20']
            rps250_map[r['date']] = r['rps_250']
    except sqlite3.OperationalError:
        pass

    structures = _get_all_structures(code, klines)
    db.close()
    if not structures: return []

    # 为每个结构预计算有效日期范围（性能优化）
    dates = [k['date'] for k in klines]
    struct_ranges = []
    for s in structures:
        l_d = s['l_date']
        c_end = s.get('c_end', dates[-1])
        if l_d in dates and c_end in dates:
            struct_ranges.append((dates.index(l_d), min(dates.index(c_end) + 5, len(klines) - 1), s))

    signals = []
    for idx in range(len(klines)):
        if klines[idx].get('close') is None or klines[idx].get('volume') is None:
            continue
        rps20 = rps20_map.get(klines[idx]['date'])
        rps250 = rps250_map.get(klines[idx]['date'])

        # 找包含当前 idx 的结构
        for l_idx_s, c_end_s, s in struct_ranges:
            if l_idx_s <= idx <= c_end_s:
                result = _evaluate(klines, idx, s, rps20, rps250)
                if result:
                    result['date'] = klines[idx]['date']  # ensure date field
                    signals.append(result)
                break  # 每个 idx 只取第一个匹配的结构

    return signals
