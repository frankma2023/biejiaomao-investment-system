"""
MW 信号引擎包装器 — 供 pattern-scan 页面使用

将 MW 信号（B1/B2/PLUS）以标准引擎接口暴露，自动注册到 engine_registry。

engine_registry 自动发现此模块后，pattern-scan 的 K 线图会显示：
  - B1 标记（首次突破日）
  - B2 标记（二次确认日）
  - PLUS 标记（高分信号）

用法：engine_registry 自动发现后调用 detect(klines, indicators)
"""

import sqlite3
import os
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')

ENGINE_META = {
    'name': 'mw_signal',
    'display_name': 'MW B1/B2',
    'category': 'breakout',
    'version': '1.0',
    'description': 'MW信号：牛市回调后再启动形态（B1首次突破 + B2二次确认 + PLUS高分）'
}


def detect(klines, indicators=None):
    """
    从 mw_signal_daily 表查询该股票的 MW 信号，
    返回 pattern-scan 兼容的信号列表。

    Args:
        klines: K线列表，每条必须有 stock_code 字段
        indicators: 技术指标 dict（暂未使用）

    Returns:
        list[dict]: 信号列表，每个信号包含 date, type, source, confidence, details
    """
    if not klines:
        return []

    # 获取 stock_code（由 server.py 的 api_pattern_scan 注入）
    stock_code = None
    for k in klines:
        if k.get('stock_code'):
            stock_code = k['stock_code']
            break

    if not stock_code:
        return []

    # 获取 K 线日期范围
    dates = [k['date'] for k in klines if k.get('date')]
    if not dates:
        return []

    date_start = dates[0]
    date_end = dates[-1]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    signals = []

    try:
        # 查询该股票在日期范围内的所有 MW 信号
        rows = conn.execute("""
            SELECT b2_date, b1_date, h_date, l_date, score, score_v2,
                   is_plus, confidence, confidence_v2, decline_pct,
                   b1_return_pct, b2_return_pct, h_price, l_price
            FROM mw_signal_daily
            WHERE stock_code = ?
              AND b2_date BETWEEN ? AND ?
            ORDER BY b2_date
        """, (stock_code, date_start, date_end)).fetchall()

        for row in rows:
            row_dict = dict(row)

            # B2 信号（二次确认日 — 主要信号）
            if row_dict['b2_date']:
                is_plus = bool(row_dict.get('is_plus', 0))
                score = row_dict.get('score_v2') or row_dict.get('score', 0)
                conf = row_dict.get('confidence_v2') or row_dict.get('confidence', 'mid')

                signals.append({
                    'date': row_dict['b2_date'],
                    'type': 'bullish',
                    'source': 'mw_signal',
                    'confidence': 'high' if conf == 'high' else ('medium' if conf == 'mid' else 'low'),
                    'pivot': None,
                    'details': {
                        'signal_type': 'MW-B2',
                        'score': score,
                        'is_plus': is_plus,
                        'decline_pct': row_dict.get('decline_pct'),
                        'b2_return_pct': row_dict.get('b2_return_pct'),
                        'h_price': row_dict.get('h_price'),
                        'l_price': row_dict.get('l_price'),
                    }
                })

            # B1 信号（首次突破日 — 辅助标记）
            if row_dict['b1_date'] and row_dict['b1_date'] != row_dict['b2_date']:
                b1_already = any(s['date'] == row_dict['b1_date'] for s in signals)
                if not b1_already:
                    signals.append({
                        'date': row_dict['b1_date'],
                        'type': 'bullish',
                        'source': 'mw_signal',
                        'confidence': 'medium',
                        'pivot': None,
                        'details': {
                            'signal_type': 'MW-B1',
                            'b1_return_pct': row_dict.get('b1_return_pct'),
                            'note': '首次突破日（需B2确认）'
                        }
                    })

    except sqlite3.OperationalError:
        pass  # 表不存在时静默
    finally:
        conn.close()

    return signals
