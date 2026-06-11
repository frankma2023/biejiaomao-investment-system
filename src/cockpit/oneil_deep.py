"""
欧奈尔深度分析引擎 — DeepSeek API + trade-like-oneil 技能

输入股票全维度数据，调用 DeepSeek LLM 生成 1000+ 字 HTML 分析报告，
缓存到 data/cockpit/oneil/{date}/{stock_code}.html

用法：
    from cockpit.oneil_deep import ONeilDeepAnalyzer
    analyzer = ONeilDeepAnalyzer()
    report_path = analyzer.analyze(stock_code, stock_data, market_data)
"""
import os
import sys
import sqlite3
import json
import re
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')

# ── DeepSeek 配置 ──
def _load_env():
    env_path = os.path.join(os.path.dirname(PROJECT_ROOT), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

DEEPSEEK_KEY = os.environ.get('DEEPSEEK_KEY', '')
DEEPSEEK_BASE_URL = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')

# trade-like-oneil 技能路径
SKILL_PATH = os.path.join(os.path.dirname(PROJECT_ROOT), '.hanako', 'skills', 'trade-like-oneil', 'SKILL.md')


class ONeilDeepAnalyzer:
    """基于 DeepSeek + trade-like-oneil 技能的深度分析引擎"""

    def __init__(self, db=None):
        self.db = db or sqlite3.connect(DB_PATH)
        self.db.row_factory = sqlite3.Row
        self._skill_content = None

    def _load_skill(self):
        """加载 trade-like-oneil 技能内容"""
        if self._skill_content:
            return self._skill_content
        try:
            with open(SKILL_PATH, 'r', encoding='utf-8') as f:
                self._skill_content = f.read()
        except FileNotFoundError:
            self._skill_content = "欧奈尔交易框架：CAN SLIM + 口袋支点 + 关键点买入 + 7-8%止损 + 头寸集中"
        return self._skill_content

    def analyze(self, stock_code, candidate_data, market_data=None, run_date=None):
        """
        生成深度欧奈尔分析报告。

        返回: HTML 文件路径 或 None
        """
        # 构建股票全维度数据
        stock_info = self._build_stock_profile(stock_code, candidate_data)

        # 构建市场环境数据
        market_info = self._build_market_profile(market_data)

        # 构建 prompt
        system_prompt = self._load_skill() + "\n\n---\n\n你是一个专业的欧奈尔投资分析师。请基于上述交易框架，对以下股票进行全面分析。"
        user_prompt = self._build_prompt(stock_code, stock_info, market_info)

        # 调用 DeepSeek
        try:
            analysis_text = self._call_deepseek(system_prompt, user_prompt)
        except Exception as e:
            print(f"  [oneil_deep] DeepSeek 调用失败: {e}")
            return None

        if not analysis_text:
            return None

        # 生成 HTML 并保存
        html_content = self._text_to_html(stock_code, stock_info, analysis_text, run_date)
        return self._save_html(stock_code, html_content, run_date)

    def _build_stock_profile(self, stock_code, candidate):
        """构建股票全维度数据"""
        code = stock_code
        profile = {
            'code': code,
            'name': candidate.get('stock_name', ''),
            'signal_date': candidate.get('signal_date', ''),
        }

        # 1. MW 信号
        mw_rows = self.db.execute("""
            SELECT b2_date, b1_date, score, score_v2, is_plus, confidence,
                   decline_pct, h_date, h_price, l_date, l_price,
                   b2_return_pct, b2_is_gap, h_rs250, ind_rs250, ind_name,
                   score_h, score_d, score_c, score_p, score_i1, score_i2
            FROM mw_signal_daily
            WHERE stock_code=? AND b2_date=?
        """, (code, candidate.get('signal_date', ''))).fetchone()

        if mw_rows:
            mw = dict(mw_rows)
            profile['mw_signal'] = {
                'b2_date': mw['b2_date'], 'b1_date': mw['b1_date'],
                'score': mw.get('score_v2') or mw.get('score'),
                'is_plus': bool(mw.get('is_plus')),
                'confidence': mw.get('confidence'),
                'decline_pct': mw.get('decline_pct'),
                'h_date': mw.get('h_date'), 'h_price': mw.get('h_price'),
                'l_date': mw.get('l_date'), 'l_price': mw.get('l_price'),
                'b2_return_pct': mw.get('b2_return_pct'),
                'b2_is_gap': mw.get('b2_is_gap'),
                'h_rs250': mw.get('h_rs250'),
                'ind_rs250': mw.get('ind_rs250'),
                'ind_name': mw.get('ind_name'),
                'dimensions': {
                    'H_前高趋势': mw.get('score_h'),
                    'D_调整深度': mw.get('score_d'),
                    'C_横盘质量': mw.get('score_c'),
                    'P_整理回撤': mw.get('score_p'),
                    'I1_行业RS': mw.get('score_i1'),
                    'I2_个股RS': mw.get('score_i2'),
                }
            }

        # 2. 口袋支点
        pp_rows = self.db.execute("""
            SELECT date, pivot_type, b1_overlap, gain_pct, vol_ratio,
                   close_position, rps_20, rps_250, base_depth, c_days
            FROM pocket_pivot_daily
            WHERE stock_code=? AND date >= date(?,'-10 days')
            ORDER BY date DESC LIMIT 3
        """, (code, candidate.get('signal_date', ''))).fetchall()
        profile['pocket_pivots'] = [dict(r) for r in pp_rows]

        # 3. 基部突破
        try:
            bo_rows = self.db.execute("""
                SELECT date, close, change_pct, volume, amount
                FROM market_breakout_daily
                WHERE stock_code=? AND date >= date(?,'-10 days')
                ORDER BY date DESC LIMIT 3
            """, (code, candidate.get('signal_date', ''))).fetchall()
            profile['base_breakouts'] = [dict(r) for r in bo_rows]
        except sqlite3.OperationalError:
            profile['base_breakouts'] = []

        # 4. CANSLIM 评分
        canslim = {}
        for field in ['canslim_total', 'canslim_c', 'canslim_a', 'canslim_n',
                       'canslim_s', 'canslim_l', 'canslim_i', 'canslim_m']:
            if candidate.get(field) is not None:
                canslim[field] = candidate[field]
        if not canslim:
            obs = self.db.execute("""
                SELECT canslim_total, canslim_c, canslim_a, canslim_n,
                       canslim_s, canslim_l, canslim_i, canslim_m, roe, eps_yoy, revenue_yoy
                FROM discipline_observation_pool
                WHERE stock_code=? ORDER BY date DESC LIMIT 1
            """, (code,)).fetchone()
            if obs:
                canslim = dict(obs)
        profile['canslim'] = canslim

        # 5. 近期涨跌幅和成交量（5/10/20日）
        profile['price_volume'] = self._calc_price_volume(code)

        # 6. TA-Lib / K线形态（取最近5个交易日）
        profile['candlestick'] = self._get_candlestick_patterns(code)

        # 7. 行业RS
        ind_rows = self.db.execute("""
            SELECT industry_name FROM stock_industry
            WHERE stock_code=? AND source='zx' LIMIT 1
        """, (code,)).fetchone()
        profile['l1_industry'] = ind_rows['industry_name'] if ind_rows else ''
        profile['l1_rs250'] = candidate.get('ind_rs250') or candidate.get('l1_rs250')

        # 8. 当前价格位置（距MA10/MA50/MA200的距离）
        profile['price_position'] = self._calc_price_position(code)

        # 9. 近5日信号汇总
        profile['recent_signals'] = candidate.get('signals', [])

        return profile

    def _calc_price_volume(self, stock_code):
        """计算近 5/10/20 日涨跌幅和均量趋势"""
        rows = self.db.execute("""
            SELECT date, close, volume, change_pct
            FROM daily_kline
            WHERE stock_code=?
            ORDER BY date DESC LIMIT 22
        """, (stock_code,)).fetchall()

        if not rows:
            return {}

        result = {'latest_close': rows[0]['close'], 'latest_date': rows[0]['date']}

        for period, days in [('5日', 5), ('10日', 10), ('20日', 20)]:
            if len(rows) >= days + 1:
                recent = rows[:days]
                older_close = rows[days]['close'] if len(rows) > days else rows[-1]['close']
                if older_close and older_close > 0:
                    pct = (recent[0]['close'] - older_close) / older_close * 100
                else:
                    pct = 0
                avg_vol = sum(r['volume'] for r in recent) / len(recent)
                # 前半段 vs 后半段均量（判断放量/缩量趋势）
                half = days // 2
                first_half_vol = sum(r['volume'] for r in recent[half:]) / half
                second_half_vol = sum(r['volume'] for r in recent[:half]) / half
                vol_trend = '放量' if second_half_vol > first_half_vol * 1.1 else (
                    '缩量' if second_half_vol < first_half_vol * 0.9 else '持平')
                result[period] = {
                    'pct': round(pct, 2),
                    'avg_vol': int(avg_vol),
                    'vol_trend': vol_trend,
                }
            else:
                result[period] = {'pct': None, 'avg_vol': None, 'vol_trend': '数据不足'}

        # 逐日涨跌（最近10日）
        daily = []
        for i in range(min(10, len(rows) - 1)):
            r = rows[i]
            raw_pct = r['change_pct']
            # daily_kline.change_pct 是小数（0.0751=7.51%），需乘100
            pct = round(raw_pct * 100, 2) if raw_pct is not None and abs(raw_pct) <= 1 else round(raw_pct, 2) if raw_pct else None
            daily.append({
                'date': r['date'],
                'close': r['close'],
                'pct': pct,
                'vol': r['volume'],
            })
        result['daily_10'] = daily

        return result

    def _get_candlestick_patterns(self, stock_code):
        """获取最近5日的 TA-Lib K线形态"""
        rows = self.db.execute("""
            SELECT date, open, high, low, close, volume
            FROM daily_kline
            WHERE stock_code=?
            ORDER BY date DESC LIMIT 35
        """, (stock_code,)).fetchall()

        if len(rows) < 5:
            return []

        patterns = []
        try:
            import numpy as np
            import talib

            closes = np.array([r['close'] for r in reversed(rows)], dtype=np.float64)
            opens = np.array([r['open'] for r in reversed(rows)], dtype=np.float64)
            highs = np.array([r['high'] for r in reversed(rows)], dtype=np.float64)
            lows = np.array([r['low'] for r in reversed(rows)], dtype=np.float64)

            pattern_funcs = [
                ('CDLDOJI', '十字星', talib.CDLDOJI),
                ('CDLHAMMER', '锤子线', talib.CDLHAMMER),
                ('CDLENGULFING', '吞没形态', talib.CDLENGULFING),
                ('CDLMORNINGSTAR', '启明星', talib.CDLMORNINGSTAR),
                ('CDLEVENINGSTAR', '黄昏星', talib.CDLEVENINGSTAR),
                ('CDLHARAMI', '孕线', talib.CDLHARAMI),
                ('CDLPIERCING', '刺透形态', talib.CDLPIERCING),
                ('CDLDARKCLOUDCOVER', '乌云盖顶', talib.CDLDARKCLOUDCOVER),
                ('CDLSHOOTINGSTAR', '射击之星', talib.CDLSHOOTINGSTAR),
                ('CDLINVERTEDHAMMER', '倒锤子', talib.CDLINVERTEDHAMMER),
                ('CDL3WHITESOLDIERS', '三白兵', talib.CDL3WHITESOLDIERS),
                ('CDL3BLACKCROWS', '三只乌鸦', talib.CDL3BLACKCROWS),
                ('CDLMARUBOZU', '光头光脚', talib.CDLMARUBOZU),
            ]

            for func_name, cn_name, func in pattern_funcs:
                result = func(opens, highs, lows, closes)
                for j in range(max(0, len(result) - 5), len(result)):
                    if result[j] != 0:
                        idx = len(rows) - 1 - j
                        if 0 <= idx < len(rows):
                            r = rows[idx]
                            patterns.append({
                                'date': r['date'],
                                'pattern': cn_name,
                                'signal': 'bullish' if result[j] > 0 else 'bearish',
                                'open': r['open'], 'close': r['close'],
                                'high': r['high'], 'low': r['low'],
                            })
        except ImportError:
            pass

        return patterns

    def _calc_price_position(self, stock_code):
        """计算当前价格相对各均线的位置"""
        rows = self.db.execute("""
            SELECT close FROM daily_kline
            WHERE stock_code=?
            ORDER BY date DESC LIMIT 250
        """, (stock_code,)).fetchall()

        closes = [r['close'] for r in rows]
        if not closes:
            return {}

        latest = closes[0]
        result = {'latest': latest}

        for period in [10, 20, 50, 60, 120, 200]:
            if len(closes) >= period:
                ma = sum(closes[:period]) / period
                pct = (latest - ma) / ma * 100
                result[f'MA{period}'] = {'value': round(ma, 2), 'pct': round(pct, 2)}

        return result

    def _build_market_profile(self, market_data):
        """构建市场环境数据"""
        m = market_data or {}
        profile = {
            'light': m.get('market_light', 'unknown'),
            'light_label': m.get('light_label', ''),
            'ftd_confirmed': m.get('ftd_confirmed', False),
            'distribution_days': m.get('distribution_days', 0),
            'crowding': m.get('crowding'),
        }

        # 大盘健康分 + 抛盘日/追盘日/吸筹日
        try:
            # 近30日抛盘日
            dd = self.db.execute("""
                SELECT COUNT(*) as cnt FROM distribution_day
                WHERE dd_date >= date('now', '-30 days')
            """).fetchone()
            profile['distribution_days_30d'] = dd['cnt'] if dd else 0

            # 最近追盘日
            ftd = self.db.execute("""
                SELECT ftd_date FROM follow_through_day
                WHERE confirmed=1 ORDER BY ftd_date DESC LIMIT 1
            """).fetchone()
            profile['last_ftd'] = ftd['ftd_date'] if ftd else '无'

            # 吸筹日
            ad = self.db.execute("""
                SELECT COUNT(*) as cnt FROM accumulation_day
                WHERE ad_date >= date('now', '-30 days')
            """).fetchone()
            profile['accumulation_days_30d'] = ad['cnt'] if ad else 0

            # 市场健康分
            mh = self.db.execute("""
                SELECT score, risk_level FROM market_health
                ORDER BY date DESC LIMIT 1
            """).fetchone()
            if mh:
                profile['market_health_score'] = mh['score']
                profile['market_health_risk'] = mh['risk_level']

            # 卖出评分
            ms = self.db.execute("""
                SELECT score, signal FROM market_sell_score
                ORDER BY date DESC LIMIT 1
            """).fetchone()
            if ms:
                profile['market_sell_score'] = ms['score']
                profile['market_sell_signal'] = ms['signal']

        except sqlite3.OperationalError:
            pass

        return profile

    def _build_prompt(self, stock_code, info, market):
        """构建分析 prompt"""
        parts = []
        parts.append(f"## 股票: {info['name']}({stock_code})")
        parts.append(f"信号日期: {info.get('signal_date', '')}")

        # MW 信号
        mw = info.get('mw_signal')
        if mw:
            parts.append("\n### MW 信号")
            parts.append(f"- 类型: {'⭐ PLUS高分信号' if mw['is_plus'] else 'MW B2二次确认'}")
            parts.append(f"- B2日期: {mw['b2_date']}, B1首次突破: {mw['b1_date']}")
            parts.append(f"- 综合评分: {mw['score']}/100, 置信度: {mw.get('confidence','')}")
            parts.append(f"- 调整深度(H→L): {(mw.get('decline_pct') or 0):.1f}%")
            parts.append(f"- 前高H: {mw.get('h_date')} ¥{(mw.get('h_price') or 0):.2f}")
            parts.append(f"- 低点L: {mw.get('l_date')} ¥{(mw.get('l_price') or 0):.2f}")
            parts.append(f"- B2涨幅: {(mw.get('b2_return_pct') or 0):.1f}%")
            parts.append(f"- B2跳空: {'是' if mw.get('b2_is_gap') else '否'}")
            parts.append(f"- 前高时点RS250: {mw.get('h_rs250') or '?'}")
            dims = mw.get('dimensions', {})
            if dims:
                parts.append(f"- 维度评分: H={dims.get('H_前高趋势') or '?'} D={dims.get('D_调整深度') or '?'} C={dims.get('C_横盘质量') or '?'} P={dims.get('P_整理回撤') or '?'} I1={dims.get('I1_行业RS') or '?'} I2={dims.get('I2_个股RS') or '?'}")

        # 口袋支点
        pp = info.get('pocket_pivots', [])
        if pp:
            parts.append("\n### 口袋支点信号")
            for p in pp[:3]:
                parts.append(f"- {p['date']}: {p['pivot_type']}, 涨幅{(p.get('gain_pct') or 0):.1f}%, 量比{(p.get('vol_ratio') or 0):.1f}, B1重合:{'是' if p.get('b1_overlap') else '否'}, 盘整{p.get('c_days') or 0}天")

        # 基部突破
        bo = info.get('base_breakouts', [])
        if bo:
            parts.append("\n### 基部突破信号")
            for b in bo[:3]:
                parts.append(f"- {b['date']}: 涨幅{b.get('change_pct',0):.1f}%")

        # CANSLIM
        canslim = info.get('canslim', {})
        if canslim:
            parts.append("\n### CAN SLIM 评分")
            parts.append(f"- 总分: {canslim.get('canslim_total','?')}/100")
            parts.append(f"- C(当季EPS): {canslim.get('canslim_c','?')} A(年度EPS): {canslim.get('canslim_a','?')}")
            parts.append(f"- N(新事物): {canslim.get('canslim_n','?')} S(供需): {canslim.get('canslim_s','?')}")
            parts.append(f"- L(领涨): {canslim.get('canslim_l','?')} I(机构): {canslim.get('canslim_i','?')}")
            parts.append(f"- M(市场): {canslim.get('canslim_m','?')}")
            parts.append(f"- ROE: {canslim.get('roe','?')}% EPS增速: {canslim.get('eps_yoy','?')}%")

        # 涨跌幅和成交量
        pv = info.get('price_volume', {})
        if pv:
            parts.append("\n### 近期涨跌幅与成交量")
            parts.append(f"最新价: ¥{pv.get('latest_close','?')} ({pv.get('latest_date','')})")
            for period in ['5日', '10日', '20日']:
                d = pv.get(period, {})
                if d.get('pct') is not None:
                    parts.append(f"- {period}: {d['pct']:+.2f}%, 均量{d['avg_vol']:,}, {d['vol_trend']}")

            daily = pv.get('daily_10', [])
            if daily:
                parts.append("- 近10日逐日涨跌:")
                for d in daily[:10]:
                    parts.append(f"  {d['date']}: {(d['pct'] or 0):+.2f}% (¥{d['close']:.2f})")

        # K线形态
        cdls = info.get('candlestick', [])
        if cdls:
            parts.append("\n### TA-Lib K线形态（近5日）")
            for c in cdls:
                parts.append(f"- {c['date']}: {c['pattern']}({'看涨' if c['signal']=='bullish' else '看跌'}), O={c['open']:.2f} H={c['high']:.2f} L={c['low']:.2f} C={c['close']:.2f}")

        # 价格位置
        pos = info.get('price_position', {})
        if pos:
            parts.append("\n### 当前价格位置（相对均线）")
            for key in ['MA10', 'MA20', 'MA50', 'MA60', 'MA120', 'MA200']:
                if key in pos:
                    d = pos[key]
                    parts.append(f"- {key}: {d['value']:.2f} (价格{'上方' if d['pct']>=0 else '下方'} {abs(d['pct']):.1f}%)")

        # 行业RS
        parts.append(f"\n### 行业背景")
        parts.append(f"- 中证一级行业: {info.get('l1_industry', '未知')}")
        parts.append(f"- 行业RS250: {info.get('l1_rs250', '?')}")

        # 近期信号汇总
        sigs = info.get('recent_signals', [])
        if sigs:
            parts.append(f"\n### 近期信号: {', '.join(sigs)}")

        # 市场环境
        parts.append("\n## 大盘环境")
        parts.append(f"- 大盘评级: {market.get('light_label','?')}")
        parts.append(f"- 近30日抛盘日: {market.get('distribution_days_30d','?')}个")
        parts.append(f"- 近30日吸筹日: {market.get('accumulation_days_30d','?')}个")
        parts.append(f"- 最近追盘日: {market.get('last_ftd','?')}")
        parts.append(f"- 市场健康分: {market.get('market_health_score','?')}")
        parts.append(f"- 市场健康风险: {market.get('market_health_risk','?')}")
        parts.append(f"- 卖出评分: {market.get('market_sell_score','?')}")
        parts.append(f"- 卖出信号: {market.get('market_sell_signal','?')}")

        parts.append("
---
注意：I（机构持股）维度数据因A股散户条件限制不可获取，此项不参与本次评估，请基于其余维度分析。

请按照欧奈尔交易框架，对上述股票进行全面分析。要求：")
        parts.append("1. 逐条对照10条核心规则给出评估")
        parts.append("2. 分析口袋支点和基部突破的质量")
        parts.append("3. 评估CANSLIM各维度的强弱")
        parts.append("4. 结合大盘环境给出择时建议")
        parts.append("5. 给出明确的买卖建议（买入/谨慎买入/观望/不建议）")
        parts.append("6. 至少1000字，用中文撰写")
        parts.append("7. 用Markdown格式输出，包含标题层级和要点")

        return '\n'.join(parts)

    def _call_deepseek(self, system_prompt, user_prompt):
        """调用 DeepSeek API"""
        import urllib.request

        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {DEEPSEEK_KEY}',
        }
        body = {
            'model': 'deepseek-chat',
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'temperature': 0.7,
            'max_tokens': 4096,
        }

        data = json.dumps(body).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')

        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode('utf-8'))

        if result.get('choices') and len(result['choices']) > 0:
            return result['choices'][0]['message']['content']
        return None

    def _text_to_html(self, stock_code, info, text, run_date):
        """将 Markdown 分析文本转为完整 HTML 页面"""
        name = info.get('name', stock_code)
        date_str = run_date or datetime.now().strftime('%Y-%m-%d')

        # 基础 Markdown → HTML 转换
        html_body = self._md_to_html(text)

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>欧奈尔分析 - {name}({stock_code})</title>
<style>
  body {{ font-family: 'Inter', -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
         max-width: 800px; margin: 0 auto; padding: 24px 20px 60px;
         background: #1a1a1f; color: #d4d4d8; line-height: 1.8; }}
  h1 {{ font-size: 1.3rem; color: #f59e0b; border-bottom: 1px solid #333; padding-bottom: 8px; }}
  h2 {{ font-size: 1.05rem; color: #e5e5eb; margin-top: 24px; }}
  h3 {{ font-size: 0.9rem; color: #a78bfa; }}
  strong {{ color: #fbbf24; }}
  em {{ color: #a1a1aa; }}
  ul, ol {{ padding-left: 20px; }}
  li {{ margin: 4px 0; }}
  blockquote {{ border-left: 3px solid #f59e0b; padding-left: 12px; color: #a1a1aa; margin: 12px 0; }}
  code {{ background: rgba(245,158,11,.1); padding: 2px 6px; border-radius: 4px; font-family: 'JetBrains Mono',monospace; }}
  .meta {{ font-size: 0.65rem; color: #666; text-align: center; margin-bottom: 24px; }}
  .verdict {{ display: inline-block; padding: 4px 14px; border-radius: 8px; font-weight: 700; margin: 8px 0; }}
  .verdict.buy {{ background: rgba(16,185,129,.15); color: #10b981; }}
  .verdict.caution {{ background: rgba(245,158,11,.15); color: #f59e0b; }}
  .verdict.wait {{ background: rgba(139,139,144,.1); color: #a1a1aa; }}
  .verdict.avoid {{ background: rgba(239,68,68,.1); color: #ef4444; }}
  a {{ color: #f59e0b; }}
</style>
</head>
<body>
<h1>欧奈尔深度分析</h1>
<div class="meta">{name}({stock_code}) · 信号日 {info.get('signal_date','')} · 分析生成 {date_str}</div>
{html_body}
<div style="margin-top:32px;padding-top:12px;border-top:1px solid #333;font-size:.6rem;color:#666;text-align:center">
  基于《像欧奈尔信徒一样交易》框架 · DeepSeek 生成 · 仅供参考，不构成投资建议
</div>
</body>
</html>"""

    def _md_to_html(self, text):
        """简单 Markdown → HTML"""
        lines = text.split('\n')
        result = []
        in_list = False
        in_ol = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if in_list or in_ol:
                    result.append('</ul>' if in_list else '</ol>')
                    in_list = in_ol = False
                continue

            # 标题
            if stripped.startswith('### '):
                if in_list or in_ol:
                    result.append('</ul>' if in_list else '</ol>')
                    in_list = in_ol = False
                result.append(f'<h3>{stripped[4:]}</h3>')
            elif stripped.startswith('## '):
                if in_list or in_ol:
                    result.append('</ul>' if in_list else '</ol>')
                    in_list = in_ol = False
                result.append(f'<h2>{stripped[3:]}</h2>')
            elif stripped.startswith('# '):
                if in_list or in_ol:
                    result.append('</ul>' if in_list else '</ol>')
                    in_list = in_ol = False
                result.append(f'<h1>{stripped[2:]}</h1>')
            # 无序列表
            elif stripped.startswith('- ') or stripped.startswith('* '):
                if not in_list:
                    if in_ol:
                        result.append('</ol>')
                        in_ol = False
                    result.append('<ul>')
                    in_list = True
                result.append(f'<li>{self._inline_md(stripped[2:])}</li>')
            # 有序列表
            elif re.match(r'^\d+\.\s', stripped):
                if not in_ol:
                    if in_list:
                        result.append('</ul>')
                        in_list = False
                    result.append('<ol>')
                    in_ol = True
                content = re.sub(r'^\d+\.\s', '', stripped)
                result.append(f'<li>{self._inline_md(content)}</li>')
            # 引用块
            elif stripped.startswith('> '):
                result.append(f'<blockquote>{self._inline_md(stripped[2:])}</blockquote>')
            # 水平线
            elif stripped == '---':
                result.append('<hr>')
            # 结论判断（特殊处理）
            elif '推荐买入' in stripped or '谨慎买入' in stripped or '观望' in stripped or '不建议' in stripped:
                cls = 'buy' if '推荐买入' in stripped else ('caution' if '谨慎买入' in stripped else ('wait' if '观望' in stripped else 'avoid'))
                result.append(f'<p><span class="verdict {cls}">{stripped}</span></p>')
            else:
                # 粗体处理
                result.append(f'<p>{self._inline_md(stripped)}</p>')

        if in_list:
            result.append('</ul>')
        if in_ol:
            result.append('</ol>')

        return '\n'.join(result)

    def _inline_md(self, text):
        """行内 Markdown 处理"""
        # 粗体
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        # 斜体
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        # 行内代码
        text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
        return text

    def _save_html(self, stock_code, html_content, run_date):
        """保存 HTML 到文件"""
        date_str = run_date or datetime.now().strftime('%Y-%m-%d')
        output_dir = os.path.join(PROJECT_ROOT, 'data', 'cockpit', 'oneil', date_str)
        os.makedirs(output_dir, exist_ok=True)

        filepath = os.path.join(output_dir, f'{stock_code}.html')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html_content)

        # 返回相对路径（供前端链接）
        return f'data/cockpit/oneil/{date_str}/{stock_code}.html'
