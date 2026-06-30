"""
Prompt 生成器 API — 收集股票全维度数据并组装分析 prompt
"""
import sys, os, json, argparse, sqlite3, yaml
from datetime import datetime, date as dt_date, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, "src"))
os.chdir(PROJECT_DIR)

from scripts.common import log as logger

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_stock_data(code, target_date):
    """收集股票全维度数据，返回 dict"""
    db = get_db()
    data = {'code': code, 'date': target_date}

    # 1. 股票基本信息
    row = db.execute("SELECT name, listing_status, market FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
    if not row:
        db.close()
        return None
    data['name'] = row['name']
    data['listing_status'] = row['listing_status']

    # 2. 最新价格和均线
    k = db.execute("""
        SELECT close, volume, amount FROM daily_kline 
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1
    """, (code, target_date)).fetchone()
    if k:
        data['close'] = k['close']
        data['volume'] = k['volume']
        data['amount'] = k['amount']

    # 3. 均线
    rows = db.execute("""
        SELECT date, close FROM daily_kline
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 250
    """, (code, target_date)).fetchall()
    closes = [r['close'] for r in rows if r['close']]
    if closes:
        data['ma5'] = sum(closes[:5]) / min(5, len(closes))
        data['ma10'] = sum(closes[:10]) / min(10, len(closes))
        data['ma20'] = sum(closes[:20]) / min(20, len(closes))
        data['ma30'] = sum(closes[:30]) / min(30, len(closes))
        data['ma60'] = sum(closes[:60]) / min(60, len(closes))
        data['ma120'] = sum(closes[:120]) / min(120, len(closes))
        data['ma250'] = sum(closes[:250]) / min(250, len(closes))

    # 4. RS 强度
    rs = db.execute("""
        SELECT rps_20, rps_60, rps_120, rps_250 FROM stock_rs_daily
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1
    """, (code, target_date)).fetchone()
    if rs:
        data['rps_20'] = rs['rps_20']
        data['rps_60'] = rs['rps_60']
        data['rps_120'] = rs['rps_120']
        data['rps_250'] = rs['rps_250']

    # 5. 行业 RS250
    ind_rs = db.execute("""
        SELECT i.industry_name, r.rs_250
        FROM stock_industry i
        JOIN index_rs_daily r ON r.stock_code = i.industry_code AND r.date = ?
        WHERE i.stock_code = ?
        LIMIT 1
    """, (target_date, code)).fetchone()
    if ind_rs:
        data['industry'] = ind_rs['industry_name']
        data['industry_rs'] = ind_rs['rs_250']

    # 6. CANSLIM
    cs = db.execute("""
        SELECT total_score, rating, c_score, a_score, n_score, s_score, l_score, i_score
        FROM cansim_scores WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1
    """, (code, target_date)).fetchone()
    if cs:
        data['canslim'] = {
            'total': cs['total_score'], 'rating': cs['rating'],
            'c': cs['c_score'], 'a': cs['a_score'], 'n': cs['n_score'],
            's': cs['s_score'], 'l': cs['l_score'], 'i': cs['i_score'],
        }

    # 7. 近10日K线
    klines = db.execute("""
        SELECT date, open, close, high, low, volume 
        FROM daily_kline WHERE stock_code=? AND date<=?
        ORDER BY date DESC LIMIT 15
    """, (code, target_date)).fetchall()
    data['klines'] = [{
        'date': r['date'], 'open': r['open'], 'close': r['close'],
        'high': r['high'], 'low': r['low'], 'volume': r['volume'],
    } for r in reversed(klines)][-10:]

    # 8. 信号扫描
    signals = db.execute("""
        SELECT date, signal_type, signal_name, direction
        FROM pattern_scan_signals WHERE stock_code=? AND date>=date(?, '-15 days') AND date<=?
        ORDER BY date
    """, (code, target_date, target_date)).fetchall()
    data['signals'] = [dict(r) for r in signals]

    db.close()
    return data


def get_market_data(target_date):
    """获取大盘环境数据"""
    db = get_db()
    mh = db.execute(
        "SELECT * FROM market_health_daily WHERE date<=? ORDER BY date DESC LIMIT 1", (target_date,)
    ).fetchone()
    ms = db.execute(
        "SELECT * FROM market_sell_score_daily WHERE date<=? ORDER BY date DESC LIMIT 1", (target_date,)
    ).fetchone()
    midx = db.execute("""
        SELECT stock_code, close FROM index_daily_kline
        WHERE date=? AND stock_code IN ('000985','000001','399001','399006','000688','000300')
    """, (target_date,)).fetchall()
    db.close()
    return {'health': dict(mh) if mh else None, 'sell': dict(ms) if ms else None,
            'indices': {r['stock_code']: r['close'] for r in midx}}


def generate_prompt(data, market, sector_context=None, market_groups=None):
    """组装 oneil 框架分析 prompt"""
    code = data['code']
    name = data.get('name', '')
    lines = []

    # ═══ 系统指令：oneil 技能框架 ═══
    lines.append("你是一个欧奈尔交易顾问，严格遵循《像欧奈尔信徒一样交易》框架。")
    lines.append("")
    lines.append("## 回答工作流（Agentic Protocol）")
    lines.append("")
    lines.append("### Step 1: 问题分类")
    lines.append("判断当前问题是需要数据分析还是纯框架讨论：")
    lines.append("- 需要数据支持（股票分析/买入建议）→ 先研究再回答")
    lines.append("- 纯框架问题（规则解释）→ 直接用心智模型回答")
    lines.append("")
    lines.append("### Step 2: 欧奈尔式研究")
    lines.append("按照以下维度系统扫描：")
    lines.append("1. 大盘环境：指数位置、追盘日/抛盘日、市场宽度")
    lines.append("2. 行业分组：该股所属行业组（强/中/弱）的健康分")
    lines.append("3. 个股技术：基部结构、均线位置、成交量形态、RS强度")
    lines.append("4. 个股基本面：CAN SLIM 评分")
    lines.append("")
    lines.append("### Step 3: 欧奈尔式回答")
    lines.append("心法：先调研保证数据准确，再运用心智模型分析，最后以欧奈尔信徒的方式输出。")
    lines.append("")
    lines.append("## 核心心智模型")
    lines.append("1. **尾部风险管理**：7-8%止损铁律，小亏损是投资过程的有机组成部分")
    lines.append("2. **系统化环境感知**：不在熊市中做多，追盘日确认反弹才入场")
    lines.append("3. **精确打击**：等口袋支点或标准突破出现再出手，不追高")
    lines.append("4. **动量优先**：只交易RS强度前20%的股票，不买低价股")
    lines.append("5. **凸性下注**：头寸集中，盈利加仓，绝不向下摊平")
    lines.append("6. **动态进化**：市场在变，方法跟着变")
    lines.append("")
    lines.append("## 关键约束（不可违反）")
    lines.append("1. -7%~-8% 止损铁律。任何一笔交易都不例外")
    lines.append("2. 永不向下摊平。亏损头寸不加仓，只加盈利的")
    lines.append("3. 不在熊市做多。等待追盘日确认反弹再入场")
    lines.append("4. 不买低价股。只交易创出历史新高的优质龙头")
    lines.append("5. 让利润奔跑。不要因为\"涨太多了\"就卖")
    lines.append("6. 头寸集中。真正的好机会不需要分散到几十只股票上")
    lines.append("")

    # ═══ 股票数据 ═══
    lines.append(f"## 股票概况")
    lines.append(f"- 代码: {code} 名称: {name}")
    lines.append(f"- 行业: {data.get('industry', 'N/A')} 市值: {data.get('amount', 0):.0f}亿")
    lines.append(f"- 信号日期: {data['date']}")
    lines.append(f"- 最新价: {data.get('close', 'N/A')}")
    lines.append("")

    # 均线
    lines.append("## 均线位置")
    for ma in ['ma5','ma10','ma20','ma30','ma60','ma120','ma250']:
        if ma in data:
            pct = (data['close'] - data[ma]) / data[ma] * 100 if data.get('close') else 0
            lines.append(f"- {ma.upper()}={data[ma]:.2f} ({pct:+.1f}%)")
    lines.append("")

    # RS
    lines.append("## RS 强度")
    lines.append(f"- RPS20={data.get('rps_20', 'N/A')} RPS60={data.get('rps_60', 'N/A')} RPS120={data.get('rps_120', 'N/A')} RPS250={data.get('rps_250', 'N/A')}")
    lines.append(f"- 行业RS250: {data.get('industry_rs', 'N/A')} ({data.get('industry', 'N/A')})")
    lines.append("")

    # CANSLIM
    if data.get('canslim'):
        cs = data['canslim']
        lines.append("## CAN SLIM 评分")
        lines.append(f"- 总分: {cs['total']}/100 评级: {cs['rating']}")
        lines.append(f"- C(当季收益): {cs['c']} A(年度收益): {cs['a']} L(领涨): {cs['l']}")
        lines.append("")

    # ═══ 行业分组上下文（v2.0 新增）═══
    if sector_context and sector_context.get('primary_index'):
        pi = sector_context['primary_index']
        sg = sector_context.get('sector_group')
        lines.append("## 行业分组上下文")
        lines.append(f"- 所属最强指数: {pi.get('name','?')} ({pi['code']}, RS_60={pi['rs_60']})")
        if sg:
            lines.append(f"- 所属行业组: {sg['group_label']} (健康分{sg['health_score']}/{sg['rating']}, 仓位{sg['position']}%)")
        lines.append("")
    
    if market_groups:
        lines.append("## 全市场行业分组速览")
        for g in market_groups:
            if g.get('total_score') is not None:
                vs = f" ↑{g['score_vs_market']} vs 大盘" if g.get('score_vs_market') else ''
                lines.append(f"- {g['group_label']}: {g['total_score']}/{g['rating']} 仓位{g['position']}%{vs}")
        lines.append("")

    # K线
    lines.append("## 近10日K线")
    lines.append("| 日期 | 开盘 | 收盘 | 涨跌幅 | 成交量(手) |")
    lines.append("|------|------|------|--------|-----------|")
    klines = data.get('klines', [])
    for i, k in enumerate(klines):
        chg = ''
        if i > 0 and klines[i-1]['close']:
            chg = (k['close'] - klines[i-1]['close']) / klines[i-1]['close'] * 100
            chg = f"{chg:+.1f}%"
        lines.append(f"| {k['date']} | {k['open']:.2f} | {k['close']:.2f} | {chg} | {k['volume']/10000:.0f} |")
    lines.append("")

    # 信号
    signals = data.get('signals', [])
    if signals:
        lines.append("## 信号扫描")
        buy_sigs = [s for s in signals if s.get('direction') == 'up' or s.get('signal_type') in ('buy','pp','bo','b1','b2')]
        sell_sigs = [s for s in signals if s.get('direction') == 'down' or s.get('signal_type') in ('sell','top','rule')]
        if buy_sigs:
            lines.append(f"- 买入信号: {', '.join(f'{s[\"signal_name\"]}({s[\"date\"]})' for s in buy_sigs[-5:])}")
        if sell_sigs:
            lines.append(f"- 卖出信号: {', '.join(f'{s[\"signal_name\"]}({s[\"date\"]})' for s in sell_sigs[-5:])}")
        lines.append("")

    # 大盘环境
    lines.append("## 大盘环境")
    if market.get('health'):
        h = market['health']
        lines.append(f"- 健康分: {h['total_score']} 评级: {h['rating']}")
    if market.get('sell'):
        s = market['sell']
        lines.append(f"- 卖出评分: {s['total_score']}")
    lines.append("")

    # 分析要求
    lines.append("## 分析要求")
    lines.append("1. 请按 oneil 框架（欧奈尔信徒交易框架）真实、客观地评估是否可以买入股票。")
    lines.append("2. 直接给买入结论（推荐买入/谨慎买入/观望/不建议），讲清楚核心理由。")
    lines.append("3. 如需引用数据，只能使用以上提供的实际数据，严禁凭空编造。")
    lines.append("4. 仓位建议参考凯利上限，实际建议单笔亏损不超过账户2%。")
    lines.append("5. 持有建议：止损位基于H/L结构或8%固定止损，止盈位看H点前高。")
    lines.append("6. I(机构认同)维度因散户数据限制存在失真可能，分析时保持审慎。")
    lines.append("")

    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', required=True)
    parser.add_argument('--date', default=None)
    args = parser.parse_args()
    target = args.date or dt_date.today().strftime('%Y-%m-%d')
    data = get_stock_data(args.code, target)
    if not data:
        print(json.dumps({'error': '股票代码不存在或无数据', 'code': args.code}))
        sys.exit(1)
    market = get_market_data(target)
    prompt = generate_prompt(data, market)
    print(json.dumps({
        'code': args.code, 'name': data.get('name', ''),
        'date': target, 'prompt': prompt, 'length': len(prompt),
    }, ensure_ascii=False))
