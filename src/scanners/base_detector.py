"""
基部识别引擎 v1.0

三条规则判断股价是否处于"合格基部右侧"：
  1. 前置上涨：存在低点→高点涨幅≥30%
  2. 价格紧凑整理：从高点至今回撤可控+时间够久+振幅收窄
  3. 右侧蓄力：放量+不创新低

用法：python src/scanners/base_detector.py --stock 600519 --date 2026-05-08
"""

import sys, os, argparse, sqlite3, json
from datetime import datetime, date as dt_date

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")


def load_params():
    """从 YAML 配置加载参数，失败则用默认值"""
    import yaml
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "base_detector.yaml")
    defaults = {
        'lookback': 120, 'min_advance': 30, 'min_consolidation': 20,
        'max_drawdown_ratio': 60, 'amp_narrow_enabled': True,
        'vol_ratio': 1.2, 'new_low_days': 20,
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        if 'base' in cfg:
            defaults.update(cfg['base'])
    return defaults


def detect(klines, params=None):
    """
    klines: [{'date','open','high','low','close','volume'}, ...] 按日期升序
    params: 参数字典，默认从 YAML 加载

    返回: list of signals, 每个信号对应一个交易日
    """
    if params is None:
        params = load_params()

    L = params['lookback']
    M = params['min_consolidation']
    min_gain = params['min_advance'] / 100.0
    max_dd = params['max_drawdown_ratio'] / 100.0
    amp_enabled = params.get('amp_narrow_enabled', True)
    vol_r = params['vol_ratio']
    nl_days = params['new_low_days']

    n = len(klines)
    if n < L:
        return []

    signals = []
    for i in range(L, n):
        today = klines[i]
        window = klines[i - L:i + 1]

        # ── 规则一：前置上涨 ──
        # 在 window[0:i-M] 内找最低的低点，然后在其后找最高的高点
        search_end = i - M  # 高点至少距今 M 天
        if search_end < 0:
            continue

        lows = [(k['low'], j) for j, k in enumerate(window[:search_end + 1]) if k['low'] is not None]
        if not lows:
            continue
        low_val, low_idx = min(lows, key=lambda x: x[0])

        # 在低点之后找高点（不晚于 search_end）
        highs = [(k['high'], j) for j, k in enumerate(window[low_idx + 1:search_end + 1], start=low_idx + 1) if k['high'] is not None]
        if not highs:
            continue
        high_val, high_idx = max(highs, key=lambda x: x[0])

        prior_gain = (high_val - low_val) / low_val
        if prior_gain < min_gain:
            continue

        prior_low_date = window[low_idx]['date']
        prior_high_date = window[high_idx]['date']

        # ── 规则二：价格紧凑整理 ──
        consolidation = window[high_idx:i + 1]
        if len(consolidation) < M:
            continue

        # 最大回撤
        cs_highs = [k['high'] for k in consolidation if k['high'] is not None]
        cs_lows = [k['low'] for k in consolidation if k['low'] is not None]
        if not cs_highs or not cs_lows:
            continue
        cs_high = max(cs_highs)
        cs_low = min(cs_lows)
        drawdown = (cs_high - cs_low) / cs_high
        if drawdown > prior_gain * max_dd:
            continue

        # 振幅收窄
        amp_trend = 1.0
        if amp_enabled and len(consolidation) >= 20:
            def safe_amp(ks):
                vals = [(k['high'] - k['low']) / k['close'] for k in ks if k['high'] is not None and k['low'] is not None and k['close'] and k['close'] > 0]
                return sum(vals) / len(vals) if vals else 0
            recent_10 = consolidation[-10:]
            prev_10_20 = consolidation[-20:-10] if len(consolidation) >= 20 else []
            if recent_10 and prev_10_20:
                recent_amp = safe_amp(recent_10)
                prev_amp = safe_amp(prev_10_20)
                amp_trend = recent_amp / prev_amp if prev_amp > 0 else 1.0
                if amp_trend >= 1.0:
                    continue

        # ── 规则三：右侧蓄力 ──
        # 放量
        if i >= 50:
            vols = [k['volume'] for k in window[-50:] if k.get('volume') is not None]
            ma50_vol = sum(vols) / len(vols) if vols else 0
            vols5 = [k['volume'] for k in window[-5:] if k.get('volume') is not None]
            ma5_vol = sum(vols5) / len(vols5) if vols5 else 0
            vol_ratio = ma5_vol / ma50_vol if ma50_vol > 0 else 1.0
            if vol_ratio < vol_r:
                continue
        else:
            vol_ratio = 1.0

        # 不创新低
        if i >= nl_days:
            lows_in_range = [klines[j]['low'] for j in range(i - nl_days, i + 1) if klines[j]['low'] is not None]
            if lows_in_range and today['low'] == min(lows_in_range):
                continue

        signals.append({
            'date': today['date'],
            'close': today['close'],
            'prior_low_date': prior_low_date,
            'prior_high_date': prior_high_date,
            'prior_gain': round(prior_gain * 100, 1),
            'drawdown': round(drawdown * 100, 1),
            'consolidation_days': len(consolidation),
            'amp_trend': round(amp_trend, 2),
            'vol_ratio': round(vol_ratio, 2),
        })

    # ── 去重 + 过滤失效基部（信号日后价格跌破前低） ──
    seen = {}
    valid = []
    for s in signals:
        key = s['prior_high_date']
        if key in seen:
            continue
        # 检查是否失效：信号日之后是否有收盘价跌破前低
        hi_idx = None; lo_idx = None; sig_idx = None
        for i, k in enumerate(klines):
            if k['date'] == s['prior_low_date']: lo_idx = i
            if k['date'] == s['date']: sig_idx = i
        if lo_idx is not None and sig_idx is not None:
            prior_low = klines[lo_idx]['low']
            failed = False
            for j in range(sig_idx + 1, len(klines)):
                if klines[j]['close'] is not None and klines[j]['close'] < prior_low:
                    failed = True
                    break
            if failed:
                continue
        seen[key] = s
        valid.append(s)
    signals = valid
    return signals


def detect_for_stock(stock_code, target_date, params=None):
    """单只股票检测"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT date, open, high, low, close, volume
        FROM daily_kline_adj WHERE stock_code = ? AND date <= ?
        ORDER BY date
    """, (stock_code, target_date)).fetchall()
    conn.close()

    if len(rows) < 120:
        return []

    klines = [dict(r) for r in rows]
    return detect(klines, params)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="基部识别")
    parser.add_argument("--stock", type=str, default="600519")
    parser.add_argument("--date", type=str, default=None)
    args = parser.parse_args()
    target = args.date or dt_date.today().strftime("%Y-%m-%d")

    signals = detect_for_stock(args.stock, target)
    print(f"信号数: {len(signals)}")
    for s in signals[-5:]:
        print(f"  {s['date']} 前涨幅{s['prior_gain']}% 回撤{s['drawdown']}% 整理{s['consolidation_days']}天 量比{s['vol_ratio']}")
