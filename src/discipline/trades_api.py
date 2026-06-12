"""
知行系统 — 交易记录 API (Flask Blueprint)

端点:
  GET    /api/discipline/observation        # 今日观察池
  GET    /api/discipline/review/<code>      # 复核详情
  POST   /api/discipline/watchlist          # 加入自选池
  POST   /api/discipline/watchlist/manual   # 手动添加自选
  GET    /api/discipline/watchlist          # 自选池列表
  DELETE /api/discipline/watchlist/<code>   # 移出自选池
  POST   /api/discipline/precheck           # 买入前检查
  POST   /api/discipline/trades             # 录入买入
  PUT    /api/discipline/trades/<id>        # 录入卖出
  GET    /api/discipline/trades             # 交易列表
  GET    /api/discipline/trades/<id>        # 单笔详情
  GET    /api/discipline/summary            # 盈亏汇总
  GET    /api/discipline/checklist          # 获取检查清单模板
  PUT    /api/discipline/checklist          # 更新检查清单模板
"""

from flask import Blueprint, request, jsonify, g
import sqlite3
import os
import json

discipline_bp = Blueprint('discipline', __name__, url_prefix='/api/discipline')

# ── 数据库路径 ─────────────────────────────────────────
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


# ══════════════════════════════════════════════════════
# 观察池
# ══════════════════════════════════════════════════════

@discipline_bp.route('/observation', methods=['GET', 'OPTIONS'])
def api_observation():
    if request.method == 'OPTIONS':
        return '', 204
    
    db = get_db()
    target_date = request.args.get('date', None)
    
    if target_date:
        rows = db.execute(
            "SELECT * FROM discipline_observation_pool WHERE date = ? ORDER BY composite_score DESC",
            (target_date,)
        ).fetchall()
    else:
        latest = db.execute("SELECT MAX(date) FROM discipline_observation_pool").fetchone()
        if not latest or not latest[0]:
            return jsonify({'items': [], 'date': None, 'count': 0})
        target_date = latest[0]
        rows = db.execute(
            "SELECT * FROM discipline_observation_pool WHERE date = ? ORDER BY composite_score DESC",
            (target_date,)
        ).fetchall()
    
    items = [dict(r) for r in rows]
    return jsonify({'items': items, 'date': target_date, 'count': len(items)})


@discipline_bp.route('/screening', methods=['GET', 'OPTIONS'])
def api_screening():
    """欧奈尔每日精选列表"""
    if request.method == 'OPTIONS':
        return '', 204
    db = get_db()
    mode = request.args.get('mode', 'stock')
    if mode == 'index':
        target_date = request.args.get('date', None)
        # 优先从快照表读取
        if not target_date:
            row = db.execute("SELECT MAX(date) FROM discipline_screening_daily_index").fetchone()
            target_date = row[0] if row and row[0] else None
        if target_date:
            cached = db.execute("SELECT * FROM discipline_screening_daily_index WHERE date=? ORDER BY rank", (target_date,)).fetchall()
            if cached:
                items = [dict(r) for r in cached]
                return jsonify({
                    'market_gate': 'ok', 'market_warning': False, 'market_phase': items[0].get('market_phase',''),
                    'items': items, 'count': len(items), 'date': target_date, 'mode': 'index',
                })
        # 缓存未命中，实时计算
        from discipline.index_screener import run as index_run
        result = index_run(target_date=request.args.get('date', None))
        return jsonify({
            'market_gate': 'ok',
            'market_warning': result.get('market_warning', False),
            'market_phase': result.get('market_phase', ''),
            'items': result.get('items', []),
            'count': len(result.get('items', [])),
            'date': result.get('date', 'latest'),
            'mode': 'index',
        })

    target_date = request.args.get('date', None)
    # 优先从快照表读取
    if not target_date:
        row = db.execute("SELECT MAX(date) FROM discipline_screening_daily").fetchone()
        target_date = row[0] if row and row[0] else None
    if target_date:
        cached = db.execute("SELECT * FROM discipline_screening_daily WHERE date=? ORDER BY rank", (target_date,)).fetchall()
        if cached:
            items = [dict(r) for r in cached]
            # 补全行业和RS字段（快照表未存）
            # 优先同天观察池，无则取最新
            obs_date = target_date
            obs_check = db.execute("SELECT COUNT(*) as cnt FROM discipline_observation_pool WHERE date=?", (obs_date,)).fetchone()
            if not obs_check or obs_check['cnt'] == 0:
                obs_date = db.execute("SELECT MAX(date) FROM discipline_observation_pool").fetchone()[0]
            for it in items:
                ext = db.execute("""
                    SELECT industry_name, rs_category, rps_60, rps_120, rps_20,
                           roe, revenue_yoy, debt_ratio, gross_margin
                    FROM discipline_observation_pool
                    WHERE stock_code=? AND date=?
                """, (it['stock_code'], obs_date)).fetchone()
                if ext:
                    it['industry_name'] = ext['industry_name']
                    it['rs_category'] = ext['rs_category']
                    it['rps_60'] = ext['rps_60']
                    it['rps_120'] = ext['rps_120']
                    it['rps_20'] = ext['rps_20']
                    it['roe'] = ext['roe']
                    it['revenue_yoy'] = ext['revenue_yoy']
                    it['debt_ratio'] = ext['debt_ratio']
                    it['gross_margin'] = ext['gross_margin']
            market = db.execute("SELECT market_phase FROM market_direction_daily ORDER BY date DESC LIMIT 1").fetchone()
            phase = market['market_phase'] if market else ''
            return jsonify({
                'market_gate': 'ok', 'market_warning': False, 'market_phase': phase,
                'items': items, 'count': len(items), 'date': target_date, 'mode': 'stock',
            })
    # 缓存未命中，实时计算
    from discipline.screener import run
    result = run(target_date=target_date)
    return jsonify({
        'market_gate': result.get('market_gate', 'ok'),
        'market_warning': result.get('market_warning', False),
        'market_phase': result.get('market_phase', ''),
        'items': result.get('items', []),
        'count': len(result.get('items', [])),
        'strongest_indices': result.get('strongest_indices', {}),
        'date': result.get('date') or target_date or 'latest',
        'mode': 'stock',
    })


@discipline_bp.route('/screening-backtest', methods=['GET', 'OPTIONS'])
def api_screening_backtest():
    """精选回测：指定日期 → 精选股票列表 + 至今收益"""
    if request.method == 'OPTIONS':
        return '', 204
    db = get_db()

    target_date = request.args.get('date', None)
    if not target_date:
        row = db.execute("SELECT MAX(date) FROM discipline_screening_daily").fetchone()
        target_date = row[0] if row and row[0] else None
    if not target_date:
        return jsonify({'error': '无历史精选数据', 'items': [], 'date': None})

    try:
        rows = db.execute("""
            SELECT * FROM discipline_screening_daily WHERE date = ? ORDER BY rank
        """, (target_date,)).fetchall()
    except sqlite3.OperationalError:
        return jsonify({'error': '数据表未创建，请重启 Flask 后先访问筛选页生成数据', 'items': [], 'date': target_date, 'available_dates': []})

    items = []
    for r in rows:
        entry = dict(r)
        ideal_buy = r['ideal_buy']

        if ideal_buy and ideal_buy > 0:
            # 取该日期之后的全部K线
            krows = db.execute("""
                SELECT date, close FROM daily_kline
                WHERE stock_code = ? AND date >= ? ORDER BY date
            """, (r['stock_code'], target_date)).fetchall()

            # 精选日当日收盘价（index 0）
            entry['close_0d'] = round(krows[0]['close'], 2) if krows else None

            # 计算第 N 个交易日（跳过信号日当天 = index 0）
            for tag, nth in [('return_5d', 5), ('return_10d', 10), ('return_20d', 20)]:
                idx = nth  # 第 N 个交易日后
                if idx < len(krows):
                    close_n = krows[idx]['close']
                    entry[tag] = round((close_n - ideal_buy) / ideal_buy * 100, 1)
                else:
                    entry[tag] = None
            entry['holding_days'] = len(krows)
        else:
            for tag in ['return_5d', 'return_10d', 'return_20d']:
                entry[tag] = None
            entry['holding_days'] = 0

        items.append(entry)

    # 汇总统计（5/10/20 日）
    stats = {'count': len(items)}
    for tag in ['return_5d', 'return_10d', 'return_20d']:
        valid = [i for i in items if i[tag] is not None]
        if valid:
            wins = [i for i in valid if i[tag] > 0]
            stats[tag] = {
                'valid': len(valid),
                'avg_return': round(sum(i[tag] for i in valid) / len(valid), 1),
                'win_rate': round(len(wins) / len(valid) * 100, 1),
                'best': max(valid, key=lambda x: x[tag]),
                'worst': min(valid, key=lambda x: x[tag]),
            }
        else:
            stats[tag] = {'valid': 0, 'avg_return': None, 'win_rate': None, 'best': None, 'worst': None}

    # 可用日期列表
    try:
        dates = [r[0] for r in db.execute(
            "SELECT DISTINCT date FROM discipline_screening_daily ORDER BY date DESC LIMIT 60"
        ).fetchall()]
    except sqlite3.OperationalError:
        dates = []

    return jsonify({
        'date': target_date,
        'available_dates': dates,
        'items': items,
        'stats': stats,
    })


@discipline_bp.route('/screening-backtest-index', methods=['GET', 'OPTIONS'])
def api_screening_backtest_index():
    """指数精选回测"""
    if request.method == 'OPTIONS':
        return '', 204
    db = get_db()
    target_date = request.args.get('date', None)
    if not target_date:
        row = db.execute("SELECT MAX(date) FROM discipline_screening_daily_index").fetchone()
        target_date = row[0] if row and row[0] else None
    if not target_date:
        return jsonify({'error': '无历史精选数据', 'items': [], 'date': None})

    try:
        rows = db.execute("""
            SELECT * FROM discipline_screening_daily_index WHERE date = ? ORDER BY rank
        """, (target_date,)).fetchall()
    except sqlite3.OperationalError:
        return jsonify({'error': '数据表未创建', 'items': [], 'date': target_date, 'available_dates': []})

    items = []
    for r in rows:
        entry = dict(r)
        ideal_buy = r['ideal_buy']
        if ideal_buy and ideal_buy > 0:
            krows = db.execute("""
                SELECT date, close FROM index_daily_kline
                WHERE stock_code=? AND date>=? AND kline_type='normal' ORDER BY date
            """, (r['index_code'], target_date)).fetchall()
            entry['close_0d'] = round(krows[0]['close'], 2) if krows else None
            for tag, nth in [('return_5d', 5), ('return_10d', 10), ('return_20d', 20)]:
                idx = nth
                if idx < len(krows):
                    entry[tag] = round((krows[idx]['close'] - ideal_buy) / ideal_buy * 100, 1)
                else:
                    entry[tag] = None
            entry['holding_days'] = len(krows)
        else:
            entry['close_0d'] = None
            for tag in ['return_5d', 'return_10d', 'return_20d']:
                entry[tag] = None
            entry['holding_days'] = 0
        items.append(entry)

    stats = {'count': len(items)}
    for tag in ['return_5d', 'return_10d', 'return_20d']:
        valid = [i for i in items if i[tag] is not None]
        if valid:
            wins = [i for i in valid if i[tag] > 0]
            stats[tag] = {
                'valid': len(valid),
                'avg_return': round(sum(i[tag] for i in valid) / len(valid), 1),
                'win_rate': round(len(wins) / len(valid) * 100, 1),
                'best': max(valid, key=lambda x: x[tag]),
                'worst': min(valid, key=lambda x: x[tag]),
            }
        else:
            stats[tag] = {'valid': 0, 'avg_return': None, 'win_rate': None}

    try:
        dates = [r[0] for r in db.execute(
            "SELECT DISTINCT date FROM discipline_screening_daily_index ORDER BY date DESC LIMIT 60"
        ).fetchall()]
    except sqlite3.OperationalError:
        dates = []

    return jsonify({
        'date': target_date,
        'available_dates': dates,
        'items': items,
        'stats': stats,
        'mode': 'index',
    })


@discipline_bp.route('/recommendation/<stock_code>', methods=['GET', 'OPTIONS'])
def api_recommendation(stock_code):
    """欧奈尔精选推荐信"""
    if request.method == 'OPTIONS':
        return '', 204
    db = get_db()

    # 六层数据汇总
    # 大盘
    market = db.execute("SELECT * FROM market_direction_daily ORDER BY date DESC LIMIT 1").fetchone()
    mkt = dict(market) if market else {}

    # 观察池
    obs = db.execute("""
        SELECT * FROM discipline_observation_pool
        WHERE stock_code=? AND date=(SELECT MAX(date) FROM discipline_observation_pool)
    """, (stock_code,)).fetchone()
    obs_data = dict(obs) if obs else {}

    # CANSLIM
    cs = db.execute("SELECT * FROM cansim_scores WHERE stock_code=? ORDER BY date DESC LIMIT 1", (stock_code,)).fetchone()
    canslim = dict(cs) if cs else {}

    # RS
    rs = db.execute("SELECT * FROM stock_rs_daily WHERE stock_code=? ORDER BY date DESC LIMIT 1", (stock_code,)).fetchone()
    rs_data = dict(rs) if rs else {}

    # 信号
    sig_rows = db.execute("SELECT signals_json FROM pattern_scan_signals WHERE stock_code=? ORDER BY date DESC", (stock_code,)).fetchall()
    all_signals = []
    for sr in sig_rows:
        if sr['signals_json']:
            try: all_signals.extend(json.loads(sr['signals_json']))
            except: pass

    # 行业
    ind = db.execute("SELECT industry_name FROM stock_sw_industry WHERE stock_code=?", (stock_code,)).fetchone()
    industry = ind['industry_name'] if ind else ''

    # 机构
    inst = db.execute("SELECT * FROM stock_institutional_holdings WHERE stock_code=? ORDER BY date DESC LIMIT 1", (stock_code,)).fetchone()
    inst_data = dict(inst) if inst else {}

    # 财务
    fin = db.execute("SELECT * FROM stock_financials_annual WHERE stock_code=? ORDER BY report_date DESC LIMIT 1", (stock_code,)).fetchone()
    fin_data = dict(fin) if fin else {}

    # 名称
    name_row = db.execute("SELECT name FROM stock_basic WHERE stock_code=?", (stock_code,)).fetchone()
    stock_name = name_row['name'] if name_row else stock_code

    # 买点/止损
    ideal_buy = None
    buy_src = ''
    for s in all_signals:
        bp = s.get('breakout_price') or s.get('buy_point')
        if bp:
            ideal_buy = round(bp, 2)
            buy_src = s.get('source', '')
            break
    stop_loss = round(ideal_buy * 0.92, 2) if ideal_buy else None

    # 信号分类统计
    buy_count = sum(1 for s in all_signals if s.get('source','') in ('base_breakout','double_bottom','flat_base','saucer_base','standard_breakout','cup_handle','pocket_pivot'))
    veto_count = sum(1 for s in all_signals if s.get('source','') in ('top_pattern','climax_top','breakout_failure'))

    return jsonify({
        'stock_code': stock_code,
        'stock_name': stock_name,
        'industry': industry,
        'market': {'phase': mkt.get('market_phase',''), 'risk': mkt.get('risk_level',''),
                   'position': mkt.get('suggested_position_size'), 'ftd': mkt.get('ftd_date',''),
                   'dist_days': mkt.get('distribution_days_25d')},
        'rs': {'rps_20': rs_data.get('rps_20'), 'rps_250': rs_data.get('rps_250'),
               'rs_line': rs_data.get('rs_line')},
        'canslim': {k: canslim.get(k) for k in ['score','score_c','score_a','score_n','score_s','score_l','score_i','grade']},
        'financial': {'roe': fin_data.get('roe'), 'revenue_yoy': fin_data.get('revenue_yoy'),
                      'gross_margin': fin_data.get('gross_margin'), 'debt_ratio': fin_data.get('asset_liability_ratio')},
        'institutional': {'fund_count': inst_data.get('fund_count'), 'top10_pct': inst_data.get('top10_inst_proportion'),
                          'total_pct': inst_data.get('total_inst_proportion')},
        'signals': {'total': len(all_signals), 'buy': buy_count, 'veto': veto_count,
                    'sources': list(set(s.get('source','?') for s in all_signals))},
        'ideal_buy': ideal_buy,
        'buy_source': buy_src,
        'stop_loss': stop_loss,
        'current_price': rs_data.get('close'),
    })


@discipline_bp.route('/lookup-name', methods=['GET', 'OPTIONS'])
def api_lookup_name():
    """查询代码名称（用于录入表单实时预览）"""
    if request.method == 'OPTIONS':
        return '', 204
    code = request.args.get('code', '').strip()
    atype = request.args.get('type', 'stock')
    if not code:
        return jsonify({'name': ''})
    db = get_db()
    name = _lookup_name(db, code, atype)
    return jsonify({'name': name, 'code': code, 'type': atype})


# ══════════════════════════════════════════════════════
# 拟阅详情
# ══════════════════════════════════════════════════════

@discipline_bp.route('/review/<stock_code>', methods=['GET', 'OPTIONS'])
def api_review(stock_code):
    if request.method == 'OPTIONS':
        return '', 204

    db = get_db()
    target_date = request.args.get('date', None)

    # 1. 日K线 — 从交易记录取 asset_type 判断股票/指数
    asset_type = 'stock'
    trade = db.execute(
        "SELECT asset_type FROM discipline_trades WHERE stock_code = ? ORDER BY buy_date DESC LIMIT 1",
        (stock_code,)
    ).fetchone()
    if trade and trade['asset_type']:
        asset_type = trade['asset_type']

    kline_rows = _lookup_kline(db, stock_code, asset_type, 400)
    klines = [dict(r) for r in reversed(kline_rows)]

    # 2. 代码信息
    name = _lookup_name(db, stock_code, asset_type)
    stock_info = {'stock_code': stock_code, 'name': name}

    # 3. 行业
    ind = db.execute(
        "SELECT industry_name FROM stock_sw_industry WHERE stock_code = ?",
        (stock_code,)
    ).fetchone()
    industry = ind['industry_name'] if ind else ''

    # 4. 最新 RS
    rs = db.execute("""
        SELECT rps_20, rps_60, rps_120, rps_250, rs_line
        FROM stock_rs_daily
        WHERE stock_code = ?
        ORDER BY date DESC LIMIT 1
    """, (stock_code,)).fetchone()
    rs_data = dict(rs) if rs else {}

    # 5. 最新 CANSLIM 评分
    cs = db.execute("""
        SELECT score, score_c, score_a, score_n, score_s, score_l, score_i, grade
        FROM cansim_scores
        WHERE stock_code = ?
        ORDER BY date DESC LIMIT 1
    """, (stock_code,)).fetchone()
    canslim_data = dict(cs) if cs else {}

    # 6. 最新年度财务
    fin = db.execute("""
        SELECT report_date, roe, revenue_yoy, gross_margin, asset_liability_ratio
        FROM stock_financials_annual
        WHERE stock_code = ?
        ORDER BY report_date DESC LIMIT 1
    """, (stock_code,)).fetchone()
    fin_data = dict(fin) if fin else {}

    # 7. 观察池快照
    obs = db.execute("""
        SELECT rs_category, composite_score, grade, suggestion,
               buy_signals_json, sell_signals_json, signals_json
        FROM discipline_observation_pool
        WHERE stock_code = ? AND date = (SELECT MAX(date) FROM discipline_observation_pool)
    """, (stock_code,)).fetchone()
    obs_data = dict(obs) if obs else {}

    # 8. V2: 信号加载 — 优先 pattern_scan_signals 历史数据，不足时实时扫描
    signals = []
    signal_source = None
    seen = set()

    # 8a. 从 pattern_scan_signals 表读取已有数据
    from datetime import datetime, timedelta
    sig_rows = db.execute("""
        SELECT date, signals_json FROM pattern_scan_signals
        WHERE stock_code = ? ORDER BY date DESC
    """, (stock_code,)).fetchall()

    if sig_rows:
        signal_source = 'pattern_scan'
        for sr in sig_rows:
            if not sr['signals_json']:
                continue
            try:
                day_sigs = json.loads(sr['signals_json'])
                for s in day_sigs:
                    key = (s.get('date',''), s.get('source',''), s.get('type',''), s.get('signal_date',''))
                    if key not in seen:
                        seen.add(key)
                        signals.append(s)
            except (json.JSONDecodeError, TypeError):
                pass

    # 8b. 历史数据不足（<5天）时，用已加载的K线实时跑扫描引擎
    if len(sig_rows) < 5 and klines and len(klines) >= 50:
        try:
            from engine_registry import run_all_engines
            # klines 已按日期升序排列
            from src.server import _compute_indicators
            ind = _compute_indicators(klines)
            engine_signals = run_all_engines(klines=klines, indicators=ind)
            if not signal_source:
                signal_source = 'real_time'
            for s in engine_signals:
                key = (s.get('date',''), s.get('source',''), s.get('type',''), s.get('signal_date',''))
                if key not in seen:
                    seen.add(key)
                    signals.append(s)
        except Exception:
            pass  # 引擎不可用时静默跳过

    # 8c. 都没数据就回退观察池快照 / 旧格式
    if not signals:
        if obs_data.get('signals_json'):
            try:
                signals = json.loads(obs_data['signals_json'])
            except (json.JSONDecodeError, TypeError):
                pass
        if not signals:
            buy_signals = []
            sell_signals = []
            if obs_data.get('buy_signals_json'):
                try:
                    buy_signals = json.loads(obs_data['buy_signals_json'])
                except (json.JSONDecodeError, TypeError):
                    pass
            if obs_data.get('sell_signals_json'):
                try:
                    sell_signals = json.loads(obs_data['sell_signals_json'])
                except (json.JSONDecodeError, TypeError):
                    pass
            signals = buy_signals + sell_signals

    # 9. V2: L维度 — 行业RS排名
    sector_rs = {}
    if industry:
        ind_row = db.execute("""
            SELECT composite_rs AS rs_line, rs_20d AS rps_20, rank
            FROM industry_strength_results
            WHERE industry_name = ? ORDER BY date DESC LIMIT 1
        """, (industry,)).fetchone()
        if ind_row:
            sector_rs = dict(ind_row)
        else:
            # 回退：从 cansim_scores.score_l 获取
            if canslim_data and canslim_data.get('score_l') is not None:
                sector_rs = {'score_l': canslim_data['score_l']}

    # 10. V2: I维度 — 机构持股
    institutional = None
    inst_row = db.execute("""
        SELECT fund_count, fund_proportion_sum, top10_inst_proportion,
               total_inst_count, total_inst_proportion, date
        FROM stock_institutional_holdings
        WHERE stock_code = ? ORDER BY date DESC LIMIT 1
    """, (stock_code,)).fetchone()
    if inst_row:
        institutional = dict(inst_row)

    # 11. V2: M维度 — 大盘环境
    market = db.execute("""
        SELECT date, market_phase, risk_level, suggested_position_size,
               distribution_days_25d, ftd_exists, ftd_date,
               market_health_score, summary
        FROM market_direction_daily
        ORDER BY date DESC LIMIT 1
    """).fetchone()
    market_data = dict(market) if market else {}

    # 12. V2: 量价结构 — 过去25日吸筹/出货日
    vol_analysis = _compute_vol_structure(db, stock_code, klines)

    # 13. V2: 理想买点 — 形态引擎 breakout_price，回退200天最高价
    ideal_buy = _compute_ideal_buy(signals, klines)

    return jsonify({
        'stock_code': stock_code,
        'stock_info': stock_info,
        'industry': industry,
        'klines': klines,
        'rs': rs_data,
        'canslim': canslim_data,
        'financial': fin_data,
        'observation': {
            'rs_category': obs_data.get('rs_category'),
            'composite_score': obs_data.get('composite_score'),
            'grade': obs_data.get('grade'),
            'suggestion': obs_data.get('suggestion'),
        },
        'signals': signals,
        'signal_count': len(signals),
        'signal_source': signal_source,
        'sector_rs': sector_rs,
        'institutional': institutional,
        'market': market_data,
        'vol_structure': vol_analysis,
        'ideal_buy': ideal_buy,
    })


# ══════════════════════════════════════════════════════
# 欧奈尔AI分析（桥接 DeepSeek CLI + Skill）
# ══════════════════════════════════════════════════════

@discipline_bp.route('/oneil-analysis/<stock_code>', methods=['GET', 'OPTIONS'])
def api_oneil_analysis(stock_code):
    if request.method == 'OPTIONS':
        return '', 204

    db = get_db()

    # 1. 收集股票数据（复用 review 的数据采集逻辑）
    asset_type = 'stock'
    trade = db.execute(
        "SELECT asset_type FROM discipline_trades WHERE stock_code = ? ORDER BY buy_date DESC LIMIT 1",
        (stock_code,)
    ).fetchone()
    if trade and trade['asset_type']:
        asset_type = trade['asset_type']

    kline_rows = _lookup_kline(db, stock_code, asset_type, 200)
    klines = [dict(r) for r in reversed(kline_rows)]
    name = _lookup_name(db, stock_code, asset_type)

    # 最近20日信号
    sig_rows = db.execute("""
        SELECT date, signals_json FROM pattern_scan_signals
        WHERE stock_code = ? ORDER BY date DESC LIMIT 20
    """, (stock_code,)).fetchall()
    import json
    all_20d_sigs = []
    for sr in sig_rows:
        if sr['signals_json']:
            try:
                for s in json.loads(sr['signals_json']):
                    all_20d_sigs.append(s)
            except (json.JSONDecodeError, TypeError):
                pass

    # CANSLIM
    cs = db.execute("SELECT * FROM cansim_scores WHERE stock_code=? ORDER BY date DESC LIMIT 1", (stock_code,)).fetchone()
    canslim = dict(cs) if cs else {}

    # RS
    rs = db.execute("SELECT * FROM stock_rs_daily WHERE stock_code=? ORDER BY date DESC LIMIT 1", (stock_code,)).fetchone()
    rs_data = dict(rs) if rs else {}

    # 行业
    ind = db.execute("SELECT industry_name FROM stock_sw_industry WHERE stock_code=?", (stock_code,)).fetchone()
    industry = ind['industry_name'] if ind else ''

    # 大盘
    mkt = db.execute("SELECT * FROM market_direction_daily ORDER BY date DESC LIMIT 1").fetchone()
    market = dict(mkt) if mkt else {}

    # 机构
    inst = db.execute("SELECT * FROM stock_institutional_holdings WHERE stock_code=? ORDER BY date DESC LIMIT 1", (stock_code,)).fetchone()
    inst_data = dict(inst) if inst else {}

    # 买点
    ideal_buy = _compute_ideal_buy(all_20d_sigs, klines)

    # 最近K线摘要
    recent_k = klines[-5:] if len(klines) >= 5 else klines
    k_summary = []
    for k in recent_k:
        chg_pct = k.get('change_pct')
        if chg_pct is not None and abs(chg_pct) <= 1:
            chg_pct = chg_pct * 100
        k_summary.append(f"{k['date']} O={k['open']} C={k['close']} H={k['high']} L={k['low']} V={k['volume']} chg%={chg_pct:.1f}" if chg_pct is not None else f"{k['date']} O={k['open']} C={k['close']} H={k['high']} L={k['low']} V={k['volume']}")

    # 信号摘要
    sig_sources = {}
    for s in all_20d_sigs:
        src = s.get('source', '?')
        sig_sources[src] = sig_sources.get(src, 0) + 1
    sig_text = '、'.join(f"{k}({v})" for k, v in sig_sources.items()) if sig_sources else '无'

    # 2. 构建上下文
    context = f"""股票：{name}（{stock_code}）
行业：{industry}
日期：{klines[-1]['date'] if klines else '—'}

【近5日K线】
{chr(10).join(k_summary)}

【CANSLIM评分】
总分：{canslim.get('score','—')}，评级：{canslim.get('grade','—')}
C(当季)：{canslim.get('score_c','—')}，A(年度)：{canslim.get('score_a','—')}
N(新事物)：{canslim.get('score_n','—')}，S(供需)：{canslim.get('score_s','—')}
L(领涨)：{canslim.get('score_l','—')}，I(机构)：{canslim.get('score_i','—')}

【RS强度】
RPS20：{rs_data.get('rps_20','—')}，RPS60：{rs_data.get('rps_60','—')}
RPS120：{rs_data.get('rps_120','—')}，RPS250：{rs_data.get('rps_250','—')}

【大盘环境】
阶段：{market.get('market_phase','—')}，健康分：{market.get('market_health_score','—')}
追盘日：{market.get('ftd_date','无')}，25日抛盘日：{market.get('distribution_days_25d','—')}个
建议仓位：{market.get('suggested_position_size','—')}

【机构持股】
基金数：{inst_data.get('fund_count','—')}，机构持股占比：{inst_data.get('top10_inst_proportion','—')}

【近20日信号({len(all_20d_sigs)}个)】
{sig_text}

【理想买点】
{json.dumps(ideal_buy, ensure_ascii=False) if ideal_buy else '—'}
"""

    # 3. 调用 DeepSeek CLI（直接用 exe，避免 cmd 包装的参数截断）
    import subprocess, os
    DEEPSEEK_EXE = r'D:\dstui\codewhale-tui-windows-x64.exe'

    prompt = f"""你是欧奈尔交易顾问。请根据以下股票数据，用欧奈尔方法论进行买入决策十二问分析，写一篇连贯的分析文章（不要表格，要叙事风格），引用欧奈尔名言，最后给出综合结论。

{context}

请用HTML格式输出（h3标题、p段落、blockquote引用名言，无需完整HTML文档结构），风格温暖、直接、像一位严师在跟你对话。"""

    try:
        result = subprocess.run(
            [DEEPSEEK_EXE, 'exec', prompt],
            capture_output=True, text=True, timeout=120, encoding='utf-8', errors='replace',
            cwd=r'D:\dstui', shell=False
        )
        output = result.stdout.strip()
        if not output:
            output = '<p style="color:var(--text-tertiary)">AI 未返回内容，请稍后重试。</p>'
    except subprocess.TimeoutExpired:
        output = '<p style="color:#F44336">AI 分析超时（120秒），请稍后重试。</p>'
    except Exception as e:
        output = f'<p style="color:#F44336">调用失败：{str(e)}</p>'

    return jsonify({
        'stock_code': stock_code,
        'stock_name': name,
        'industry': industry,
        'html': output,
    })


# ══════════════════════════════════════════════════════
# 欧奈尔AI分析 — 指数版
# ══════════════════════════════════════════════════════

@discipline_bp.route('/oneil-analysis-index/<index_code>', methods=['GET', 'OPTIONS'])
def api_oneil_analysis_index(index_code):
    if request.method == 'OPTIONS':
        return '', 204

    db = get_db()
    asset_type = 'index'

    kline_rows = _lookup_kline(db, index_code, asset_type, 200)
    klines = [dict(r) for r in reversed(kline_rows)]
    name = _lookup_name(db, index_code, asset_type)

    # 最近20日信号
    sig_rows = db.execute("""
        SELECT date, signals_json FROM pattern_scan_signals
        WHERE stock_code = ? ORDER BY date DESC LIMIT 20
    """, (index_code,)).fetchall()
    import json
    all_20d_sigs = []
    for sr in sig_rows:
        if sr['signals_json']:
            try:
                for s in json.loads(sr['signals_json']):
                    all_20d_sigs.append(s)
            except (json.JSONDecodeError, TypeError):
                pass

    # 指数 RS
    rs = db.execute("SELECT * FROM index_rs_daily WHERE stock_code=? ORDER BY date DESC LIMIT 1", (index_code,)).fetchone()
    rs_data = dict(rs) if rs else {}

    # 大盘
    mkt = db.execute("SELECT * FROM market_direction_daily ORDER BY date DESC LIMIT 1").fetchone()
    market = dict(mkt) if mkt else {}

    # 指数池归属（从 discipline_screening_daily_index 获取）
    pool_row = db.execute(
        "SELECT pool_name FROM discipline_screening_daily_index WHERE index_code=? ORDER BY date DESC LIMIT 1",
        (index_code,)
    ).fetchone()
    pool_name = pool_row['pool_name'] if pool_row else ''

    # 精选评分
    scr_row = db.execute(
        "SELECT index_score, rps_250, rps_20, signal_count, signal_summary, ideal_buy FROM discipline_screening_daily_index WHERE index_code=? ORDER BY date DESC LIMIT 1",
        (index_code,)
    ).fetchone()
    scr_data = dict(scr_row) if scr_row else {}

    # 最近K线摘要
    recent_k = klines[-5:] if len(klines) >= 5 else klines
    k_summary = []
    for k in recent_k:
        chg_pct = k.get('change_pct')
        if chg_pct is not None and abs(chg_pct) <= 1:
            chg_pct = chg_pct * 100
        k_summary.append(f"{k['date']} O={k['open']} C={k['close']} H={k['high']} L={k['low']} V={k['volume']} chg%={chg_pct:.1f}" if chg_pct is not None else f"{k['date']} O={k['open']} C={k['close']} H={k['high']} L={k['low']} V={k['volume']}")

    # 信号摘要
    sig_sources = {}
    for s in all_20d_sigs:
        src = s.get('source', '?')
        sig_sources[src] = sig_sources.get(src, 0) + 1
    sig_text = '、'.join(f"{k}({v})" for k, v in sig_sources.items()) if sig_sources else '无'

    # 构建上下文
    context = f"""指数：{name}（{index_code}）
日期：{klines[-1]['date'] if klines else '—'}
所属池：{pool_name}

【近5日K线】
{chr(10).join(k_summary)}

【指数RS强度】
RPS20：{rs_data.get('rs_20','—')}，RPS60：{rs_data.get('rs_60','—')}
RPS120：{rs_data.get('rs_120','—')}，RPS250：{rs_data.get('rs_250','—')}
MA50：{rs_data.get('ma50','—')}，MA150：{rs_data.get('ma150','—')}，MA200：{rs_data.get('ma200','—')}
AD线斜率20日：{rs_data.get('ad_slope_20d','—')}

【大盘环境】
阶段：{market.get('market_phase','—')}，健康分：{market.get('market_health_score','—')}
追盘日：{market.get('ftd_date','无')}，25日抛盘日：{market.get('distribution_days_25d','—')}个
建议仓位：{market.get('suggested_position_size','—')}

【近20日信号({len(all_20d_sigs)}个)】
{sig_text}

【精选评分】
指数精选得分：{scr_data.get('index_score','—')}
理想买点：{scr_data.get('ideal_buy','—')}
"""

    # 调用 DeepSeek CLI
    import subprocess, os
    DEEPSEEK_EXE = r'D:\dstui\codewhale-tui-windows-x64.exe'

    prompt = f"""你是欧奈尔交易顾问。请根据以下指数数据，用欧奈尔方法论进行指数分析，重点评估：
1. 大盘环境（欧奈尔强调3/4的股票跟随大盘，指数方向至关重要）
2. RS动量强度（该指数是否处于领涨地位）
3. 技术形态信号（基部突破、口袋支点等信号的含义）
4. 理想买点（如果有的话）

写一篇连贯的分析文章（不要表格，要叙事风格），引用欧奈尔名言。记住欧奈尔对指数的核心观点："首先关注大盘指数，当大盘趋势向下时，不要买入任何股票。"

{context}

请用HTML格式输出（h3标题、p段落、blockquote引用名言，无需完整HTML文档结构），风格温暖、直接、像一位严师在跟你对话。"""

    try:
        result = subprocess.run(
            [DEEPSEEK_EXE, 'exec', prompt],
            capture_output=True, text=True, timeout=120, encoding='utf-8', errors='replace',
            cwd=r'D:\dstui', shell=False
        )
        output = result.stdout.strip()
        if not output:
            output = '<p style="color:var(--text-tertiary)">AI 未返回内容，请稍后重试。</p>'
    except subprocess.TimeoutExpired:
        output = '<p style="color:#F44336">AI 分析超时（120秒），请稍后重试。</p>'
    except Exception as e:
        output = f'<p style="color:#F44336">调用失败：{str(e)}</p>'

    return jsonify({
        'index_code': index_code,
        'index_name': name,
        'html': output,
    })


# ══════════════════════════════════════════════════════
# 自选池
# ══════════════════════════════════════════════════════

@discipline_bp.route('/watchlist', methods=['GET', 'POST', 'OPTIONS'])
def api_watchlist():
    if request.method == 'OPTIONS':
        return '', 204
    
    db = get_db()
    
    if request.method == 'POST':
        data = request.get_json()
        stock_code = data.get('stock_code', '').strip()
        stock_name = data.get('stock_name', '')
        
        if not stock_code:
            return jsonify({'error': 'stock_code required'}), 400
        
        # 检查是否已在自选池中（未移除）
        active = db.execute(
            "SELECT stock_code FROM watchlist WHERE stock_code = ? AND removed_at IS NULL", (stock_code,)
        ).fetchone()
        
        if active:
            return jsonify({'error': '已在自选池中', 'stock_code': stock_code}), 409
        
        # 检查是否有旧记录（曾被移除），有则复活，无则插入
        old = db.execute(
            "SELECT stock_code FROM watchlist WHERE stock_code = ?", (stock_code,)
        ).fetchone()
        
        if old:
            db.execute(
                "UPDATE watchlist SET stock_name = ?, source = 'observation', review_status = 'reviewed', removed_at = NULL, added_at = datetime('now','localtime') WHERE stock_code = ?",
                (stock_name, stock_code)
            )
        else:
            db.execute(
                "INSERT INTO watchlist (stock_code, stock_name, source, review_status, added_at) VALUES (?, ?, 'observation', 'reviewed', datetime('now','localtime'))",
                (stock_code, stock_name)
            )
        db.commit()
        return jsonify({'added': True, 'stock_code': stock_code})
    
    # GET
    rows = db.execute(
        "SELECT stock_code, stock_name, source, review_status, manual_reason, added_at, removed_at, note FROM watchlist WHERE removed_at IS NULL ORDER BY added_at DESC"
    ).fetchall()
    
    return jsonify({'items': [dict(r) for r in rows], 'count': len(rows)})


@discipline_bp.route('/watchlist/manual', methods=['POST', 'OPTIONS'])
def api_watchlist_manual():
    if request.method == 'OPTIONS':
        return '', 204
    
    db = get_db()
    data = request.get_json()
    stock_code = data.get('stock_code', '').strip()
    stock_name = data.get('stock_name', '')
    manual_reason = data.get('manual_reason', '')
    
    if not stock_code:
        return jsonify({'error': 'stock_code required'}), 400
    
    existing = db.execute(
        "SELECT stock_code FROM watchlist WHERE stock_code = ? AND removed_at IS NULL", (stock_code,)
    ).fetchone()
    
    if existing:
        return jsonify({'error': '已在自选池中', 'stock_code': stock_code}), 409
    
    # 自动查询名称（股票优先，指数回退）
    if not stock_name:
        row = db.execute("SELECT name FROM stock_basic WHERE stock_code = ?", (stock_code,)).fetchone()
        if row:
            stock_name = row['name']
        else:
            idx = db.execute("SELECT index_name FROM stock_index WHERE index_code = ?", (stock_code,)).fetchone()
            stock_name = idx['index_name'] if idx else stock_code
    
    # 检查是否曾有记录（被移除后重新添加），用 UPSERT 避免 UNIQUE 冲突
    old = db.execute(
        "SELECT stock_code FROM watchlist WHERE stock_code = ?", (stock_code,)
    ).fetchone()
    
    if old:
        db.execute(
            "UPDATE watchlist SET stock_name = ?, source = 'manual', review_status = 'pending_review', manual_reason = ?, removed_at = NULL, added_at = datetime('now','localtime') WHERE stock_code = ?",
            (stock_name, manual_reason, stock_code)
        )
    else:
        db.execute(
            "INSERT INTO watchlist (stock_code, stock_name, source, review_status, manual_reason, added_at) VALUES (?, ?, 'manual', 'pending_review', ?, datetime('now','localtime'))",
            (stock_code, stock_name, manual_reason)
        )
    db.commit()
    return jsonify({'added': True, 'stock_code': stock_code, 'status': 'pending_review'})


@discipline_bp.route('/watchlist/<stock_code>', methods=['DELETE', 'OPTIONS'])
def api_watchlist_remove(stock_code):
    if request.method == 'OPTIONS':
        return '', 204
    
    db = get_db()
    db.execute(
        "UPDATE watchlist SET removed_at = datetime('now','localtime') WHERE stock_code = ? AND removed_at IS NULL",
        (stock_code,)
    )
    db.commit()
    return jsonify({'removed': True, 'stock_code': stock_code})


# ══════════════════════════════════════════════════════
# 买入前检查
# ══════════════════════════════════════════════════════

@discipline_bp.route('/precheck', methods=['POST', 'OPTIONS'])
def api_precheck():
    if request.method == 'OPTIONS':
        return '', 204

    from discipline.precheck import PreTradeChecker

    data = request.get_json()
    stock_code = data.get('stock_code', '').strip()
    buy_qty = int(data.get('buy_qty', 0))
    buy_price = float(data.get('buy_price', 0))
    total_capital = float(data.get('total_capital', 0))

    if not stock_code or buy_qty <= 0 or buy_price <= 0:
        return jsonify({'error': 'stock_code, buy_qty, buy_price 均为必填'}), 422

    db = get_db()
    checker = PreTradeChecker()
    result = checker.check(db, stock_code, buy_qty, buy_price, total_capital)

    return jsonify(result)


# ══════════════════════════════════════════════════════
# 交易记录
# ══════════════════════════════════════════════════════

@discipline_bp.route('/trades', methods=['GET', 'POST', 'OPTIONS'])
def api_trades():
    if request.method == 'OPTIONS':
        return '', 204

    db = get_db()

    # ── POST：录入买入 ──
    if request.method == 'POST':
        data = request.get_json()
        required = ['stock_code', 'buy_date', 'buy_price', 'buy_qty', 'buy_reason', 'stop_loss_price']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'缺少必填字段: {field}'}), 422

        stock_code = data['stock_code'].strip()
        buy_date = data['buy_date']
        buy_price = float(data['buy_price'])
        buy_qty = int(data['buy_qty'])
        buy_reason = data['buy_reason']
        stop_loss_price = float(data['stop_loss_price'])
        total_capital = float(data.get('total_capital', 0))

        # ── 买入前检查清单 ──
        from discipline.precheck import PreTradeChecker
        checker = PreTradeChecker()
        check_result = checker.check(db, stock_code, buy_qty, buy_price, total_capital)

        # 硬拦截未通过 → 422
        hard_fails = [r for r in check_result['results']
                      if r['result'] == 'fail']
        if hard_fails:
            return jsonify({
                'error': '硬拦截未通过',
                'checklist': check_result,
                'fails': [{'rule': r['display_name'], 'message': r['message']} for r in hard_fails]
            }), 422

        # 合规检查未通过 → 422
        compliance_fails = [r for r in check_result['results']
                            if r['result'] == 'compliance_fail']
        if compliance_fails:
            return jsonify({
                'error': '合规检查未通过',
                'checklist': check_result,
                'fails': [{'rule': r['display_name'], 'message': r['message']} for r in compliance_fails]
            }), 422

        stock_name = data.get('stock_name', '')
        asset_type = data.get('asset_type', 'stock')
        buy_emotion = data.get('buy_emotion', '')
        target_period = data.get('target_period', 'medium')
        target_price = data.get('target_price')
        position_pct = data.get('position_pct')
        buy_amount = round(buy_price * buy_qty, 2)
        checklist_json = json.dumps(check_result['results']) if check_result else None

        # 自动获取名称
        if not stock_name:
            stock_name = _lookup_name(db, stock_code, asset_type)

        db.execute("""
            INSERT INTO discipline_trades
            (stock_code, stock_name, asset_type, buy_date, buy_price, buy_qty, buy_amount,
             buy_reason, buy_emotion, target_period, target_price, stop_loss_price,
             position_pct, checklist_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (stock_code, stock_name, asset_type, buy_date, buy_price, buy_qty, buy_amount,
              buy_reason, buy_emotion, target_period, target_price, stop_loss_price,
              position_pct, checklist_json))
        db.commit()

        trade_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        return jsonify({'id': trade_id, 'stock_code': stock_code, 'status': 'holding'}), 201

    # ── GET：交易列表 ──
    status = request.args.get('status', 'all')  # all / holding / closed
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))

    where = ''
    params = []
    if status == 'holding':
        where = 'WHERE sell_date IS NULL'
    elif status == 'closed':
        where = 'WHERE sell_date IS NOT NULL'

    rows = db.execute(
        f"SELECT * FROM discipline_trades {where} ORDER BY buy_date DESC LIMIT ? OFFSET ?",
        (*params, limit, offset)
    ).fetchall()

    total = db.execute(f"SELECT COUNT(*) FROM discipline_trades {where}", params).fetchone()[0]

    return jsonify({
        'trades': [dict(r) for r in rows],
        'total': total,
        'limit': limit,
        'offset': offset
    })


@discipline_bp.route('/trades/<int:trade_id>', methods=['GET', 'PUT', 'OPTIONS'])
def api_trade_detail(trade_id):
    if request.method == 'OPTIONS':
        return '', 204

    db = get_db()

    # ── GET：单笔详情 ──
    if request.method == 'GET':
        row = db.execute("SELECT * FROM discipline_trades WHERE id = ?", (trade_id,)).fetchone()
        if not row:
            return jsonify({'error': '交易记录不存在'}), 404
        return jsonify(dict(row))

    # ── PUT：录入卖出 ──
    if request.method == 'PUT':
        data = request.get_json()

        trade = db.execute("SELECT * FROM discipline_trades WHERE id = ?", (trade_id,)).fetchone()
        if not trade:
            return jsonify({'error': '交易记录不存在'}), 404
        if trade['sell_date']:
            return jsonify({'error': '该交易已卖出'}), 409

        sell_date = data['sell_date']
        sell_price = float(data['sell_price'])
        sell_reason = data.get('sell_reason', '')
        sell_emotion = data.get('sell_emotion', '')

        # 计算盈亏
        pnl_amount = round((sell_price - trade['buy_price']) * trade['buy_qty'], 2)
        pnl_pct = round((sell_price - trade['buy_price']) / trade['buy_price'] * 100, 2)

        # 计算持股天数
        from datetime import datetime
        buy_dt = datetime.strptime(trade['buy_date'], '%Y-%m-%d')
        sell_dt = datetime.strptime(sell_date, '%Y-%m-%d')
        hold_days = (sell_dt - buy_dt).days

        db.execute("""
            UPDATE discipline_trades
            SET sell_date = ?, sell_price = ?, sell_reason = ?, sell_emotion = ?,
                pnl_amount = ?, pnl_pct = ?, hold_days = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
        """, (sell_date, sell_price, sell_reason, sell_emotion,
              pnl_amount, pnl_pct, hold_days, trade_id))
        db.commit()

        return jsonify({
            'id': trade_id,
            'stock_code': trade['stock_code'],
            'pnl_amount': pnl_amount,
            'pnl_pct': pnl_pct,
            'hold_days': hold_days,
            'status': 'closed'
        })


# ══════════════════════════════════════════════════════
# 盈亏汇总
# ══════════════════════════════════════════════════════

@discipline_bp.route('/summary', methods=['GET', 'OPTIONS'])
def api_summary():
    if request.method == 'OPTIONS':
        return '', 204

    db = get_db()

    # 全部已平仓交易
    closed = db.execute("""
        SELECT COUNT(*) AS total_trades,
               SUM(pnl_amount) AS total_pnl,
               AVG(pnl_pct) AS avg_pnl_pct,
               AVG(hold_days) AS avg_hold_days,
               SUM(CASE WHEN pnl_amount > 0 THEN 1 ELSE 0 END) AS win_count,
               SUM(CASE WHEN pnl_amount < 0 THEN 1 ELSE 0 END) AS lose_count
        FROM discipline_trades
        WHERE sell_date IS NOT NULL
    """).fetchone()

    # 持仓中
    holding = db.execute("""
        SELECT COUNT(*) AS count,
               SUM(buy_amount) AS total_cost
        FROM discipline_trades
        WHERE sell_date IS NULL
    """).fetchone()

    # V2: 计算持仓市值（优先手工快照，其次K线表）
    market_value = 0.0
    holding_trades = db.execute("""
        SELECT id, stock_code, buy_qty, asset_type FROM discipline_trades WHERE sell_date IS NULL
    """).fetchall()
    for ht in holding_trades:
        code = ht['stock_code']
        atype = ht['asset_type'] if 'asset_type' in ht.keys() else 'stock'
        # 优先手工录入的快照
        snap = db.execute("""
            SELECT current_price FROM discipline_daily_snapshots
            WHERE trade_id = ? ORDER BY date DESC LIMIT 1
        """, (ht['id'],)).fetchone()
        if snap and snap['current_price'] is not None:
            market_value += snap['current_price'] * ht['buy_qty']
        else:
            rows = _lookup_kline(db, code, atype, 1)
            if rows:
                market_value += rows[0]['close'] * ht['buy_qty']

    win_count = closed['win_count'] or 0
    total_closed = closed['total_trades'] or 0
    win_rate = round(win_count / total_closed * 100, 1) if total_closed > 0 else 0

    # V2 D模块: 盈亏比 = 平均盈利 / |平均亏损|
    avg_win = db.execute("""
        SELECT AVG(pnl_amount) FROM discipline_trades
        WHERE sell_date IS NOT NULL AND pnl_amount > 0
    """).fetchone()[0]
    avg_loss = db.execute("""
        SELECT AVG(pnl_amount) FROM discipline_trades
        WHERE sell_date IS NOT NULL AND pnl_amount < 0
    """).fetchone()[0]
    profit_loss_ratio = 0.0
    if avg_win and avg_loss:
        profit_loss_ratio = round(avg_win / abs(avg_loss), 2)
    elif avg_win:
        profit_loss_ratio = 999.0  # 全胜
    elif avg_loss:
        profit_loss_ratio = -999.0  # 全败


    # V2 D模块: 最大回撤 = 按卖出日期累计盈亏，峰值到谷底最大跌幅
    trades_ordered = db.execute("""
        SELECT pnl_amount FROM discipline_trades
        WHERE sell_date IS NOT NULL
        ORDER BY sell_date ASC
    """).fetchall()
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for t in trades_ordered:
        cumulative += t['pnl_amount']
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    max_drawdown_pct = round(max_drawdown / peak * 100, 1) if peak > 0 else 0.0

    return jsonify({
        'closed': {
            'total_trades': total_closed,
            'total_pnl': round(closed['total_pnl'] or 0, 2),
            'avg_pnl_pct': round(closed['avg_pnl_pct'] or 0, 2),
            'avg_hold_days': round(closed['avg_hold_days'] or 0, 1),
            'win_count': win_count,
            'lose_count': closed['lose_count'] or 0,
            'win_rate': win_rate,
            'profit_loss_ratio': profit_loss_ratio,
            'max_drawdown_pct': max_drawdown_pct,
            'max_drawdown': round(max_drawdown, 2),
        },
        'holding': {
            'count': holding['count'] or 0,
            'total_cost': round(holding['total_cost'] or 0, 2),
            'market_value': round(market_value, 2),
        }
    })


# ══════════════════════════════════════════════════════
# 检查清单模板
# ══════════════════════════════════════════════════════

DEFAULT_CHECKLIST = {
    'items': [
        {'id': 'market_phase', 'name': '大盘环境', 'auto': True, 'required': True},
        {'id': 'canslim',     'name': 'CAN SLIM 评分', 'auto': True, 'required': False},
        {'id': 'rs_strength', 'name': 'RS 强度', 'auto': True, 'required': False},
        {'id': 'position_limit', 'name': '单票仓位上限', 'auto': True, 'required': True},
        {'id': 'total_position', 'name': '总仓位上限', 'auto': True, 'required': True},
        {'id': 'industry',    'name': '行业集中度', 'auto': True, 'required': False},
        {'id': 'stop_loss',   'name': '止损价设置', 'auto': False, 'required': True},
        {'id': 'buy_reason',  'name': '买入理由（≥20字）', 'auto': False, 'required': True},
    ]
}


@discipline_bp.route('/checklist', methods=['GET', 'PUT', 'OPTIONS'])
def api_checklist():
    if request.method == 'OPTIONS':
        return '', 204

    db = get_db()

    if request.method == 'PUT':
        data = request.get_json()
        if data and 'items' in data:
            db.execute(
                "INSERT OR REPLACE INTO discipline_rules_config (rule_name, display_name, category, parameters_json) VALUES (?,?,?,?)",
                ('checklist_template', '检查清单模板', 'pre_trade', json.dumps(data))
            )
            db.commit()
        return jsonify({'updated': True})

    # GET: 读取模板
    row = db.execute(
        "SELECT parameters_json FROM discipline_rules_config WHERE rule_name = 'checklist_template'"
    ).fetchone()

    if row and row['parameters_json']:
        try:
            template = json.loads(row['parameters_json'])
        except json.JSONDecodeError:
            template = DEFAULT_CHECKLIST
    else:
        template = DEFAULT_CHECKLIST

    return jsonify(template)


# ══════════════════════════════════════════════════════
# V2: 配置管理
# ══════════════════════════════════════════════════════

@discipline_bp.route('/config', methods=['GET', 'PUT', 'OPTIONS'])
def api_config():
    """获取/设置知行系统配置（总资产等）"""
    if request.method == 'OPTIONS':
        return '', 204

    db = get_db()

    if request.method == 'PUT':
        data = request.get_json()
        key = data.get('key', '')
        value = data.get('value')
        if not key:
            return jsonify({'error': 'key required'}), 400

        db.execute("""
            INSERT OR REPLACE INTO discipline_rules_config (rule_name, display_name, category, parameters_json)
            VALUES (?, ?, 'system_config', ?)
        """, (key, key, json.dumps({'value': value})))
        db.commit()
        return jsonify({'updated': True, 'key': key, 'value': value})

    # GET: 返回所有系统配置
    rows = db.execute("""
        SELECT rule_name, parameters_json FROM discipline_rules_config
        WHERE category = 'system_config'
    """).fetchall()

    config = {}
    for r in rows:
        try:
            params = json.loads(r['parameters_json'])
            config[r['rule_name']] = params.get('value')
        except (json.JSONDecodeError, TypeError):
            pass

    return jsonify(config)


# ══════════════════════════════════════════════════════
# V2: 持仓监控
# ══════════════════════════════════════════════════════

@discipline_bp.route('/monitor', methods=['GET', 'OPTIONS'])
def api_monitor():
    """获取持仓监控状态"""
    if request.method == 'OPTIONS':
        return '', 204

    from discipline.monitoring import get_holdings_status
    db = get_db()
    holdings, total_mv = get_holdings_status(db)
    return jsonify({'holdings': holdings, 'count': len(holdings), 'total_market_value': total_mv})


@discipline_bp.route('/monitor/scan', methods=['POST', 'OPTIONS'])
def api_monitor_scan():
    """手动触发持仓扫描"""
    if request.method == 'OPTIONS':
        return '', 204

    from discipline.monitoring import run_scan
    db = get_db()
    stock_code = request.args.get('stock_code')
    alerts = run_scan(db, target_stock=stock_code)
    return jsonify({'alerts_count': len(alerts), 'alerts': alerts})


@discipline_bp.route('/monitor/alerts/<int:alert_id>/acknowledge', methods=['PUT', 'OPTIONS'])
def api_acknowledge_alert(alert_id):
    """确认告警"""
    if request.method == 'OPTIONS':
        return '', 204

    db = get_db()
    alert = db.execute("SELECT * FROM discipline_alerts WHERE id = ?", (alert_id,)).fetchone()
    if not alert:
        return jsonify({'error': '告警不存在'}), 404

    db.execute("UPDATE discipline_alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
    db.commit()
    return jsonify({'acknowledged': True, 'alert_id': alert_id})


@discipline_bp.route('/monitor/price', methods=['PUT', 'OPTIONS'])
def api_update_price():
    """手工更新持仓现价（用于无K线数据的指数）"""
    if request.method == 'OPTIONS':
        return '', 204
    db = get_db()
    data = request.get_json()
    trade_id = data.get('trade_id')
    price = data.get('price')
    if not trade_id or price is None:
        return jsonify({'error': 'trade_id and price required'}), 400

    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    db.execute("""
        INSERT OR REPLACE INTO discipline_daily_snapshots (date, trade_id, current_price)
        VALUES (?, ?, ?)
    """, (today, trade_id, float(price)))
    db.commit()
    return jsonify({'updated': True, 'trade_id': trade_id, 'date': today, 'price': float(price)})


# ══════════════════════════════════════════════════════
# V2: 辅助函数
# ══════════════════════════════════════════════════════

def _compute_vol_structure(db, stock_code, klines):
    """计算过去25日吸筹/出货日数量。价涨量增=吸筹日，价跌量增=出货日。"""
    if not klines or len(klines) < 25:
        return {'accumulation_days': 0, 'distribution_days': 0, 'sample_days': len(klines)}

    recent = klines[-25:]
    acc_days = 0
    dist_days = 0
    for i, k in enumerate(recent):
        if i == 0:
            continue
        prev = recent[i - 1]
        price_up = k['close'] > prev['close']
        price_down = k['close'] < prev['close']
        vol_up = k['volume'] > prev['volume'] if prev['volume'] > 0 else False

        if price_up and vol_up:
            acc_days += 1
        elif price_down and vol_up:
            dist_days += 1

    return {
        'accumulation_days': acc_days,
        'distribution_days': dist_days,
        'sample_days': len(recent) - 1,
    }


def _compute_ideal_buy(signals, klines):
    """计算理想买点：优先形态引擎 breakout_price，回退200天最高价。"""
    if not klines:
        return {'price': None, 'source': 'none', 'warning': True}

    # 1. 尝试从信号中找 breakout_price
    for s in (signals or []):
        bp = s.get('breakout_price') or s.get('buy_point')
        if bp:
            return {
                'price': round(bp, 2),
                'source': 'pattern_engine',
                'signal_type': s.get('type', s.get('pattern', 'unknown')),
                'warning': False,
            }

    # 2. 回退：200天最高价
    lookback = min(200, len(klines))
    highs = [k['high'] for k in klines[-lookback:]]
    high_200 = max(highs) if highs else klines[-1]['close']

    return {
        'price': round(high_200, 2),
        'source': '200d_high',
        'warning': True,
        'warning_msg': '⚠ 基于简单前高，未结合形态分析',
    }


def _lookup_name(db, code, asset_type='stock'):
    """统一名称查询：按类型优先，回退另一类型"""
    if asset_type == 'index':
        row = db.execute("SELECT index_name FROM stock_index WHERE index_code = ?", (code,)).fetchone()
        if row and row['index_name']:
            return row['index_name']
        row = db.execute("SELECT name FROM stock_basic WHERE stock_code = ?", (code,)).fetchone()
        if row and row['name']:
            return row['name']
    else:
        row = db.execute("SELECT name FROM stock_basic WHERE stock_code = ?", (code,)).fetchone()
        if row and row['name']:
            return row['name']
        row = db.execute("SELECT index_name FROM stock_index WHERE index_code = ?", (code,)).fetchone()
        if row and row['index_name']:
            return row['index_name']
    return code


def _lookup_kline(db, code, asset_type='stock', limit=400):
    """统一K线查询：按类型优先，回退另一类型"""
    if asset_type == 'index':
        rows = db.execute("""
            SELECT date, open, high, low, close, volume, amount, change AS change_pct
            FROM index_daily_kline WHERE stock_code = ? ORDER BY date DESC LIMIT ?
        """, (code, limit)).fetchall()
        if rows:
            return rows
        rows = db.execute("""
            SELECT date, open, high, low, close, volume, amount, change_pct
            FROM daily_kline WHERE stock_code = ? ORDER BY date DESC LIMIT ?
        """, (code, limit)).fetchall()
    else:
        rows = db.execute("""
            SELECT date, open, high, low, close, volume, amount, change_pct
            FROM daily_kline WHERE stock_code = ? ORDER BY date DESC LIMIT ?
        """, (code, limit)).fetchall()
        if rows:
            return rows
        rows = db.execute("""
            SELECT date, open, high, low, close, volume, amount, change AS change_pct
            FROM index_daily_kline WHERE stock_code = ? ORDER BY date DESC LIMIT ?
        """, (code, limit)).fetchall()
    return rows or []
