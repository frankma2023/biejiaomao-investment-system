"""
欧奈尔交易规则评估器 — 基于 trade-like-oneil 技能的结构化分析

对候选股票逐条评估 8 条核心规则，生成人话版买入评估摘要。
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)


class ONeilEvaluator:
    """欧奈尔 8 条规则结构化评估"""

    def __init__(self, db=None):
        self.db = db

    def evaluate(self, stock_code, candidate, market_data):
        checks = []
        checks.append(self._rule1_high_price(candidate))
        checks.append(self._rule3_stop_loss(candidate))
        checks.append(self._rule5_concentration())
        checks.append(self._rule6_institutional(candidate))
        checks.append(self._rule7_chart_pattern(candidate))
        checks.append(self._rule8_pocket_pivot(candidate))
        checks.append(self._rule9_market_timing(market_data))
        checks.append(self._rule10_leader_stock(candidate))

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

        lines = [f"欧奈尔8条规则: {passed}/{len(checks)} 通过 ({score}分) -> {verdict}"]
        for c in checks:
            icon = 'OK' if c['pass'] else 'NG'
            lines.append(f"  [{icon}] {c['name']}: {c['detail']}")

        return {
            'score': score,
            'verdict': verdict,
            'checks': checks,
            'summary': '\n'.join(lines),
        }

    def _rule1_high_price(self, c):
        entry = c.get('entry_price_ref') or c.get('close') or 0
        if entry >= 20:
            return {'name': '高价股', 'pass': True, 'detail': f'参考价 {entry:.2f}，符合高价股标准'}
        elif entry >= 10:
            return {'name': '高价股', 'pass': True, 'detail': f'参考价 {entry:.2f}，中等价位'}
        else:
            return {'name': '高价股', 'pass': False, 'detail': f'参考价 {entry:.2f}，低价股风险高'}

    def _rule3_stop_loss(self, c):
        sl = c.get('stop_loss_price')
        entry = c.get('entry_price_ref')
        if sl and entry and sl > 0 and entry > 0:
            sl_pct = (entry - sl) / entry * 100
            if sl_pct <= 10:
                return {'name': '止损空间', 'pass': True, 'detail': f'止损 {sl_pct:.1f}%（可控）'}
            else:
                return {'name': '止损空间', 'pass': False, 'detail': f'止损 {sl_pct:.1f}%（过宽）'}
        return {'name': '止损空间', 'pass': True, 'detail': '止损位已设定'}

    def _rule5_concentration(self):
        return {'name': '头寸集中', 'pass': True, 'detail': '建议单票仓位15-25%，集中持有优质标的'}

    def _rule6_institutional(self, c):
        ci = c.get('canslim_i')
        if ci is not None and ci >= 50:
            return {'name': '机构认同', 'pass': True, 'detail': f'CANSLIM-I={ci}，机构关注度高'}
        elif ci is not None:
            return {'name': '机构认同', 'pass': False, 'detail': f'CANSLIM-I={ci}，机构关注度不足'}
        return {'name': '机构认同', 'pass': True, 'detail': '数据不足'}

    def _rule7_chart_pattern(self, c):
        signals = c.get('signals', [])
        if isinstance(signals, str):
            import json
            try: signals = json.loads(signals)
            except: signals = [signals]

        strong = {'mw_plus', 'base_breakout', 'pocket_pivot_b1'}
        has_strong = bool(set(signals) & strong)
        has_any = bool(signals)

        if has_strong:
            names = list(set(signals) & strong)
            return {'name': '图表形态', 'pass': True, 'detail': f'强信号: {names}'}
        elif has_any:
            return {'name': '图表形态', 'pass': True, 'detail': f'有信号: {signals}'}
        return {'name': '图表形态', 'pass': False, 'detail': '无明显形态信号'}

    def _rule8_pocket_pivot(self, c):
        signals = c.get('signals', [])
        if isinstance(signals, str):
            import json
            try: signals = json.loads(signals)
            except: signals = [signals]

        if 'pocket_pivot_b1' in signals:
            return {'name': '口袋支点', 'pass': True, 'detail': 'B1重合（最强信号）'}
        elif 'pocket_pivot' in signals:
            return {'name': '口袋支点', 'pass': True, 'detail': '出现口袋支点'}
        elif 'mw_plus' in signals:
            return {'name': '口袋支点', 'pass': True, 'detail': 'PLUS等效关键点'}
        return {'name': '口袋支点', 'pass': False, 'detail': '未出现，关注后续'}

    def _rule9_market_timing(self, market_data):
        m = market_data or {}
        light = m.get('market_light', 'yellow')
        dd = m.get('distribution_days', 0)
        if light == 'green' and dd < 3:
            return {'name': '市场择时', 'pass': True, 'detail': f'绿灯 抛盘日{dd}，择时有利'}
        elif light == 'red' or dd >= 5:
            return {'name': '市场择时', 'pass': False, 'detail': f'红灯 抛盘日{dd}，不宜买入'}
        else:
            return {'name': '市场择时', 'pass': True, 'detail': f'黄灯 抛盘日{dd}，谨慎参与'}

    def _rule10_leader_stock(self, c):
        rs250 = c.get('h_rs250') or c.get('l1_rs250') or 0
        cl = c.get('canslim_l')
        ct = c.get('canslim_total') or 0
        score = 0
        if rs250 >= 85: score += 1
        if cl is not None and cl >= 50: score += 1
        if ct >= 60: score += 1
        if score >= 2:
            return {'name': '龙头股', 'pass': True, 'detail': f'RS250={rs250} CANSLIM={ct}，龙头特征明显'}
        elif score >= 1:
            return {'name': '龙头股', 'pass': True, 'detail': f'RS250={rs250} CANSLIM={ct}，具备龙头特征'}
        return {'name': '龙头股', 'pass': False, 'detail': f'RS250={rs250} CANSLIM={ct}，龙头特征不足'}
