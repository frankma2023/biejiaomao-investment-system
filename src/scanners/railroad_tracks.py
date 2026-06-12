"""
铁轨线检测引擎 v1.0（Railroad Tracks）

三种形态类型：
  - 欧奈尔原著（周线 S级）：两根连续周K线，第一周巨幅波动，第二周价格停滞
  - 经典双根形态（日线 A级）：两根相邻日K线，上下影线极长，实体极短
  - 单根强化变体（日线 B级）：单根长腿十字星，影线极长，实体近乎零

信号级别：S > A+ > A > B
"""

import sys, os, argparse, sqlite3, yaml
from datetime import datetime, timedelta
from typing import Optional, Dict, List

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "railroad_tracks",
    "display_name": "铁轨线检测",
    "category": "sell_signal",
    "version": "1.0",
    "description": "检测铁轨线形态（Railroad Tracks）：周线卖出 + 日线反转预警",
}


# ─── 默认参数 ──────────────────────────────────────────

def load_params():
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "railroad_tracks.yaml")
    defaults = {
        # 周线——欧奈尔原著（S级）
        'w_prior_gain_min': 0.50,          # 前期最小涨幅（50%）
        'w_prior_lookback': 52,            # 52周低点作为基准
        'w_week1_amp_min': 0.15,           # 第一周最小振幅（15%）
        'w_week2_entity_ratio_max': 0.30,  # 第二周实体/振幅上限（30%）
        'w_week2_gain_min': -0.02,         # 第二周涨幅下限（-2%）
        'w_week2_gain_max': 0.02,          # 第二周涨幅上限（2%）
        'w_week2_high_strict': True,       # True=严格≤前周高点, False=≤101%
        'w_vol_lookback': 20,              # 量均线周数
        'w_week1_vol_ratio': 1.5,          # 第一周量/20周均量
        'w_week2_vol_ratio': 1.2,          # 第二周量/20周均量
        'w_vol_required_week1': False,     # 回测开关：要求Week1放量
        'w_vol_required_week2': False,     # 回测开关：要求Week2放量
        'w_signal_ttl_weeks': 8,           # 信号有效期（周）

        # 日线——经典双根（A级）
        'd_double_body_max': 0.20,        # 实体≤振幅×20%
        'd_double_shadow_min': 0.35,      # 上影≥振幅×35% 且 下影≥振幅×35%
        'd_double_vol_lookback': 5,       # 量均线天数
        'd_double_require_opposite': False, # 是否要求一阴一阳
        'd_double_near_high': 0.10,       # 距52周高点<10%

        # 日线——单根强化（B级）
        'd_single_body_max': 0.15,        # 实体≤振幅×15%
        'd_single_shadow_min': 0.40,      # 上影≥振幅×40% 且 下影≥振幅×40%
        'd_single_vol_ratio': 1.2,        # 量>前5日均量×1.2
        'd_single_near_high': 0.10,       # 距52周高点<10%
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        defaults.update(cfg.get('railroad_tracks', {}))
    return defaults


# ─── 工具函数 ──────────────────────────────────────────

def _aggr_weekly(daily: List[Dict]) -> List[Dict]:
    """日线→周线聚合"""
    weeks = {}
    for k in daily:
        d_str = k['date']
        dt = datetime.strptime(d_str, '%Y-%m-%d') if '-' in d_str else datetime.strptime(d_str, '%Y%m%d')
        monday = dt - timedelta(days=dt.weekday())
        wk = monday.strftime('%Y-%m-%d')
        if wk not in weeks:
            weeks[wk] = {'date': wk, 'open': k['open'], 'high': k['high'],
                         'low': k['low'], 'close': k['close'], 'volume': k['volume']}
        else:
            w = weeks[wk]
            w['high'] = max(w['high'], k['high'])
            w['low'] = min(w['low'], k['low'])
            w['close'] = k['close']
            w['volume'] += k['volume']
    return sorted(weeks.values(), key=lambda x: x['date'])


def _sma(arr: List[float], n: int) -> float:
    return sum(arr[-n:]) / max(len(arr[-n:]), 1)


# ─── 周线检测（欧奈尔原著 S级）─────────────────────────

def detect_weekly(
    weekly: List[Dict],
    params: Dict,
    stock_code: str = '',
) -> List[Dict]:
    p = params
    n = len(weekly)
    if n < max(p['w_prior_lookback'], p['w_vol_lookback']) + 2:
        return []

    volumes_w = [w['volume'] for w in weekly]
    signals = []

    for i in range(p['w_prior_lookback'], n - 1):
        w1 = weekly[i]
        w2 = weekly[i + 1]

        # 前期涨幅（52周低点）
        low_52 = min(wk['low'] for wk in weekly[max(0, i - p['w_prior_lookback']):i + 1])
        prior_gain = (w1['close'] - low_52) / low_52 if low_52 > 0 else 0
        if prior_gain < p['w_prior_gain_min']:
            continue

        # 第一周振幅
        w1_amp = (w1['high'] - w1['low']) / w1['low'] if w1['low'] > 0 else 0
        if w1_amp < p['w_week1_amp_min']:
            continue

        # 第二周实体
        w2_amp = (w2['high'] - w2['low']) / w2['low'] if w2['low'] > 0 else 0
        w2_body = abs(w2['close'] - w2['open']) / w2['low'] if w2['low'] > 0 else 0
        entity_ratio = w2_body / w2_amp if w2_amp > 0 else 1
        if entity_ratio > p['w_week2_entity_ratio_max']:
            continue

        # 第二周涨幅
        w2_gain = (w2['close'] - w1['close']) / w1['close'] if w1['close'] > 0 else 0
        if w2_gain < p['w_week2_gain_min'] or w2_gain > p['w_week2_gain_max']:
            continue

        # 第二周未突破第一周高点
        if p['w_week2_high_strict']:
            if w2['high'] > w1['high']:
                continue
        else:
            if w2['high'] > w1['high'] * 1.01:
                continue

        # 成交量（辅助）
        vol_ma20 = _sma(volumes_w[:i + 1], p['w_vol_lookback'])
        w1_vol_ok = w1['volume'] >= vol_ma20 * p['w_week1_vol_ratio']
        w2_vol_ok = w2['volume'] >= vol_ma20 * p['w_week2_vol_ratio']
        vol_count = (1 if w1_vol_ok else 0) + (1 if w2_vol_ok else 0)

        # 成交量强制开关
        if p.get('w_vol_required_week1', False) and not w1_vol_ok:
            continue
        if p.get('w_vol_required_week2', False) and not w2_vol_ok:
            continue

        # 置信度
        if vol_count == 2:
            confidence = 'high'
        elif vol_count == 1:
            confidence = 'medium'
        else:
            confidence = 'low'

        signals.append({
            'signal_date': w2['date'],
            'stock_code': stock_code,
            'signal_type': 'railroad_weekly',
            'signal_level': 'strong_sell',
            'label': '🔴 铁轨线（周线卖出）',
            'details': {
                'week1_date': w1['date'], 'week2_date': w2['date'],
                'week1_amp': round(w1_amp * 100, 1),
                'week2_gain': round(w2_gain * 100, 2),
                'prior_gain': round(prior_gain * 100, 1),
                'vol_week1': w1_vol_ok, 'vol_week2': w2_vol_ok,
                'vol_count': vol_count, 'confidence': confidence,
            },
            'ttl_weeks': p['w_signal_ttl_weeks'],
        })

    return signals


# ─── 日线双根检测（A级）─────────────────────────────

def detect_daily_double(
    daily: List[Dict],
    params: Dict,
    weekly_data: Optional[List[Dict]] = None,
    stock_code: str = '',
) -> List[Dict]:
    p = params
    n = len(daily)
    if n < 2:
        return []

    # 52周高点
    high_52 = max(k['high'] for k in daily[-250:]) if len(daily) >= 250 else max(k['high'] for k in daily)

    volumes_d = [k['volume'] for k in daily]
    signals = []

    for i in range(50, n - 2):  # 跳过前50天（均线未稳定）
        d1 = daily[i]
        d2 = daily[i + 1]

        # 位置：上升趋势末端（收盘>MA50 且 距52周高点<10%）
        ma50 = _sma([k['close'] for k in daily[:i + 1]], 50)
        if d1['close'] <= ma50 and d2['close'] <= ma50:
            continue
        if d1['close'] < high_52 * (1 - p['d_double_near_high']):
            continue

        # 实体
        amp1 = (d1['high'] - d1['low']) / d1['low'] if d1['low'] > 0 else 0
        amp2 = (d2['high'] - d2['low']) / d2['low'] if d2['low'] > 0 else 0
        body1 = abs(d1['close'] - d1['open']) / d1['low'] if d1['low'] > 0 else 0
        body2 = abs(d2['close'] - d2['open']) / d2['low'] if d2['low'] > 0 else 0
        if amp1 == 0 or amp2 == 0:
            continue
        if body1 / amp1 > p['d_double_body_max'] or body2 / amp2 > p['d_double_body_max']:
            continue

        # 影线：上下影都长
        upper1 = d1['high'] - max(d1['close'], d1['open'])
        lower1 = min(d1['close'], d1['open']) - d1['low']
        upper2 = d2['high'] - max(d2['close'], d2['open'])
        lower2 = min(d2['close'], d2['open']) - d2['low']
        amp1_abs = d1['high'] - d1['low']
        amp2_abs = d2['high'] - d2['low']
        if amp1_abs == 0 or amp2_abs == 0:
            continue
        if (upper1 / amp1_abs < p['d_double_shadow_min'] or lower1 / amp1_abs < p['d_double_shadow_min']):
            continue
        if (upper2 / amp2_abs < p['d_double_shadow_min'] or lower2 / amp2_abs < p['d_double_shadow_min']):
            continue

        # 阴阳检查
        is_opposite = (d1['close'] > d1['open']) != (d2['close'] > d2['open'])
        if p.get('d_double_require_opposite', False) and not is_opposite:
            continue

        # 成交量
        vol_ma5 = _sma(volumes_d[:i + 1], 5)
        vol_ok = d1['volume'] > vol_ma5 and d2['volume'] > vol_ma5

        # 后续确认
        d3 = daily[i + 2] if i + 2 < n else None
        confirmed = d3 is not None and d3['close'] < min(d1['low'], d2['low'])

        level = 'A_plus' if is_opposite else 'A'
        label = '🟠 铁轨预警+' if is_opposite else '🟡 铁轨预警'
        if confirmed:
            label += ' ✓确认'

        signals.append({
            'signal_date': d2['date'],
            'stock_code': stock_code,
            'signal_type': 'railroad_daily_double',
            'signal_level': level,
            'label': label,
            'details': {
                'day1_date': d1['date'], 'day2_date': d2['date'],
                'is_opposite': is_opposite, 'vol_ok': vol_ok, 'confirmed': confirmed,
            },
        })

    return signals


# ─── 日线单根检测（B级）─────────────────────────────

def detect_daily_single(
    daily: List[Dict],
    params: Dict,
    stock_code: str = '',
) -> List[Dict]:
    p = params
    n = len(daily)
    if n < 2:
        return []

    high_52 = max(k['high'] for k in daily[-250:]) if len(daily) >= 250 else max(k['high'] for k in daily)
    volumes_d = [k['volume'] for k in daily]
    signals = []

    for i in range(50, n - 1):
        d = daily[i]

        # 位置：上升趋势末端
        ma50 = _sma([k['close'] for k in daily[:i + 1]], 50)
        if d['close'] <= ma50:
            continue
        if d['close'] < high_52 * (1 - p['d_single_near_high']):
            continue

        # 实体
        amp = (d['high'] - d['low']) / d['low'] if d['low'] > 0 else 0
        body = abs(d['close'] - d['open']) / d['low'] if d['low'] > 0 else 0
        if amp == 0 or body / amp > p['d_single_body_max']:
            continue

        # 影线
        upper = d['high'] - max(d['close'], d['open'])
        lower = min(d['close'], d['open']) - d['low']
        amp_abs = d['high'] - d['low']
        if amp_abs == 0:
            continue
        if upper / amp_abs < p['d_single_shadow_min'] or lower / amp_abs < p['d_single_shadow_min']:
            continue

        # 成交量
        vol_ma5 = _sma(volumes_d[:i + 1], 5)
        vol_ok = d['volume'] > vol_ma5 * p['d_single_vol_ratio']

        # 后续确认
        d_next = daily[i + 1]
        confirmed = d_next['close'] < d['low']

        label = '⚪ 长腿十字星'
        if confirmed:
            label += ' ✓确认'

        signals.append({
            'signal_date': d['date'],
            'stock_code': stock_code,
            'signal_type': 'railroad_daily_single',
            'signal_level': 'B',
            'label': label,
            'details': {
                'day_date': d['date'], 'vol_ok': vol_ok, 'confirmed': confirmed,
            },
        })

    return signals


# ─── 综合检测 ──────────────────────────────────────────

def detect_all(
    daily: List[Dict],
    params: Optional[Dict] = None,
    stock_code: str = '',
) -> Dict:
    if params is None:
        params = load_params()
    p = params

    weekly = _aggr_weekly(daily)

    ws = detect_weekly(weekly, p, stock_code)
    dd = detect_daily_double(daily, p, weekly, stock_code)
    ds = detect_daily_single(daily, p, stock_code)

    return {
        'weekly': weekly[-120:],
        'daily': daily,
        'signals_weekly': ws,
        'signals_daily_double': dd,
        'signals_daily_single': ds,
        'all_signals': sorted(ws + dd + ds, key=lambda x: x['signal_date']),
    }


def detect(
    daily: List[Dict],
    params: Optional[Dict] = None,
    stock_code: str = '',
) -> List[Dict]:
    """引擎注册标准接口。调用 detect_all 并提取 all_signals 列表。"""
    result = detect_all(daily, params, stock_code)
    signals = result.get('all_signals', [])
    for s in signals:
        s['type'] = 'bearish'
    return signals


# ─── CLI ───────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='铁轨线检测')
    parser.add_argument('--stock', type=str, default='600519')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    klines = conn.execute("""SELECT date, open, high, low, close, volume FROM daily_kline
        WHERE stock_code=? AND date<=? AND date>=date(?, '-600 days')
        ORDER BY date""", (args.stock, args.date, args.date)).fetchall()
    conn.close()

    daily = [dict(r) for r in klines]
    result = detect_all(daily, stock_code=args.stock)

    print(f"🔍 {args.stock}")
    print(f"   周线铁轨线(S): {len(result['signals_weekly'])}")
    print(f"   日线双根(A):   {len(result['signals_daily_double'])}")
    print(f"   日线单根(B):   {len(result['signals_daily_single'])}")
    for s in result['all_signals'][-10:]:
        print(f"   {s['label']} @ {s['signal_date']}")
