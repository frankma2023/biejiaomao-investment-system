"""
抛盘日检测引擎 v3.0（Distribution Day）

欧奈尔体系卖出信号。对指数日K线进行每日判定，25个交易日滑动窗口计数。

v3.0 规则（基于 docs/抛盘日_v3.md）：
  - 标准抛盘日：收跌≥0.1% + 量≥前日×1.0 + 非平盘
  - 特殊抛盘日（假阳线滞涨）：涨幅∈[-0.3%,0.2%] + 冲高≥0.5% + 量>前日×1.1 + 长上影
  - 盘中反转：收<开 + 冲高≥前收×0.5% + 量>前日×1.1 + 长上影+收≤中位
  - 重抛盘日：跌幅≥1.5% + 放量 → ×2 权重
  - 确认日抵消：涨幅≥1.5% + 放量 → 抵消1个
  - 平盘日过滤：|涨跌幅|<0.05% 不算任何抛盘日
  - 同天优先级：反转 > 特殊 > 标准（只计1个）
  - 25日严格滚动窗口
"""

import sys, os, argparse, sqlite3, yaml
from datetime import datetime, timedelta
from typing import Optional, Dict, List

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "distribution_day",
    "display_name": "抛盘日检测 v3.0",
    "category": "sell_signal",
    "version": "3.0",
    "description": "检测指数抛盘日（Distribution Day），25日滑动窗口计数，含重抛盘日×2 + 确认日抵消",
}


# ─── 默认参数 ──────────────────────────────────────────

def load_params():
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "distribution_day.yaml")
    defaults = {
        # 标准抛盘日
        'standard_decline_min': -0.001,      # 跌幅阈值（-0.001 = -0.1%）
        'standard_vol_ratio': 1.0,           # 量比阈值（≥1.0）

        # 特殊抛盘日（假阳线滞涨）
        'fake_yang_gain_min': -0.003,        # 涨跌幅下限（-0.3%）
        'fake_yang_gain_max': 0.002,         # 涨跌幅上限（0.2%）
        'fake_yang_surge_min': 0.005,        # 盘中最高涨幅下限（0.5%）
        'fake_yang_vol_ratio': 1.1,          # 量比阈值
        'fake_yang_wick_body': 1.5,          # 上影线/实体（或）
        'fake_yang_wick_amp': 0.5,           # 上影线/振幅（或）

        # 盘中反转抛盘日
        'intraday_surge_min': 0.005,         # 盘中最高涨幅下限（相对于前收）
        'intraday_vol_ratio': 1.1,           # 量比阈值
        'intraday_wick_body': 1.5,           # 上影线/实体
        'intraday_midpt_rule': True,         # 收盘 ≤ (高+低)/2

        # 平盘日过滤
        'flat_day_threshold': 0.0005,        # |涨跌幅| < 0.05% 为平盘

        # 重抛盘日
        'heavy_decline_min': -0.015,         # 跌幅 ≥ 1.5%
        'heavy_vol_ratio': 1.0,              # 量 ≥ 前日

        # 确认日抵消
        'ftd_gain_min': 0.015,               # 涨幅 ≥ 1.5%
        'ftd_vol_ratio': 1.0,                # 量 ≥ 前日

        # 信号计数
        'window_days': 25,
        'warning_count': 3,
        'confirmed_count': 5,

        # 指数配置
        'primary_index': '000985',
        'secondary_indices': ['000300', '000905'],
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        defaults.update(cfg.get('distribution_day', {}))
    return defaults


# ─── 单日判定 ──────────────────────────────────────────

def _classify_day(k: Dict, prev_k: Dict) -> Optional[Dict]:
    """
    对单个交易日进行抛盘日分类。
    返回 {'type': 'standard'|'fake_yang'|'intraday_rev'|'heavy', 'weight': 1|2} 或 None
    优先级：反转 > 特殊 > 标准；重抛盘日可与标准叠加（权重×2）
    """
    close = k['close']
    high = k['high']
    low = k['low']
    open_p = k['open']
    volume = k['volume']
    prev_close = prev_k['close']
    prev_volume = prev_k['volume']

    body = abs(close - open_p)
    upper_wick = high - max(close, open_p)
    amplitude = high - low
    midpt = (high + low) / 2

    change_pct = (close - prev_close) / prev_close if prev_close > 0 else 0
    intraday_surge = (high - prev_close) / prev_close if prev_close > 0 else 0
    vol_ratio = volume / prev_volume if prev_volume > 0 else 0

    is_flat = abs(change_pct) < 0.0005  # |涨跌| < 0.05%

    result_type = None
    weight = 1

    # ── 1. 盘中反转抛盘日（优先级最高） ──
    wick_ok = (body > 0 and upper_wick >= body * 1.5) or (amplitude > 0 and upper_wick >= amplitude * 0.5)
    if (close < open_p                                          # 收<开
            and intraday_surge >= 0.005                          # 盘中冲高≥前收×0.5%
            and vol_ratio > 1.1                                  # 量>前日×1.1
            and wick_ok                                          # 长上影
            and close <= midpt):                                 # 收≤中位
        result_type = 'intraday_rev'

    # ── 2. 特殊抛盘日（假阳线滞涨） ──
    elif (change_pct >= -0.003 and change_pct < 0.002            # 涨跌∈[-0.3%,0.2%]
            and intraday_surge >= 0.005                           # 冲高≥0.5%
            and vol_ratio > 1.1                                   # 量>前日×1.1
            and wick_ok):                                         # 长上影
        result_type = 'fake_yang'

    # ── 3. 标准抛盘日 ──
    elif (change_pct <= -0.001                                   # 跌≥0.1%
            and vol_ratio >= 1.0                                  # 量≥前日
            and not is_flat):                                     # 非平盘
        result_type = 'standard'

    else:
        return None

    # ── 重抛盘日检查 ──
    if (change_pct <= -0.015                                     # 跌≥1.5%
            and vol_ratio >= 1.0):                                # 放量
        weight = 2

    return {'type': result_type, 'weight': weight}


# ─── 确认日判定 ────────────────────────────────────────

def _is_confirmation_day(k: Dict, prev_k: Dict) -> bool:
    """检查是否为升势确认日（可抵消1个抛盘日）"""
    close = k['close']
    volume = k['volume']
    prev_close = prev_k['close']
    prev_volume = prev_k['volume']
    change_pct = (close - prev_close) / prev_close if prev_close > 0 else 0
    vol_ratio = volume / prev_volume if prev_volume > 0 else 0
    return change_pct >= 0.015 and vol_ratio >= 1.0


# ─── 主检测 ────────────────────────────────────────────

def detect(
    klines: List[Dict],
    params: Optional[Dict] = None,
) -> Dict:
    if params is None:
        params = load_params()
    p = params

    n = len(klines)
    if n < p['window_days'] + 1:
        return {'daily': [], 'counts': [], 'signals': []}

    # ── 单日判定 ──
    daily_results = []
    for i in range(n):
        k = klines[i]
        dist_info = None
        is_ftd = False

        if i > 0:
            prev_k = klines[i - 1]
            dist_info = _classify_day(k, prev_k)
            is_ftd = _is_confirmation_day(k, prev_k)

        # 标签
        label = ''
        if dist_info:
            t = dist_info['type']
            w = dist_info['weight']
            if t == 'intraday_rev': label = '🔄 盘中反转'
            elif t == 'fake_yang': label = '🟡 假阳线滞涨'
            elif t == 'standard': label = '📉 标准抛盘日' + (' ×2' if w > 1 else '')

        daily_results.append({
            'date': k['date'],
            'open': k['open'], 'high': k['high'], 'low': k['low'], 'close': k['close'],
            'volume': k['volume'],
            'dist_type': dist_info['type'] if dist_info else None,
            'dist_weight': dist_info['weight'] if dist_info else 0,
            'dist_label': label,
            'is_ftd': is_ftd,
        })

    # ── 25日滚动窗口计数（含确认日抵消） ──
    counts = []
    signals = []
    prev_level = 'none'

    for i in range(n):
        start = max(0, i - p['window_days'] + 1)
        window = daily_results[start:i + 1]

        # 加权计数
        raw_count = sum(d['dist_weight'] for d in window)

        # 确认日抵消：从最早的抛盘日开始抵消
        ftp_indices = [j for j, d in enumerate(window) if d['is_ftd'] and d['date'] == klines[start + j]['date']]
        offset = len(ftp_indices)  # 每个确认日抵消1个

        cnt = max(0, raw_count - offset)

        level = 'none'
        if cnt >= p['confirmed_count']:
            level = 'confirmed'
        elif cnt >= p['warning_count']:
            level = 'warning'

        counts.append({'date': klines[i]['date'], 'count': cnt, 'raw_count': raw_count, 'offset': offset, 'level': level})

        # 信号事件
        if level == 'confirmed' and prev_level != 'confirmed':
            signals.append({'type': 'bearish', 'signal_date': klines[i]['date'], 'count': cnt, 'level': 'distribution_confirmed', 'label': '🔴 大盘见顶确认'})
        elif level == 'warning' and prev_level == 'none':
            signals.append({'type': 'bearish', 'signal_date': klines[i]['date'], 'count': cnt, 'level': 'distribution_warning', 'label': '⚠️ 抛压增加'})
        elif level == 'none' and prev_level != 'none':
            signals.append({'type': 'bullish', 'signal_date': klines[i]['date'], 'count': cnt, 'level': 'distribution_cleared', 'label': '✅ 抛压解除'})
        prev_level = level

    return {'daily': daily_results, 'counts': counts, 'signals': signals}


# ─── 多指数联合检测 ────────────────────────────────────

def detect_multi(
    index_dict: Dict[str, List[Dict]],
    params: Optional[Dict] = None,
) -> Dict:
    """
    index_dict: {'000985': klines, '000300': klines, '000905': klines}
    返回每个指数的独立结果 + 联合信号
    """
    results = {}
    for idx, kl in index_dict.items():
        results[idx] = detect(kl, params)

    # 联合信号：同一天所有指数都是抛盘日时特别标注
    date_set = set()
    for idx, r in results.items():
        for d in r['daily']:
            date_set.add(d['date'])

    joint_dates = []
    for date in sorted(date_set):
        all_dist = True
        dist_count = 0
        for idx in results:
            for d in results[idx]['daily']:
                if d['date'] == date and d['dist_type']:
                    dist_count += 1
                    break
        if dist_count == len(index_dict):
            joint_dates.append(date)

    return {'individual': results, 'joint_dates': joint_dates}


# ─── CLI ───────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='抛盘日检测 v3.0')
    parser.add_argument('--index', type=str, default='000985')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--start', type=str, default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    start = args.start or (datetime.strptime(args.date, '%Y-%m-%d') - timedelta(days=730)).strftime('%Y-%m-%d')
    klines = conn.execute("""SELECT date, open, high, low, close, volume FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>=? AND date<=?
        ORDER BY date""", (args.index, start, args.date)).fetchall()
    conn.close()

    if len(klines) < 26:
        print(f"K线不足: {len(klines)} 条")
        sys.exit(1)

    daily = [dict(r) for r in klines]
    result = detect(daily)
    total_dist = sum(1 for d in result['daily'] if d['dist_type'])
    total_w = sum(d['dist_weight'] for d in result['daily'])
    print(f"🔍 {args.index} @ {args.date}")
    print(f"   抛盘日: {total_dist} 天 | 加权: {total_w} | 信号: {len(result['signals'])}")
    for s in result['signals'][-10:]:
        print(f"   {s['label']} @ {s['signal_date']} (计数={s['count']})")
