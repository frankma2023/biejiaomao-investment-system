"""
高潮见顶检测引擎（Climax Top）v1.0

欧奈尔体系卖出信号。检测股价在长期上涨后进入加速赶顶阶段的顶部形态。

信号分级：
  - climax_warning: 高潮进行中（加速赶顶周）
  - climax_confirmed: 高潮见顶确认（反转蜡烛或日线危险信号）

评分模型（满分 105%）：
  前期涨幅≥100%    25%
  1-3周加速25%-50%  30%  (+5% 日线加速验证)
  周量比≥2x         20%
  K线形态           10%
  RS(20)≥99          8%
  RS(250)≥95         7%
"""

import sys, os, argparse, sqlite3, yaml
from datetime import datetime, timedelta
from typing import Optional, Dict, List

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "climax_top",
    "display_name": "高潮见顶检测",
    "category": "sell_signal",
    "version": "1.0",
    "description": "检测高潮见顶形态（Climax Top）：长期上涨后加速赶顶 + 放量反转",
}


# ─── 默认参数 ──────────────────────────────────────────

def load_params():
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "climax_top.yaml")
    defaults = {
        'min_prior_gain': 1.0,           # 前期最小涨幅（100%）
        'climax_weeks_min': 2,           # 最少高潮周数
        'climax_weeks_max': 3,           # 最多高潮周数
        'climax_weekly_gain_min': 0.15,  # 单周最小涨幅（15%）
        'climax_total_gain_min': 0.25,   # 高潮期总涨幅（25%）
        'vol_ratio_spike': 2.0,          # 单周量/20周均量
        'vol_ratio_cumulative': 5.0,     # 3周累计量/20周均量
        'daily_accel_chg': 0.03,         # 日线加速：单日涨幅>3%
        'daily_accel_vol': 1.5,          # 日线加速：量/50均
        'daily_accel_days': 2,           # 日线加速：连续天数
        'daily_reversal_wick': 2.0,      # 提前确认：上影/实体
        'daily_reversal_vol': 3.0,       # 提前确认：量/50均
        'lookback_weeks': 78,            # 回溯周数（≈1.5年）
        'vol_lookback_weeks': 20,        # 量均线周数
        'score_warning': 65,             # 触发 warning 最低分
        'score_confirmed': 80,           # 触发 confirmed 最低分
        'rs20_threshold': 99,            # RS(20) 阈值
        'rs250_threshold': 95,           # RS(250) 阈值
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        defaults.update(cfg.get('climax_top', {}))
    return defaults


# ─── 工具函数 ──────────────────────────────────────────

def _sma(arr: List[float], n: int) -> float:
    if len(arr) < n:
        return sum(arr) / max(len(arr), 1)
    return sum(arr[-n:]) / n


def _aggr_weekly(daily: List[Dict]) -> List[Dict]:
    """日线 → 周线聚合。周一为每周日期标识。"""
    weeks = {}
    for k in daily:
        d_str = k['date']
        if '-' in d_str:
            dt = datetime.strptime(d_str, '%Y-%m-%d')
        else:
            dt = datetime.strptime(d_str, '%Y%m%d')
        # ISO 周一
        monday = dt - timedelta(days=dt.weekday())
        wk = monday.strftime('%Y-%m-%d')
        if wk not in weeks:
            weeks[wk] = {
                'date': wk, 'open': k['open'], 'high': k['high'],
                'low': k['low'], 'close': k['close'], 'volume': k['volume'],
            }
        else:
            w = weeks[wk]
            w['high'] = max(w['high'], k['high'])
            w['low'] = min(w['low'], k['low'])
            w['close'] = k['close']
            w['volume'] += k['volume']
    return sorted(weeks.values(), key=lambda x: x['date'])


# ─── 基准点查找 ────────────────────────────────────────

def _find_baseline(weekly: List[Dict], daily: List[Dict], date_str: str,
                   db_path: str = None) -> Optional[float]:
    """
    多层 fallback 查找前期涨幅起点：
      1. 最近一次基部突破或口袋支点突破信号
      2. 20 周整理区间后最高点
      3. 52 周低点
    """
    # Fallback 3: 52 周低点
    fallback_price = None
    if len(weekly) >= 52:
        fallback_price = min(w['low'] for w in weekly[-52:])

    # Fallback 1: 查数据库中的突破信号
    if db_path:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            # 查 base_breakout 信号
            bb = conn.execute("""SELECT signal_date, buy_point FROM base_breakout_signals
                WHERE stock_code=(SELECT stock_code FROM daily_kline WHERE date=? LIMIT 1)
                AND signal_date<=? ORDER BY signal_date DESC LIMIT 1""",
                (date_str, date_str)).fetchone()
            # 查 pocket_pivot 信号
            pp = conn.execute("""SELECT signal_date, close FROM pocket_pivot_signals
                WHERE stock_code=(SELECT stock_code FROM daily_kline WHERE date=? LIMIT 1)
                AND signal_date<=? ORDER BY signal_date DESC LIMIT 1""",
                (date_str, date_str)).fetchone()
            conn.close()

            candidates = []
            if bb:
                candidates.append((bb['signal_date'], bb['buy_point']))
            if pp:
                candidates.append((pp['signal_date'], pp['close']))
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                return candidates[0][1]
        except:
            pass

    return fallback_price


# ─── 主检测 ────────────────────────────────────────────

def detect(
    daily: List[Dict],
    params: Optional[Dict] = None,
    baseline_price: Optional[float] = None,
    rs_data: Optional[Dict] = None,
    stock_code: str = '',
) -> List[Dict]:
    """
    检测高潮见顶信号。

    Args:
        daily: 日线数据（至少 78 周 × 5 = 390 条）
        params: 参数覆盖
        baseline_price: 前期涨幅起点价格。None 则 fallback 到 52 周低点
        rs_data: {'rs20': float, 'rs250': float} 或 None
        stock_code: 股票代码（用于日志/调试）

    Returns:
        信号列表，每条含 score / signal_type / details
    """
    if params is None:
        params = load_params()
    p = params

    n = len(daily)
    min_daily = p['lookback_weeks'] * 5 + 10
    if n < min_daily:
        return []

    # 日→周聚合
    weekly = _aggr_weekly(daily)
    if len(weekly) < p['lookback_weeks']:
        return []

    # 确保 enough weeks
    weekly = weekly[-p['lookback_weeks']:]
    closes_w = [w['close'] for w in weekly]
    volumes_w = [w['volume'] for w in weekly]
    nw = len(weekly)

    # 基线价格
    if baseline_price is None:
        baseline_price = min(w['low'] for w in weekly[-52:]) if nw >= 52 else min(w['low'] for w in weekly)
    if baseline_price is None or baseline_price <= 0:
        return []

    # 20 周均量
    vol_ma20 = []
    for i in range(nw):
        start = max(0, i - p['vol_lookback_weeks'] + 1)
        avg = sum(volumes_w[start:i + 1]) / (i - start + 1)
        vol_ma20.append(avg)

    signals = []

    # 扫描每个可能的"高潮窗口"结束点
    for end_idx in range(p['climax_weeks_min'] + 1, nw - 1):
        for start_idx in range(max(0, end_idx - p['climax_weeks_max'] + 1), end_idx - p['climax_weeks_min'] + 1):
            seg = weekly[start_idx:end_idx + 1]
            if len(seg) < p['climax_weeks_min']:
                continue

            seg_closes = [w['close'] for w in seg]
            seg_volumes = [w['volume'] for w in seg]

            # 总涨幅
            seg_gain = (seg_closes[-1] - seg[0]['open']) / seg[0]['open'] if seg[0]['open'] > 0 else 0
            if seg_gain < p['climax_total_gain_min']:
                continue

            # 检查每周涨幅
            big_weeks = 0
            for j in range(len(seg)):
                prev_close = weekly[start_idx + j - 1]['close'] if j > 0 else seg[0]['open']
                w_gain = (seg[j]['close'] - prev_close) / prev_close if prev_close > 0 else 0
                if w_gain >= p['climax_weekly_gain_min']:
                    big_weeks += 1
            if big_weeks < p['climax_weeks_min']:
                continue

            # ── 评分计算 ──
            score = 0.0
            detail = {}

            # 1. 前期涨幅 (25%)
            prior_gain = (seg[0]['open'] - baseline_price) / baseline_price if baseline_price > 0 else 0
            if prior_gain >= p['min_prior_gain']:
                score += 25
                detail['prior_gain'] = round(prior_gain * 100, 1)
            else:
                detail['prior_gain'] = round(prior_gain * 100, 1)

            # 2. 加速 (30%)
            accel_score = 0
            if seg_gain >= 0.50:
                accel_score = 30
            elif seg_gain >= 0.35:
                accel_score = 22
            elif seg_gain >= 0.25:
                accel_score = 15
            detail['accel'] = {'gain': round(seg_gain * 100, 1), 'score': accel_score}

            # 日线加速验证 (+5%)
            daily_accel_ok = _check_daily_accel(daily, weekly, start_idx, end_idx, p)
            detail['daily_accel'] = daily_accel_ok
            if daily_accel_ok:
                accel_score += 5
            score += accel_score

            # 3. 周量比 (20%)
            vol_score = 0
            # 单周 2x
            any_spike = any(
                seg_volumes[j] >= vol_ma20[start_idx + j] * p['vol_ratio_spike']
                for j in range(len(seg))
            )
            # 累计 5x
            cum_vol = sum(seg_volumes)
            cum_ma = sum(vol_ma20[start_idx:end_idx + 1])
            cum_ok = cum_vol >= cum_ma * p['vol_ratio_cumulative']
            if any_spike:
                vol_score += 14
            if cum_ok:
                vol_score += 6
            score += min(vol_score, 20)
            detail['volume'] = {'spike': any_spike, 'cumulative': cum_ok, 'score': min(vol_score, 20)}

            # 4. K线形态 (10%)
            candle_score = 0
            # 长阳线
            long_candles = sum(
                1 for w in seg if w['close'] > w['open']
                and (w['close'] - w['open']) / w['open'] > 0.08
            )
            if long_candles >= 2:
                candle_score += 5
            # 长上影线
            for w in seg:
                body = abs(w['close'] - w['open'])
                upper = w['high'] - max(w['close'], w['open'])
                if body > 0 and upper / body > 1.5:
                    candle_score += 3
                    break
            score += min(candle_score, 10)
            detail['candles'] = {'long': long_candles, 'score': min(candle_score, 10)}

            # 5. RS 指标 (15%)
            if rs_data:
                rs20 = rs_data.get('rs20', 0) or 0
                rs250 = rs_data.get('rs250', 0) or 0
                if rs20 >= p['rs20_threshold']:
                    score += 8
                    detail['rs20'] = round(rs20, 1)
                if rs250 >= p['rs250_threshold']:
                    score += 7
                    detail['rs250'] = round(rs250, 1)

            detail['total_score'] = round(score, 1)

            # ── 信号分级 ──
            if score < p['score_warning']:
                continue

            signal_type = 'climax_warning'
            signal_level = 'moderate'

            # 检查是否应提升为 confirmed
            # a) 高潮周后一周反转
            if end_idx + 1 < nw:
                next_w = weekly[end_idx + 1]
                if (next_w['high'] <= seg[-1]['high']
                        and next_w['close'] < seg[-1]['close']):
                    signal_type = 'climax_confirmed'
                    signal_level = 'strong_sell'
                    detail['confirmed_by'] = 'weekly_reversal'

            # b) 日线提前确认
            if signal_type == 'climax_warning':
                if _check_daily_reversal(daily, weekly, start_idx, end_idx, p):
                    signal_type = 'climax_confirmed'
                    signal_level = 'strong_sell'
                    detail['confirmed_by'] = 'daily_reversal'

            if signal_type == 'climax_confirmed' and score < p['score_confirmed']:
                signal_type = 'climax_warning'
                signal_level = 'moderate'
                if 'confirmed_by' in detail:
                    del detail['confirmed_by']

            signals.append({
                'type': 'bearish',
                'signal_date': weekly[end_idx]['date'],
                'stock_code': stock_code,
                'signal_type': signal_type,
                'signal_level': signal_level,
                'score': round(score, 1),
                'climax_start': weekly[start_idx]['date'],
                'climax_end': weekly[end_idx]['date'],
                'climax_weeks': len(seg),
                'climax_gain_pct': round(seg_gain * 100, 1),
                'climax_high': max(w['high'] for w in seg),
                'baseline_price': round(baseline_price, 2),
                'prior_gain_pct': round(prior_gain * 100, 1),
                'details': detail,
            })

    return signals


def _check_daily_accel(daily, weekly, wk_start, wk_end, p) -> bool:
    """检查高潮周内是否存在日线加速（连续N天涨幅>3%+放量）"""
    seg_start_date = weekly[wk_start]['date']
    seg_end_date = weekly[wk_end]['date']
    seg_daily = [k for k in daily if seg_start_date <= k['date'] <= seg_end_date]
    if len(seg_daily) < 5:
        return False

    volumes = [k['volume'] for k in daily]
    vol_50_start = 0
    for idx, k in enumerate(daily):
        if k['date'] >= seg_start_date:
            vol_50_start = idx
            break

    vol_ma50 = sum(volumes[max(0, vol_50_start - 50):vol_50_start]) / min(50, vol_50_start)

    streak = 0
    for i in range(len(seg_daily)):
        k = seg_daily[i]
        chg = (k['close'] - k['open']) / k['open'] if k['open'] > 0 else 0
        if chg > p['daily_accel_chg'] and vol_ma50 > 0 and k['volume'] > vol_ma50 * p['daily_accel_vol']:
            streak += 1
        else:
            streak = 0
        if streak >= p['daily_accel_days']:
            return True
    return False


def _check_daily_reversal(daily, weekly, wk_start, wk_end, p) -> bool:
    """日线危险信号：放量冲高回落 或 连续新高后收低"""
    seg_start_date = weekly[wk_start]['date']
    seg_end_date = weekly[wk_end]['date']
    seg_daily = [k for k in daily if seg_start_date <= k['date'] <= seg_end_date]
    if len(seg_daily) < 3:
        return False

    volumes = [k['volume'] for k in daily]
    vol_50_start = 0
    for idx, k in enumerate(daily):
        if k['date'] >= seg_start_date:
            vol_50_start = idx
            break
    vol_ma50 = sum(volumes[max(0, vol_50_start - 50):vol_50_start]) / min(50, vol_50_start)

    # 放量冲高回落
    for k in seg_daily:
        body = abs(k['close'] - k['open'])
        upper = k['high'] - max(k['close'], k['open'])
        if body > 0 and upper > body * p['daily_reversal_wick']:
            if vol_ma50 > 0 and k['volume'] > vol_ma50 * p['daily_reversal_vol']:
                return True

    # 连续2天新高后收低
    streak = 0
    for i in range(len(seg_daily)):
        k = seg_daily[i]
        is_new_high = k['high'] > max(d['high'] for d in seg_daily[:i]) if i > 0 else True
        closed_low = k['close'] < k['open']
        if is_new_high and closed_low:
            streak += 1
        else:
            streak = 0
        if streak >= 2:
            return True

    return False


# ─── CLI ───────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='高潮见顶检测')
    parser.add_argument('--stock', type=str, default='600519')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--baseline', type=float, default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    klines = conn.execute("""SELECT date, open, high, low, close, volume FROM daily_kline
        WHERE stock_code=? AND date<=? AND date>=date(?, '-600 days')
        ORDER BY date""", (args.stock, args.date, args.date)).fetchall()
    conn.close()

    if len(klines) < 390:
        print(f"K线不足: {len(klines)} 条 (需要 ≥ 390)")
        sys.exit(1)

    daily = [dict(r) for r in klines]
    params = load_params()

    # 尝试找 baseline
    baseline = args.baseline
    if baseline is None:
        baseline = _find_baseline(_aggr_weekly(daily), daily, args.date, DB_PATH)

    signals = detect(daily, params, baseline_price=baseline, stock_code=args.stock)

    print(f"🔍 {args.stock} @ {args.date}  baseline={baseline}")
    print(f"   高潮见顶信号: {len(signals)}")
    for s in signals:
        typ = '🔴 confirmed' if s['signal_type'] == 'climax_confirmed' else '⚠️ warning'
        print(f"   {typ} | {s['climax_start']} → {s['climax_end']} "
              f"({s['climax_weeks']}周 +{s['climax_gain_pct']}%) "
              f"得分={s['score']} 前期涨幅={s['prior_gain_pct']}%")
