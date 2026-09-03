"""
src/analysis/financial.py — 财务分析引擎

四大模块:
  1. DCF估值: 自由现金流折现 → 目标价
  2. 可比公司分析 (Comps): 同行业倍数比较 → 估值区间
  3. 盈利趋势分析 (Earnings): 季度盈利变化 → 趋势判断
  4. 三表联动预测 (3-Statement): IS→BS→CF 推导

数据源: stock_financials_annual, stock_financials_quarterly,
        fundamental_indicator, stock_sw_industry
"""

import sqlite3
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "lixinger.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════
# 1. DCF 估值模型
# ═══════════════════════════════════════════

def dcf_valuation(stock_code, assumptions=None):
    """
    DCF估值。基于最近年报真实数据 + 用户假设参数。

    所有财务数据取自 stock_financials_annual + stock_financials_annual_ext，
    不使用任何兜底估算值。数据缺失时在返回的 warnings 中列出。
    """
    db = get_db()

    if assumptions is None:
        assumptions = {}

    annual = db.execute('''SELECT * FROM stock_financials_annual
        WHERE stock_code = ? ORDER BY report_date DESC LIMIT 1''',
        (stock_code,)).fetchone()
    if not annual or not annual['revenue']:
        db.close(); return {'error': f'{stock_code} 无年报数据', 'base_basis': 'annual'}

    t_g = assumptions.get('terminal_growth', 0.025)
    exit_multiple = assumptions.get('exit_multiple', None)
    tax = assumptions.get('tax_rate', 0.25)
    rev_yoy = annual['revenue_yoy'] if annual['revenue_yoy'] is not None else 12
    base_g = max(rev_yoy * 0.7, 5)
    g = assumptions.get('growth_rates', [base_g, max(base_g-2,5), max(base_g-4,5), max(base_g-5,4), max(base_g-6,3)])
    g = [x/100.0 for x in g]
    wacc = assumptions.get('wacc', 0.10)

    ext = db.execute('''SELECT * FROM stock_financials_annual_ext
        WHERE stock_code = ? AND report_date = ?''',
        (stock_code, annual['report_date'])).fetchone()

    warnings = []

    # ── 营收（v1.3：TTM 检测——最新4季营收/盈利偏离年报 >15% 时起点切换滚动TTM）──
    revenue = annual['revenue']
    ttm_flag = False
    ttm_np_note = ''
    q4 = db.execute('''SELECT report_date, revenue_single, net_profit_single, gross_margin_single
        FROM stock_financials_quarterly WHERE stock_code=?
        ORDER BY report_date DESC LIMIT 4''', (stock_code,)).fetchall()
    if len(q4) == 4:
        ttm_rev = sum((r['revenue_single'] or 0) for r in q4)
        ttm_np = sum((r['net_profit_single'] or 0) for r in q4)
        ann_rev = annual['revenue'] or 0
        ann_np = annual['net_profit'] or 0
        # abs 分母：亏损年报（ann_np<0）下扭亏为盈同样可触发；ttm_rev>0 防全 NULL 求和为 0 的荒谬起点
        rev_drift = abs(ttm_rev - ann_rev) / abs(ann_rev) if ann_rev else None
        np_drift = abs(ttm_np - ann_np) / abs(ann_np) if ann_np else None
        if ttm_rev > 0 and ttm_np >= 0 and ((rev_drift is not None and rev_drift > 0.15) or (np_drift is not None and np_drift > 0.15)):
            revenue = ttm_rev
            ttm_flag = True
            ttm_np_note = f'（TTM营收 {ttm_rev/1e8:.0f}亿/净利 {ttm_np/1e8:.1f}亿）'

    # ── EBITDA（v1.3：TTM 起点下按最新季毛利率比例校准，反映盈利爆发）──
    if ext and ext['ebitda']:
        ebitda_margin = ext['ebitda'] / annual['revenue']
        if ttm_flag:
            gm_q = q4[0]['gross_margin_single']
            gm_ann = annual['gross_margin']
            if gm_q and gm_ann and gm_ann > 0:
                ebitda_margin = ebitda_margin * (gm_q / gm_ann)
                warnings.append(
                    f'⚠️ 盈利偏离年报 >15%，DCF起点已切换滚动TTM {ttm_np_note}；'
                    f'EBITDA率按最新季毛利率比例校准为 {ebitda_margin*100:.1f}%（原年报口径 {ext["ebitda"]/annual["revenue"]*100:.1f}%）；D&A/CapEx比率按年报绝对值/TTM营收略偏低；'
                    '周期/剧变公司前瞻需结合量价情景，勿纯外推；比例校准未计经营杠杆，方向偏保守')
        ebitda_val = ext['ebitda']
    else:
        warnings.append('EBITDA: stock_financials_annual_ext 中无数据')
        ebitda_margin = None; ebitda_val = None

    # ── 折旧与摊销 ──
    if ext and (ext['depreciation_fa'] or ext['depreciation_ip']):
        da_pct = (ext['depreciation_fa'] + ext['depreciation_ip']) / revenue
    else:
        warnings.append('D&A(折旧摊销): 缺失')
        da_pct = None

    # ── CapEx ──
    ocf = annual['operating_cash_flow']
    fcf_annual = annual['free_cash_flow']
    if ocf and fcf_annual:
        capex_pct = (ocf - fcf_annual) / revenue
    else:
        warnings.append('CapEx: 经营CF或自由CF缺失')
        capex_pct = None

    # ── 利息支出 ──
    if ext and ext['interest_expense']:
        interest = ext['interest_expense']
    else:
        warnings.append('利息支出: 缺失')
        interest = None

    # ── 股本 ──
    eq_row = db.execute('''SELECT capitalization FROM stock_equity_change
        WHERE stock_code = ? ORDER BY change_date DESC LIMIT 1''',
        (stock_code,)).fetchone()
    if eq_row and eq_row['capitalization']:
        shares = eq_row['capitalization']
    else:
        warnings.append('总股本: stock_equity_change 中无数据')
        shares = None

    # ── 当前股价 ──
    kline_row = db.execute('''SELECT close FROM daily_kline
        WHERE stock_code = ? ORDER BY date DESC LIMIT 1''',
        (stock_code,)).fetchone()
    current_price = kline_row['close'] if kline_row else None
    if not current_price:
        warnings.append('当前股价: daily_kline 中无数据')

    # ── 净债务 ──
    if ext and ext['total_assets'] and annual['interest_bearing_debt_ratio']:
        net_debt = ext['total_assets'] * (annual['interest_bearing_debt_ratio'] / 100)
    else:
        warnings.append('净债务: 总资产或有息负债率缺失')
        net_debt = None

    # ── 名称 ──
    info = db.execute('SELECT name FROM stock_basic WHERE stock_code=?',
                      (stock_code,)).fetchone()
    name = info['name'] if info else stock_code
    db.close()

    # 检查是否所有必要数据齐全
    if ebitda_margin is None or da_pct is None or capex_pct is None or shares is None or current_price is None:
        return {
            'stock_code': stock_code, 'name': name, 'method': 'DCF',
            'error': '必要财务数据缺失，无法完成DCF估值',
            'warnings': warnings,
            'current_price': current_price,
        }

    market_cap = shares * current_price

    # ── 确定基准年 ──
    base_year = int(annual['report_date'][:4]) if annual['report_date'] else 2025

    # ── FCF预测 ──
    rev = revenue
    pv_fcfs = []
    fcf_details = []
    for yr in range(len(g)):
        rev = rev * (1 + g[yr])
        ebitda = rev * ebitda_margin
        da = rev * da_pct
        ebit = ebitda - da
        nopat = ebit * (1 - tax)
        capex = rev * capex_pct
        prev_rev = rev / (1 + g[yr])
        dnwc = (rev - prev_rev) * 0.01  # NWC变动 = 增量营收 × 1%
        ufcf = nopat + da - capex - dnwc
        period = yr + 0.5
        pv = ufcf / ((1 + wacc) ** period)
        pv_fcfs.append(pv)
        fcf_details.append({'year': base_year + yr + 1, 'revenue': round(rev,1), 'ufcf': round(ufcf,1), 'pv': round(pv,1)})

    last_fcf = fcf_details[-1]['ufcf']
    last_ebitda = fcf_details[-1]['revenue'] * ebitda_margin

    # 终值计算
    if exit_multiple:
        tv = last_ebitda * exit_multiple
        tv_method = f'EV/EBITDA {exit_multiple}x'
    else:
        tv = last_fcf * (1 + t_g) / (wacc - t_g)
        tv_method = f'永续增长 {t_g*100:.1f}%'

    pv_tv = tv / ((1 + wacc) ** (len(g) + 0.5))
    enterprise_value = sum(pv_fcfs) + pv_tv
    equity_value = enterprise_value - (net_debt or 0)
    target_price = equity_value / shares if shares > 0 else 0

    sensitivity = []
    if exit_multiple:
        for m in [exit_multiple-4, exit_multiple-2, exit_multiple, exit_multiple+2, exit_multiple+4]:
            if m <= 0: continue
            ev = sum(d['ufcf'] / ((1 + wacc) ** (yr + 0.5)) for yr, d in enumerate(fcf_details))
            tv_s = last_ebitda * m
            ev += tv_s / ((1 + wacc) ** (len(g) + 0.5))
            tp = (ev - (net_debt or 0)) / shares if shares > 0 else 0
            sensitivity.append({'label': f'EV/EBITDA {m}x', 'target_price': round(tp,2)})
    else:
        for w in [0.08, 0.09, 0.10, 0.11, 0.12]:
            ev = sum(d['ufcf'] / ((1 + w) ** (yr + 0.5)) for yr, d in enumerate(fcf_details))
            tv_s = last_fcf * (1 + t_g) / (w - t_g)
            ev += tv_s / ((1 + w) ** (len(g) + 0.5))
            tp = (ev - (net_debt or 0)) / shares if shares > 0 else 0
            sensitivity.append({'label': f'WACC {w*100:.0f}%', 'target_price': round(tp,2)})

    return {
        'stock_code': stock_code, 'name': name, 'method': 'DCF',
        'current_price': round(current_price, 2),
        'base_revenue': round(revenue, 1),
        'ebitda_margin': f'{ebitda_margin*100:.1f}%',
        'da_pct': f'{da_pct*100:.1f}%',
        'capex_pct': f'{capex_pct*100:.1f}%',
        'interest_expense': round(interest, 1) if interest else None,
        'net_debt': round(net_debt, 1) if net_debt else None,
        'wacc': f'{wacc*100:.0f}%',
        'tax_rate': f'{tax*100:.0f}%',
        'terminal_growth': f'{t_g*100:.1f}%',
        'tv_method': tv_method,
        'projections': fcf_details,
        'terminal_value': round(pv_tv, 1),
        'enterprise_value': round(enterprise_value, 1),
        'equity_value': round(equity_value, 1),
        'target_price': round(target_price, 2),
        'upside_pct': round((target_price/current_price - 1) * 100, 1) if current_price > 0 else None,
        'sensitivity': sensitivity,
        'warnings': warnings if warnings else None,
        'base_basis': 'ttm_rolled' if ttm_flag else 'annual',
    }


# ═══════════════════════════════════════════
# 2. 可比公司分析 (Comps)
# ═══════════════════════════════════════════

def _load_l2_indices():
    """加载中证二级行业指数列表"""
    import yaml
    cfg_path = os.path.join(PROJECT_ROOT, 'config', 'index_style.yaml')
    with open(cfg_path, encoding='utf-8') as f:
        data = yaml.safe_load(f)
    return {item['code']: item['name'] for item in data.get('categories', {}).get('sector_l2', [])}


def _load_l1_indices():
    """加载中证一级行业指数列表"""
    import yaml
    cfg_path = os.path.join(PROJECT_ROOT, 'config', 'index_style.yaml')
    with open(cfg_path, encoding='utf-8') as f:
        data = yaml.safe_load(f)
    return {item['code']: item['name'] for item in data.get('categories', {}).get('sector_l1', [])}


def comps_analysis(stock_code, peer_codes=None):
    """
    可比公司估值。通过中证二级行业指数成分股找可比公司。
    """
    db = get_db()

    # 1. 三级查找：L2 → L1 → 申万一级
    l2_indices = _load_l2_indices()
    l1_indices = _load_l1_indices()
    industry_name = None
    industry_source = None

    # L2（最精确，45个指数，覆盖32%）
    for name, codes, idx_map in [('L2', list(l2_indices.keys()), l2_indices),
                                   ('L1', list(l1_indices.keys()), l1_indices)]:
        ph = ','.join(['?' for _ in codes])
        row = db.execute(f'''SELECT DISTINCT index_code FROM index_constituents
            WHERE stock_code = ? AND index_code IN ({ph})
            AND date >= date('now', '-3 months') LIMIT 1''',
            [stock_code] + codes).fetchone()
        if row:
            industry_name = idx_map.get(row['index_code'], row['index_code'])
            industry_source = '中证二级行业' if name == 'L2' else '中证一级行业'
            break

    # 申万兜底（全覆盖）
    if not industry_name:
        sw = db.execute('''SELECT industry_name FROM stock_sw_industry
            WHERE stock_code = ?''', (stock_code,)).fetchone()
        if sw:
            industry_name = sw['industry_name']
            industry_source = '申万一级行业'

    if not industry_name:
        db.close()
        return {'error': f'{stock_code} 无行业分类数据'}

    # 2. 获取可比公司：L2/L1用前20权重股，申万用同行业
    if industry_source != '申万一级行业':
        # L2/L1: 权重股
        idx_code = row['index_code']
        peers = db.execute('''SELECT DISTINCT ic.stock_code, sb.name,
            (SELECT weighting FROM index_constituent_weightings icw
             WHERE icw.index_code=ic.index_code AND icw.stock_code=ic.stock_code
             ORDER BY icw.date DESC LIMIT 1) as weight
            FROM index_constituents ic
            LEFT JOIN stock_basic sb ON ic.stock_code=sb.stock_code
            WHERE ic.index_code = ? AND ic.date >= date('now', '-3 months')
            AND ic.stock_code != ?
            ORDER BY weight DESC NULLS LAST LIMIT 20''',
            (idx_code, stock_code)).fetchall()
        peer_codes = [p['stock_code'] for p in peers if p['stock_code']]
    else:
        # 申万: 同行业，按总市值降序取TOP20
        peers = db.execute('''SELECT DISTINCT sw.stock_code FROM stock_sw_industry sw
            LEFT JOIN stock_equity_change eq ON sw.stock_code=eq.stock_code
            LEFT JOIN daily_kline k ON sw.stock_code=k.stock_code
                AND k.date = (SELECT MAX(date) FROM daily_kline WHERE stock_code=sw.stock_code)
            WHERE sw.industry_name = ? AND sw.stock_code != ?
            ORDER BY eq.capitalization * k.close DESC LIMIT 20''',
            (industry_name, stock_code)).fetchall()
        peer_codes = [p['stock_code'] for p in peers]

    if not peer_codes:
        db.close()
        return {'error': f'{industry_name} 无可比公司'}

    # 3. 收集财务数据
    all_codes = [stock_code] + peer_codes
    ph2 = ','.join(['?' for _ in all_codes])

    # 最近年报数据
    annuals = db.execute(f'''SELECT stock_code, revenue, revenue_yoy, gross_margin,
        roe, net_profit, asset_liability_ratio
        FROM stock_financials_annual WHERE stock_code IN ({ph2})
        ORDER BY report_date DESC''',
        all_codes).fetchall()

    # 去重（每个股票只取最新）
    seen_codes = set()
    ann_data = {}
    for a in annuals:
        if a['stock_code'] not in seen_codes:
            seen_codes.add(a['stock_code'])
            ann_data[a['stock_code']] = dict(a)

    # 估值倍数（v1.3：优先理杏仁 TTM 每日字段——pe_ttm/pb/ps_ttm 及时反映盈利变化；
    # 卫星案例：理杏仁 TTM PE 10.53 vs 市值÷年报净利静态 17.4，差 65%）
    mult_data = {}
    for code in all_codes:
        # 理杏仁 TTM 估值（fundamental_indicator 每日更新）
        lx = db.execute('''SELECT date, metric_code, value FROM fundamental_indicator
            WHERE stock_code=? AND metric_code IN ('pe_ttm','pb','ps_ttm')
            AND value IS NOT NULL ORDER BY date DESC''', (code,)).fetchall()
        lx_map = {}
        for r in lx:
            if r['metric_code'] not in lx_map:
                lx_map[r['metric_code']] = r['value']
        # 股本（股本核验用）
        eq_row = db.execute('''SELECT capitalization FROM stock_equity_change
            WHERE stock_code = ? ORDER BY change_date DESC LIMIT 1''', (code,)).fetchone()
        shares = eq_row['capitalization'] if eq_row else None
        # 年报数据
        a = ann_data.get(code, {})
        # 扩展数据（净资产）
        ext2 = db.execute('''SELECT total_equity FROM stock_financials_annual_ext
            WHERE stock_code = ? ORDER BY report_date DESC LIMIT 1''', (code,)).fetchone()
        total_equity = ext2['total_equity'] if ext2 else None
        mult_data[code] = {}
        # TTM 口径主值（理杏仁）；缺失回退自算年报静态
        if lx_map.get('pe_ttm') is not None:
            mult_data[code]['pe_ttm'] = round(lx_map['pe_ttm'], 1)
        if lx_map.get('pb') is not None and 0 < lx_map['pb'] < 30:  # O4：资不抵债负PB/失真值不入池
            mult_data[code]['pb'] = round(lx_map['pb'], 1)
        if lx_map.get('ps_ttm') is not None and 0 < lx_map['ps_ttm'] < 60:
            mult_data[code]['ps_ttm'] = round(lx_map['ps_ttm'], 1)
        if total_equity:
            mult_data[code]['total_equity'] = total_equity
        # 市值兜底（理杏仁字段缺失时自算年报静态）
        if any(k not in mult_data[code] for k in ('pe_ttm', 'pb', 'ps_ttm')) and shares:
            k_row = db.execute('''SELECT close FROM daily_kline
                WHERE stock_code = ? ORDER BY date DESC LIMIT 1''', (code,)).fetchone()
            price = k_row['close'] if k_row else None
            if price:
                mkt_cap = price * shares
                np = a.get('net_profit')
                rev = a.get('revenue')
                if np and np > 0 and 'pe_ttm' not in mult_data[code]:
                    mult_data[code]['pe_ttm'] = round(mkt_cap / np, 1)
                if total_equity and total_equity > 0 and 'pb' not in mult_data[code]:
                    mult_data[code]['pb'] = round(mkt_cap / total_equity, 1)
                if rev and rev > 0 and 'ps_ttm' not in mult_data[code]:
                    mult_data[code]['ps_ttm'] = round(mkt_cap / rev, 1)

    # 名称
    names = db.execute(f'''SELECT stock_code, name FROM stock_basic
        WHERE stock_code IN ({ph2})''', all_codes).fetchall()
    name_map = {n['stock_code']: n['name'] for n in names}
    # v1.3.1：peers 全行 TTM 口径——批量取各 code 季度营收/毛利率（近4季 vs 再前4季）
    qall = db.execute(f'''SELECT stock_code, report_date, revenue_single, gross_margin_single
        FROM stock_financials_quarterly WHERE stock_code IN ({ph2})
        ORDER BY stock_code, report_date DESC''', all_codes).fetchall()
    ttm_map = {}
    for r in qall:
        ttm_map.setdefault(r['stock_code'], []).append(r)
    # B1：目标公司 TTM 分子（近4季滚动；db 关闭前查）
    tgt_tq = db.execute('''SELECT revenue_single, net_profit_single FROM stock_financials_quarterly
        WHERE stock_code=? ORDER BY report_date DESC LIMIT 4''', (stock_code,)).fetchall()
    db.close()

    # 构建可比表格
    def get_metric(code, mkey, fmt='.1f'):
        v = mult_data.get(code, {}).get(mkey)
        return round(v, 1) if v else None

    def ttm_stats(code):
        """近4季 vs 再前4季：营收/TTM同比/毛利率（无季报回退 None）"""
        rows = ttm_map.get(code, [])
        r4 = rows[:4] if len(rows) >= 4 else []
        r8 = rows[4:8] if len(rows) >= 8 else []
        rev4 = sum((x['revenue_single'] or 0) for x in r4)
        rev8 = sum((x['revenue_single'] or 0) for x in r8)
        if not r4 or rev4 <= 0:
            return None, None, None
        yoy = round((rev4 / rev8 - 1) * 100, 1) if r8 and rev8 > 0 else None
        # TTM 毛利率 ≈ 近4季毛利和/营收和（按单季毛利率×营收加权）
        wsum = sum(((x['gross_margin_single'] or 0) * (x['revenue_single'] or 0)) for x in r4)
        gm_ttm = round(wsum / rev4, 1) if wsum and wsum > 0 else None
        return round(rev4, 1), yoy, gm_ttm

    peers_table = []
    pe_vals, pb_vals, ps_vals, rev_growth_vals, roe_vals = [], [], [], [], []

    for code in all_codes:
        a = ann_data.get(code, {})
        pe = get_metric(code, 'pe_ttm')
        pb = get_metric(code, 'pb')
        ps = get_metric(code, 'ps_ttm')
        rg = a.get('revenue_yoy')
        roe_val = a.get('roe')
        rev_ttm, yoy_ttm, gm_ttm = ttm_stats(code)

        row = {
            'code': code, 'name': name_map.get(code, code),
            # v1.3.1：营收/增长/毛利率 TTM 口径（同 PE 列一致），无季报回退年报
            'revenue': rev_ttm if rev_ttm else round(a.get('revenue', 0) or 0, 1),
            'revenue_growth': yoy_ttm if yoy_ttm is not None else (round(rg, 1) if rg else None),
            'growth_basis': 'ttm' if yoy_ttm is not None else ('annual' if rg else None),
            'gross_margin': gm_ttm if gm_ttm is not None else round(a.get('gross_margin', 0) or 0, 1),
            'roe': round(roe_val, 1) if roe_val else None,
            'pe': pe, 'pb': pb, 'ps': ps,
        }
        peers_table.append(row)

        if code != stock_code:
            # v1.2：异常倍数过滤（PE>200 或 <0 多为微利/扭亏失真，污染中位数）
            if pe and 0 < pe < 200: pe_vals.append(pe)
            if pb: pb_vals.append(pb)
            if ps: ps_vals.append(ps)
            if rg: rev_growth_vals.append(rg)
            if roe_val: roe_vals.append(roe_val)

    # 中位数统计
    def median(vals):
        if not vals: return None
        sv = sorted(vals)
        n = len(sv)
        return sv[n // 2] if n % 2 else (sv[n // 2 - 1] + sv[n // 2]) / 2

    med_pe = median(pe_vals)
    med_pb = median(pb_vals)
    med_ps = median(ps_vals)

    # 目标公司数据
    target = peers_table[0] if peers_table else {}
    target_revenue = target.get('revenue', 0)
    # v1.2：真实净资产（mult_data 已存 total_equity），替代营收×30% 粗糙假设
    tgt_equity = (mult_data.get(stock_code, {}) or {}).get('total_equity')

    # 估值（单位：亿元；v1.3：目标分子同口径 TTM——med_TTM倍数 × TTM绝对值，避免"TTM倍数×年报绝对值"混搭低估 40%）
    ttm_target_rev = sum((r['revenue_single'] or 0) for r in tgt_tq) if len(tgt_tq) == 4 else None
    ttm_target_np = sum((r['net_profit_single'] or 0) for r in tgt_tq) if len(tgt_tq) == 4 else None
    ann_target_np = ann_data.get(stock_code, {}).get('net_profit', 0) or 0
    valuations = {}
    pe_base_np = ttm_target_np if ttm_target_np and ttm_target_np > 0 else (ann_target_np if ann_target_np > 0 else None)
    if med_pe and pe_base_np:
        valuations['PE法'] = round(med_pe * pe_base_np / 1e8, 1)
    if med_pb and tgt_equity:
        valuations['PB法'] = round(med_pb * tgt_equity / 1e8, 1)
    ps_base_rev = ttm_target_rev if ttm_target_rev and ttm_target_rev > 0 else (target_revenue if target_revenue > 0 else None)
    if med_ps and ps_base_rev:
        valuations['PS法'] = round(med_ps * ps_base_rev / 1e8, 1)

    avg_val = sum(valuations.values()) / len(valuations) if valuations else None  # W4：全空返回 None

    return {
        'stock_code': stock_code,
        'name': name_map.get(stock_code, stock_code),
        'industry': industry_name,
        'method': 'Comparable Company Analysis',
        'val_basis': '估值与目标分子均TTM口径（理杏仁pe_ttm/ps_ttm + 近4季滚动）；无季度数据回退年报静态',
        'peer_count': len(peer_codes),
        'peers': peers_table,
        'median_multiples': {'pe': round(med_pe, 1) if med_pe else None,
                             'pb': round(med_pb, 1) if med_pb else None,
                             'ps': round(med_ps, 1) if med_ps else None},
        'implied_valuations': valuations,
        'average_valuation': round(avg_val, 1),
    }


# ═══════════════════════════════════════════
# 3. 盈利趋势分析 (Earnings Analysis)
# ═══════════════════════════════════════════

def earnings_analysis(stock_code, quarters=8):
    """
    季度盈利趋势分析。分析近N个季度的营收/净利变化。

    Returns: dict with trends, surprises, acceleration detection
    """
    db = get_db()

    # 季度数据
    rows = db.execute('''SELECT * FROM stock_financials_quarterly
        WHERE stock_code = ? ORDER BY report_date DESC LIMIT ?''',
        (stock_code, quarters)).fetchall()

    if not rows:
        db.close()
        return {'error': f'{stock_code} 无季度财务数据'}

    name_row = db.execute('SELECT name FROM stock_basic WHERE stock_code=?',
                          (stock_code,)).fetchone()
    name = name_row['name'] if name_row else stock_code
    db.close()

    quarters_data = []
    for r in reversed(rows):  # 从旧到新
        quarters_data.append({
            'report_date': r['report_date'],
            'revenue': round(r['revenue_single'] or 0, 1),
            'revenue_yoy': round(r['revenue_yoy'] or 0, 1),
            'revenue_qoq': round(r['revenue_qoq'] or 0, 1),
            'net_profit': round(r['net_profit_single'] or 0, 1),
            'net_profit_yoy': round(r['net_profit_yoy'] or 0, 1),
            'net_profit_qoq': round(r['net_profit_qoq'] or 0, 1),
            'gross_margin': round(r['gross_margin_single'] or 0, 1),
            'roe': round(r['roe_single'] or 0, 1),
        })

    if len(quarters_data) < 3:
        return {'error': f'{stock_code} 季度数据不足(需≥3)', 'quarters': quarters_data}

    # 趋势判断
    recent = quarters_data[-3:]  # 最近3个季度
    rev_trend = [q['revenue_yoy'] for q in recent]
    np_trend = [q['net_profit_yoy'] for q in recent]

    # 加速/减速判断
    rev_accel = rev_trend[-1] - rev_trend[0] if len(rev_trend) >= 2 else 0
    np_accel = np_trend[-1] - np_trend[0] if len(np_trend) >= 2 else 0

    # 盈利质量
    last = quarters_data[-1]
    margin_trend = [q['gross_margin'] for q in recent]

    if rev_accel > 5 and np_accel > 5:
        trend = '强劲增长 · 营收净利双加速'
    elif rev_accel > 0 and np_accel > 0:
        trend = '温和增长 · 趋势向好'
    elif rev_accel < -5 and np_accel < -5:
        trend = '明显减速 · 关注拐点'
    elif rev_accel < 0:
        trend = '增速放缓 · 营收先于净利减速'
    else:
        trend = '趋势分化 · 需进一步分析'

    return {
        'stock_code': stock_code,
        'name': name,
        'method': 'Earnings Trend Analysis',
        'quarters': quarters_data,
        'revenue_trend': rev_trend,
        'profit_trend': np_trend,
        'revenue_acceleration': round(rev_accel, 1),
        'profit_acceleration': round(np_accel, 1),
        'margin_trend': margin_trend,
        'trend_summary': trend,
        'latest': {
            'revenue_yoy': last['revenue_yoy'],
            'net_profit_yoy': last['net_profit_yoy'],
            'gross_margin': last['gross_margin'],
            'roe': last['roe'],
        }
    }


# ═══════════════════════════════════════════
# 4. 三表联动预测
# ═══════════════════════════════════════════

def three_statement_projection(stock_code, assumptions=None):
    """
    三表联动预测。基于最近年报真实数据 + 用户假设增长率。
    """
    db = get_db()

    if assumptions is None:
        assumptions = {}

    annual = db.execute('''SELECT * FROM stock_financials_annual
        WHERE stock_code = ? ORDER BY report_date DESC LIMIT 1''',
        (stock_code,)).fetchone()
    if not annual or not annual['revenue']:
        db.close(); return {'error': f'{stock_code} 无年报数据', 'base_basis': 'annual'}

    rev_yoy = annual['revenue_yoy'] if annual['revenue_yoy'] is not None else 12
    base_g = max(rev_yoy * 0.7, 5)
    g_list_assume = assumptions.get('growth_rates', [base_g, max(base_g-2,5), max(base_g-4,5)])
    g_list = [x/100.0 for x in g_list_assume]
    tax = assumptions.get('tax_rate', 0.25)
    nwc_pct = assumptions.get('nwc_pct', 0.12)
    ext = db.execute('''SELECT * FROM stock_financials_annual_ext
        WHERE stock_code = ? AND report_date = ?''',
        (stock_code, annual['report_date'])).fetchone()

    name_row = db.execute('SELECT name FROM stock_basic WHERE stock_code=?',
                          (stock_code,)).fetchone()
    name = name_row['name'] if name_row else stock_code

    warnings = []
    base_revenue = annual['revenue']
    gm = (annual['gross_margin'] or 0) / 100.0

    # ── v1.2 时效修复：整合最新季报（滚动TTM检测）──
    # 盈利爆发期（如 002648 卫星化学 2026H1 净利+127%）若仍以年报为基准，
    # 预测会严重低估（2026 预测 38.6亿 < H1 实际 62.3亿）。检测 TTM vs 年报偏差 >15%
    # 时自动切换滚动基准，并校准毛利率为最新季度值，同时输出失真警示。
    q_rows = db.execute('''SELECT report_date, revenue_single, net_profit_single,
        gross_margin_single FROM stock_financials_quarterly
        WHERE stock_code = ? ORDER BY report_date DESC LIMIT 4''', (stock_code,)).fetchall()
    if len(q_rows) == 4:
        ttm_rev = sum((r['revenue_single'] or 0) for r in q_rows)
        ttm_np = sum((r['net_profit_single'] or 0) for r in q_rows)
        ann_np = annual['net_profit'] or 0
        if ann_np and abs(ttm_np - ann_np) / abs(ann_np) > 0.15 and ttm_rev > 0 and ttm_np >= 0:
            base_revenue = ttm_rev
            latest_gm = q_rows[0]['gross_margin_single']
            if latest_gm:
                gm = latest_gm / 100.0
            # W3：sga/da/capex 比率仍以年报绝对值÷TTM营收（分母变大比率偏低），警告声明口径
            warnings.append('⚠️ SG&A/折旧/CapEx 比率仍基于年报绝对值（TTM 分母下略偏低），仅毛利率已校准')
            warnings.append(
                '⚠️ 盈利偏离年报 >15%，基准已自动切换为滚动TTM（整合最新季报）；'
                f'TTM 净利 {ttm_np/1e8:.1f}亿 vs 年报 {ann_np/1e8:.1f}亿，'
                '增长率假设需结合周期位置人工校准（勿直接外推）')
            base_is_ttm = True
        else:
            base_is_ttm = False
    else:
        base_is_ttm = False

    # 真实 SG&A
    if ext and (ext['selling_expense'] or ext['admin_expense']):
        sga_pct = ((ext['selling_expense'] or 0) + (ext['admin_expense'] or 0)) / base_revenue
    else:
        warnings.append('SG&A: 缺失')
        sga_pct = None

    # 真实 D&A
    if ext and (ext['depreciation_fa'] or ext['depreciation_ip']):
        da_pct = ((ext['depreciation_fa'] or 0) + (ext['depreciation_ip'] or 0)) / base_revenue
    else:
        warnings.append('D&A: 缺失')
        da_pct = None

    # 真实 CapEx
    ocf = annual['operating_cash_flow']
    fcf_a = annual['free_cash_flow']
    if ocf and fcf_a:
        capex_pct = (ocf - fcf_a) / base_revenue
    else:
        warnings.append('CapEx: 缺失')
        capex_pct = None

    # 真实利息
    if ext and ext['interest_expense']:
        interest_pct = ext['interest_expense'] / base_revenue
    else:
        warnings.append('利息支出: 缺失')
        interest_pct = 0

    db.close()

    # 确定基准年份
    base_year = int(annual['report_date'][:4]) if annual['report_date'] else 2025

    if sga_pct is None or da_pct is None or capex_pct is None:
        return {'stock_code': stock_code, 'name': name, 'method': '3-Statement Projection',
                'error': '必要财务数据缺失', 'warnings': warnings}

    projections = []
    rev = base_revenue

    for yr, g in enumerate(g_list):
        rev = rev * (1 + g)
        gross_profit = rev * gm
        sga = rev * sga_pct
        da = rev * da_pct
        ebit = gross_profit - sga - da
        interest = rev * interest_pct
        pretax = ebit - interest
        net_income = pretax * (1 - tax)
        nwc = rev * nwc_pct
        capex = rev * capex_pct
        ocf_proj = net_income + da
        fcf_proj = ocf_proj - capex

        projections.append({
            'year': base_year + yr + 1,
            'income_statement': {
                'revenue': round(rev, 1),
                'gross_profit': round(gross_profit, 1),
                'ebit': round(ebit, 1),
                'net_income': round(net_income, 1),
                'gross_margin': f'{gm*100:.1f}%',
                'sga_margin': f'{sga_pct*100:.1f}%',
                'net_margin': f'{net_income/rev*100:.1f}%' if rev > 0 else 'N/A',
            },
            'cash_flow': {
                'operating_cf': round(ocf_proj, 1),
                'free_cf': round(fcf_proj, 1),
            }
        })

    return {
        'stock_code': stock_code, 'name': name, 'method': '3-Statement Projection',
        'base_year': base_year,
        'base_basis': 'ttm_rolled' if base_is_ttm else 'annual',
        'base_growth': f'{base_g:.1f}%',
        'base_revenue': round(base_revenue, 1),
        'base_gross_margin': f'{gm*100:.1f}%',
        'sga_pct': f'{sga_pct*100:.1f}%',
        'da_pct': f'{da_pct*100:.1f}%',
        'capex_pct': f'{capex_pct*100:.1f}%',
        'projections': projections,
        'warnings': warnings if warnings else None,
    }


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

if __name__ == '__main__':
    import sys, json

    if len(sys.argv) < 3:
        print("用法: python financial.py <method> <stock_code>")
        print("  method: dcf | comps | earnings | model")
        sys.exit(1)

    method = sys.argv[1]
    code = sys.argv[2]

    if method == 'dcf':
        result = dcf_valuation(code)
    elif method == 'comps':
        result = comps_analysis(code)
    elif method == 'earnings':
        result = earnings_analysis(code)
    elif method == 'model':
        result = three_statement_projection(code)
    else:
        print(f"未知方法: {method}")
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════
# 5. 盈利质量警示 (Quality Analysis) —— v1.2 新增
# 源自 financial-report-analysis-pro 核验纪律：剔一次性项 / 股数反推
# ═══════════════════════════════════════════

def quality_analysis(stock_code):
    """盈利质量警示：扣非占比 / 一次性项检测 / 现金含量 / 股本核验"""
    db = get_db()
    q = db.execute('''SELECT report_date, net_profit_single, net_profit_adj_single,
        free_cash_flow FROM stock_financials_quarterly
        WHERE stock_code=? ORDER BY report_date DESC LIMIT 1''', (stock_code,)).fetchall()
    name_row = db.execute('SELECT name FROM stock_basic WHERE stock_code=?', (stock_code,)).fetchone()
    name = name_row['name'] if name_row else stock_code
    ann = db.execute('''SELECT net_profit, net_profit_adj, free_cash_flow FROM stock_financials_annual
        WHERE stock_code=? ORDER BY report_date DESC LIMIT 1''', (stock_code,)).fetchone()
    eq = db.execute('''SELECT capitalization, change_date FROM stock_equity_change
        WHERE stock_code=? ORDER BY change_date DESC LIMIT 1''', (stock_code,)).fetchone()
    db.close()

    alerts = []
    flags = {}
    latest = None
    if q:
        r = q[0]
        np_ = r['net_profit_single'] or 0
        adj = r['net_profit_adj_single']
        if np_ and np_ > 0:  # B1+W7：净利>0 才判扣非占比；亏损季跳过
            if adj is not None:
                ratio = adj / np_ * 100
                latest = {
                    'report_date': r['report_date'],
                    'net_profit': round(np_ / 1e8, 2),
                    'net_profit_adj': round(adj / 1e8, 2),
                    'adj_ratio': round(ratio, 1),
                }
                if ratio < 90:
                    alerts.append(f'一次性项占比 >10%（扣非占比 {ratio:.0f}%）：盈利被非经常损益污染，用扣非口径')
                    flags['one_off'] = True
                else:
                    flags['one_off'] = False
            # W1：现金含量改用年报口径（FCF/净利 同为累计值自洽；quarterly FCF 为年内累计，配单季净利会高估 2-4 倍）
            if ann and ann['free_cash_flow'] and ann['net_profit'] and ann['net_profit'] > 0:
                cash_ratio = ann['free_cash_flow'] / ann['net_profit']
                if latest is None:
                    latest = {'report_date': r['report_date'], 'net_profit': round(np_ / 1e8, 2)}
                latest['fcf_np_ratio_annual'] = round(cash_ratio, 2)
                if cash_ratio < 0.5:
                    alerts.append(f'现金含量低（年报FCF/净利 {cash_ratio:.2f}）：利润含金量需警惕')
    if ann:
        a_np = ann['net_profit'] or 0
        a_adj = ann['net_profit_adj']
        if a_np and a_adj is not None and a_np > 0:
            ar = a_adj / a_np * 100
            if ar < 90:
                alerts.append(f'年报扣非占比 {ar:.0f}%（一次性项影响年度盈利）')
    # 股数核验：净利/股本 = 每股净利（无披露 EPS 数据源，仅信息性输出，不作 >2% 偏差警示）
    share_note = None
    if eq and eq['capitalization'] and latest and latest.get('net_profit'):
        eps_implied = latest['net_profit'] * 1e8 / eq['capitalization']
        share_note = f'股本 {eq["capitalization"]/1e8:.2f} 亿股（{eq["change_date"]}）；隐含EPS {eps_implied:.2f} 元（净利/股本，非披露值）'
    return {
        'stock_code': stock_code, 'name': name, 'method': 'Earnings Quality Check',
        'latest': latest, 'alerts': alerts, 'flags': flags, 'share_note': share_note,
    }


# ═══════════════════════════════════════════
# 6. 现金流与股东回报 (Cashflow & Returns) —— v1.2 新增
# ═══════════════════════════════════════════

def cashflow_analysis(stock_code):
    """现金流与股东回报：FCF自算核验 / 经营现金流 / 资本配置 / 股本变动"""
    db = get_db()
    qs = db.execute('''SELECT report_date, free_cash_flow FROM stock_financials_quarterly
        WHERE stock_code=? ORDER BY report_date DESC LIMIT 6''', (stock_code,)).fetchall()
    ann = db.execute('''SELECT report_date, operating_cash_flow, free_cash_flow,
        revenue FROM stock_financials_annual
        WHERE stock_code=? ORDER BY report_date DESC LIMIT 2''', (stock_code,)).fetchall()
    eqs = db.execute('''SELECT change_date, capitalization, change_reason
        FROM stock_equity_change WHERE stock_code=? ORDER BY change_date DESC LIMIT 3''', (stock_code,)).fetchall()
    name_row = db.execute('SELECT name FROM stock_basic WHERE stock_code=?', (stock_code,)).fetchone()
    name = name_row['name'] if name_row else stock_code
    # v1.2 bugfix：上年同期 FCF（必须在 db.close 前查）
    prev_y_cum = None
    if qs:
        this_d = qs[0]['report_date']
        last_y = f'{int(this_d[:4]) - 1}{this_d[4:]}'
        r2 = db.execute('''SELECT free_cash_flow FROM stock_financials_quarterly
            WHERE stock_code=? AND report_date=?''', (stock_code, last_y)).fetchone()
        prev_y_cum = r2['free_cash_flow'] / 1e8 if r2 and r2['free_cash_flow'] else None
    db.close()

    # FCF：理杏仁 quarterly free_cash_flow 为年内累计口径（fetch 脚本注释“当季”有误，实测 2025 Q1 11.9→Q4 69.6 单调递增验证；W1 已统一）
    q_list = list(reversed(qs))
    fcf_series = []
    prev = None
    for r in q_list:
        if r['free_cash_flow'] is None:
            continue
        inc = None
        if prev is not None:
            # W8：累计口径下新年首季值重置（r < prev）→ 增量置 None 不产生虚假负值
            if r['free_cash_flow'] >= prev:
                inc = round((r['free_cash_flow'] - prev) / 1e8, 1)
            fcf_series.append({'report_date': r['report_date'], 'fcf_cum': round(r['free_cash_flow'] / 1e8, 1),
                               'fcf_qoq_inc': inc})
        prev = r['free_cash_flow']
        if qs and qs[0]['free_cash_flow']:
            latest_cum = qs[0]['free_cash_flow'] / 1e8
        else:
            latest_cum = None

    # 年度口径
    ann_list = []
    for a in ann:
        ocf = a['operating_cash_flow']
        fcf = a['free_cash_flow']
        capex = (ocf - fcf) / 1e8 if ocf and fcf is not None else None
        ann_list.append({'year': a['report_date'][:4], 'ocf': round(ocf / 1e8, 1) if ocf else None,
                         'fcf': round(fcf / 1e8, 1) if fcf is not None else None,
                         'capex_est': round(capex, 1) if capex is not None else None})
    # 股本变动
    eq_changes = [{'date': e['change_date'], 'cap': e['capitalization'] / 1e8 if e['capitalization'] else None,
                   'reason': e['change_reason']} for e in eqs]
    return {
        'stock_code': stock_code, 'name': name, 'method': 'Cashflow & Shareholder Returns',
        'fcf_latest_cum': round(latest_cum, 1) if latest_cum is not None else None,
        'fcf_report_date': qs[0]['report_date'] if qs else None,
        'fcf_yoy': round((latest_cum / prev_y_cum - 1) * 100, 1) if latest_cum is not None and prev_y_cum else None,
        'fcf_series': fcf_series,
        'annual': ann_list,
        'equity_changes': eq_changes,
        'note': 'FCF为理杏仁累计口径（年内单调累计）；资本开支=OCF-FCF估算',
    }
