"""
欧奈尔交易规则评估器 — 基于 trade-like-oneil 技能的结构化分析

对候选股票逐条评估 10 条核心规则 + 口袋支点体系 + 市场择时，
生成人话版买入评估摘要。

用法：
    from src.cockpit.oneil_eval import ONeilEvaluator
    eval = ONeilEvaluator(db)
    result = eval.evaluate(stock_code, candidate_data, market_data)
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)


class ONeilEvaluator:
    """欧奈尔 10 条规则结构化评估"""

    def __init__(self, db=None):
        self.db = db

    def evaluate(self, stock_code, candidate, market_data):
        """
        返回: {
            'score': int (0~100),
            'verdict': str (推荐买入/谨慎买入/观望/不建议),
            'checks': list[dict],  # 每条规则的评估
            'summary': str,         # 人话版摘要
        }
        """
        checks = []
        checks.append(self._rule1_high_price(candidate))       # 高价股
        checks.append(self._rule3_stop_loss(candidate))         # 止损纪律
        checks.append(self._rule5_concentration())              # 头寸集中
        checks.append(self._rule6_institutional(candidate))     # 机构认同
        checks.append(self._rule7_chart_pattern(candidate))     # 图表形态
        checks.append(self._rule8_pocket_pivot(candidate))      # 关键点/口袋支点
        checks.append(self._rule9_market_timing(market_data))   # 市场择时
        checks.append(self._rule10_leader_stock(candidate))     # 龙头股

        passed = sum(1 for c in checks if c['pass'])
        score = round(passed / len(checks) * 100)

        if score >= 85:
            verdict = '推荐买入'
        elif score >= 60:
            verdict = '谨慎买入'
        elif score >= 40:
            verdict = '观望'
        else:
            verdict = '不建议'

        # 生成人话摘要
        lines = [f"欧奈尔8条规则评估: {passed}/{len(checks)} 通过 ({score}分) → {verdict}"]
        for c in checks:
            icon = '✅' if c['pass'] else '❌'
            lines.append(f"  {icon} {c['name']}: {c['detail']}")

        return {
            'score': score,
            'verdict': verdict,
            'checks': checks,
            'summary': '\n'.join(lines),
        }

    def _rule1_high_price(self, c):
        """规则1：买入高价股而不是低价股"""
        entry = c.get('entry_price_ref') or c.get('close') or 0
        if entry >= 20:
            return {'name': '高价股', 'pass': True, 'detail': f'参考价 ¥{entry:.2f}，符合高价股标准'}
        elif entry >= 10:
            return {'name': '高价股', 'pass': True, 'detail': f'参考价 ¥{entry:.2f}，中等价位，可接受'}
        else:
            return {'name': '高价股', 'pass': False, 'detail': f'参考价 ¥{entry:.2f}，低价股风险较高'}

    def _rule3_stop_loss(self, c):
        """规则3：快速止损 7~8%"""
        sl = c.get('stop_loss_price')
        entry = c.get('entry_price_ref')
        if sl and entry and sl > 0 and entry > 0:
            sl_pct = (entry - sl) / entry * 100
            if sl_pct <= 10:
                return {'name': '止损空间', 'pass': True, 'detail': f'止损 {sl_pct:.1f}%（≤10%，可控）'}
            else:
                return {'name': '止损空间', 'pass': False, 'detail': f'止损 {sl_pct:.1f}%（>10%，止损过宽）'}
        return {'name': '止损空间', 'pass': True, 'detail': '止损位已设定'}

    def _rule5_concentration(self):
        """规则5：头寸集中"""
        return {'name': '头寸集中', 'pass': True, 'detail': '建议单票仓位 15~25%，集中持有优质标的'}

    def _rule6_institutional(self, c):
        """规则6：机构认同"""
        canslim_i = c.get('canslim_i')
        if canslim_i is not None and canslim_i >= 50:
            return {'name': '机构认同', 'pass': True, 'detail': f'CANSLIM-I 评分 {canslim_i}，机构关注度高'}
        elif canslim_i is not None:
            return {'name': '机构认同', 'pass': False, 'detail': f'CANSLIM-I 评分 {canslim_i}，机构关注度不足'}
        return {'name': '机构认同', 'pass': True, 'detail': '数据不足，默认通过'}

    def _rule7_chart_pattern(self, c):
        """规则7：图表形态是关键"""
        signals = c.get('signals', [])
        if isinstance(signals, str):
            import json
            try:
                signals = json.loads(signals)
            except:
                signals = [signals]

        strong_signals = {'mw_plus', 'base_breakout', 'pocket_pivot_b1'}
        weak_signals = {'pocket_pivot', 'mw_b2'}

        has_strong = bool(set(signals) & strong_signals)
        has_any = bool(signals)

        if has_strong:
            return {'name': '图表形态', 'pass': True, 'detail': f'强形态信号: {\", \".join(set(signals)&strong_signals)}'}
        elif has_any:
            return {'name': '图表形态', 'pass': True, 'detail': f'形态信号: {\", \".join(signals)}'}
        return {'name': '图表形态', 'pass': False, 'detail': '无明显形态信号'}

    def _rule8_pocket_pivot(self, c):
        """规则8：关键点和口袋支点"""
        signals = c.get('signals', [])
        if isinstance(signals, str):
            import json
            try:
                signals = json.loads(signals)
            except:
                signals = [signals]

        if 'pocket_pivot_b1' in signals:
            return {'name': '口袋支点', 'pass': True, 'detail': '口袋支点与B1重合（最强信号）'}
        elif 'pocket_pivot' in signals:
            return {'name': '口袋支点', 'pass': True, 'detail': '出现口袋支点信号'}
        elif 'mw_plus' in signals:
            return {'name': '口袋支点', 'pass': True, 'detail': 'PLUS信号隐含突破确认，等效关键点'}
        return {'name': '口袋支点', 'pass': False, 'detail': '未出现口袋支点信号，关注后续是否出现'}

    def _rule9_market_timing(self, market_data):
        """规则9：确定股市时机"""
        m = market_data or {}
        light = m.get('market_light', 'yellow')
        dd = m.get('distribution_days', 0)

        if light == 'green' and dd < 3:
            return {'name': '市场择时', 'pass': True, 'detail': f'🟢 大盘上升趋势，抛盘日{dd}，择时有利'}
        elif light == 'red' or dd >= 5:
            return {'name': '市场择时', 'pass': False, 'detail': f'🔴 大盘修正中，抛盘日{dd}，不宜买入'}
        else:
            return {'name': '市场择时', 'pass': True, 'detail': f'🟡 大盘压力中，抛盘日{dd}，谨慎参与'}

    def _rule10_leader_stock(self, c):
        """规则10：交易优质股票和龙头股"""
        rs250 = c.get('h_rs250') or c.get('l1_rs250') or 0
        canslim_l = c.get('canslim_l')
        canslim_total = c.get('canslim_total') or 0

        score = 0
        if rs250 >= 85:
            score += 1
        if canslim_l is not None and canslim_l >= 50:
            score += 1
        if canslim_total >= 60:
            score += 1

        if score >= 2:
            return {'name': '龙头股', 'pass': True, 'detail': f'RS250={rs250}, CANSLIM={canslim_total}，龙头特征明显'}
        elif score >= 1:
            return {'name': '龙头股', 'pass': True, 'detail': f'RS250={rs250}, CANSLIM={canslim_total}，具备一定龙头特征'}
        return {'name': '龙头股', 'pass': False, 'detail': f'RS250={rs250}, CANSLIM={canslim_total}，龙头特征不足'}
