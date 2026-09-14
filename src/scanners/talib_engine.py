"""
TA-Lib 指标判断引擎 v1

基于 TA-Lib 技术指标输出独立判断信号：
  趋势判断: SMA多周期交叉、价格vs均线关系
  波动率: BBANDS带宽、ATR
  超买超卖: RSI
  成交量: 放量异动
  趋势强度: MACD金叉死叉

所有信号独立产出，和自研引擎平级。
"""

import sys, os
import numpy as np
import talib

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

ENGINE_META = {
    "name": "talib",
    "display_name": "TA-Lib指标",
    "category": "indicator",
    "version": "1.0",
    "description": "基于TA-Lib的技术指标信号：SMA趋势交叉、RSI超买超卖、成交量异动、MACD金叉死叉、布林带带宽收缩"
}


def detect(klines, indicators=None):
    """
    Args:
        klines: list of dicts with [date, open, high, low, close, volume]
        indicators: unused (本引擎自行计算所有指标），保留以兼容统一接口

    Returns:
        List[dict] 信号列表，不含 source 字段
    """
    n = len(klines)
    if n < 60:
        return []

    # ── 提取 numpy 数组（TA-Lib 要求 float64） ──
    close = np.array([k.get('close') or np.nan for k in klines], dtype=np.float64)
    high = np.array([k.get('high') or np.nan for k in klines], dtype=np.float64)
    low = np.array([k.get('low') or np.nan for k in klines], dtype=np.float64)
    open_ = np.array([k.get('open') or np.nan for k in klines], dtype=np.float64)
    vol = np.array([k.get('volume') or 0 for k in klines], dtype=np.float64)

    # ── TA-Lib 指标计算 ──
    sma5 = talib.SMA(close, 5)
    sma10 = talib.SMA(close, 10)
    sma20 = talib.SMA(close, 20)
    sma50 = talib.SMA(close, 50)
    sma120 = talib.SMA(close, 120)
    sma200 = talib.SMA(close, 250)  # 近似年线
    rsi14 = talib.RSI(close, 14)
    atr14 = talib.ATR(high, low, close, 14)
    vol_ma50 = talib.SMA(vol, 50)
    macd, macd_signal, macd_hist = talib.MACD(close, 12, 26, 9)
    bb_upper, bb_middle, bb_lower = talib.BBANDS(close, 20, 2, 2, 0)

    signals = []

    # ── 辅助函数：安全取值 ──
    def ok(arr, i):
        return i >= 0 and i < len(arr) and not np.isnan(arr[i]) and arr[i] > 0

    # ── 趋势信号：价格上穿/下穿 SMA50 ──
    for i in range(1, n):
        if ok(sma50, i) and close[i] > 0:
            prev_above = ok(sma50, i-1) and close[i-1] > sma50[i-1]
            prev_below = ok(sma50, i-1) and close[i-1] <= sma50[i-1]
            cur_above = close[i] > sma50[i]
            cur_below = close[i] <= sma50[i]

            if prev_below and cur_above:
                signals.append({
                    'type': 'bullish',
                    'date': klines[i]['date'],
                    'close': float(close[i]),
                    'pivot': float(sma50[i]),
                    'confidence': 'medium',
                    'details': {
                        'signal_type': 'price_cross_sma50_up',
                        'description': '价格上穿SMA50，中期趋势转强',
                        'sma50': float(sma50[i]),
                    }
                })

            if prev_above and cur_below:
                signals.append({
                    'type': 'bearish',
                    'date': klines[i]['date'],
                    'close': float(close[i]),
                    'pivot': float(sma50[i]),
                    'confidence': 'medium',
                    'details': {
                        'signal_type': 'price_cross_sma50_down',
                        'description': '价格下穿SMA50，中期趋势转弱',
                        'sma50': float(sma50[i]),
                    }
                })

    # ── 趋势信号：SMA50 上穿/下穿 SMA200（金叉/死叉） ──
    for i in range(1, n):
        if ok(sma50, i) and ok(sma200, i) and ok(sma50, i-1) and ok(sma200, i-1):
            if sma50[i-1] <= sma200[i-1] and sma50[i] > sma200[i]:
                signals.append({
                    'type': 'bullish',
                    'date': klines[i]['date'],
                    'close': float(close[i]),
                    'pivot': float(sma50[i]),
                    'confidence': 'high',
                    'details': {
                        'signal_type': 'golden_cross_50_200',
                        'description': 'SMA50上穿SMA200，金叉信号',
                        'sma50': float(sma50[i]),
                        'sma200': float(sma200[i]),
                    }
                })

            if sma50[i-1] >= sma200[i-1] and sma50[i] < sma200[i]:
                signals.append({
                    'type': 'bearish',
                    'date': klines[i]['date'],
                    'close': float(close[i]),
                    'pivot': float(sma50[i]),
                    'confidence': 'high',
                    'details': {
                        'signal_type': 'death_cross_50_200',
                        'description': 'SMA50下穿SMA200，死叉信号',
                        'sma50': float(sma50[i]),
                        'sma200': float(sma200[i]),
                    }
                })

    # ── RSI 超买超卖 ──
    for i in range(14, n):
        if ok(rsi14, i):
            if rsi14[i] > 70:
                # 检查是否首次进入超买区
                if not ok(rsi14, i-1) or rsi14[i-1] <= 70:
                    signals.append({
                        'type': 'bearish',
                        'date': klines[i]['date'],
                        'close': float(close[i]),
                        'pivot': None,
                        'confidence': 'medium',
                        'details': {
                            'signal_type': 'rsi_overbought',
                            'description': 'RSI进入超买区(>70)，注意回调风险',
                            'rsi14': float(rsi14[i]),
                        }
                    })
            elif rsi14[i] < 30:
                if not ok(rsi14, i-1) or rsi14[i-1] >= 30:
                    signals.append({
                        'type': 'bullish',
                        'date': klines[i]['date'],
                        'close': float(close[i]),
                        'pivot': None,
                        'confidence': 'medium',
                        'details': {
                            'signal_type': 'rsi_oversold',
                            'description': 'RSI进入超卖区(<30)，可能存在反弹机会',
                            'rsi14': float(rsi14[i]),
                        }
                    })

    # ── 成交量异动 ──
    for i in range(50, n):
        if ok(vol_ma50, i) and vol[i] > vol_ma50[i] * 2:
            signals.append({
                'type': 'bullish',
                'date': klines[i]['date'],
                'close': float(close[i]),
                'pivot': None,
                'confidence': 'low',
                'details': {
                    'signal_type': 'volume_surge',
                    'description': '成交量放大至50日均量的2倍以上',
                    'vol_ratio': float(vol[i] / vol_ma50[i]),
                    'vol_ma50': float(vol_ma50[i]),
                }
            })

    # ── BBANDS 带宽收缩（纯数据输出，不判多空） ──
    if len(bb_upper) > 200 and ok(bb_upper, n-1) and ok(bb_lower, n-1) and ok(bb_middle, n-1):
        # 计算最近200天的BB带宽历史
        widths = []
        for i in range(max(0, n-200), n):
            if ok(bb_upper, i) and ok(bb_lower, i) and ok(bb_middle, i) and bb_middle[i] > 0:
                widths.append((bb_upper[i] - bb_lower[i]) / bb_middle[i])

        if widths:
            current_width = (bb_upper[n-1] - bb_lower[n-1]) / bb_middle[n-1]
            widths_sorted = sorted(widths)
            # 当前带宽是否在历史最低10%（收缩到极致）
            pct_rank = sum(1 for w in widths_sorted if w < current_width) / len(widths)

            if pct_rank < 0.10:
                signals.append({
                    'type': 'bullish',
                    'date': klines[n-1]['date'],
                    'close': float(close[n-1]),
                    'pivot': None,
                    'confidence': 'low',
                    'details': {
                        'signal_type': 'bb_squeeze',
                        'description': '布林带带宽收缩至历史低位，可能酝酿大幅波动',
                        'bb_width': round(current_width, 4),
                        'bb_pct_rank': round(pct_rank, 2),
                        'bb_upper': float(bb_upper[n-1]),
                        'bb_lower': float(bb_lower[n-1]),
                    }
                })

    # ── MACD 金叉/死叉 ──
    for i in range(26, n):
        if ok(macd, i) and ok(macd_signal, i):
            prev_pos = ok(macd, i-1) and ok(macd_signal, i-1) and macd[i-1] > macd_signal[i-1]
            prev_neg = ok(macd, i-1) and ok(macd_signal, i-1) and macd[i-1] <= macd_signal[i-1]
            cur_pos = macd[i] > macd_signal[i]
            cur_neg = macd[i] <= macd_signal[i]

            if prev_neg and cur_pos:
                signals.append({
                    'type': 'bullish',
                    'date': klines[i]['date'],
                    'close': float(close[i]),
                    'pivot': None,
                    'confidence': 'medium',
                    'details': {
                        'signal_type': 'macd_golden_cross',
                        'description': 'MACD金叉，短期趋势转强',
                        'macd': float(macd[i]),
                        'macd_signal': float(macd_signal[i]),
                    }
                })

            if prev_pos and cur_neg:
                signals.append({
                    'type': 'bearish',
                    'date': klines[i]['date'],
                    'close': float(close[i]),
                    'pivot': None,
                    'confidence': 'medium',
                    'details': {
                        'signal_type': 'macd_death_cross',
                        'description': 'MACD死叉，短期趋势转弱',
                        'macd': float(macd[i]),
                        'macd_signal': float(macd_signal[i]),
                    }
                })

    return signals


if __name__ == "__main__":
    import sqlite3, json

    DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT date, open, high, low, close, volume
        FROM daily_kline_adj
        WHERE stock_code='600519' AND date<='2026-05-13'
        ORDER BY date
    """).fetchall()
    conn.close()

    klines = [dict(r) for r in rows]
    signals = detect(klines)
    print(f"贵州茅台: {len(klines)}K线, {len(signals)} TA-Lib信号")
    for s in signals[-10:]:
        print(f"  {s['date']} [{s['type']}] {s['details'].get('description','')}")
