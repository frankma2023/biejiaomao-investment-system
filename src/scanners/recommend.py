"""
建议合成层 - 规则引擎

读取全部引擎信号 + TA-Lib指标数据，生成中文分析建议。
建议层是裁判，不是选手：不修改任何引擎输出，只做综合解读。
"""

import sys, os
from collections import defaultdict
from datetime import datetime, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))


def generate(signals, indicators, klines=None, code_name=None):
    """
    Args:
        signals: List[dict]
        indicators: dict of lists
        klines: optional list of dicts
        code_name: optional str
    Returns:
        dict with trend, signals_summary, assessment, advice, score
    """
    def last_valid(arr):
        if not arr:
            return None
        for v in reversed(arr):
            if v is not None:
                return v
        return None

    def count_recent(sigs, days=90):
        if not sigs or not klines:
            return 0
        today = klines[-1]['date'] if klines else sigs[-1]['date']
        try:
            cutoff = (datetime.strptime(today, '%Y-%m-%d') - timedelta(days=days)).strftime('%Y-%m-%d')
        except ValueError:
            cutoff = '2000-01-01'
        return sum(1 for s in sigs if s['date'] >= cutoff)

    latest_close = klines[-1]['close'] if klines else None
    sma50_val = last_valid(indicators.get('sma50', []))
    sma200_val = last_valid(indicators.get('sma250', []))
    rsi_val = last_valid(indicators.get('rsi14', []))
    bb_upper_val = last_valid(indicators.get('bb_upper', []))
    bb_lower_val = last_valid(indicators.get('bb_lower', []))
    vol_ma_val = last_valid(indicators.get('vol_ma50', []))

    bullish_signals = [s for s in signals if s.get('type') == 'bullish']
    bearish_signals = [s for s in signals if s.get('type') == 'bearish']
    bullish_signals_count = len(bullish_signals)
    bearish_signals_count = len(bearish_signals)

    by_source = {}
    for s in signals:
        src = s.get('source', 'unknown')
        if src not in by_source:
            by_source[src] = {'bullish': 0, 'bearish': 0}
        by_source[src][s.get('type', 'bullish')] += 1

    resonance_dates = _detect_resonance(signals, window=5, min_sources=2)

    # Trend
    trend_parts = []
    if latest_close and sma50_val and sma200_val:
        if latest_close > sma50_val > sma200_val:
            trend_parts.append('Price above SMA50 and SMA200, medium-term uptrend')
        elif latest_close < sma50_val < sma200_val:
            trend_parts.append('Price below SMA50 and SMA200, medium-term downtrend')
        elif sma50_val > sma200_val and latest_close < sma50_val:
            trend_parts.append('Price below SMA50 but SMA50 still above SMA200, short-term pullback')
        elif sma50_val < sma200_val and latest_close > sma50_val:
            trend_parts.append('Price crossed above SMA50 but MAs not yet golden-cross, attempting bottom')
        else:
            trend_parts.append('Price oscillating between MAs, direction unclear')

    if bb_upper_val and bb_lower_val and latest_close and latest_close > 0:
        bb_amp = (bb_upper_val - bb_lower_val) / latest_close * 100
        if bb_amp < 5:
            trend_parts.append('BB bandwidth extremely narrow, potential volatile breakout brewing')
        elif bb_amp > 30:
            trend_parts.append('BB bandwidth wide, volatility elevated')

    trend_text = '; '.join(trend_parts) if trend_parts else 'Insufficient data for trend analysis'

    # Signals summary
    summary_parts = []
    total = len(signals)
    recent = count_recent(signals, 90)

    summary_parts.append('Total signals in scan range: %d' % total)
    if recent > 0:
        summary_parts.append('Recent 3 months: %d signals' % recent)
    if bullish_signals:
        summary_parts.append('Bullish: %d' % bullish_signals_count)
    if bearish_signals:
        summary_parts.append('Bearish: %d' % bearish_signals_count)

    source_names = {
        'double_bottom': 'Double Bottom',
        'breakout': 'Breakout',
        'pocket_pivot': 'Pocket Pivot',
        'flat_base': 'Flat Base',
        'talib': 'TA-Lib Indicators',
        'cdl': 'Candlestick Patterns',
    }
    for src, counts in sorted(by_source.items()):
        name = source_names.get(src, src)
        p = []
        if counts['bullish'] > 0:
            p.append('Bullish x%d' % counts['bullish'])
        if counts['bearish'] > 0:
            p.append('Bearish x%d' % counts['bearish'])
        if p:
            summary_parts.append('%s: %s' % (name, ', '.join(p)))

    if resonance_dates:
        summary_parts.append('Multi-engine resonance detected at %d time windows' % len(resonance_dates))

    signals_summary = '\n  * '.join(summary_parts)

    # Assessment
    assessment_parts = []
    if rsi_val is not None:
        if rsi_val > 70:
            assessment_parts.append('RSI %.0f overbought (>70), watch for near-term pullback' % rsi_val)
        elif rsi_val > 60:
            assessment_parts.append('RSI %.0f strong but not overbought, room to run' % rsi_val)
        elif rsi_val < 30:
            assessment_parts.append('RSI %.0f oversold (<30), potential bounce' % rsi_val)
        elif rsi_val < 40:
            assessment_parts.append('RSI %.0f weak, momentum lacking' % rsi_val)
        else:
            assessment_parts.append('RSI %.0f neutral zone' % rsi_val)

    if bullish_signals and bearish_signals:
        assessment_parts.append('Contradictory signals present (B:%d / S:%d), await clearer confirmation' % (
            bullish_signals_count, bearish_signals_count))

    if len(resonance_dates) >= 3:
        assessment_parts.append('Strong multi-engine resonance (>=%d zones), pattern + indicator layers confirm each other' % len(resonance_dates))
    elif resonance_dates:
        assessment_parts.append('Signal resonance detected, different engines confirming in close proximity')

    if latest_close and vol_ma_val and klines and len(klines) > 0:
        latest_vol = klines[-1].get('volume', 0)
        if vol_ma_val > 0 and latest_vol > 0:
            vol_ratio = latest_vol / vol_ma_val
            if vol_ratio > 2:
                assessment_parts.append('Latest volume %.1fx MA50, strong participation' % vol_ratio)
            elif vol_ratio < 0.5:
                assessment_parts.append('Latest volume very low, weak participation')

    assessment = '\n  * '.join(assessment_parts) if assessment_parts else 'Insufficient data'

    # Score
    score = 0
    if sma50_val and sma200_val and latest_close:
        if latest_close > sma50_val > sma200_val:
            score += 2
        elif sma50_val > sma200_val:
            score += 1
    if rsi_val is not None:
        if 40 <= rsi_val <= 60:
            score += 1
        elif rsi_val > 70 or rsi_val < 30:
            score -= 1
    diff = bullish_signals_count - bearish_signals_count
    if diff > 0:
        score += min(diff, 3)
    elif diff < 0:
        score -= min(-diff, 3)
    if resonance_dates:
        score += min(len(resonance_dates), 3)

    # Advice
    advice_parts = []
    if score >= 5:
        advice_parts.append('Multiple buy signals resonating, actively monitor')
        if sma50_val and latest_close and latest_close > sma50_val * 1.05:
            advice_parts.append('Price far above SMA50, wait for pullback to MA for confirmation before entry')
        else:
            advice_parts.append('Consider small position at current level, stop loss below SMA50')
    elif score >= 2:
        advice_parts.append('Overall bias bullish but moderate strength')
        advice_parts.append('Add to watchlist, await clearer breakout confirmation')
    elif score >= -1:
        advice_parts.append('Neutral signals, mixed bullish and bearish')
        advice_parts.append('Recommend waiting for trend clarity')
    elif score >= -3:
        advice_parts.append('Bearish bias, some downside risk')
        advice_parts.append('Holders consider reducing, potential buyers wait for better entry')
    else:
        advice_parts.append('Strong bearish signals, multiple sell indicators stacking')
        advice_parts.append('Recommend avoiding, wait for trend reversal signals')

    advice = '\n  * '.join(advice_parts)

    return {
        'trend': trend_text,
        'signals_summary': signals_summary,
        'assessment': assessment,
        'advice': advice,
        'score': score,
    }


def _detect_resonance(signals, window=5, min_sources=2):
    if not signals:
        return []

    by_date = defaultdict(list)
    for s in signals:
        by_date[s['date']].append(s['source'])

    dates = sorted(by_date.keys())
    resonance = []

    i = 0
    while i < len(dates):
        current_dt = datetime.strptime(dates[i], '%Y-%m-%d')
        window_sources = set()
        window_end_idx = i

        for j in range(i, len(dates)):
            d = datetime.strptime(dates[j], '%Y-%m-%d')
            if (d - current_dt).days <= window:
                window_sources.update(by_date[dates[j]])
                window_end_idx = j
            else:
                break

        if len(window_sources) >= min_sources:
            resonance.append({
                'start': dates[i],
                'end': dates[window_end_idx],
                'sources': list(window_sources),
            })
            i = window_end_idx + 1
        else:
            i += 1

    return resonance


if __name__ == '__main__':
    import json, sqlite3
    import numpy as np
    import talib
    from engine_registry import run_all_engines

    DB_PATH = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT date, open, high, low, close, volume
        FROM daily_kline_adj WHERE stock_code='600519'
        AND date>='2024-01-01' AND date<='2026-05-13'
        ORDER BY date
    """).fetchall()
    conn.close()

    klines = [dict(r) for r in rows]

    close = np.array([k['close'] for k in klines], dtype=np.float64)
    high = np.array([k['high'] for k in klines], dtype=np.float64)
    low = np.array([k['low'] for k in klines], dtype=np.float64)
    vol = np.array([k['volume'] for k in klines], dtype=np.float64)

    def arr(x):
        return [float(v) if not np.isnan(v) else None for v in x]

    indicators = {
        'sma50': arr(talib.SMA(close, 50)),
        'sma250': arr(talib.SMA(close, 250)),
        'rsi14': arr(talib.RSI(close, 14)),
        'atr14': arr(talib.ATR(high, low, close, 14)),
        'bb_upper': arr(talib.BBANDS(close)[0]),
        'bb_lower': arr(talib.BBANDS(close)[2]),
        'vol_ma50': arr(talib.SMA(vol, 50)),
    }

    signals = run_all_engines(klines=klines, indicators=indicators)
    rec = generate(signals, indicators, klines, '600519')

    print('=' * 50)
    print('TREND:')
    print(rec['trend'])
    print()
    print('SIGNALS SUMMARY:')
    print(rec['signals_summary'])
    print()
    print('ASSESSMENT:')
    print(rec['assessment'])
    print()
    print('ADVICE:')
    print(rec['advice'])
    print()
    print('Score: %d' % rec['score'])
