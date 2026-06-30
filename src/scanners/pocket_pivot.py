"""
口袋支点(Pocket Pivot)识别引擎 v1

五步规则：
  1. 趋势基础: close > SMA50, SMA50斜率>0, close > SMA10
  2. 日内行为: 收盘位置≥50%, 阳线
  3. 成交量: vol > 过去10日最大阴线量
  4. 延伸区: 距65日低点≤50%
  5. RS确认: RPS_20≥80 或 RPS_250≥80

类型：延续口袋支点 / 10日线反弹口袋支点
"""

import sys, os, argparse, sqlite3, yaml
from datetime import datetime, date as dt_date

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "pocket_pivot",
    "display_name": "口袋支点",
    "category": "pattern",
    "version": "1.0",
    "description": "识别欧奈尔口袋支点买入点，五步规则：趋势基础、日内行为、成交量、延伸区、RS确认"
}


def load_params():
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "pocket_pivot.yaml")
    defaults = {
        'sma10_check': True, 'sma50_check': True, 'sma50_slope_10d': 0.002,
        'close_position_min': 0.50, 'require_green': True,
        'vol_down_lookback': 10, 'vol_fallback_enabled': True,
        'extension_lookback': 65, 'extension_max_pct': 0.50,
        'rs_threshold': 80, 'rs_dual_require': False,
        'ma10_bounce_proximity': 0.02, 'below_65d_high': True,
        'exclude_limit_up': True, 'min_listing_days': 120,
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        defaults.update(cfg.get('pocket_pivot', {}))
    return defaults


def detect(klines, params=None, rs_info=None):
    """
    klines: [{'date','open','high','low','close','volume'}, ...] 升序
    rs_info: {'rs_20': int, 'rs_250': int} 或 None
    """
    if params is None:
        params = load_params()

    SMA10_EN = params.get('sma10_check', True)
    SMA50_EN = params.get('sma50_check', True)
    SMA50_SLOPE = params.get('sma50_slope_10d', 0.002)
    SMA50_SLOPE_EN = params.get('sma50_slope_enabled', True)
    CLOSE_POS = params.get('close_position_min', 0.50)
    GREEN = params.get('require_green', True)
    VOL_LB = params.get('vol_down_lookback', 10)
    VOL_FB = params.get('vol_fallback_enabled', True)
    EXT_LB = params.get('extension_lookback', 65)
    EXT_MAX = params.get('extension_max_pct', 0.50)
    RS_THR = params.get('rs_threshold', 80)
    RS_DUAL = params.get('rs_dual_require', False)
    MA10_PROX = params.get('ma10_bounce_proximity', 0.02)
    BELOW_65H = params.get('below_65d_high', True)
    EXCL_UP = params.get('exclude_limit_up', True)

    n = len(klines)
    if n < 120:
        return []

    # 预计算均线
    sma10 = [0]*n; sma50 = [0]*n
    for i in range(n):
        if i >= 10:
            cs = [klines[j]['close'] for j in range(i-10, i) if klines[j].get('close') is not None]
            sma10[i] = sum(cs)/len(cs) if cs else 0
        if i >= 50:
            cs = [klines[j]['close'] for j in range(i-50, i) if klines[j].get('close') is not None]
            sma50[i] = sum(cs)/len(cs) if cs else 0

    signals = []
    for i in range(65, n):
        k = klines[i]
        close = k['close']
        if close is None: continue

        # ── 步骤 1: 趋势 ──
        if SMA50_EN and (sma50[i] <= 0 or close <= sma50[i]):
            continue
        if SMA50_EN and sma50[i] > 0 and i >= 10 and SMA50_SLOPE_EN:
            if sma50[i-10] > 0 and (sma50[i] - sma50[i-10]) / sma50[i-10] < SMA50_SLOPE:
                continue
        if SMA10_EN and sma10[i] > 0 and close <= sma10[i]:
            continue

        # ── 步骤 2: 日内 ──
        if k['high'] != k['low']:
            pos = (close - k['low']) / (k['high'] - k['low'])
        else:
            pos = 1.0
        if pos < CLOSE_POS: continue
        if GREEN and close <= k['open']: continue

        # ── 排除一字涨停 ──
        if EXCL_UP and k['high'] == k['low'] and close >= k['open']:
            continue

        # ── 步骤 3: 成交量 ──
        vol_ratio = 0
        max_down_vol = 0
        has_down = False
        for j in range(max(0, i-VOL_LB), i):
            if klines[j].get('close') and klines[j].get('open') and klines[j]['close'] < klines[j]['open']:
                v = klines[j].get('volume') or 0
                if v > max_down_vol: max_down_vol = v
                has_down = True
        if has_down and max_down_vol > 0:
            vol_ratio = k['volume'] / max_down_vol if max_down_vol > 0 else 0
            if k['volume'] <= max_down_vol: continue
        elif VOL_FB:
            # 无阴线退化为50日均量
            if i >= 50:
                vs = [klines[j]['volume'] for j in range(i-50,i) if klines[j].get('volume') is not None]
                ma50v = sum(vs)/len(vs) if vs else 0
                if ma50v > 0 and k['volume'] <= ma50v * 1.5:
                    continue
                vol_ratio = k['volume'] / ma50v if ma50v > 0 else 0
            else:
                vol_ratio = 0
        else:
            vol_ratio = 0

        # ── 步骤 4: 延伸区 ──
        if EXT_LB > 0:
            vals_65 = [klines[j]['close'] for j in range(max(0,i-EXT_LB), i) if klines[j].get('close') is not None]
            if vals_65:
                low_65 = min(vals_65)
                if low_65 > 0 and close > low_65 * (1 + EXT_MAX):
                    continue

        # ── 步骤 4.5: 不高于65日最高收盘 ──
        if BELOW_65H and EXT_LB > 0:
            vals_h65 = [klines[j]['close'] for j in range(max(0,i-EXT_LB), i) if klines[j].get('close') is not None]
            if vals_h65:
                high_65 = max(vals_h65)
                if close > high_65:
                    continue

        # ── 步骤 5: RS ──
        if rs_info:
            if RS_DUAL:
                if rs_info['rs_20'] < RS_THR or rs_info['rs_250'] < RS_THR:
                    continue
            else:
                if rs_info['rs_20'] < RS_THR and rs_info['rs_250'] < RS_THR:
                    continue

        # ── 类型判定 ──
        ptype = 'continuation'
        if sma10[i] > 0 and k['low'] <= sma10[i] * (1 + MA10_PROX) and i > 0:
            yc = klines[i-1].get('close')
            if yc and close > yc:
                ptype = '10ma_bounce'

        signals.append({
            'date': k['date'], 'close': close, 'volume': k['volume'],
            'close_position': round(pos, 2),
            'vol_ratio': round(vol_ratio, 2) if vol_ratio else 0,
            'sma50': round(sma50[i], 2), 'sma10': round(sma10[i], 2),
            'rs_20': rs_info['rs_20'] if rs_info else 0,
            'rs_250': rs_info['rs_250'] if rs_info else 0,
            'pivot_type': ptype,
        })

    return signals


def get_rs(conn, stock_code, target_date, mode='stock'):
    if mode == 'index':
        row = conn.execute("""SELECT rs_20, rs_250 FROM index_rs_daily
            WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1""",
            (stock_code, target_date)).fetchone()
        if not row: return None
        return {'rs_20': row['rs_20'] or 0, 'rs_250': row['rs_250'] or 0}
    else:
        row = conn.execute("""SELECT rps_20, rps_250 FROM stock_rs_daily
            WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1""",
            (stock_code, target_date)).fetchone()
        if not row: return None
        return {'rs_20': row['rps_20'] or 0, 'rs_250': row['rps_250'] or 0}


def detect_for_stock(stock_code, target_date, params=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""SELECT date,open,high,low,close,volume FROM daily_kline
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 200""",
        (stock_code, target_date)).fetchall()
    rows.reverse()  # 恢复升序
    if len(rows) < 120: conn.close(); return []
    klines = [dict(r) for r in rows]
    rs_info = get_rs(conn, stock_code, target_date)
    conn.close()
    return detect(klines, params, rs_info)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", type=str, default="600519")
    parser.add_argument("--date", type=str, default=None)
    args = parser.parse_args()
    target = args.date or dt_date.today().strftime("%Y-%m-%d")
    params = load_params()
    signals = detect_for_stock(args.stock, target, params)
    print(f"{args.stock}: {len(signals)} 信号")
    for s in signals[-8:]:
        print(f"  {s['date']} C={s['close']:.2f} pos={s['close_position']} vol_r={s['vol_ratio']} type={s['pivot_type']}")
