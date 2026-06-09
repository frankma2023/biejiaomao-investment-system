"""
投资决策驾驶舱 - 简报生成引擎

为每只候选股票生成完整的 9 模块简报。

用法：
    from src.cockpit.briefing import BriefingEngine
    engine = BriefingEngine(db)
    briefing = engine.generate(candidate, pool_data)
"""
import os
import sys
import sqlite3
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')


class BriefingEngine:
    """简报生成引擎"""

    def __init__(self, db=None):
        self.db = db or sqlite3.connect(DB_PATH)
        self.db.row_factory = sqlite3.Row

    def generate(self, candidate, pool_data=None, market_data=None):
        """
        生成完整简报。

        返回: dict with 9 modules
        """
        code = candidate['stock_code']

        # 并行生成各模块
        signal_mod = self._module_signal(candidate)
        win_mod = self._module_win_rate(candidate)
        fund_mod = self._module_fundamentals(candidate, pool_data)
        ind_mod = self._module_industry(code, candidate)
        mkt_mod = market_data if market_data else self._module_market()
        sl_mod = self._module_stop_loss(candidate)

        # 仓位计算
        pos_mod = {}
        try:
            from .position import calculate_position
            entry = sl_mod.get('entry_price_ref', 10)
            stop = sl_mod.get('stop_loss_price', entry * 0.92)
            # 简单凯利参数（使用 MW PLUS 回测数据兜底）
            pos_mod = calculate_position(
                entry, stop,
                account_size=1000000,
                max_loss_pct=0.02,
                kelly_fraction=0.25,
                win_rate=0.62,  # MW PLUS 10日胜率
                avg_win=0.15,     # 估算平均盈利
                avg_loss=0.08,    # 8%止损
            )
        except Exception:
            pass

        # 舆情
        sent_mod = {}
        try:
            from .sentiment import SentimentEngine
            engine = SentimentEngine()
            sent_mod = engine.fetch(code, candidate.get('stock_name', ''))
        except Exception:
            sent_mod = {'summary': '', 'sources': []}

        # 欧奈尔分析
        oneil_mod = {}
        try:
            from .oneil_eval import ONeilEvaluator
            evaluator = ONeilEvaluator(self.db)
            oneil_mod = evaluator.evaluate(code, candidate, mkt_mod)
        except Exception:
            oneil_mod = {'summary': '分析暂不可用', 'verdict': '', 'score': 0}

        briefing = {
            'stock_code': code,
            'stock_name': candidate.get('stock_name', ''),
            'signal_summary': signal_mod,
            'win_rate': win_mod,
            'fundamentals': fund_mod,
            'industry': ind_mod,
            'market': mkt_mod,
            'position': pos_mod,
            'sentiment': sent_mod,
            'oneil': oneil_mod,
            'stop_loss': sl_mod,
        }
        return briefing
        """模块1：信号概要"""
        return {
            'signal_types': c.get('signals', []),
            'signal_date': c.get('signal_date', ''),
            'confidence': c.get('confidence', ''),
            'h_date': c.get('h_date'),
            'h_price': c.get('h_price'),
            'l_date': c.get('l_date'),
            'l_price': c.get('l_price'),
            'decline_pct': c.get('decline_pct'),
            'consolidation_days': c.get('consolidation_days'),
            'is_plus': c.get('is_plus', False),
            'mw_score': c.get('mw_score'),
        }

    def _module_win_rate(self, c):
        """模块2：胜率参考"""
        signals = c.get('signals', [])
        win_rate_info = {
            'available': False,
            'source': '暂无回测数据',
            'period': '',
            'win_rate_5d': None,
            'win_rate_10d': None,
            'win_rate_20d': None,
            'median_return_5d': None,
            'median_return_10d': None,
            'median_return_20d': None,
            'note': '回测数据仅覆盖有限时间段，不代表未来表现',
        }

        # MW PLUS 回测数据（来自 HANDOFF）
        if 'mw_plus' in signals:
            win_rate_info['available'] = True
            win_rate_info['source'] = 'MW PLUS 回测 (2026-01~2026-05)'
            win_rate_info['period'] = '5个月'
            win_rate_info['win_rate_10d'] = 0.621  # B1=PP重合 10d胜率
            win_rate_info['median_return_10d'] = 0.0571  # 中位+5.71%
            win_rate_info['note'] = '仅覆盖5个月回测数据，样本量有限，不代表未来表现'

        return win_rate_info

    def _module_fundamentals(self, c, pool_data):
        """模块3：基本面速览"""
        code = c['stock_code']
        result = {
            'canslim_total': c.get('canslim_total'),
            'canslim_c': c.get('canslim_c'),
            'canslim_a': c.get('canslim_a'),
            'canslim_n': c.get('canslim_n'),
            'canslim_s': c.get('canslim_s'),
            'canslim_l': c.get('canslim_l'),
            'canslim_i': c.get('canslim_i'),
            'canslim_m': c.get('canslim_m'),
            'market_cap': c.get('market_cap'),
            'roe': None,
            'eps_yoy': None,
            'revenue_yoy': None,
            'profit_trend': '',
        }

        # 从观察池补充
        if pool_data and code in pool_data:
            p = pool_data[code]
            for k in ['roe', 'eps_yoy', 'revenue_yoy']:
                if result.get(k) is None:
                    result[k] = p.get(k)
            for k in ['canslim_total', 'canslim_c', 'canslim_a', 'canslim_n',
                       'canslim_s', 'canslim_l', 'canslim_i', 'canslim_m']:
                if result.get(k) is None:
                    result[k] = p.get(k)

        # 获取市值
        if not result.get('market_cap'):
            row = self.db.execute(
                "SELECT close FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 1",
                (code,)
            ).fetchone()
            if row:
                basic = self.db.execute(
                    "SELECT total_shares FROM stock_basic WHERE stock_code=?",
                    (code,)
                ).fetchone()
                if basic and basic['total_shares']:
                    try:
                        result['market_cap'] = row['close'] * float(basic['total_shares']) / 1e8
                    except (ValueError, TypeError):
                        pass

        return result

    def _module_industry(self, code, candidate):
        """模块4：行业背景"""
        result = {
            'l1_industry': candidate.get('ind_name') or candidate.get('l1_industry', ''),
            'l1_rs250': candidate.get('ind_rs250'),
            'l1_rs20': None,
            'l1_pct_5d': None,
            'theme_indices': [],
        }

        # 从 stock_industry 获取行业
        if not result['l1_industry']:
            row = self.db.execute(
                "SELECT industry_name, industry_code FROM stock_industry WHERE stock_code=? AND source='zx' LIMIT 1",
                (code,)
            ).fetchone()
            if row:
                result['l1_industry'] = row['industry_name']
                # 查该行业RS
                rs_row = self.db.execute(
                    "SELECT rs_20, rs_250 FROM index_rs_daily WHERE index_code=? ORDER BY date DESC LIMIT 1",
                    (row['industry_code'],)
                ).fetchone()
                if rs_row:
                    result['l1_rs250'] = rs_row['rs_250']
                    result['l1_rs20'] = rs_row['rs_20']

        # 近5日涨跌
        if result.get('l1_rs20') is None:
            result['l1_pct_5d'] = self._calc_5d_change(code)

        return result

    def _calc_5d_change(self, code):
        """计算股票近5日涨跌"""
        try:
            row = self.db.execute(
                "SELECT close FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 6",
                (code,)
            ).fetchall()
            if len(row) >= 6:
                latest = row[0]['close']
                prev = row[5]['close']
                if prev and prev > 0:
                    return round((latest - prev) / prev * 100, 2)
        except Exception:
            pass
        return None

    def _module_market(self):
        """模块5：大盘环境"""
        result = {
            'market_light': 'yellow',
            'ftd_confirmed': False,
            'distribution_days': 0,
            'crowding': None,
            'nhnl_diff': None,
            'index_trend': '',
        }

        # 追盘日
        try:
            row = self.db.execute(
                "SELECT * FROM follow_through_day ORDER BY ftd_date DESC LIMIT 1"
            ).fetchone()
            if row:
                result['ftd_confirmed'] = bool(row.get('confirmed', 0))
        except sqlite3.OperationalError:
            pass

        # 抛盘日
        try:
            row = self.db.execute(
                "SELECT COUNT(*) as cnt FROM distribution_day WHERE dd_date >= date('now', '-25 days')"
            ).fetchone()
            if row:
                result['distribution_days'] = row['cnt']
        except sqlite3.OperationalError:
            pass

        # 拥挤度
        try:
            row = self.db.execute(
                "SELECT crowding_score FROM index_crowding_daily WHERE index_code='000985' ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if row:
                result['crowding'] = row['crowding_score']
        except sqlite3.OperationalError:
            pass

        # 红绿灯判定
        dd = result['distribution_days']
        ftd = result['ftd_confirmed']
        if ftd and dd < 3:
            result['market_light'] = 'green'
        elif dd >= 5:
            result['market_light'] = 'red'
        else:
            result['market_light'] = 'yellow'

        light_labels = {'green': '🟢 上升趋势', 'yellow': '🟡 压力中', 'red': '🔴 修正中'}
        result['light_label'] = light_labels.get(result['market_light'], '⚪ 未知')

        # 中证全指趋势
        try:
            rows = self.db.execute(
                "SELECT close FROM index_daily_kline WHERE index_code='000985' ORDER BY date DESC LIMIT 20"
            ).fetchall()
            if rows:
                result['index_trend'] = f"最新 {rows[0]['close']:.0f}"
        except sqlite3.OperationalError:
            pass

        return result

    def _module_stop_loss(self, c):
        """模块9：止损/止盈建议"""
        from .position import calculate_stop_loss, get_trailing_stop_rule_text

        signals = c.get('signals', [])
        primary_signal = signals[0] if signals else ''

        # 估算入场价（信号次日开盘价，或当前最新收盘价兜底）
        entry_price = None
        code = c['stock_code']

        # 获取信号日 K 线
        signal_date = c.get('signal_date', '')
        if signal_date:
            row = self.db.execute(
                "SELECT open, high, low, close FROM daily_kline WHERE stock_code=? AND date=?",
                (code, signal_date)
            ).fetchone()
            if row:
                entry_price = row['close']  # 信号日收盘作为参考

        if not entry_price:
            row = self.db.execute(
                "SELECT close FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 1",
                (code,)
            ).fetchone()
            entry_price = row['close'] if row else 10.0

        # 获取B2日最低价（MW信号）
        b2_low = None
        if 'mw_plus' in signals or 'mw_b2' in signals:
            b2_date = c.get('signal_date', '')
            if b2_date:
                row = self.db.execute(
                    "SELECT low FROM daily_kline WHERE stock_code=? AND date=?",
                    (code, b2_date)
                ).fetchone()
                if row:
                    b2_low = row['low']

        # 获取L点价格
        l_price = c.get('l_price')

        # 获取MA10
        ma10 = None
        row = self.db.execute(
            "SELECT close FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 10",
            (code,)
        ).fetchall()
        if len(row) >= 10:
            ma10 = sum(r['close'] for r in row) / 10

        # 计算止损
        signal_type_map = {
            'mw_plus': 'mw_plus',
            'mw_b2': 'mw_b2',
            'pocket_pivot_b1': 'pocket_pivot_base',
            'pocket_pivot': 'pocket_pivot_base',
            'base_breakout': 'base_breakout',
        }
        st = signal_type_map.get(primary_signal, 'mw_b2')

        stop_price, stop_rule = calculate_stop_loss(
            st, entry_price,
            h_price=c.get('h_price'),
            l_price=l_price,
            b2_low=b2_low,
            signal_low=entry_price * 0.92,  # 兜底取8%
            ma10=ma10
        )

        return {
            'entry_price_ref': round(entry_price, 2),
            'stop_loss_price': round(stop_price, 2) if stop_price else round(entry_price * 0.92, 2),
            'stop_loss_rule': stop_rule,
            'trailing_stop_rule': get_trailing_stop_rule_text(),
            'target_price': c.get('h_price'),  # 第一目标位 = H点
        }

    def close(self):
        self.db.close()
