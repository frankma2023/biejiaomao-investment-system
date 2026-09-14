"""
双重底(Double Bottom)形态识别引擎 v1

五节点时序：前低 → 前高 → 左底 → 中间峰 → 右底 → 突破
"""

import sys, os, argparse, sqlite3, yaml
from datetime import datetime, date as dt_date

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "double_bottom",
    "display_name": "双重底",
    "category": "pattern",
    "version": "1.0",
    "description": "识别W形双重底形态及其颈线突破，五节点时序：前低→前高→左底→中间峰→右底→突破"
}


def load_params():
    p = {
        'min_prior_advance': .30, 'prior_high_min_age': 40,
        'left_bottom_decline_min': .15, 'left_bottom_decline_max': .50, 'left_bottom_min_days_from_high': 10,
        'middle_peak_min_rebound': .10, 'middle_peak_min_days': 5, 'middle_peak_tolerance': 0.0,
        'right_bottom_max_undercut': .03, 'right_bottom_min_price_ratio': .85,
        'right_bottom_min_days': 5, 'right_bottom_max_age': 5,
        'left_vol_ratio_min': .8, 'middle_peak_vol_ratio_min': 1.0, 'right_vol_contraction': .7,
        'right_rebound_confirm': .05, 'breakout_vol_ratio': 1.4,
        'require_green': True, 'close_position_min': .50,
        'max_pattern_span': 200, 'min_pattern_span': 40, 'bottom_distance_min': 10,
        'handle_enabled': False, 'min_listing_days': 180,
    }
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "double_bottom.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            c = yaml.safe_load(f) or {}
        p.update(c.get('double_bottom', {}))
    return p


def detect(klines, params=None):
    if params is None: params = load_params()
    P = params; n = len(klines)
    if n < P['min_listing_days']: return []

    # 预计算MA50成交量
    ma50v = [0]*n
    for i in range(n):
        if i >= 50:
            vs = [klines[j]['volume'] for j in range(i-50,i) if klines[j].get('volume') is not None]
            ma50v[i] = sum(vs)/len(vs) if vs else 0

    signals = []

    for i in range(P['prior_high_min_age']+P['min_pattern_span'], n):
        today = klines[i]
        if today.get('close') is None: continue
        window = klines[:i+1]

        # ── 步骤0: 找前高和前低 ──
        ph = _find_prior_high(window, P)
        if not ph: continue
        pl = _find_prior_low(window, ph['idx'])
        if not pl: continue
        gain = (ph['price'] - pl['price']) / pl['price']
        if gain < P['min_prior_advance']: continue

        # ── 步骤1: 找左底 ──
        lb = _find_bottom(window, ph['idx'], i, P['left_bottom_decline_min'],
                          P['left_bottom_decline_max'], ph['price'], P['left_bottom_min_days_from_high'])
        if not lb: continue

        # ── 步骤2: 找中间峰 ──
        mp = _find_peak_between(window, lb['idx'], i, lb['price'], ph['price'], P)
        if not mp: continue

        # ── 步骤3: 找右底 ──
        rb = _find_bottom(window, mp['idx'], i, None, None, mp['price'], P['right_bottom_min_days'],
                          is_right=True, left_bottom=lb, params=P)
        if not rb: continue

        # ── 左右底间隔检查 ──
        if rb['idx'] - lb['idx'] < P['bottom_distance_min']: continue

        # ── 形态跨度检查 ──
        span = i - ph['idx']
        if span > P['max_pattern_span'] or span < P['min_pattern_span']: continue

        # ── 右底后反弹确认 ──
        if not _check_rebound(window, rb['idx'], i, P['right_rebound_confirm']): continue

        # ── 成交量验证 ──
        lv = _vol_at(window, lb['idx'], ma50v)
        rv = _vol_at(window, rb['idx'], ma50v)
        if lv < P['left_vol_ratio_min']: continue
        mv = _vol_at(window, mp['idx'], ma50v)
        if mv < P['middle_peak_vol_ratio_min']: continue
        if rv > lv * P['right_vol_contraction']: continue

        # ── 突破信号 ──
        mp_high = max(k['high'] for k in window[mp['idx']:i+1] if k.get('high') is not None)
        if today['close'] < mp_high: continue

        if i >= 50 and ma50v[i] > 0 and today['volume'] < ma50v[i] * P['breakout_vol_ratio']: continue
        if P['require_green'] and today['close'] <= today['open']: continue
        if today['high'] != today['low']:
            pos = (today['close']-today['low'])/(today['high']-today['low'])
            if pos < P['close_position_min']: continue

        signals.append({
            'date': today['date'], 'close': today['close'], 'volume': today['volume'],
            'prior_low_date': pl['date'], 'prior_low_price': round(pl['price'],2),
            'prior_high_date': ph['date'], 'prior_high_price': round(ph['price'],2),
            'prior_advance_pct': round(gain*100,1),
            'left_bottom_date': lb['date'], 'left_bottom_price': round(lb['price'],2),
            'middle_peak_date': mp['date'], 'middle_peak_price': round(mp['price'],2),
            'right_bottom_date': rb['date'], 'right_bottom_price': round(rb['price'],2),
            'right_undercut_pct': round((lb['price']-rb['price'])/lb['price']*100,1),
            'decline_from_high': round((ph['price']-lb['price'])/ph['price']*100,1),
            'left_vol_ratio': round(lv,2), 'right_vol_ratio': round(rv,2),
            'vol_contraction': round(rv/lv,2) if lv>0 else 0,
            'breakout_vol_ratio': round(today['volume']/ma50v[i],2) if ma50v[i]>0 else 0,
            'middle_peak_to_now_days': i-mp['idx'],
        })

    return signals


def _find_prior_high(window, P):
    """在窗口内找最高收盘价，且距今天≥prior_high_min_age"""
    end = len(window) - P['prior_high_min_age']
    if end < 10: return None
    best_idx, best_p = None, -1
    for j in range(end):
        c = window[j].get('close')
        if c and c > best_p: best_p, best_idx = c, j
    return {'idx': best_idx, 'date': window[best_idx]['date'], 'price': best_p} if best_idx is not None else None


def _find_prior_low(window, ph_idx, lookback=200):
    """前高之前的 lookback 天内最低收盘价"""
    start = max(0, ph_idx - lookback)
    if ph_idx - start < 5: return None
    best_idx, best_p = None, 1e99
    for j in range(start, ph_idx):
        c = window[j].get('close')
        if c and c < best_p: best_p, best_idx = c, j
    return {'idx': best_idx, 'date': window[best_idx]['date'], 'price': best_p} if best_idx is not None else None


def _find_bottom(window, start_idx, end_idx, decline_min, decline_max, ref_price, min_days,
                 is_right=False, left_bottom=None, params=None):
    """找左底或右底"""
    search_start = start_idx + min_days
    search_end = min(end_idx - (params['right_bottom_max_age'] if is_right and params else 0), end_idx)
    if search_end - search_start < 3: return None

    best_idx, best_p = None, 1e99
    for j in range(search_start, search_end):
        c = window[j].get('close')
        if c and c < best_p: best_p, best_idx = c, j
    if best_idx is None: return None

    if decline_min is not None:
        dd = (ref_price - best_p) / ref_price
        if dd < decline_min or dd > (decline_max or 1.0):
            return None

    if is_right and left_bottom:
        ratio = best_p / left_bottom['price']
        if ratio < params.get('right_bottom_min_price_ratio', .85): return None
        undercut = (left_bottom['price'] - best_p) / left_bottom['price']
        if undercut > params.get('right_bottom_max_undercut', .03): return None

    return {'idx': best_idx, 'date': window[best_idx]['date'], 'price': best_p}


def _find_peak_between(window, lb_idx, end_idx, lb_price, ph_price, P):
    """找左底之后的中间峰"""
    search_start = lb_idx + P['middle_peak_min_days']
    search_end = end_idx - 20
    if search_end - search_start < 3: return None

    best_idx, best_p = None, -1
    for j in range(search_start, search_end):
        c = window[j].get('close')
        if c and c > best_p: best_p, best_idx = c, j
    if best_idx is None: return None

    if (best_p - lb_price) / lb_price < P['middle_peak_min_rebound']: return None
    if best_p > ph_price * (1 + P.get('middle_peak_tolerance', 0)): return None

    return {'idx': best_idx, 'date': window[best_idx]['date'], 'price': best_p}


def _check_rebound(window, rb_idx, today_idx, threshold):
    """右底后是否已有反弹确认"""
    for j in range(rb_idx+1, today_idx+1):
        c = window[j].get('close')
        if c and c > window[rb_idx]['close'] * (1+threshold):
            return True
    return False


def _vol_at(window, idx, ma50v):
    """取某点附近±2天的平均量比MA50"""
    vs = []
    for j in range(max(0,idx-2), min(len(window), idx+3)):
        v = window[j].get('volume')
        if v is not None and ma50v[j] > 0: vs.append(v/ma50v[j])
    return sum(vs)/len(vs) if vs else 0


def detect_for_stock(code, date, params=None):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT date,open,high,low,close,volume FROM daily_kline_adj WHERE stock_code=? AND date<=? ORDER BY date", (code, date)).fetchall()
    conn.close()
    if len(rows) < 180: return []
    return detect([dict(r) for r in rows], params)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", default="600519")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    target = args.date or dt_date.today().strftime("%Y-%m-%d")
    sigs = detect_for_stock(args.stock, target)
    print(f"{args.stock}: {len(sigs)} signals")
    for s in sigs[-5:]:
        print(f"  {s['date']} LB={s['left_bottom_date']}({s['left_bottom_price']}) MP={s['middle_peak_date']}({s['middle_peak_price']}) RB={s['right_bottom_date']}({s['right_bottom_price']})")
