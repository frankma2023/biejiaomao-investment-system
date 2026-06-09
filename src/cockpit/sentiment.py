"""
投资决策驾驶舱 - 舆情模块

数据源优先级：pysnowball → Snowball MCP → autoglm-browser-agent

用法：
    from src.cockpit.sentiment import SentimentEngine
    engine = SentimentEngine()
    summary = engine.fetch(stock_code, stock_name)
"""
import os
import sys
import subprocess
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)


class SentimentEngine:
    """舆情抓取引擎"""

    def __init__(self, config=None):
        self.config = config or {}
        self.timeout = self.config.get('timeout_seconds', 60)

    def fetch(self, stock_code, stock_name=''):
        """
        获取舆情摘要。
        返回: dict {summary, sources, error}
        """
        result = {
            'summary': '',
            'sources': [],
            'error': None,
        }

        # 优先尝试 pysnowball
        try:
            snowball_data = self._fetch_pysnowball(stock_code)
            if snowball_data:
                result['sources'].append('pysnowball')
                result['summary'] = snowball_data
        except Exception as e:
            pass  # 降级

        # 如果没有数据，用基本信息生成一个简单摘要
        if not result['summary']:
            result['summary'] = self._generate_basic_summary(stock_code, stock_name)
            result['sources'].append('本地数据')

        return result

    def _fetch_pysnowball(self, stock_code):
        """通过 pysnowball 获取行情和财务数据"""
        try:
            # 检查 pysnowball 是否安装
            import importlib
            spec = importlib.util.find_spec('pysnowball')
            if spec is None:
                return None

            # 尝试获取行情快照
            symbol = self._to_xq_symbol(stock_code)
            # pysnowball 的 API 调用
            import pysnowball as ball
            token = os.environ.get('XUEQIU_TOKEN', '')
            if token:
                ball.set_token(token)

            try:
                quote = ball.quote_detail(symbol)
                if quote and 'data' in quote:
                    data = quote['data']
                    items = []
                    name = data.get('name', '')
                    current = data.get('current', 0)
                    pct = data.get('percent', 0)
                    vol = data.get('volume', 0)
                    items.append(f"雪球行情: {name} 现价{current} ({pct:+.2f}%) 成交量{vol}")

                    if items:
                        return '; '.join(items)
            except Exception:
                pass

            return None
        except ImportError:
            return None
        except Exception:
            return None

    def _to_xq_symbol(self, stock_code):
        """股票代码转雪球格式"""
        if stock_code.startswith('6'):
            return f'SH{stock_code}'
        elif stock_code.startswith('0') or stock_code.startswith('3'):
            return f'SZ{stock_code}'
        elif stock_code.startswith('688') or stock_code.startswith('689'):
            return f'SH{stock_code}'
        return f'SZ{stock_code}'

    def _generate_basic_summary(self, stock_code, stock_name):
        """生成基础摘要（无外部数据源时兜底）"""
        import sqlite3
        db_path = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        parts = []

        # 行业信息
        row = conn.execute(
            "SELECT industry_name FROM stock_industry WHERE stock_code=? AND source='zx' LIMIT 1",
            (stock_code,)
        ).fetchone()
        if row:
            parts.append(f"所属行业: {row['industry_name']}")

        # 最近5日涨跌
        rows = conn.execute(
            "SELECT close, date FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 6",
            (stock_code,)
        ).fetchall()
        if len(rows) >= 6:
            pct = (rows[0]['close'] - rows[5]['close']) / rows[5]['close'] * 100
            parts.append(f"近5日涨跌: {pct:+.2f}%")

        # MW信号统计
        mw_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM mw_signal_daily WHERE stock_code=? AND is_plus=1",
            (stock_code,)
        ).fetchone()
        if mw_count and mw_count['cnt'] > 0:
            parts.append(f"历史PLUS信号: {mw_count['cnt']}次")

        conn.close()

        if parts:
            return f"{stock_name or stock_code}: " + '; '.join(parts)
        return f"{stock_name or stock_code}: 暂无额外舆情信息"

    def fetch_browser(self, stock_code, stock_name=''):
        """
        通过 autoglm-browser-agent 获取东财资讯（兜底方案）。
        注意：此方法速度较慢，建议仅在用户手动触发时调用。
        """
        # V1 简化实现：返回提示
        # 完整实现需要调用 browser_subagent
        return {
            'summary': f"{stock_name or stock_code}: 浏览器模式暂未实现，请手动查看东财/雪球。",
            'sources': ['local'],
            'error': 'browser mode not implemented in V1',
        }
