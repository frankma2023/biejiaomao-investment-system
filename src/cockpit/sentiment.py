"""
投资决策驾驶舱 - 舆情模块 v2.0

数据源：pysnowball（程序化） → 本地K线兜底
支持：实时行情、行业信息、资金流向

用法：
    from src.cockpit.sentiment import SentimentEngine
    engine = SentimentEngine()
    summary = engine.fetch(stock_code, stock_name)
"""
import os
import sys
import sqlite3

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')

# pysnowball 符号映射
def to_xq_symbol(stock_code):
    if stock_code.startswith('6'):
        return f'SH{stock_code}'
    elif stock_code.startswith('0') or stock_code.startswith('3'):
        return f'SZ{stock_code}'
    elif stock_code.startswith('688') or stock_code.startswith('689'):
        return f'SH{stock_code}'
    return f'SZ{stock_code}'


class SentimentEngine:
    """舆情抓取引擎 v2.0"""

    def __init__(self, config=None):
        self.config = config or {}
        self.timeout = self.config.get('timeout_seconds', 60)

    def fetch(self, stock_code, stock_name=''):
        """
        获取舆情摘要。
        优先级：pysnowball 实时数据 → 本地K线兜底
        返回: dict {summary, sources, error}
        """
        result = {
            'summary': '',
            'sources': [],
            'error': None,
            'quote': None,
        }

        parts = []

        # 1. pysnowball 实时行情
        try:
            quote = self._fetch_quote(stock_code)
            if quote:
                result['sources'].append('pysnowball')
                result['quote'] = quote
                parts.append(
                    f"{quote['name']} 现价 {quote['current']:.2f} "
                    f"({quote['pct']:+.2f}%) "
                    f"振幅 {quote.get('amplitude',0):.2f}% "
                    f"换手 {quote.get('turnover',0):.2f}%"
                )
        except Exception as e:
            pass  # 降级

        # 2. 资金流向
        try:
            flow = self._fetch_capital_flow(stock_code)
            if flow:
                parts.append(flow)
        except Exception:
            pass

        # 3. 行业信息
        try:
            ind_info = self._fetch_industry(stock_code)
            if ind_info:
                parts.append(ind_info)
        except Exception:
            pass

        # 4. 兜底：本地K线摘要
        if not parts:
            parts.append(self._local_summary(stock_code, stock_name))
            result['sources'].append('local')

        result['summary'] = '; '.join(parts)
        return result

    def _fetch_quote(self, stock_code):
        """pysnowball 行情快照"""
        try:
            import pysnowball as ball
            symbol = to_xq_symbol(stock_code)
            data = ball.quotec(symbol)
            if data and data.get('error_code') == 0 and data.get('data'):
                items = data['data']
                if isinstance(items, list) and len(items) > 0:
                    d = items[0]
                else:
                    d = items
                return {
                    'name': d.get('name', ''),
                    'current': d.get('current', 0),
                    'pct': d.get('percent', 0),
                    'high': d.get('high', 0),
                    'low': d.get('low', 0),
                    'volume': d.get('volume', 0),
                    'amount': d.get('amount', 0),
                    'amplitude': d.get('amplitude', 0),
                    'turnover': d.get('turnover_rate', 0),
                    'market_cap': d.get('market_capital', 0),
                }
        except Exception:
            pass
        return None

    def _fetch_capital_flow(self, stock_code):
        """资金流向摘要"""
        try:
            import pysnowball as ball
            symbol = to_xq_symbol(stock_code)
            # capital_flow 返回主力/散户资金流
            data = ball.capital_flow(symbol)
            if data and 'data' in data and data['data']:
                items = data['data'].get('items', [])
                if items:
                    latest = items[0]
                    main_net = latest[5] if len(latest) > 5 else 0  # 主力净流入
                    if main_net != 0:
                        direction = '流入' if main_net > 0 else '流出'
                        return f"主力资金{direction} {abs(main_net)/1e8:.2f}亿"
        except Exception:
            pass
        return ''

    def _fetch_industry(self, stock_code):
        """行业分类信息"""
        try:
            import pysnowball as ball
            symbol = to_xq_symbol(stock_code)
            data = ball.industry(symbol)
            if data and 'data' in data and data['data']:
                d = data['data']
                # 雪球行业分类
                ind_name = d.get('industry_name', '') or d.get('industry', '')
                if ind_name:
                    return f"雪球行业: {ind_name}"
        except Exception:
            pass
        return ''

    def _local_summary(self, stock_code, stock_name):
        """本地数据摘要兜底"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        parts = []
        nm = stock_name or stock_code

        # 行业
        row = conn.execute(
            "SELECT industry_name FROM stock_industry WHERE stock_code=? AND source='zx' LIMIT 1",
            (stock_code,)
        ).fetchone()
        if row:
            parts.append(f"行业: {row['industry_name']}")

        # 近5日
        rows = conn.execute(
            "SELECT close FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 6",
            (stock_code,)
        ).fetchall()
        if len(rows) >= 6 and rows[5]['close'] > 0:
            pct = (rows[0]['close'] - rows[5]['close']) / rows[5]['close'] * 100
            parts.append(f"近5日: {pct:+.2f}%")

        # MW信号统计
        cnt = conn.execute(
            "SELECT COUNT(*) as c FROM mw_signal_daily WHERE stock_code=? AND is_plus=1",
            (stock_code,)
        ).fetchone()
        if cnt and cnt['c'] > 0:
            parts.append(f"历史PLUS信号: {cnt['c']}次")

        conn.close()

        if parts:
            return f"{nm}: " + '; '.join(parts)
        return f"{nm}: 暂无舆情信息"
