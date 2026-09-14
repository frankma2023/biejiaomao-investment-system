"""
扁平基部(Flat Base)形态识别引擎 v1

特征：极窄振幅横盘 + 成交量持续萎缩 + 布林带带宽收缩 + 放量突破
"""

import sys, os, argparse, sqlite3, yaml, math
from datetime import datetime, date as dt_date

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "flat_base",
    "display_name": "扁平基部",
    "category": "pattern",
    "version": "1.0",
    "description": "识别扁平基部形态：极窄振幅横盘、成交量持续萎缩、布林带带宽收缩、放量突破"
}


def load_params():
    p = {
        'min_prior_advance': .30, 'sma50_check': True,
        'min_duration': 25, 'max_duration': 100, 'amp_max': .12, 'amp_ideal': .08,
        'slope_enabled': True, 'slope_abs_max': .001,
        'max_days_from_base_end': 5,
        'vol_contraction_ratio': .60, 'vol_cv_check': False,
        'bb_enabled': True, 'bb_lookback': 750, 'bb_width_window': 20, 'bb_pct_max': .20,
        'breakout_vol_ratio': 1.4, 'require_green': True, 'close_position_min': .50,
        'breakout_min_gain': 0.0, 'min_listing_days': 750,
    }
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "flat_base.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            c = yaml.safe_load(f) or {}
        p.update(c.get('flat_base', {}))
    return p


def detect(klines, params=None):
    if params is None: params = load_params()
    P = params; n = len(klines)
    if n < P['min_listing_days']: return []

    # 预计算
    ma50v = [0]*n; sma50 = [0]*n
    for i in range(n):
        if i >= 50:
            vs = [klines[j]['volume'] for j in range(i-50,i) if klines[j].get('volume') is not None]
            ma50v[i] = sum(vs)/len(vs) if vs else 0
            cs = [klines[j]['close'] for j in range(i-50,i) if klines[j].get('close') is not None]
            sma50[i] = sum(cs)/len(cs) if cs else 0

    # 布林带带宽历史
    bb_history = []
    if P['bb_enabled']:
        for i in range(P['bb_width_window'], n):
            cs = [klines[j]['close'] for j in range(i-P['bb_width_window'], i) if klines[j].get('close') is not None]
            if len(cs) >= 5:
                ma = sum(cs)/len(cs)
                sd = math.sqrt(sum((x-ma)**2 for x in cs)/len(cs))
                bb_history.append(sd*2/ma if ma > 0 else 1)
            else:
                bb_history.append(1)

    signals = []

    for i in range(P['min_duration'], n):
        today = klines[i]
        close = today.get('close')
        if close is None: continue

        # ── 前置: SMA50趋势 ──
        if P['sma50_check'] and i >= 50 and sma50[i] > 0 and close <= sma50[i]:
            continue

        # ── 滑动窗口找基部区间 ──
        base = _find_flat_base(klines, i, P)
        if not base: continue

        # ── 前置上涨 ──
        if not _check_prior_advance(klines, base['start_idx'], P):
            continue

        # ── 成交量萎缩 ──
        if not _check_vol_contraction(klines, base, P):
            continue

        # ── 布林带 ──
        bb_ok = True; bb_w = 0; bb_p = 0
        if P['bb_enabled'] and bb_history:
            bi = i - P['bb_width_window']
            if 0 <= bi < len(bb_history):
                bb_w = bb_history[bi]
                better = sum(1 for h in bb_history[max(0,len(bb_history)-P['bb_lookback']):] if h < bb_w)
                total = min(P['bb_lookback'], len(bb_history))
                bb_p = better/total if total > 0 else 1
                bb_ok = bb_p <= P['bb_pct_max']

        # ── 突破信号 ──
        if close < base['high']: continue
        # 昨日收盘必须低于基部高点（只标记首次突破，非连续突破）
        if i > 0 and klines[i-1].get('close') and klines[i-1]['close'] >= base['high']: continue
        if i >= 50 and ma50v[i] > 0 and today['volume'] < ma50v[i] * P['breakout_vol_ratio']:
            continue
        if P['require_green'] and close <= today['open']: continue
        if today['high'] != today['low']:
            pos = (close-today['low'])/(today['high']-today['low'])
            if pos < P['close_position_min']: continue

        signals.append({
            'date': today['date'], 'close': close, 'volume': today['volume'],
            'base_start_date': klines[base['start_idx']]['date'],
            'base_end_date': klines[base['end_idx']]['date'],
            'base_duration': base['end_idx']-base['start_idx']+1,
            'base_high': round(base['high'],2), 'base_low': round(base['low'],2),
            'base_amplitude': round(base['amp']*100,1),
            'price_slope': round(base.get('slope',0),6),
            'vol_first_half_avg': int(base['vol1']),
            'vol_second_half_avg': int(base['vol2']),
            'vol_contraction': round(base['vol2']/base['vol1'],2) if base['vol1']>0 else 0,
            'bb_width': round(bb_w,4), 'bb_width_pct': round(bb_p,2),
            'breakout_vol_ratio': round(today['volume']/ma50v[i],2) if ma50v[i]>0 else 0,
            'bb_passed': bb_ok,
        })

    return signals


def _find_flat_base(klines, today_idx, P):
    """滑动窗口：从 today 往前扩展，找振幅≤amp_max 的最大区间"""
    close = klines[today_idx]['close']
    if close is None: return None

    best_start = today_idx - P['min_duration']
    if best_start < 0: return None

    # 从 today_idx 往前扩，直到振幅超标
    window_high = close; window_low = close
    start = today_idx
    for j in range(today_idx-1, max(0, today_idx-P['max_duration']), -1):
        c = klines[j].get('close')
        if c is None: break
        window_high = max(window_high, c)
        window_low = min(window_low, c)
        amp = (window_high - window_low) / window_high
        if amp > P['amp_max']: break
        start = j

    duration = today_idx - start
    if duration < P['min_duration']: return None

    # 距基部结束 ≤ max_days_from_base_end
    # 基部的"结束"是振幅最后一次在范围内的位置
    high = max(k['close'] for k in klines[start:today_idx] if k.get('close') is not None)
    low = min(k['close'] for k in klines[start:today_idx] if k.get('close') is not None)
    amp = (high-low)/high

    # 斜率检查
    slope = 0
    if P.get('slope_enabled', True):
        cs = [k['close'] for k in klines[start:today_idx] if k.get('close') is not None]
        if len(cs) >= 5:
            n_pts = len(cs); xs = list(range(n_pts))
            xm = sum(xs)/n_pts; ym = sum(cs)/n_pts
            num = sum((xs[k]-xm)*(cs[k]-ym) for k in range(n_pts))
            den = sum((xs[k]-xm)**2 for k in range(n_pts))
            slope = num/den if den != 0 else 0
            if abs(slope) > P['slope_abs_max']: return None

    # 成交量分半
    mid = start + (today_idx - start)//2
    vol1 = [k['volume'] for k in klines[start:mid] if k.get('volume') is not None]
    vol2 = [k['volume'] for k in klines[mid:today_idx] if k.get('volume') is not None]
    avg1 = sum(vol1)/len(vol1) if vol1 else 0
    avg2 = sum(vol2)/len(vol2) if vol2 else 0

    return {
        'start_idx': start, 'end_idx': today_idx-1,
        'high': high, 'low': low, 'amp': amp,
        'slope': slope, 'vol1': avg1, 'vol2': avg2,
    }


def _check_prior_advance(klines, base_start, P):
    """检查基部之前是否有前置上涨≥30%"""
    if base_start < 50: return False
    # 在基部开始前找低点和高点
    window = klines[max(0,base_start-400):base_start]
    if len(window) < 10: return False
    ph = max(k['close'] for k in window if k.get('close') is not None)
    # 在最高点之前找最低点
    ph_idx = None
    for j in range(len(window)):
        if window[j].get('close') == ph:
            ph_idx = j; break
    if ph_idx is None or ph_idx < 5: return False
    pl = min(k['close'] for k in window[:ph_idx] if k.get('close') is not None)
    return (ph-pl)/pl >= P['min_prior_advance']


def _check_vol_contraction(klines, base, P):
    """后半段成交量 < 前半段 × 60%"""
    start, end = base['start_idx'], base['end_idx']
    mid = start + (end-start)//2
    v1 = [k['volume'] for k in klines[start:mid] if k.get('volume') is not None]
    v2 = [k['volume'] for k in klines[mid:end+1] if k.get('volume') is not None]
    if not v1 or not v2: return False
    a1 = sum(v1)/len(v1); a2 = sum(v2)/len(v2)
    return a2 <= a1 * P['vol_contraction_ratio']


def detect_for_stock(code, date, params=None):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT date,open,high,low,close,volume FROM daily_kline_adj WHERE stock_code=? AND date<=? ORDER BY date", (code, date)).fetchall()
    conn.close()
    if len(rows) < 750: return []
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
        print(f"  {s['date']} amp={s['base_amplitude']}% dur={s['base_duration']}d vol_r={s['vol_contraction']}")
