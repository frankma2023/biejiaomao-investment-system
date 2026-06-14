"""
量价背离检测引擎 v1.0（Volume Divergence）

检测个股的量价背离信号，识别机构出货行为。
V1.0 实现 4 种子信号：
  - 新高缩量：创新高 + 成交量萎缩
  - 滞涨放量：横盘 + 成交量放大
  - 下跌放量：下跌日放量（个股版抛盘日）
  - 回升无量：反弹无量（死猫跳）
V1.1 预留：逐波萎缩

信号级别（三级）：
  🔴 strong   — 下跌放量 / 滞涨放量 → 清仓
  🟡 moderate — 新高缩量 → 减仓
  ⚠️ weak    — 回升无量 → 减仓观察

用法:
  python -m src.scanners.volume_divergence --stock 600519 --date 2026-05-17
"""

import sys, os, argparse, sqlite3, yaml
from datetime import datetime, timedelta
from typing import Optional, Dict, List

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "volume_divergence",
    "display_name": "量价背离检测",
    "category": "sell_signal",
    "version": "1.0",
    "description": "检测个股量价背离信号：新高缩量/滞涨放量/下跌放量/回升无量",
}


# ══════════════════════════════════════════════════════════
def load_params() -> Dict:
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "volume_divergence.yaml")
    defaults = {
        'vol_ma_days': 50,
        'enable_new_high_shrink': True,
        'enable_stall_surge': True,
        'enable_drop_surge': True,
        'enable_rally_dry': True,
        'enable_wave_shrink': False,
        'enable_rising_shrink': True,
        'enable_high_shrink': True,
        'high_shrink_dist_max': 0.10,
        'high_shrink_vol_ratio_max': 0.70,
        'enable_composite': True,
        'composite': {
            'threshold': 40,
            'factor1': 25,
            'factor2': 25,
            'factor3': 25,
            'factor4': 25,
        },
        'nhs_lookback_days': 20,
        'nhs_vol_ratio_max': 0.85,
        'ss_lookback_days': 10,
        'ss_price_range_max': 0.08,
        'ss_prior_gain_min': 0.05,
        'ss_vol_ratio_min': 1.20,
        'ds_decline_min': 0.015,
        'ds_vol_ratio_min': 1.20,
        'rd_low_lookback': 20,
        'rd_rally_min': 0.02,
        'rd_prior_decline_min': 0.03,
        'rd_vol_ratio_max': 0.80,
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        # 解包顶层 volume_divergence 键
        if 'volume_divergence' in cfg:
            cfg = cfg['volume_divergence']
        for k, v in cfg.items():
            if isinstance(v, dict):
                # composite 保持嵌套结构，其他平铺
                if k == 'composite':
                    defaults['composite'].update(v)
                else:
                    prefix = {'new_high_shrink': 'nhs', 'stall_surge': 'ss',
                              'drop_surge': 'ds', 'rally_dry': 'rd'}.get(k, k)
                    for kk, vv in v.items():
                        defaults[f'{prefix}_{kk}'] = vv
            else:
                defaults[k] = v
    return defaults


# ══════════════════════════════════════════════════════════
def _sma(arr: List[float], n: int) -> float:
    if len(arr) < n:
        return sum(arr) / max(len(arr), 1)
    return sum(arr[-n:]) / n


def _sma_at(arr: List[float], n: int, idx: int) -> float:
    start = max(0, idx - n + 1)
    vals = arr[start:idx + 1]
    return sum(vals) / max(len(vals), 1)


# ══════════════════════════════════════════════════════════
def detect(
    daily: List[Dict],
    params: Optional[Dict] = None,
    stock_code: str = '',
) -> List[Dict]:
    """检测量价背离信号，每个子信号独立返回一行。"""
    if params is None:
        params = load_params()

    n = len(daily)
    if n < params['vol_ma_days'] + 10:
        return []

    closes = [r['close'] for r in daily]
    highs = [r['high'] for r in daily]
    lows = [r['low'] for r in daily]
    volumes = [r['volume'] for r in daily]
    dates = [r['date'] for r in daily]

    signals = []
    today_idx = n - 1

    # 均量
    vol_ma = _sma_at(volumes, params['vol_ma_days'], today_idx - 1)
    if vol_ma <= 0:
        return []
    vol_ma50_seq = [_sma_at(volumes, params['vol_ma_days'], i) for i in range(n)]

    vol_ratio = volumes[today_idx] / vol_ma

    # ─── 信号1：新高缩量 ───
    if params.get('enable_new_high_shrink', True):
        lb = params.get('nhs_lookback_days', 20)
        recent_high = max(closes[max(0, today_idx - lb):today_idx])
        if closes[today_idx] > recent_high:
            # 条件A：相对均量萎缩
            shrink_a = vol_ratio < params['nhs_vol_ratio_max']
            # 条件B：相对前高量萎缩（前40日最高量日，当日量 < 其60%）
            seg_vols_b = volumes[max(0, today_idx - 40):today_idx]
            max_vol_idx = seg_vols_b.index(max(seg_vols_b)) + max(0, today_idx - 40)
            prior_peak_vol = volumes[max_vol_idx]
            shrink_b = prior_peak_vol > 0 and volumes[today_idx] < prior_peak_vol * params.get('nhs_prior_peak_vol_ratio', 0.60)
            if shrink_a or shrink_b:
                signals.append({'type': 'bearish', 
                        'signal_date': dates[today_idx],
                        'stock_code': stock_code,
                        'signal_type': 'new_high_shrink',
                        'signal_level': 'moderate',
                        'label': '🟡 新高缩量',
                        'details': {
                            'high': highs[today_idx],
                            'recent_high': recent_high,
                            'vol_ratio': round(vol_ratio, 2),
                        },
                    })
    
    # ─── 信号2：滞涨放量 ───
    if params.get('enable_stall_surge', True):
        lb = params['ss_lookback_days']
        if today_idx >= lb + 20:
            # 前置验证：横盘之前收盘价趋势向上（首尾涨幅 ≥ 阈值）
            pre_start = max(0, today_idx - lb - 20)
            pre_end = today_idx - lb
            pre_close_start = closes[pre_start]
            pre_close_end = closes[pre_end]
            pre_gain = (pre_close_end - pre_close_start) / pre_close_start if pre_close_start > 0 else 0
            if pre_gain >= params.get('ss_prior_gain_min', 0.05):
                seg_closes = closes[today_idx - lb + 1:today_idx + 1]
                price_range = (max(seg_closes) - min(seg_closes)) / max(seg_closes) if max(seg_closes) > 0 else 99
                seg_vols = volumes[today_idx - lb + 1:today_idx + 1]
                seg_vol_ma = sum(seg_vols) / len(seg_vols)
                if price_range < params['ss_price_range_max'] and seg_vol_ma / vol_ma > params['ss_vol_ratio_min']:
                    signals.append({'type': 'bearish', 
                        'signal_date': dates[today_idx],
                        'stock_code': stock_code,
                        'signal_type': 'stall_surge',
                        'signal_level': 'strong',
                        'label': '🔴 滞涨放量',
                        'details': {
                            'price_range_pct': round(price_range * 100, 1),
                            'seg_vol_ratio': round(seg_vol_ma / vol_ma, 2),
                        },
                    })

    # ─── 信号3：下跌放量 ───
    if params.get('enable_drop_surge', True) and today_idx > 0:
        decline = (closes[today_idx - 1] - closes[today_idx]) / closes[today_idx - 1]
        if decline >= params['ds_decline_min'] and vol_ratio > params['ds_vol_ratio_min']:
            signals.append({'type': 'bearish', 
                'signal_date': dates[today_idx],
                'stock_code': stock_code,
                'signal_type': 'drop_surge',
                'signal_level': 'strong',
                'label': '🔴 下跌放量',
                'details': {
                    'decline_pct': round(decline * 100, 1),
                    'vol_ratio': round(vol_ratio, 2),
                },
            })

    # ─── 信号4：回升无量 ───
    if params.get('enable_rally_dry', True) and today_idx >= 10:
        lb = params['rd_low_lookback']
        low_20d = min(lows[max(0, today_idx - lb):today_idx])
        rally = (closes[today_idx] - low_20d) / low_20d if low_20d > 0 else 0
        # 前10天跌幅
        prior_low = min(lows[max(0, today_idx - 10):today_idx])
        prior_high = max(highs[max(0, today_idx - 10):today_idx])
        prior_decline = (prior_high - prior_low) / prior_high if prior_high > 0 else 0
        if (rally >= params['rd_rally_min']
                and prior_decline >= params['rd_prior_decline_min']
                and vol_ratio < params['rd_vol_ratio_max']
                and closes[today_idx] > closes[today_idx - 1]):
            signals.append({'type': 'bearish', 
                'signal_date': dates[today_idx],
                'stock_code': stock_code,
                'signal_type': 'rally_dry',
                'signal_level': 'weak',
                'label': '⚠️ 回升无量',
                'details': {
                    'rally_pct': round(rally * 100, 1),
                    'prior_decline_pct': round(prior_decline * 100, 1),
                    'vol_ratio': round(vol_ratio, 2),
                },
            })

    # ─── 信号5：上涨缩量（简化逐波萎缩）───
    if params.get('enable_rising_shrink', True) and today_idx >= 20:
        seg_recent_vols = volumes[today_idx - 9:today_idx + 1]
        seg_prior_vols = volumes[max(0, today_idx - 20):today_idx - 9]
        if len(seg_prior_vols) >= 5:
            recent_ma = sum(seg_recent_vols) / len(seg_recent_vols)
            prior_ma = sum(seg_prior_vols) / len(seg_prior_vols)
            recent_high_10 = max(highs[today_idx - 9:today_idx + 1])
            prior_high_10 = max(highs[max(0, today_idx - 20):today_idx - 9])
            if recent_high_10 > prior_high_10 and prior_ma > 0 and recent_ma < prior_ma * 0.70:
                signals.append({'type': 'bearish', 
                    'signal_date': dates[today_idx],
                    'stock_code': stock_code,
                    'signal_type': 'rising_shrink',
                    'signal_level': 'moderate',
                    'label': '🟡 上涨缩量',
                    'details': {
                        'recent_vol_ma': round(recent_ma, 0),
                        'prior_vol_ma': round(prior_ma, 0),
                        'vol_ratio_10v20': round(recent_ma / prior_ma, 2),
                    },
                })

    # ─── 信号6：高位缩量 ───
    if params.get('enable_high_shrink', True) and today_idx >= 50:
        high_200d = max(highs[max(0, today_idx - 200):today_idx + 1])
        if high_200d > 0 and closes[today_idx] > high_200d * (1 - params.get('high_shrink_dist_max', 0.10)):
            seg_c = closes[today_idx - 9:today_idx + 1]
            price_range = (max(seg_c) - min(seg_c)) / max(seg_c) if max(seg_c) > 0 else 99
            seg_v = volumes[today_idx - 9:today_idx + 1]
            seg_v_ma = sum(seg_v) / len(seg_v)
            if price_range < 0.08 and seg_v_ma < vol_ma * params.get('high_shrink_vol_ratio_max', 0.70):
                signals.append({'type': 'bearish', 
                    'signal_date': dates[today_idx],
                    'stock_code': stock_code,
                    'signal_type': 'high_shrink',
                    'signal_level': 'moderate',
                    'label': '🟡 高位缩量',
                    'details': {
                        'dist_from_high_pct': round((1 - closes[today_idx] / high_200d) * 100, 1),
                        'seg_vol_ratio': round(seg_v_ma / vol_ma, 2),
                    },
                })

    # ─── 综合因子评分 ───
    if params.get('enable_composite', True) and today_idx >= 60:
        score = 0
        reasons = []
        cp = params.get('composite', {}) or {}
        fw = {'f1': cp.get('factor1', 25), 'f2': cp.get('factor2', 25), 'f3': cp.get('factor3', 25), 'f4': cp.get('factor4', 25)}
        threshold = cp.get('threshold', 40)
        # 因子1: VR弹性 (0-25) — 价格加速但VR未同步放大
        seg30_c = closes[today_idx - 29:today_idx + 1]
        seg30_v = volumes[today_idx - 29:today_idx + 1]
        seg30_vr = [v / vol_ma50_seq[today_idx - 29 + j] if vol_ma50_seq[today_idx - 29 + j] > 0 else 0
                    for j, v in enumerate(seg30_v)]
        seg_pc = closes[max(0, today_idx - 59):today_idx - 29]
        seg_pv = volumes[max(0, today_idx - 59):today_idx - 29]
        seg_pvr = [v / vol_ma50_seq[max(0, today_idx - 59) + j] if vol_ma50_seq[max(0, today_idx - 59) + j] > 0 else 0
                   for j, v in enumerate(seg_pv)]
        if seg_pc and seg30_c[0] > 0 and seg_pc[0] > 0:
            near_chg = (seg30_c[-1] - seg30_c[0]) / seg30_c[0]
            prior_chg = (seg_pc[-1] - seg_pc[0]) / seg_pc[0] if seg_pc[0] > 0 else 0
            near_vr = sum(seg30_vr) / len(seg30_vr) if seg30_vr else 0
            prior_vr = sum(seg_pvr) / len(seg_pvr) if seg_pvr else 0
            if prior_chg > 0 and near_chg > prior_chg * 1.5:
                vr_growth = (near_vr - prior_vr) / prior_vr if prior_vr > 0 else 0
                if vr_growth < 0.20:
                    score += fw['f1']; reasons.append('VR_elastic')

        # 因子2: 量趋势斜率 (0-25) — 近30天量斜率为负 + 价格新高
        if len(seg30_v) >= 10:
            xs = list(range(len(seg30_v))); xm = sum(xs) / len(xs); ym = sum(seg30_v) / len(seg30_v)
            num = sum((x - xm) * (v - ym) for x, v in zip(xs, seg30_v))
            den = sum((x - xm) ** 2 for x in xs)
            slope = num / den if den else 0; slope_pct = slope / ym * 100 if ym else 0
            if slope_pct < 0 and max(highs[today_idx - 29:today_idx + 1]) > max(highs[max(0, today_idx - 59):today_idx - 29]):
                score += fw['f2']; reasons.append('vol_slope_neg')

        # 因子3: 量峰衰减比 (0-25) — 近30天最高量 / 前30天最高量 < 0.7
        prior_vp = max(seg_pv) if seg_pv else 0; near_vp = max(seg30_v)
        if prior_vp > 0 and near_vp < prior_vp * 0.70:
            score += fw['f3']; reasons.append('vol_peak_decay')

        # 因子4: 均价量背离 (0-25) — 近10天涨>前10天涨，但近10天均量 < 前10天均量×0.7
        seg10_c = closes[today_idx - 9:today_idx + 1]; seg10_v = volumes[today_idx - 9:today_idx + 1]
        sp10_c = closes[max(0, today_idx - 19):today_idx - 9]; sp10_v = volumes[max(0, today_idx - 19):today_idx - 9]
        if len(sp10_c) >= 5 and seg10_c[0] > 0 and sp10_c[0] > 0:
            chg10 = (seg10_c[-1] - seg10_c[0]) / seg10_c[0]
            chg_p10 = (sp10_c[-1] - sp10_c[0]) / sp10_c[0] if sp10_c[0] > 0 else 0
            ma10 = sum(seg10_v) / len(seg10_v); ma_p10 = sum(sp10_v) / len(sp10_v)
            if chg10 > chg_p10 and ma_p10 > 0 and ma10 < ma_p10 * 0.70:
                score += fw['f4']; reasons.append('price_up_vol_down')

        if score >= threshold:
            level = 'strong' if score >= 80 else 'moderate'
            signals.append({'type': 'bearish', 
                'signal_date': dates[today_idx], 'stock_code': stock_code,
                'signal_type': 'composite_divergence', 'signal_level': level,
                'label': f'{"🔴" if score >= 80 else "🟡"} 综合背离({score}分)',
                'details': {'score': score, 'factors': reasons},
            })

    return signals


# ══════════════════════════════════════════════════════════
def detect_range(
    daily: List[Dict],
    params: Optional[Dict] = None,
    stock_code: str = '',
) -> List[Dict]:
    """逐日扫描整个日期范围，返回所有信号（按类型去重并按日期排序）。"""
    if params is None:
        params = load_params()
    n = len(daily)
    if n < 60:
        return []
    all_signals = []
    seen = set()
    last_of_type = {}
    for i in range(60, n):
        seg = daily[:i + 1]
        sigs = detect(seg, params, stock_code)
        for s in sigs:
            key = f"{s['signal_date']}_{s['signal_type']}"
            if key not in seen:
                # 去重：同类型信号间隔 < 5天则跳过
                st = s['signal_type']
                if st in last_of_type:
                    d1 = datetime.strptime(last_of_type[st], '%Y-%m-%d')
                    d2 = datetime.strptime(s['signal_date'], '%Y-%m-%d')
                    if (d2 - d1).days < 5:
                        continue
                last_of_type[st] = s['signal_date']
                seen.add(key)
                all_signals.append(s)
    return sorted(all_signals, key=lambda x: x['signal_date'])

def detect_all(
    daily: List[Dict],
    params: Optional[Dict] = None,
    stock_code: str = '',
) -> Dict:
    """综合检测，返回 daily + signals。"""
    if params is None:
        params = load_params()
    signals = detect(daily, params, stock_code)
    return {
        'daily': daily,
        'signals': signals,
        'stock_code': stock_code,
    }


# ══════════════════════════════════════════════════════════
def get_diag(daily: List[Dict], params=None, stock_code='') -> Dict:
    """诊断信息。"""
    if params is None:
        params = load_params()
    signals = detect(daily, params, stock_code)
    return {
        'date': daily[-1]['date'] if daily else '',
        'stock': stock_code,
        'total_kline': len(daily),
        'signals_count': len(signals),
        'signals': [{'type': s['signal_type'], 'level': s['signal_level']} for s in signals],
    }


# ══════════════════════════════════════════════════════════
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='量价背离检测')
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

    print(f"🔍 {args.stock} @ {args.date}")
    for s in result['signals']:
        print(f"   {s['label']} | {s['signal_date']} | {s['details']}")
    if not result['signals']:
        print(f"   无量价背离信号")
