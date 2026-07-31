"""
MW 信号引擎包装器 — 供 pattern-scan 页面使用

将 MW 信号（B1/B2/PLUS）以标准引擎接口暴露，读取 mw_signal_daily 表。
信号由回填脚本（backfill_all_signals_v2.py）产生并写入。
"""

import sqlite3
import os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')

ENGINE_META = {
    'name': 'mw_signal',
    'display_name': 'MW B1/B2',
    'category': 'breakout',
    'version': '2.0',
    'description': 'MW信号：牛市回调后再启动形态（读取 mw_signal_daily 表）'
}


def detect(klines, indicators=None):
    """从 mw_signal_daily 表读取 MW 信号"""
    if not klines:
        return []

    stock_code = None
    for k in klines:
        if k.get('stock_code'):
            stock_code = k['stock_code']
            break
    if not stock_code:
        return []

    dates = [k['date'] for k in klines if k.get('date')]
    if not dates:
        return []
    date_start, date_end = dates[0], dates[-1]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    signals = []

    try:
        rows = conn.execute("""
            SELECT b2_date, b1_date, h_date, l_date, score, score_v2,
                   is_plus, confidence, confidence_v2, decline_pct,
                   b1_return_pct, b2_return_pct, h_price, l_price
            FROM mw_signal_daily
            WHERE stock_code = ?
              AND (
                  (b2_date IS NOT NULL AND b2_date BETWEEN ? AND ?)
                  OR (b2_date IS NULL AND b1_date BETWEEN ? AND ?)
              )
            ORDER BY COALESCE(b2_date, b1_date)
        """, (stock_code, date_start, date_end, date_start, date_end)).fetchall()

        for row in rows:
            row_dict = dict(row)

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

            if row_dict['b1_date'] and row_dict['b1_date'] != row_dict['b2_date']:
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
    except Exception:
        pass
    finally:
        conn.close()

    return signals
