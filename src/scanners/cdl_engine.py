"""
TA-Lib K线形态识别引擎 v1

基于 TA-Lib CDL 函数独立输出蜡烛图形态信号。
14种经典形态覆盖：吞没、锤子、晨星/黄昏星、刺透/乌云盖顶等。

所有信号独立产出，和自研引擎及指标引擎平级。
"""

import sys, os
import numpy as np
import talib

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

ENGINE_META = {
    "name": "cdl",
    "display_name": "K线形态",
    "category": "candlestick",
    "version": "1.0",
    "description": "识别14种经典日本蜡烛图形态：吞没/锤子/晨星/黄昏星/刺透/乌云盖顶/射击之星/三白兵/三黑鸦等"
}

# ── 形态配置：函数、名称、方向、最小强度阈值 ──
CDL_CONFIG = [
    # 看涨形态
    {'func': talib.CDLENGULFING,      'name': '看涨吞没',        'direction': 'bullish',  'threshold': 60},
    {'func': talib.CDLHAMMER,          'name': '锤子线',          'direction': 'bullish',  'threshold': 50},
    {'func': talib.CDLMORNINGSTAR,     'name': '晨星',            'direction': 'bullish',  'threshold': 70},
    {'func': talib.CDLPIERCING,        'name': '刺透形态',        'direction': 'bullish',  'threshold': 60},
    {'func': talib.CDLHARAMI,          'name': '十字孕线',        'direction': 'bullish',  'threshold': 50},
    {'func': talib.CDL3WHITESOLDIERS,  'name': '三白兵',          'direction': 'bullish',  'threshold': 80},
    {'func': talib.CDLHOMINGPIGEON,    'name': '家鸽形态',        'direction': 'bullish',  'threshold': 50},
    {'func': talib.CDLDRAGONFLYDOJI,   'name': '蜻蜓十字',        'direction': 'bullish',  'threshold': 60},
    # 看跌形态
    {'func': talib.CDLEVENINGSTAR,     'name': '黄昏之星',        'direction': 'bearish',  'threshold': 70},
    {'func': talib.CDLDARKCLOUDCOVER,  'name': '乌云盖顶',        'direction': 'bearish',  'threshold': 60},
    {'func': talib.CDLSHOOTINGSTAR,    'name': '射击之星',        'direction': 'bearish',  'threshold': 60},
    {'func': talib.CDLGRAVESTONEDOJI,  'name': '墓碑十字',        'direction': 'bearish',  'threshold': 60},
    {'func': talib.CDL3BLACKCROWS,     'name': '三黑鸦',          'direction': 'bearish',  'threshold': 80},
    {'func': talib.CDLHANGINGMAN,      'name': '上吊线',          'direction': 'bearish',  'threshold': 60},
]


def detect(klines, indicators=None):
    """
    Args:
        klines: list of dicts with [date, open, high, low, close, volume]
        indicators: unused, 保留以兼容统一接口

    Returns:
        List[dict] 信号列表，不含 source 字段
    """
    n = len(klines)
    if n < 5:
        return []

    # ── 提取 numpy 数组 ──
    open_ = np.array([k.get('open') or np.nan for k in klines], dtype=np.float64)
    high = np.array([k.get('high') or np.nan for k in klines], dtype=np.float64)
    low = np.array([k.get('low') or np.nan for k in klines], dtype=np.float64)
    close = np.array([k.get('close') or np.nan for k in klines], dtype=np.float64)

    signals = []

    for cfg in CDL_CONFIG:
        try:
            result = cfg['func'](open_, high, low, close)
        except Exception:
            continue

        # 去重：同一形态连续多天出现只保留第一天
        last_signal_date = None

        for i in range(len(result)):
            val = result[i]
            if np.isnan(val) or val == 0:
                continue

            # 方向和强度判断
            if cfg['direction'] == 'bullish' and val < cfg['threshold']:
                continue
            if cfg['direction'] == 'bearish' and val > -cfg['threshold']:
                continue

            # 去重
            if last_signal_date is not None:
                # 简单策略：和前一个信号距离 < 3天 → 合并
                pass  # 按日期去重在外层处理，这里不过滤

            signals.append({
                'type': cfg['direction'],
                'date': klines[i]['date'],
                'close': float(close[i]),
                'pivot': None,
                'confidence': 'high' if abs(val) >= 80 else 'medium',
                'details': {
                    'cdl_type': cfg['func'].__name__,
                    'cdl_name': cfg['name'],
                    'strength': int(abs(val)),
                }
            })

    return signals


if __name__ == "__main__":
    import sqlite3

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

    # 统计各形态数量
    from collections import Counter
    ctr = Counter(s['details']['cdl_name'] for s in signals)
    print(f"贵州茅台: {len(klines)}K线, {len(signals)} CDL信号")
    for name, cnt in ctr.most_common():
        print(f"  {name}: {cnt}")
    print()
    # 看最近5个
    for s in signals[-5:]:
        print(f"  {s['date']} [{s['type']}] {s['details']['cdl_name']} strength={s['details']['strength']}")
