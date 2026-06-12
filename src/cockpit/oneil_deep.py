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

# trade-like-oneil 技能路径（先找用户目录，再找项目本地）
SKILL_PATH = os.path.join(os.path.expanduser('~'), '.hanako', 'skills', 'trade-like-oneil', 'SKILL.md')
if not os.path.exists(SKILL_PATH):
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


    def _build_rich_profile(self, stock_code, candidate):
        """采集15项全维度数据"""
        code = stock_code
        p = {'code': code, 'name': candidate.get('stock_name', '')}
        db = self.db

        # 1. 基本信息
        p['signal_date'] = candidate.get('signal_date', '')
        row = db.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
        p['name'] = p['name'] or (row['name'] if row else code)
        # 行业
        ind = db.execute("SELECT industry_name FROM stock_industry WHERE stock_code=? LIMIT 1", (code,)).fetchone()
        p['industry'] = ind['industry_name'] if ind else ''; obs_ind = db.execute('SELECT industry_name FROM discipline_observation_pool WHERE stock_code=? ORDER BY date DESC LIMIT 1', (code,)).fetchone(); p['industry'] = p['industry'] or (obs_ind['industry_name'] if obs_ind else '未知')
        # 市值 (from pysnowball or market_cap_snapshot)
        try:
            from cockpit.sentiment import SentimentEngine
            q = SentimentEngine()._fetch_quote(code)
            if q and q.get('market_cap', 0) > 0:
                p['market_cap_yi'] = round(q['market_cap'] / 1e8, 0)
        except: pass
        if not p.get('market_cap_yi'):
            mc = db.execute("SELECT market_cap FROM market_cap_snapshot WHERE stock_code=?", (code,)).fetchone()
            p['market_cap_yi'] = round(mc['market_cap'], 0) if mc and mc['market_cap'] else None

        # 2. 发现路径
        p['discovery_path'] = '观察池→市值≥50亿→行业RS≥75→个股RS(H点)≥80→形态信号(B1/B2/PP/BO)'

        # 3. 大盘环境
        mh = db.execute("SELECT * FROM market_health_daily ORDER BY date DESC LIMIT 1").fetchone()
        if mh:
            p['market_health'] = {'score': mh['total_score'], 'rating': mh['rating'],
                'ma50_above': mh['ma50_above_value'], 'hl_ratio': mh['hl_ratio_value'],
                'ad_ratio': mh['ad_ratio_value'], 'vol_breakout': mh['vol_breakout_value']}
        ms = db.execute("SELECT total_score as score, signal_details as signal FROM market_sell_score_daily ORDER BY date DESC LIMIT 1").fetchone()
        if ms: p['market_sell'] = {'score': ms['score'], 'signal': ms['signal']}
        # 成交额
        amt = db.execute("SELECT date, amount FROM index_daily_kline WHERE stock_code='000985' ORDER BY date DESC LIMIT 20").fetchall()
        if amt:
            amt_list = [a['amount'] for a in amt if a['amount']]
            if amt_list:
                for d, n in [(5, 'avg5d'), (10, 'avg10d'), (20, 'avg20d')]:
                    if len(amt_list) >= d: p[f'index_amount_{n}'] = round(sum(amt_list[:d])/d/1e8, 1)
                # trend
                if len(amt_list) >= 10:
                    first5 = sum(amt_list[5:10])/5; last5 = sum(amt_list[:5])/5
                    p['index_amount_trend'] = '放量' if last5 > first5*1.1 else ('缩量' if last5 < first5*0.9 else '持平')
        # 中证全指均线
        idx_k = db.execute("SELECT close FROM index_daily_kline WHERE stock_code='000985' ORDER BY date DESC LIMIT 250").fetchall()
        if idx_k:
            closes = [k['close'] for k in idx_k]
            p['index_close'] = closes[0]
            for ma_n in [50, 120, 250]:
                if len(closes) >= ma_n:
                    ma_v = sum(closes[:ma_n]) / ma_n
                    p[f'index_ma{ma_n}'] = round(ma_v, 1)
                    p[f'index_vs_ma{ma_n}'] = round((closes[0]-ma_v)/ma_v*100, 1)
        # 抛盘日/追盘日
        snap = db.execute("SELECT dist_30d_count, ftd_30d_count, acc_30d_count FROM market_snapshot_daily ORDER BY date DESC LIMIT 1").fetchone()
        if snap:
            p['dist_30d'] = snap['dist_30d_count']; p['ftd_30d'] = snap['ftd_30d_count']; p['acc_30d'] = snap['acc_30d_count']

        # 4. 主要指数近10日
        major_idx = {'000001':'上证','399001':'深证','399006':'创业板','000688':'科创50','000300':'沪深300','000985':'中证全指'}
        p['major_indices'] = {}
        for ic, iname in major_idx.items():
            rows = db.execute(f"SELECT date, close FROM index_daily_kline WHERE stock_code='{ic}' ORDER BY date DESC LIMIT 10").fetchall()
            if rows: p['major_indices'][iname] = {r['date']: round(r['close'],1) for r in reversed(rows)}

        # 5. RS强度
        rs = db.execute("SELECT rps_20, rps_60, rps_120, rps_250 FROM stock_rs_daily WHERE stock_code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        if rs: p['rs'] = {'rps20': rs['rps_20'], 'rps60': rs['rps_60'], 'rps120': rs['rps_120'], 'rps250': rs['rps_250']}

        # 6. 行业RS
        mw_ind = db.execute("SELECT ind_rs250, ind_name FROM mw_signal_daily WHERE stock_code=? AND ind_rs250 IS NOT NULL ORDER BY b2_date DESC LIMIT 1", (code,)).fetchone()
        if mw_ind: p['ind_rs250'] = mw_ind['ind_rs250']; p['ind_name'] = mw_ind['ind_name']
        else:
            rs_tmp = db.execute("SELECT rps_250 FROM stock_rs_daily WHERE stock_code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
            if rs_tmp: p['ind_rs250'] = rs_tmp['rps_250']; p['ind_name'] = '全市场RS250近似'

        # 7. CAN SLIM 评分
        cs = db.execute("""
            SELECT score, grade, score_c, score_a, score_n, score_s, score_l, score_i
            FROM cansim_scores 
            WHERE stock_code=? AND date=(SELECT MAX(date) FROM cansim_scores WHERE stock_code=?)
        """, (code, code)).fetchone()
        if cs:
            p['canslim'] = {
                'total': cs['score'], 'grade': cs['grade'],
                'c': cs['score_c'], 'a': cs['score_a'], 'n': cs['score_n'],
                's': cs['score_s'], 'l': cs['score_l'], 'i': cs['score_i']
            }

        # 8. 均线位置
        kl = db.execute("SELECT close FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 250", (code,)).fetchall()
        if kl:
            cs = [k['close'] for k in kl]; p['latest_close'] = cs[0]
            for ma_n in [5,10,20,30,60,120,250]:
                if len(cs) >= ma_n:
                    ma_v = sum(cs[:ma_n]) / ma_n
                    p[f'ma{ma_n}'] = round(ma_v, 2)
                    p[f'vs_ma{ma_n}'] = round((cs[0]-ma_v)/ma_v*100, 1)

        # 9. 近20天股价
        kl20 = db.execute("SELECT date, open, high, low, close, volume FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 20", (code,)).fetchall()
        p['klines_20d'] = [{'date': k['date'], 'o': k['open'], 'h': k['high'], 'l': k['low'], 'c': k['close'], 'v': k['volume']} for k in reversed(kl20)]

        # 10. 成交量
        if kl20:
            vols = [k['v'] for k in p['klines_20d']]
            for d, n in [(5, 'vol5d'), (10, 'vol10d'), (20, 'vol20d')]:
                if len(vols) >= d: p[n] = int(sum(vols[:d]) / d)

        # 11. H/L/C结构
        mw = db.execute("SELECT h_date, h_price, l_date, l_price, decline_pct, c_start, c_end FROM mw_signal_daily WHERE stock_code=? AND h_date < l_date AND h_date IS NOT NULL ORDER BY b2_date DESC LIMIT 1", (code,)).fetchone()
        if mw and mw["h_date"] and mw["l_date"]:
            p['hlc'] = {'h_date': mw['h_date'], 'h_price': mw['h_price'], 'l_date': mw['l_date'],
                        'l_price': mw['l_price'], 'decline_pct': mw['decline_pct']}

        # 12. 延伸风险
        if p.get('ma10') and p.get('latest_close'):
            ext = (p['latest_close'] - p['ma10']) / p['ma10'] * 100
            p['extension_risk'] = f"距MA10 {ext:.1f}%{' ⚠️延伸超20%' if ext > 20 else ''}"

        # 13. 买入信号近5日
        buy_sigs = []
        sd = candidate.get('signal_date', '')
        # PP
        pp = db.execute("SELECT date, pivot_type FROM pocket_pivot_daily WHERE stock_code=? AND date >= date(?, '-5 days')", (code, sd)).fetchall()
        for r in pp: buy_sigs.append(f"PP-{r['pivot_type']}({r['date']})")
        # BO
        try:
            bo = db.execute("SELECT date FROM market_breakout_daily WHERE stock_code=? AND date >= date(?, '-5 days')", (code, sd)).fetchall()
            for r in bo: buy_sigs.append(f"BO({r['date']})")
        except: pass
        # MW
        mw_s = db.execute("SELECT b2_date, is_plus FROM mw_signal_daily WHERE stock_code=? AND b2_date >= date(?, '-5 days')", (code, sd)).fetchall()
        for r in mw_s: buy_sigs.append(f"{'PLUS' if r['is_plus'] else 'MW-B2'}({r['b2_date']})")
        # Pattern scan (TA-Lib)
        try:
            ps = db.execute("SELECT signals_json FROM pattern_scan_signals WHERE stock_code=? AND date >= date(?, '-5 days')", (code, sd)).fetchall()
            for r in ps:
                if r['signals_json']:
                    import json
                    sigs = json.loads(r['signals_json'])
                    for s in sigs:
                        if s.get('type') == 'bullish' and s.get('source') == 'cdl':
                            buy_sigs.append(f"{s.get('details',{}).get('cdl_name','TA-Lib')}({s.get('date','')})")
        except: pass
        p['buy_signals_5d'] = list(set(buy_sigs)) if buy_sigs else ['无']

        # 14. 卖出信号近5日
        sell_sigs = []
        try:
            ps2 = db.execute("SELECT signals_json FROM pattern_scan_signals WHERE stock_code=? AND date >= date(?, '-5 days')", (code, sd)).fetchall()
            for r in ps2:
                if r['signals_json']:
                    import json
                    sigs = json.loads(r['signals_json'])
                    for s in sigs:
                        if s.get('type') == 'bearish':
                            src = s.get('source',''); detail = s.get('details',{})
                            label = detail.get('rule_label', detail.get('signal_type', src))
                            sell_sigs.append(f"{label}({s.get('date','')})")
        except: pass
        p['sell_signals_5d'] = list(set(sell_sigs)) if sell_sigs else ['无']

        # 15a. 逐日信号明细（供prompt用）
        sbd = {}
        try:
            import json as _json2
            sd2 = candidate.get('signal_date', '')
            ps_rows = db.execute(
                "SELECT date, signals_json FROM pattern_scan_signals WHERE stock_code=? AND date >= date(?, '-10 days') ORDER BY date",
                (code, sd2)
            ).fetchall()
            for row in ps_rows:
                dt = row['date']
                if dt not in sbd:
                    sbd[dt] = []
                if row['signals_json']:
                    sigs = _json2.loads(row['signals_json'])
                    for s in sigs:
                        src = s.get('source', '?')
                        tp = s.get('type', '?')
                        detail = s.get('details', {})
                        label = detail.get('cdl_name') or detail.get('rule_label') or detail.get('signal_type') or src
                        direction = '↑' if tp == 'bullish' else ('↓' if tp == 'bearish' else '·')
                        sbd[dt].append(f"{direction}{label}")
        except:
            pass
        p['signals_by_date'] = sbd

        # 15. 胜率参考
        try:
            import yaml
            ypath = os.path.join(PROJECT_ROOT, 'config', 'strategy', 'high_conf_pocket_pivot.yaml')
            if os.path.exists(ypath):
                with open(ypath, encoding='utf-8') as f: yd = yaml.safe_load(f)
                perf = yd.get('performance',{})
                kelly = yd.get('kelly',{})
                p['backtest'] = {'source': '高置信度口袋支点(B1=PP日)', 'period': '2023-06~2026-06', 'samples': yd.get('strategy',{}).get('total_signals','?'),
                    'win5d': perf.get('5d',{}).get('win_rate'), 'med5d': perf.get('5d',{}).get('median_return'),
                    'win10d': perf.get('10d',{}).get('win_rate'), 'med10d': perf.get('10d',{}).get('median_return'),
                    'win20d': perf.get('20d',{}).get('win_rate'), 'med20d': perf.get('20d',{}).get('median_return'),
                    'kelly_p': kelly.get('win_rate'), 'kelly_b': round(kelly.get('avg_win_pct',0)/kelly.get('avg_loss_pct',1),2) if kelly.get('avg_loss_pct') else None}
        except: pass

        return p

    def _build_rich_prompt(self, stock_code, info):
        """构建高质量prompt（v2：含CAN SLIM + K线 + 逐日信号）"""
        p = info
        parts = []
        parts.append("## 股票概况")
        parts.append(f"- 代码: {p['code']} 名称: {p.get('name','')}")
        parts.append(f"- 行业: {p.get('industry','未知')} 市值: {p.get('market_cap_yi','?')}亿")
        parts.append(f"- 信号日期: {p.get('signal_date','')}")
        parts.append(f"- 发现路径: {p.get('discovery_path','')}")

        parts.append("\n## CAN SLIM 评分")
        cs = p.get('canslim', {})
        if cs:
            parts.append(f"- 总分: {cs.get('total')}/100 评级: {cs.get('grade')}")
            parts.append(f"- C(当季收益): {cs.get('c')}分 A(年度收益): {cs.get('a')}分 N(新事物): {cs.get('n')}分")
            parts.append(f"- S(供需): {cs.get('s')}分 L(领涨): {cs.get('l')}分 I(机构): {cs.get('i')}分")
            parts.append("- \u26a0\ufe0f I(机构认同)得分仅供参考——散户无法获取实时机构持仓数据，该项可能失真，请勿以此为主要判断依据")
        else:
            parts.append("- 暂无CAN SLIM评分数据")

        parts.append("\n## 大盘环境")
        mh = p.get('market_health', {})
        if mh:
            parts.append(f"- 健康分: {mh.get('score')}/100 评级: {mh.get('rating')}")
            parts.append(f"- MA50上方占比: {mh.get('ma50_above')}% 新高新低比: {mh.get('hl_ratio')} 涨跌比: {mh.get('ad_ratio')}")
            parts.append(f"- 放量突破数: {mh.get('vol_breakout')}")
        ms = p.get('market_sell', {})
        if ms: parts.append(f"- 卖出评分: {ms.get('score')} 信号: {ms.get('signal')}")
        parts.append(f"- 30日抛盘日: {p.get('dist_30d','?')} 追盘日: {p.get('ftd_30d','?')} 吸筹日: {p.get('acc_30d','?')}")
        parts.append(f"- 全市场成交额: 5日均{p.get('index_amount_avg5d','?')}亿 10日{p.get('index_amount_avg10d','?')}亿 趋势:{p.get('index_amount_trend','?')}")
        parts.append(f"- 中证全指: {p.get('index_close','?')} MA50:{p.get('index_ma50','?')}({p.get('index_vs_ma50','?')}%) MA200:{p.get('index_ma250','?')}({p.get('index_vs_ma250','?')}%)")

        parts.append("\n## 个股技术面")
        rs = p.get('rs', {})
        if rs: parts.append(f"- RS强度: RPS20={rs.get('rps20')} RPS60={rs.get('rps60')} RPS120={rs.get('rps120')} RPS250={rs.get('rps250')}")
        if p.get("ind_rs250"): parts.append(f"- 行业RS250: {p['ind_rs250']} ({p.get('ind_name','')})")
        parts.append(f"- 最新价: {p.get('latest_close','?')}")
        parts.append("- 均线: " + ", ".join([f"MA{n}={p.get(f'ma{n}','?')}({p.get(f'vs_ma{n}','?')}%)" for n in [5,10,20,30,60,120,250] if p.get(f'ma{n}')]))
        if p.get('extension_risk'): parts.append(f"- 延伸风险: {p['extension_risk']}")
        parts.append(f"- 成交量: 5日均{p.get('vol5d','?')} 10日{p.get('vol10d','?')} 20日{p.get('vol20d','?')}")
        hlc = p.get("hlc", {})
        if hlc: parts.append(f"- H/L结构: H={hlc.get('h_date')} {hlc.get('h_price')} L={hlc.get('l_date')} {hlc.get('l_price')} 回撤{hlc.get('decline_pct')}%")

        hlc = p.get('hlc', {})
        if hlc: parts.append(f"- H/L结构: H={hlc.get('h_date')} \u00a5{hlc.get('h_price')} L={hlc.get('l_date')} \u00a5{hlc.get('l_price')} 回撤{hlc.get('decline_pct')}%")

        parts.append("\n## 近10日K线（已核实，请勿编造价格数据）")
        kl = p.get('klines_20d', [])
        if kl:
            recent = kl[-10:] if len(kl) >= 10 else kl
            parts.append("| 日期 | 开盘 | 收盘 | 涨跌幅 | 成交量(手) |")
            parts.append("|------|------|------|--------|-----------|")
            prev_c = None
            for k in recent:
                day_chg = round((k['c'] - k['o']) / k['o'] * 100, 1) if k['o'] else 0
                dof_chg = round((k['c'] - prev_c) / prev_c * 100, 1) if prev_c else 0
                if prev_c:
                    label = f"{day_chg:+.1f}% (隔日{dof_chg:+.1f}%)"
                else:
                    label = f"{day_chg:+.1f}%"
                parts.append(f"| {k['date']} | {k['o']:.2f} | {k['c']:.2f} | {label} | {k['v']} |")
                prev_c = k['c']
        else:
            parts.append("- 暂无K线数据")

        parts.append("\n## 信号扫描")
        parts.append(f"- 买入信号(近5日): {', '.join(p.get('buy_signals_5d',['无']))}")
        parts.append(f"- 卖出信号(近5日): {', '.join(p.get('sell_signals_5d',['无']))}")
        sbd = p.get('signals_by_date', {})
        if sbd:
            parts.append("- 逐日明细:")
            for dt in sorted(sbd.keys()):
                sigs = sbd[dt]
                parts.append(f"  {dt}: {' | '.join(sigs) if sigs else '无信号'}")

        bt = p.get('backtest', {})
        if bt:
            parts.append("\n## 回测参考 (" + bt.get('source') + ")")
            parts.append(f"- 样本: {bt.get('samples')}个 周期: {bt.get('period')}")
            parts.append(f"- 5日胜率: {bt.get('win5d')}% 中位: {bt.get('med5d')}%")
            parts.append(f"- 10日胜率: {bt.get('win10d')}% 中位: {bt.get('med10d')}%")
            if bt.get('kelly_p'):
                kelly_f = bt['kelly_p'] * bt.get('kelly_b', 1) - (1-bt['kelly_p'])
                kelly_f = kelly_f / bt.get('kelly_b', 1) if bt.get('kelly_b') else 0
                parts.append(f"- 凯利仓位参考(1/4): {round(max(0,kelly_f)*25,1)}% (p={bt['kelly_p']} b={bt.get('kelly_b')})")

        parts.append("\n---")
        parts.append("## 分析要求（务必遵守）")
        parts.append("1. 请按收到的数据，真实、客观地对是否可以买入股票进行评估，既不夸大，也不要过于谨慎小心。没有人要求你百发百中，但要求你专业认真。")
        parts.append("2. 直接给买入结论（推荐买入/谨慎买入/观望/不建议），讲清楚核心理由，不要绕弯子。")
        parts.append("3. 如需引用价格、涨跌幅、成交量等数据，只能使用上面「近10日K线」表格中提供的实际数据，严禁凭空编造数字。")
        parts.append("4. 仓位建议：参考凯利上限，实际建议单笔亏损不超过账户2%。")
        parts.append("5. 持有建议：止损位基于H/L结构或8%固定止损，止盈位看H点前高。")
        parts.append("6. I(机构认同)维度因散户数据限制存在失真可能，分析时对此维度保持审慎，勿将其作为核心判断依据。")
        parts.append("7. 用Markdown格式输出，重点加粗。")

        return '\n'.join(parts)
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
  *,*::before,*::after{{box-sizing:border-box}}
  body{{font-family:'Inter',-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;max-width:780px;margin:0 auto;padding:32px 24px 60px;background:#111118;color:#d4d4d8;line-height:1.9;font-size:15px}}
  h1{{font-family:'Instrument Serif',Georgia,serif;font-size:1.4rem;font-weight:400;color:#f59e0b;border-bottom:1px solid #2a2a35;padding-bottom:10px;margin-bottom:6px;letter-spacing:.02em}}
  h2{{font-family:'Instrument Serif',Georgia,serif;font-size:1.1rem;font-weight:400;color:#e5e5eb;margin-top:28px;padding-bottom:4px;border-bottom:1px solid #1e1e28}}
  h3{{font-size:.9rem;color:#a78bfa;margin-top:20px;font-weight:600}}
  p{{margin:.7em 0;text-indent:0}}
  strong{{color:#fbbf24;font-weight:600}}
  em{{color:#94a3b8;font-style:italic}}
  ul,ol{{padding-left:22px;margin:.6em 0}}
  li{{margin:6px 0;line-height:1.7}}
  li::marker{{color:#f59e0b}}
  blockquote{{border-left:3px solid #f59e0b;padding:10px 18px;margin:16px 0;background:rgba(245,158,11,.06);border-radius:0 8px 8px 0;color:#a1a1aa;font-style:italic}}
  code{{background:rgba(245,158,11,.12);padding:2px 8px;border-radius:5px;font-family:'JetBrains Mono',monospace;font-size:.85em;color:#fbbf24}}
  hr{{border:none;border-top:1px solid #2a2a35;margin:24px 0}}
  .meta{{font-size:.65rem;color:#555;text-align:center;margin-bottom:28px;letter-spacing:.04em}}
  .verdict{{display:inline-block;padding:5px 16px;border-radius:8px;font-weight:700;margin:8px 4px;font-size:.9rem}}
  .verdict.buy{{background:rgba(16,185,129,.12);color:#34d399;border:1px solid rgba(16,185,129,.2)}}
  .verdict.caution{{background:rgba(245,158,11,.12);color:#fbbf24;border:1px solid rgba(245,158,11,.2)}}
  .verdict.wait{{background:rgba(139,139,144,.08);color:#a1a1aa;border:1px solid rgba(139,139,144,.15)}}
  .verdict.avoid{{background:rgba(239,68,68,.1);color:#f87171;border:1px solid rgba(239,68,68,.2)}}
  a{{color:#f59e0b;text-decoration:none;border-bottom:1px dotted rgba(245,158,11,.3)}}
  a:hover{{border-bottom-style:solid}}
  .footer{{margin-top:40px;padding-top:14px;border-top:1px solid #2a2a35;font-size:.6rem;color:#444;text-align:center}}
</style>
</head>
<body>
<h1>欧奈尔深度分析</h1>
<div class="meta">{name}({stock_code}) · 信号日 {info.get('signal_date','')} · 分析生成 {date_str}</div>
{html_body}
<div class="footer">基于《像欧奈尔信徒一样交易》框架 · DeepSeek 生成 · 仅供参考，不构成投资建议</div>
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
