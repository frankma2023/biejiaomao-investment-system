"""
基部突破检测引擎 (Layer 1) v1.0

核心问题: 今天是否是一个有效的基部突破日？
不判断形态类型。形态标注由 Layer 2 完成。

检测流程:
  谷底驱动法定位前高+谷 → 回调验证 → 回升验证 → 前置上涨
  → 突破验证 → 假突破排除

用法:
  python base_breakout.py --stock 600519 --date 2026-05-17
  python base_breakout.py --stock 000300 --date 2026-05-17 --mode index
"""

import sys, os, argparse, sqlite3, yaml
from datetime import datetime, timedelta
from typing import Optional, Dict, List

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "base_breakout",
    "display_name": "基部突破检测",
    "category": "layer1",
    "version": "1.0",
    "description": "Layer 1 核心引擎：判断是否为有效基部突破日，不区分形态类型"
}


def load_params():
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "base_breakout.yaml")
    defaults = {
        'lookback': 120,
        'local_window': 10,
        'min_base_days': 20,
        'min_descent_days': 10,
        'drawdown_min': 0.05,
        'drawdown_max': 0.40,
        'min_recovery': 0.75,
        'min_prior_advance': 0.30,
        'breakout_vol_ratio': 1.3,
        'require_green': True,
        'sma50_check': True,
        'close_position_min': 0.50,
        'fake_breakout_lookback': 3,
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        defaults.update(cfg.get('base_breakout', {}))
    return defaults


def _sma(arr, n):
    clean = [v for v in arr if v is not None]
    if len(clean) < n: return sum(clean) / max(len(clean), 1) if clean else 0
    return sum(clean[-n:]) / n

def _sma_before(arr, n, idx):
    start = max(0, idx - n)
    vals = [v for v in arr[start:idx] if v is not None]
    return sum(vals) / max(len(vals), 1) if vals else 0

def _linear_slope(y):
    clean = [v for v in y if v is not None]
    n = len(clean)
    if n < 2: return 0
    xs = list(range(n))
    xm = (n - 1) / 2; ym = sum(clean) / n
    num = sum((xs[i] - xm) * (clean[i] - ym) for i in range(n))
    den = sum((xs[i] - xm) ** 2 for i in range(n))
    return num / den if den else 0

def _is_local_high(values, idx, window=10):
    peak = values[idx]
    if peak is None: return False
    lo, hi = max(0, idx - window), min(len(values) - 1, idx + window)
    for i in range(lo, hi + 1):
        vi = values[i]
        if vi is not None and vi > peak: return False
    return True


def _find_trough_and_prior_high(daily, t_idx, params):
    lookback = params['lookback']
    local_window = params.get('local_window', 10)
    min_base = params['min_base_days']
    min_descent = params['min_descent_days']
    dd_min = params['drawdown_min']

    closes = [k['close'] for k in daily]
    dates = [k['date'] for k in daily]

    # 跳过 None
    def _close(i):
        v = closes[i]
        return v if v is not None else float('inf')  # None 当作无穷大，不会被选为谷

    search_end = t_idx - min_base
    search_start = max(0, t_idx - lookback)
    if search_end < search_start: return None

    trough_price = float('inf'); trough_idx = None
    for i in range(search_start, search_end + 1):
        if _close(i) < trough_price:
            trough_price = _close(i); trough_idx = i
    if trough_idx is None: return None

    candidate_price = trough_price
    candidate_idx = trough_idx
    prior_high = None
    max_back = max(0, trough_idx - lookback)

    for i in range(trough_idx - 1, max_back - 1, -1):
        ci = _close(i)
        if ci is None or ci == float('inf'): continue
        if ci > candidate_price:
            candidate_price = ci; candidate_idx = i
        if _is_local_high(closes, i, local_window):
            if candidate_price > trough_price:
                dd = (candidate_price - trough_price) / candidate_price
                if dd >= dd_min:
                    prior_high = candidate_price
                    prior_high_idx = candidate_idx
                    break

    if prior_high is None:
        if candidate_price > trough_price:
            dd = (candidate_price - trough_price) / candidate_price
            if dd >= dd_min:
                prior_high = candidate_price
                prior_high_idx = candidate_idx

    if prior_high is None: return None

    descent_days = trough_idx - prior_high_idx
    if descent_days < min_descent: return None

    return {
        'prior_high': prior_high, 'prior_high_idx': prior_high_idx,
        'prior_high_date': dates[prior_high_idx],
        'trough': trough_price, 'trough_idx': trough_idx,
        'trough_date': dates[trough_idx],
        'drawdown': (prior_high - trough_price) / prior_high,
        'descent_days': descent_days,
    }


def _check_prior_advance(daily, prior_high_idx, prior_high, lookback, min_adv):
    start = max(0, prior_high_idx - lookback)
    lows = [k['close'] for k in daily[start:prior_high_idx] if k.get('close') is not None]
    if not lows: return 0
    prior_low = min(lows)
    return (prior_high - prior_low) / prior_low if prior_low > 0 else 0


def detect(daily, params=None, market_cap=None):
    if params is None: params = load_params()

    lookback = params['lookback']
    min_adv = params['min_prior_advance']
    dd_min = params['drawdown_min']
    dd_max = params['drawdown_max']
    min_recovery = params['min_recovery']
    vol_ratio = params['breakout_vol_ratio']
    require_green = params.get('require_green', True)
    close_pos_min = params.get('close_position_min', 0.50)
    sma50_check = params.get('sma50_check', True)
    fake_lb = params.get('fake_breakout_lookback', 3)

    n = len(daily)
    if n < lookback + 50: return []

    closes = [k['close'] for k in daily]
    volumes = [k['volume'] for k in daily]
    signals = []

    for t_idx in range(lookback + 50, n):
        today = daily[t_idx]
        # 跳过缺失关键数据的交易日
        if today.get('close') is None or today.get('volume') is None:
            continue

        if sma50_check:
            sma50c = _sma(closes[:t_idx+1], 50)
            if today['close'] <= sma50c: continue

        base = _find_trough_and_prior_high(daily, t_idx, params)
        if base is None: continue

        dd = base['drawdown']
        if dd < dd_min or dd > dd_max: continue

        max_rec = max(k['close'] for k in daily[base['trough_idx']:t_idx+1] if k.get('close') is not None)
        if max_rec is None: continue
        if max_rec < base['prior_high'] * min_recovery: continue
        asc_slope = _linear_slope([k['close'] for k in daily[base['trough_idx']:t_idx+1] if k.get('close') is not None])
        if asc_slope <= 0: continue

        pa = _check_prior_advance(daily, base['prior_high_idx'], base['prior_high'], lookback, min_adv)
        if pa < min_adv: continue

        buy_point = base['prior_high'] + 0.01
        if today['close'] < buy_point: continue
        sma50v = _sma_before(volumes, 50, t_idx)
        if sma50v <= 0 or today['volume'] < sma50v * vol_ratio: continue
        if require_green and today['close'] <= today['open']: continue
        pos = 1
        if today['high'] > today['low']:
            pos = (today['close'] - today['low']) / (today['high'] - today['low'])
        if pos < close_pos_min: continue

        if today['low'] < base['prior_high']: continue
        if today['volume'] < sma50v: continue
        fake = False
        for j in range(max(0, t_idx - fake_lb), t_idx):
            r = daily[j]
            chg = (r['close'] - r['open']) / r['open'] if r['open'] > 0 else 0
            if chg < -0.03 and r['volume'] > sma50v * 1.3:
                fake = True; break
        if fake: continue

        rec_pct = max_rec / base['prior_high']
        signals.append({
            'signal_date': today['date'],
            'prior_high_date': base['prior_high_date'],
            'prior_high_price': round(base['prior_high'], 2),
            'trough_date': base['trough_date'],
            'trough_price': round(base['trough'], 2),
            'drawdown_pct': round(dd * 100, 1),
            'recovery_pct': round(rec_pct * 100, 1),
            'base_days': t_idx - base['prior_high_idx'],
            'buy_point': round(buy_point, 2),
            'breakout_close': round(today['close'], 2),
            'breakout_vol_ratio': round(today['volume'] / sma50v, 2) if sma50v > 0 else 0,
            'breakout_chg_pct': round((today['close'] - today['open']) / today['open'] * 100, 2) if today['open'] > 0 else 0,
            'close_position': round(pos, 2),
            'prior_advance_pct': round(pa * 100, 1),
            'market_cap': round(market_cap, 1) if market_cap else None,
        })

    return signals


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='基部突破检测 (Layer 1)')
    parser.add_argument('--stock', type=str, default='600519')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--mode', type=str, default='stock')
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    table = 'index_daily_kline' if args.mode == 'index' else 'daily_kline_adj'
    kf = "AND kline_type='normal'" if args.mode == 'index' else ''

    rows = conn.execute(f"""
        SELECT date, open, high, low, close, volume FROM {table}
        WHERE stock_code=? {kf} AND date<=? AND date>=date(?,'-500 days')
        ORDER BY date
    """, (args.stock, args.date, args.date)).fetchall()
    conn.close()

    if len(rows) < 170:
        print(f"K线不足: {len(rows)} 条 (需要 >= 170)")
        sys.exit(1)

    daily = [dict(r) for r in rows]
    params = load_params()
    signals = detect(daily, params)

    today = [s for s in signals if s['signal_date'] == args.date]
    print(f"🔍 {args.stock} @ {args.date}")
    print(f"   基部突破: 全部={len(signals)} 当日={len(today)}")
    for s in today[:5]:
        print(f"   📅 {s['signal_date']} 买点={s['buy_point']}")
        print(f"      前高={s['prior_high_price']}({s['prior_high_date']}) 谷={s['trough_price']}({s['trough_date']})")
        print(f"      回调={s['drawdown_pct']}% 回升={s['recovery_pct']}% 基部={s['base_days']}d 量比={s['breakout_vol_ratio']}")
