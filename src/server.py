#!/usr/bin/env python3
"""
O'Neil 信号回测框架 — Flask API Server (Multi-signal)
端口: 8788
信号: distribution_day | (future: follow_through_day, accumulation, breakout, ...)
"""
import json, sqlite3, math, os, sys, re
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, g

# Add parent to path for detector imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from detectors.distribution_day import detect as detect_distribution_days
from detectors.follow_through_day import detect as detect_follow_through_days
from detectors.accumulation_day import detect as detect_accumulation_days
from detectors.index_ad import detect as detect_index_ad
from detectors.divergence import (
    compute_rsi, compute_macd,
    detect_volume_price_divergence, detect_rsi_divergence,
    detect_macd_divergence, detect_breadth_divergence,
    confirm_divergence, compute_resonance
)
from engine_registry import discover_engines, get_engine_list, run_all_engines
from scanners.recommend import generate as generate_recommendation
from scanners.canslim_score import score_stock as canslim_score_stock, load_params as canslim_load_params
from discipline.trades_api import discipline_bp
import numpy as np
import talib

# ── Config ───────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)  # ~/investment-system/
CONFIG_DIR = os.path.join(PROJECT_DIR, 'config', 'market')
INDEX_RS_CONFIG = os.path.join(PROJECT_DIR, 'config', 'index_style.yaml')
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
DATA_DIR = os.path.join(PROJECT_DIR, 'data')

app = Flask(__name__)
app.register_blueprint(discipline_bp)

# CORS — 允许前端独立部署（http.server :8772）跨域访问 API
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

# ═══════════════════════════════════════════════
# Database helpers
# ═══════════════════════════════════════════════

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA synchronous=NORMAL")
        g.db.execute("PRAGMA cache_size=-64000")
        g.db.execute("PRAGMA busy_timeout=5000")
        g.db.execute("PRAGMA foreign_keys=ON")
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db: db.close()

def init_schema():
    db = sqlite3.connect(DB_PATH)
    schema_path = os.path.join(PROJECT_DIR, 'data', 'schema.sql')
    with open(schema_path, encoding='utf-8') as f:
        db.executescript(f.read())
    # 扩展 watchlist 表（安全添加列，已存在则忽略）
    for col, col_def in [
        ('source', "TEXT DEFAULT 'observation'"),
        ('review_status', "TEXT DEFAULT 'reviewed'"),
        ('manual_reason', 'TEXT'),
    ]:
        try:
            db.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError:
            pass  # 列已存在
    # V2: 添加 signals_json 列到观察池
    try:
        db.execute("ALTER TABLE discipline_observation_pool ADD COLUMN signals_json TEXT")
    except sqlite3.OperationalError:
        pass
    # V2: 添加 asset_type 列到交易记录（区分股票/指数）
    try:
        db.execute("ALTER TABLE discipline_trades ADD COLUMN asset_type TEXT DEFAULT 'stock'")
    except sqlite3.OperationalError:
        pass
    # 自选池日报：落盘表 + 查看锚点
    db.execute("CREATE TABLE IF NOT EXISTS watchlist_report_daily (date TEXT PRIMARY KEY, report_json TEXT, created_at TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS watchlist_review_state (key TEXT PRIMARY KEY, value TEXT)")
    # 交易成本调整审计（透明可追溯）
    db.execute("""CREATE TABLE IF NOT EXISTS discipline_trade_adjustments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER, old_cost REAL, new_cost REAL,
        reason TEXT, adjusted_at TEXT)""")
    db.commit()
    # V2: 添加 buy_signal_date 列到精选快照
    try:
        db.execute("ALTER TABLE discipline_screening_daily ADD COLUMN buy_signal_date TEXT")
    except sqlite3.OperationalError:
        pass
    db.commit()
    db.close()

# ═══════════════════════════════════════════════
# Technical indicators (computed on the fly)
# ═══════════════════════════════════════════════

def compute_ma(closes, window):
    if len(closes) < window: return None
    return sum(closes[-window:]) / window

def compute_volatility(changes, window):
    if len(changes) < window: return None
    recent = changes[-window:]
    mean = sum(recent) / len(recent)
    variance = sum((x - mean) ** 2 for x in recent) / len(recent)
    return math.sqrt(variance)

# ═══════════════════════════════════════════════
# K-line enrichment (shared by all signals)
# ═══════════════════════════════════════════════

def enrich_klines(rows):
    """Add change_pct, prev_close, K-line positions, MAs, volatility to raw DB rows."""
    klines = []
    prev_close = None
    changes_pct = []

    for r in rows:
        d = dict(r)
        if prev_close and prev_close != 0:
            d['change_pct'] = round((d['close'] - prev_close) / prev_close * 100, 4)
        else:
            d['change_pct'] = 0.0
        d['prev_close'] = prev_close or d['close']
        prev_close = d['close']
        changes_pct.append(d['change_pct'])

        hl_range = d['high'] - d['low']
        if hl_range > 0:
            d['close_position'] = round((d['close'] - d['low']) / hl_range * 100, 1)
            d['upper_shadow_pct'] = round((d['high'] - max(d['close'], d['open'])) / hl_range * 100, 1)
            d['lower_shadow_pct'] = round((min(d['close'], d['open']) - d['low']) / hl_range * 100, 1)
            d['body_pct'] = round(abs(d['close'] - d['open']) / hl_range * 100, 1)
        else:
            d['close_position'] = 50
            d['upper_shadow_pct'] = d['lower_shadow_pct'] = d['body_pct'] = 0

        klines.append(d)

    closes = [k['close'] for k in klines]
    volumes = [k['volume'] for k in klines]

    for i, k in enumerate(klines):
        if i > 0 and volumes[i-1] > 0:
            k['volume_ratio'] = round(volumes[i] / volumes[i-1], 4)
        else:
            k['volume_ratio'] = 1.0
        if i >= 4:
            ma5v = sum(volumes[i-4:i+1]) / 5
            k['volume_ratio_ma5'] = round(volumes[i] / ma5v, 4) if ma5v > 0 else 1.0
        else:
            k['volume_ratio_ma5'] = 1.0

        w = i + 1
        k['ma5']   = round(compute_ma(closes[:i+1], min(5, w)), 2) if w >= 5 else None
        k['ma10']  = round(compute_ma(closes[:i+1], min(10, w)), 2) if w >= 10 else None
        k['ma20']  = round(compute_ma(closes[:i+1], min(20, w)), 2) if w >= 20 else None
        k['ma50']  = round(compute_ma(closes[:i+1], min(50, w)), 2) if w >= 50 else None
        k['ma120'] = round(compute_ma(closes[:i+1], min(120, w)), 2) if w >= 120 else None
        k['ma250'] = round(compute_ma(closes[:i+1], min(250, w)), 2) if w >= 250 else None
        k['vol_5d']  = round(compute_volatility(changes_pct[:i+1], min(5, w)), 4) if w >= 5 else None
        k['vol_10d'] = round(compute_volatility(changes_pct[:i+1], min(10, w)), 4) if w >= 10 else None
        k['vol_20d'] = round(compute_volatility(changes_pct[:i+1], min(20, w)), 4) if w >= 20 else None

    return klines

# ═══════════════════════════════════════════════
# YAML config loader (simple, no PyYAML needed)
# ═══════════════════════════════════════════════

def load_config(signal_type):
    """Load YAML config file as a flat dict.
    Search order: config/market/ → config/ → return empty
    """
    # Paths to try, in order
    candidates = [
        os.path.join(CONFIG_DIR, f'{signal_type}.yaml'),           # config/market/
        os.path.join(PROJECT_DIR, 'config', f'{signal_type}.yaml'), # config/
    ]
    path = None
    for p in candidates:
        if os.path.exists(p):
            path = p
            break
    if not path:
        return {}
    # Try PyYAML first (handles nested structures), fall back to simple parser
    if HAS_YAML:
        with open(path, encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    # Simple YAML parser fallback
    with open(path, encoding='utf-8') as f:
        content = f.read()
    # Parse simple YAML (no nested structures beyond 1 level)
    config = {}
    current_section = None
    for line in content.split('\n'):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if ':' in stripped and not stripped.startswith(' ') and not stripped.startswith('-'):
            # Top-level key
            key = stripped.split(':')[0].strip()
            val = stripped.split(':', 1)[1].strip()
            if val:
                config[key] = _parse_yaml_val(val)
            else:
                current_section = key
                config[key] = {}
        elif current_section and ':' in stripped:
            key = stripped.split(':')[0].strip()
            val = stripped.split(':', 1)[1].strip()
            if val:
                config[current_section][key] = _parse_yaml_val(val)
    return config

def _parse_yaml_val(s):
    s = s.strip().strip('"').strip("'")
    if s.lower() in ('true', 'yes'): return True
    if s.lower() in ('false', 'no'): return False
    try: return float(s) if '.' in s else int(s)
    except: return s

def save_config(signal_type, raw_yaml):
    """Save config to YAML file. Accepts YAML or JSON, always writes YAML.
    Preserves existing location: config/ → config/market/ → default config/market/
    """
    candidates = [
        os.path.join(PROJECT_DIR, 'config', f'{signal_type}.yaml'),   # config/
        os.path.join(CONFIG_DIR, f'{signal_type}.yaml'),               # config/market/
    ]
    path = None
    for p in candidates:
        if os.path.exists(p):
            path = p
            break
    if not path:
        # New file: prefer config/ for non-market types
        if signal_type in ('canslim_scorecard',):
            path = candidates[0]  # config/
        else:
            path = candidates[1]  # config/market/

    # If the content is JSON, convert to YAML
    content = raw_yaml.strip()
    if content.startswith('{') or content.startswith('['):
        try:
            data = json.loads(content)
            # If frontend wrapped in {signal_type: ...}, unwrap
            if isinstance(data, dict) and signal_type in data:
                data = data[signal_type]
            if HAS_YAML:
                raw_yaml = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except json.JSONDecodeError:
            pass  # Not valid JSON, keep as-is

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(raw_yaml)

# ═══════════════════════════════════════════════
# API: GET /api/indices
# ═══════════════════════════════════════════════

INDEX_NAMES = {
    '000001': '上证综指', '000016': '上证50', '000300': '沪深300',
    '000688': '科创50', '000852': '中证1000', '000905': '中证500',
    '000985': '中证全指', '399001': '深证成指', '399006': '创业板指',
    '399673': '创业板50', '399986': '中证银行', '399995': '基建工程',
    '399998': '中证煤炭', '931008': '中证红利', 'H11057': '中证全债',
}

@app.route('/api/indices')
def api_indices():
    db = get_db()
    rows = db.execute("""SELECT DISTINCT stock_code FROM index_daily_kline WHERE kline_type='normal' ORDER BY stock_code""").fetchall()
    return jsonify([{'code': r['stock_code'], 'name': INDEX_NAMES.get(r['stock_code'], r['stock_code'])} for r in rows])

# ═══════════════════════════════════════════════
# API: GET /api/kline
# ═══════════════════════════════════════════════

@app.route('/api/kline')
def api_kline():
    stock_code = request.args.get('stock_code', '000985')
    start = request.args.get('start', '2020-01-01')
    end = request.args.get('end', '2024-12-31')

    db = get_db()
    rows = db.execute("""SELECT stock_code, date, open, high, low, close, volume, amount, change
        FROM index_daily_kline WHERE stock_code=? AND kline_type='normal'
        AND date >= date(?,'-300 days') AND date <= ? ORDER BY date""",
        (stock_code, start, end)).fetchall()

    klines = enrich_klines(rows)
    klines = [k for k in klines if k['date'] >= start]
    return jsonify(klines)

# ═══════════════════════════════════════════════
# API: POST /api/backtest (distribution_day)
# ═══════════════════════════════════════════════

@app.route('/api/backtest', methods=['POST', 'OPTIONS'])
def api_backtest():
    if request.method == 'OPTIONS': return '', 204

    data = request.get_json()
    stock_code = data.get('stock_code', '000985')
    start = data.get('start') or data.get('start_date', '2024-01-01')
    end = data.get('end') or data.get('end_date', '2024-12-31')
    signal_type = data.get('signal_type', 'distribution_day')
    params = data.get('params', {})

    db = get_db()
    rows = db.execute("""SELECT stock_code, date, open, high, low, close, volume, amount, change
        FROM index_daily_kline WHERE stock_code=? AND kline_type='normal'
        AND date >= date(?,'-365 days') AND date <= date(?,'+365 days') ORDER BY date""",
        (stock_code, start, end)).fetchall()

    klines = enrich_klines(rows)
    klines_for_chart = klines
    klines_in_range = [k for k in klines if k['date'] >= start and k['date'] <= end]

    # Route to detector
    if signal_type == 'follow_through_day':
        dist_signals = detect_distribution_days(klines_in_range, params) if params.get('use_distribution_signals', True) else []
        rally_attempts, signals, failed_ftds = detect_follow_through_days(klines, params, dist_signals)
        signals = [s for s in signals if start <= s.get('date','') <= end]
        failed_ftds = [s for s in failed_ftds if start <= s.get('date','') <= end]
        rally_attempts = [r for r in rally_attempts if start <= r.get('date','') <= end]
    elif signal_type == 'accumulation_day':
        dist_signals = detect_distribution_days(klines_in_range, params) if params.get('use_distribution_signals', True) else []
        rally_attempts, acc_signals = detect_accumulation_days(klines, params, dist_signals)
        signals = [s for s in acc_signals if start <= s.get('date','') <= end]
        rally_attempts = [r for r in rally_attempts if start <= r.get('date','') <= end]
        failed_ftds = []
    else:
        # Default: distribution_day (also works for future signals)
        signals = detect_distribution_days(klines_in_range, params)
        rally_attempts = []
        failed_ftds = []

    # Stats
    total = len(klines_in_range)
    signal_count = len(signals)
    type_counts = {}
    for s in signals: type_counts[s.get('signal_type', s.get('ftd_type', 'standard'))] = type_counts.get(s.get('signal_type', s.get('ftd_type', 'standard')), 0) + 1
    weighted = sum(s.get('weight', 1) for s in signals)

    return jsonify({
        'stock_code': stock_code, 'start': start, 'end': end,
        'signal_type': signal_type, 'params': params,
        'klines': klines_for_chart, 'signals': signals,
        'rally_attempts': rally_attempts,
        'failed_ftds': failed_ftds,
        'stats': {
            'total_days': total, 'signal_count': signal_count,
            'standard_count': type_counts.get('standard', 0),
            'heavy_count': type_counts.get('heavy', 0),
            'special_count': type_counts.get('special', 0),
            'reversal_count': type_counts.get('reversal', 0),
            'ftd_normal': type_counts.get('normal', 0),
            'ftd_volume': type_counts.get('volume', 0),
            'ftd_mega': type_counts.get('mega', 0),
            'weighted_count': weighted,
            'rally_count': len(rally_attempts),
            'failed_ftd_count': len(failed_ftds),
            'rally_attempts_count': len(rally_attempts),
            'accumulation_count': len(signals) if signal_type == 'accumulation' else 0,
            'ftd_count': len(signals) if signal_type == 'follow_through_day' else 0,
        }
    })

# ═══════════════════════════════════════════════
# API: POST /api/config (GET/POST)
# ═══════════════════════════════════════════════

@app.route('/api/config', methods=['GET', 'POST', 'OPTIONS'])
def api_config():
    if request.method == 'OPTIONS': return '', 204

    signal_type = request.args.get('signal_type', 'distribution_day')

    if request.method == 'POST':
        raw = request.get_data(as_text=True)
        if raw:
            save_config(signal_type, raw)
            return jsonify({'ok': True})
        return jsonify({'ok': False, 'error': 'empty body'}), 400

    # GET
    config = load_config(signal_type)
    return jsonify(config)

def get_capital_flow_data(target_date, db):
    """获取指数资金活跃度 TOP 10 + 行业组均值"""
    result = {'top_10d': [], 'top_65d': [], 'top_250d': [], 'groups': {}}
    try:
        rows = db.execute(
            "SELECT * FROM index_capital_flow_daily WHERE date=? ORDER BY score_10d DESC", (target_date,)
        ).fetchall()
        if not rows:
            return result
        
        # 构建名称映射
        import yaml
        yaml_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'index_style.yaml')
        with open(yaml_path, encoding='utf-8') as f:
            style = yaml.safe_load(f)
        name_map = {}
        for item in style['categories'].get('sector_l2', []): name_map[item['code']] = item['name']
        for item in style['categories'].get('thematic', []): name_map[item['code']] = item['name']
        for item in style['categories'].get('market', []): name_map[item['code']] = item['name']
        for item in style['categories'].get('strategy', []): name_map[item['code']] = item['name']
        
        # 各窗口 TOP 10（含名称）
        for wl, sk in [('top_10d', 'score_10d'), ('top_65d', 'score_65d'), ('top_250d', 'score_250d')]:
            top = sorted(rows, key=lambda r: r[sk] or 0, reverse=True)[:10]
            result[wl] = [{'code': r['stock_code'], 'name': name_map.get(r['stock_code'], r['stock_code']), 'score': r[sk], 'label': r['flow_label']} for r in top]
        
        l2_codes = {item['code'] for item in style['categories'].get('sector_l2', [])}
        theme_codes = {item['code'] for item in style['categories'].get('thematic', [])}
        
        # 从 market_health_sector_daily 取组信息
        groups = db.execute("SELECT * FROM market_health_sector_daily WHERE date=?", (target_date,)).fetchall()
        for g in groups:
            gn = g['group_name']
            pool_key = gn.rsplit('_', 1)[0]
            suffix = gn.rsplit('_', 1)[1]
            pool_set = l2_codes if pool_key == 'l2' else theme_codes
            
            if suffix == 'strong':
                cond = lambda rs: rs >= 75
            elif suffix == 'weak':
                cond = lambda rs: rs < 30
            else:
                cond = lambda rs: 30 <= rs < 75
            
            # 从 index_rs_daily 获取该组指数
            idx_codes = list(pool_set)
            if not idx_codes:
                continue
            ph = ','.join('?' * len(idx_codes))
            rs_rows = db.execute(f"SELECT stock_code, rs_20 FROM index_rs_daily WHERE date=? AND stock_code IN ({ph})", (target_date, *idx_codes)).fetchall()
            group_codes = [r['stock_code'] for r in rs_rows if cond(r['rs_20'])]
            
            if not group_codes:
                continue
            ph2 = ','.join('?' * len(group_codes))
            flow_rows = db.execute(f"""SELECT AVG(score_10d) as s10, AVG(score_65d) as s65, AVG(score_250d) as s250
                FROM index_capital_flow_daily WHERE date=? AND stock_code IN ({ph2})""", (target_date, *group_codes)).fetchone()
            if flow_rows:
                result['groups'][gn] = {
                    's10': round(flow_rows['s10']) if flow_rows['s10'] else 0,
                    's65': round(flow_rows['s65']) if flow_rows['s65'] else 0,
                    's250': round(flow_rows['s250']) if flow_rows['s250'] else 0,
                }
    except Exception as e:
        print(f"[capital_flow] error: {e}", flush=True)
    return result


# ═══════════════════════════════════════════════
# API: GET /api/market-health
# ═══════════════════════════════════════════════

@app.route('/api/market-health')
def api_market_health():
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    db = get_db()

    row = db.execute(
        "SELECT * FROM market_health_daily WHERE date <= ? ORDER BY date DESC LIMIT 1",
        (target_date,)
    ).fetchone()

    if not row:
        return jsonify({'status': 'no_data', 'date': target_date, 'total_score': 0, 'indicators': [], 'rotations': []})

    indicators = [
        {'key': 'ma50_above',   'value': row['ma50_above_value'],   'score': row['ma50_above_score'],   'detail': ''},
        {'key': 'hl_ratio',     'value': row['hl_ratio_value'],     'score': row['hl_ratio_score'],     'detail': ''},
        {'key': 'ad_ratio',     'value': row['ad_ratio_value'],     'score': row['ad_ratio_score'],
         'today': row['ad_ratio_today'] if 'ad_ratio_today' in row.keys() else None, 'detail': ''},
        {'key': 'vol_breakout', 'value': row['vol_breakout_value'], 'score': row['vol_breakout_score'],  'detail': ''},
        {'key': 'margin_5d',    'value': row['margin_5d_value'],    'score': row['margin_5d_score'],     'detail': ''},
        {'key': 'sector_rot',   'value': row['sector_rot_score'],   'score': row['sector_rot_score'],   'detail': ''},
        {'key': 'fear_greed',   'value': row['fear_greed_value'],   'score': row['fear_greed_score'],   'detail': ''},
    ]

    # 涨跌停家数（情绪补充指标，不计分）
    limit_up = row['limit_up_count'] if 'limit_up_count' in row.keys() else None
    limit_down = row['limit_down_count'] if 'limit_down_count' in row.keys() else None
    if limit_up is not None:
        indicators.append({'key': 'limit_up_down', 'value': limit_up, 'score': 0,
                           'detail': f'涨停 {limit_up} / 跌停 {limit_down}',
                           'limit_up': limit_up, 'limit_down': limit_down})

    rot_rows = db.execute(
        "SELECT * FROM market_rotation_daily WHERE date = ?", (row['date'],)
    ).fetchall()

    rotations = []
    pool_icons = {"一级行业": "🏭", "二级行业": "🔧", "主题指数": "🎯", "策略指数": "🧩"}
    for r in rot_rows:
        rot = {
            'name': r['pool'],
            'icon': pool_icons.get(r['pool'], '📦'),
            'method': r['method'],
            'value': r['value'],
            'participates': r['method'] == 'overlap',
            'count': '',
        }
        if r['top5_current']:
            try:
                rot['top5_current'] = json.loads(r['top5_current'])
                rot['top5_last'] = json.loads(r.get('top5_last') or '[]')
                if r['method'] == 'overlap':
                    curr_set = set(rot['top5_current'])
                    last_set = set(rot['top5_last'])
                    rot['top5_overlap'] = list(curr_set & last_set)
            except Exception:
                pass
        rotations.append(rot)

    # 行业分组健康分（v3.0）
    group_rows = db.execute(
        "SELECT * FROM market_health_sector_daily WHERE date = ? ORDER BY group_name", (row['date'],)
    ).fetchall()
    
    # 读取 index_style.yaml 获取池代码到名称的映射
    import yaml
    yaml_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'index_style.yaml')
    with open(yaml_path, encoding='utf-8') as f:
        style = yaml.safe_load(f)
    l2_names = {item['code']: item['name'] for item in style['categories'].get('sector_l2', [])}
    theme_names = {item['code']: item['name'] for item in style['categories'].get('thematic', [])}
    strategy_names = {item['code']: item['name'] for item in style['categories'].get('strategy', [])}
    
    groups = []
    for g in group_rows:
        gn = g['group_name']
        pool_key = gn.rsplit('_', 1)[0]
        pool_names = l2_names if pool_key == 'l2' else (theme_names if pool_key == 'theme' else strategy_names)
        
        # 计算该组的技术健康度
        codes = list(pool_names.keys())
        placeholders = ','.join('?' * len(codes))
        
        # 确定 RS 条件
        suffix = gn.rsplit('_', 1)[1]
        if suffix == 'strong':
            rs_cond = 'rs_20 >= 75'
        elif suffix == 'weak':
            rs_cond = 'rs_20 < 30'
        else:
            rs_cond = 'rs_20 >= 30 AND rs_20 < 75'
        
        idx_rows = db.execute(f"""
            SELECT r.rs_20, r.rs_60, r.ma50, r.ma200,
                   r.close, r.ret_20
            FROM index_rs_daily r
            WHERE r.date = ? AND r.stock_code IN ({placeholders}) AND ({rs_cond})
        """, (row['date'], *codes)).fetchall()
        
        tech = {
            'above_ma50': sum(1 for r in idx_rows if r['ma50'] and r['close'] > r['ma50']),
            'above_ma200': sum(1 for r in idx_rows if r['ma200'] and r['close'] > r['ma200']),
            'avg_rs_20': round(sum(r['rs_20'] for r in idx_rows) / len(idx_rows), 1) if idx_rows else 0,
            'positive_20d': sum(1 for r in idx_rows if r['ret_20'] and r['ret_20'] > 0),
        }
        
        groups.append({
            'group_name': gn,
            'group_label': g['group_label'],
            'indices_count': g['indices_count'],
            'stocks_count': g['stocks_count'],
            'total_score': g['total_score'],
            'rating': g['rating'],
            'position': g['position'],
            'score_vs_market': g['score_vs_market'],
            'tech': tech,
        })

    # ── 当日行业分组快照（v2.6）──
    # 基于当日涨跌幅的池内百分位排名，实时计算不存库
    def _groups_d1(target_date):
        pools = [
            ('l2', 'L2', list(l2_names.keys())),
            ('theme', '主题', list(theme_names.keys())),
            ('strategy', '策略', list(strategy_names.keys())),
        ]
        # 先取前一交易日，用于反推精确涨跌幅（index_daily_kline.change 仅1%粒度）
        prev_date_row = db.execute(
            "SELECT MAX(date) as d FROM index_daily_kline WHERE date < ?", (target_date,)
        ).fetchone()
        prev_date = prev_date_row['d'] if prev_date_row else None
        prev_close_map = {}
        if prev_date:
            prev_rows = db.execute(
                "SELECT stock_code, close FROM index_daily_kline WHERE date = ?", (prev_date,)
            ).fetchall()
            prev_close_map = {r['stock_code']: r['close'] for r in prev_rows}

        result = []
        for pool_key, pool_label, pool_codes in pools:
            if not pool_codes:
                continue
            ph = ','.join('?' * len(pool_codes))
            rows = db.execute(f"""
                SELECT stock_code, close, change FROM index_daily_kline
                WHERE date = ? AND stock_code IN ({ph}) AND close IS NOT NULL
            """, (target_date, *pool_codes)).fetchall()
            if not rows:
                continue
            # 用 close 反推精确涨跌幅：change 字段只有 1% 粒度
            # 优先用 (close/prev_close-1)，prev 缺失时退回 change 字段
            enriched = []
            for r in rows:
                prev = prev_close_map.get(r['stock_code'])
                if prev and prev > 0:
                    chg = (r['close'] / prev) - 1
                else:
                    chg = r['change'] or 0
                enriched.append({'stock_code': r['stock_code'], 'change': chg})
            # 池内百分位排名：把涨跌幅从小到大排序，取每只的分位
            sorted_rows = sorted(enriched, key=lambda r: r['change'])
            n = len(sorted_rows)
            rank_map = {}
            for idx, r in enumerate(sorted_rows):
                # 百分位 = 排名/(n-1)*100，排名0=最弱，n-1=最强
                rank_map[r['stock_code']] = (idx / (n - 1)) * 100 if n > 1 else 50.0
            # 分组
            groups_d1 = {'strong': [], 'mid': [], 'weak': []}
            for r in enriched:
                score = rank_map.get(r['stock_code'], 50)
                if score >= 75:
                    groups_d1['strong'].append(r)
                elif score >= 30:
                    groups_d1['mid'].append(r)
                else:
                    groups_d1['weak'].append(r)
            for suffix, label_suffix, grp in [
                ('strong', '强势', groups_d1['strong']),
                ('mid', '中性', groups_d1['mid']),
                ('weak', '弱势', groups_d1['weak']),
            ]:
                if not grp:
                    continue
                avg_chg = sum(r['change'] for r in grp) / len(grp)
                up = sum(1 for r in grp if r['change'] > 0)
                # tech：MA50 上方数 + RS_20 均值（与 groups 区块同口径，当日组 vs 20日RS对照）
                codes_in = [r['stock_code'] for r in grp]
                ph2 = ','.join('?' * len(codes_in))
                irs = db.execute(f"""
                    SELECT rs_20, ma50, close FROM index_rs_daily
                    WHERE date = ? AND stock_code IN ({ph2})
                """, (target_date, *codes_in)).fetchall()
                above_ma50 = sum(1 for r in irs if r['ma50'] and r['close'] and r['close'] > r['ma50'])
                rs_vals = [r['rs_20'] for r in irs if r['rs_20'] is not None]
                avg_rs_20 = round(sum(rs_vals) / len(rs_vals), 1) if rs_vals else 0
                result.append({
                    'group_name': f'{pool_key}_{suffix}_d1',
                    'group_label': f'{pool_label}{label_suffix}·当日',
                    'indices_count': len(grp),
                    'avg_change': round(avg_chg * 100, 2),
                    'up_count': up,
                    'down_count': len(grp) - up,
                    'tech': {'above_ma50': above_ma50, 'avg_rs_20': avg_rs_20},
                })
        return result

    groups_d1 = _groups_d1(row['date'])

    return jsonify({
        'date': row['date'],
        'total_score': row['total_score'],
        'rating': row['rating'],
        'indicators': indicators,
        'rotations': rotations,
        'groups': groups,
        'groups_d1': groups_d1,
        'capital_flow': get_capital_flow_data(row['date'], db),
    })

# ═══════════════════════════════════════════════
# API: GET /api/market-sell-score
# ═══════════════════════════════════════════════

@app.route('/api/market-sell-score')
def api_market_sell_score():
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    db = get_db()
    row = db.execute(
        "SELECT * FROM market_sell_score_daily WHERE date <= ? ORDER BY date DESC LIMIT 1",
        (target_date,)
    ).fetchone()

    if not row:
        return jsonify({'status': 'no_data', 'date': target_date, 'total_score': 0, 'signals': []})

    signals = []
    signal_meta = {
        'dist_score':        ('抛盘日', '25日内抛盘日数量'),
        'ftd_score':         ('追盘日失效', '追盘日确认后失效'),
        'leader_score':      ('龙头股见顶', 'RS≥99龙头股>50%回落>10%'),
        'junk_score':        ('垃圾股补涨', '低价股涨幅远超高价股'),
        'divergence_score':  ('指数背离', '成分股涨跌背离'),
        'ma50_score':        ('跌破50日线', '收盘跌破且5日未收复'),
        'ma200_score':       ('跌破200日线', '收盘跌破200日均线'),
        'death_cross_score': ('均线死叉', 'MA50下穿MA200'),
        'ad_low5_score':     ('AD<0.7持续5天', '涨跌家数比连续低迷'),
        'ad_crash_score':    ('AD<0.5', '涨跌家数比极端低迷（清仓级）'),
        'hlnl_score':        ('NH/NL<0.5持续3天', '新高新低比恶化'),
        'vol_dry_score':     ('上涨无量', '连续涨但量萎缩'),
    }

    for key, (name, desc) in signal_meta.items():
        score_val = row[key] if row[key] else 0
        if score_val > 0:
            signals.append({'name': name, 'description': desc, 'score': score_val})

    cleared = []
    if row['cleared_signals']:
        try:
            cleared = json.loads(row['cleared_signals'])
        except Exception:
            pass

    signal_details = {}
    if row['signal_details']:
        try:
            signal_details = json.loads(row['signal_details'])
        except Exception:
            pass

    return jsonify({
        'date': row['date'],
        'total_score': row['total_score'],
        'position_advice': row['position_advice'],
        'meltdown_triggered': bool(row['meltdown_triggered']),
        'signals': signals,
        'cleared_signals': cleared,
        'signal_details': signal_details,
    })


# ═══════════════════════════════════════════════
# API: GET /api/market-health/sector-indices
# ═══════════════════════════════════════════════

@app.route('/api/market-health/sector-indices')
def api_market_sector_indices():
    """返回某个行业分组下的指数列表及RS/MA数据（支持当日分组 _d1 后缀）"""
    group_name = request.args.get('group_name', '')
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))

    # 当日分组模式：group_name 形如 l2_strong_d1 / theme_weak_d1
    is_d1 = group_name.endswith('_d1')
    if is_d1:
        group_name = group_name[:-3]  # 去掉 _d1

    # 解析分组名称获取池和RS条件
    pool_key, suffix = group_name.rsplit('_', 1)
    # pool_key: 'l2' or 'theme' or 'strategy', suffix: 'strong'/'mid'/'weak'

    import yaml
    yaml_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'index_style.yaml')
    with open(yaml_path, encoding='utf-8') as f:
        style = yaml.safe_load(f)

    if pool_key == 'l2':
        pool_codes = {item['code']: item['name'] for item in style['categories'].get('sector_l2', [])}
    elif pool_key == 'theme':
        pool_codes = {item['code']: item['name'] for item in style['categories'].get('thematic', [])}
    else:
        pool_codes = {item['code']: item['name'] for item in style['categories'].get('strategy', [])}

    db = get_db()
    codes_list = list(pool_codes.keys())
    placeholders = ','.join('?' * len(codes_list))

    # ── 当日分组模式：基于当日涨跌幅的池内百分位排名 ──
    if is_d1:
        # 先取前一交易日，用 close 反推精确涨跌幅（change 字段仅1%粒度）
        prev_date_row = db.execute(
            "SELECT MAX(date) as d FROM index_daily_kline WHERE date < ?", (target_date,)
        ).fetchone()
        prev_date = prev_date_row['d'] if prev_date_row else None
        prev_close_map = {}
        if prev_date:
            prev_rows = db.execute(
                "SELECT stock_code, close FROM index_daily_kline WHERE date = ?", (prev_date,)
            ).fetchall()
            prev_close_map = {r['stock_code']: r['close'] for r in prev_rows}

        rows = db.execute(f"""
            SELECT stock_code, close, change FROM index_daily_kline
            WHERE date = ? AND stock_code IN ({placeholders}) AND close IS NOT NULL
        """, (target_date, *codes_list)).fetchall()
        if not rows:
            return jsonify({'date': target_date, 'indices': [], 'stats': {'total': 0, 'above_ma50': 0, 'avg_rs_20': 0}})
        # 用 close 反推精确涨跌幅
        enriched = []
        for r in rows:
            prev = prev_close_map.get(r['stock_code'])
            if prev and prev > 0:
                chg = (r['close'] / prev) - 1
            else:
                chg = r['change'] or 0
            enriched.append({'stock_code': r['stock_code'], 'change': chg})
        # 池内百分位排名
        sorted_rows = sorted(enriched, key=lambda r: r['change'])
        n = len(sorted_rows)
        rank_map = {}
        for idx, r in enumerate(sorted_rows):
            rank_map[r['stock_code']] = (idx / (n - 1)) * 100 if n > 1 else 50.0
        # 按档位过滤
        if suffix == 'strong':
            filtered = [r for r in enriched if rank_map.get(r['stock_code'], 50) >= 75]
        elif suffix == 'weak':
            filtered = [r for r in enriched if rank_map.get(r['stock_code'], 50) < 30]
        else:
            filtered = [r for r in enriched if 30 <= rank_map.get(r['stock_code'], 50) < 75]
        filtered.sort(key=lambda r: r['change'], reverse=True)
        indices = [{
            'code': r['stock_code'],
            'name': pool_codes.get(r['stock_code'], r['stock_code']),
            'rs_20': round(rank_map.get(r['stock_code'], 50), 1),  # 当日强度分
            'rs_60': None,
            'rs_250': None,
            'close': None,
            'ma50': None,
            'ma200': None,
            'ad_slope': None,
            'ret_20': None,
            'ret_60': None,
            'above_ma50': False,
            'above_ma200': False,
            'change_d1': round((r['change'] or 0) * 100, 2),
        } for r in filtered]
        return jsonify({
            'date': target_date,
            'indices': indices,
            'stats': {'total': len(filtered), 'above_ma50': 0, 'avg_rs_20': round(sum(i['rs_20'] for i in indices) / len(indices), 1) if indices else 0},
        })

    # RS阈值
    if suffix == 'strong':
        cond = 'rs_20 >= 75'
    elif suffix == 'weak':
        cond = 'rs_20 < 30'
    else:
        cond = 'rs_20 >= 30 AND rs_20 < 75'

    # 日期回退：如果目标日期无 RS 数据，取最近有数据的日期
    has_rs = db.execute("SELECT COUNT(*) FROM index_rs_daily WHERE date=?", (target_date,)).fetchone()[0]
    rs_date = target_date
    if not has_rs:
        fallback = db.execute("SELECT MAX(date) FROM index_rs_daily").fetchone()[0]
        if fallback:
            rs_date = fallback

    rows = db.execute(f"""
        SELECT r.stock_code, r.rs_20, r.rs_60, r.rs_250,
               r.ma50, r.ma200, r.ad_slope_display,
               r.close, r.ret_20, r.ret_60,
               (SELECT k.change FROM index_daily_kline k WHERE k.stock_code=r.stock_code AND k.date=?) as chg_d1
        FROM index_rs_daily r
        WHERE r.date = ? AND r.stock_code IN ({placeholders})
          AND ({cond})
        ORDER BY r.rs_20 DESC
    """, (target_date, rs_date, *codes_list))
    
    indices = []
    for r in rows:
        code = r['stock_code']
        indices.append({
            'code': code,
            'name': pool_codes.get(code, code),
            'rs_20': r['rs_20'],
            'rs_60': r['rs_60'],
            'rs_250': r['rs_250'],
            'close': r['close'],
            'ma50': r['ma50'],
            'ma200': r['ma200'],
            'ad_slope': r['ad_slope_display'] or r['rs_60'],
            'ret_20': r['ret_20'],
            'ret_60': r['ret_60'],
            'chg_d1': round((r['chg_d1'] or 0) * 100, 2) if r['chg_d1'] is not None else None,
            'above_ma50': r['close'] > r['ma50'] if r['ma50'] else False,
            'above_ma200': r['close'] > r['ma200'] if r['ma200'] else False,
        })
    
    # 组统计
    stats = {
        'total': len(indices),
        'above_ma50': sum(1 for i in indices if i['above_ma50']),
        'above_ma200': sum(1 for i in indices if i['above_ma200']),
        'avg_rs_20': round(sum(i['rs_20'] for i in indices) / len(indices), 1) if indices else 0,
    }
    
    db.close()
    return jsonify({'group_name': group_name, 'date': target_date, 'indices': indices, 'stats': stats})


# ═══════════════════════════════════════════════
# API: GET /api/market-health/sector-constituents
# ═══════════════════════════════════════════════

@app.route('/api/market-health/sector-constituents')
def api_market_sector_constituents():
    """返回某个指数的成分股及权重/RS"""
    index_code = request.args.get('index_code', '')
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    
    if not index_code:
        return jsonify({'status': 'no_data', 'stocks': []})
    
    db = get_db()
    
    # 查成分股
    rows = db.execute("""
        SELECT ic.stock_code, ic.date as latest_date,
               sb.name,
               k.close, k.volume, k.amount,
               sr.rps_20, sr.rps_60, sr.rps_250
        FROM index_constituents ic
        JOIN stock_basic sb ON ic.stock_code = sb.stock_code
        LEFT JOIN daily_kline k ON ic.stock_code = k.stock_code AND k.date = ?
        LEFT JOIN stock_rs_daily sr ON ic.stock_code = sr.stock_code AND sr.date = ?
        WHERE ic.index_code = ?
          AND ic.date = (SELECT MAX(date) FROM index_constituents WHERE index_code = ?)
        ORDER BY k.amount DESC
        LIMIT 100
    """, (target_date, target_date, index_code, index_code))
    
    stocks = []
    for r in rows:
        stocks.append({
            'code': r['stock_code'],
            'name': r['name'],
            'close': r['close'],
            'volume': r['volume'],
            'amount': r['amount'],
            'rps_20': r['rps_20'],
            'rps_60': r['rps_60'],
            'rps_250': r['rps_250'],
        })
    
    db.close()
    return jsonify({'index_code': index_code, 'date': target_date, 'stocks': stocks, 'count': len(stocks)})


# ═══════════════════════════════════════════════
# API: GET /api/stock-sector-context
# ═══════════════════════════════════════════════

@app.route('/api/stock-sector-context')
def api_stock_sector_context():
    """查询某只股票的行业分组上下文（所属最强指数 + 行业组健康分）"""
    stock_code = request.args.get('code', '').strip()
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    if not stock_code:
        return jsonify({'status': 'no_data', 'error': '缺少股票代码'})
    
    db = get_db()
    
    # 查股票名称
    row = db.execute("SELECT name FROM stock_basic WHERE stock_code=?", (stock_code,)).fetchone()
    stock_name = row['name'] if row else ''
    
    # 从 index_constituents 查该股票属于哪些指数
    indices = db.execute("""
        SELECT ic.index_code, ic.date
        FROM index_constituents ic
        WHERE ic.stock_code = ?
          AND ic.date = (SELECT MAX(date) FROM index_constituents WHERE stock_code = ?)
    """, (stock_code, stock_code)).fetchall()
    
    if not indices:
        db.close()
        return jsonify({'status': 'no_data', 'stock_code': stock_code, 'stock_name': stock_name,
                        'primary_index': None, 'sector_group': None, 'all_indices': []})
    
    index_codes = [r['index_code'] for r in indices]
    
    # 加载 index_style.yaml 获取指数分类
    import yaml
    yaml_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'index_style.yaml')
    with open(yaml_path, encoding='utf-8') as f:
        style = yaml.safe_load(f)
    
    l2_set = {item['code'] for item in style['categories'].get('sector_l2', [])}
    theme_set = {item['code'] for item in style['categories'].get('thematic', [])}
    valid_set = l2_set | theme_set
    
    # 只保留 L2 + 主题指数，获取 RS_60
    placeholders = ','.join('?' * len(index_codes))
    
    rs_rows = db.execute(f"""
        SELECT r.stock_code, r.rs_60, r.rs_250
        FROM index_rs_daily r
        WHERE r.date = ? AND r.stock_code IN ({placeholders})
    """, (target_date, *index_codes)).fetchall()
    
    # 构建列表，只保留有效指数
    all_idx = []
    for r in rs_rows:
        code = r['stock_code']
        if code in valid_set:
            idx_type = 'sector_l2' if code in l2_set else 'thematic'
            all_idx.append({
                'code': code,
                'rs_60': r['rs_60'],
                'rs_250': r['rs_250'],
                'type': idx_type
            })
    
    if not all_idx:
        db.close()
        return jsonify({'status': 'no_data', 'stock_code': stock_code, 'stock_name': stock_name,
                        'primary_index': None, 'sector_group': None, 'all_indices': []})
    
    # 取 RS_60 最高的作为 primary_index（相同 RS 时 L2 优先）
    all_idx.sort(key=lambda x: (-x['rs_60'], 0 if x['type'] == 'sector_l2' else 1))
    primary = all_idx[0]
    primary_code = primary['code']
    
    # 从 yaml 里查 primary 指数名称
    primary_name = ''
    for item in style['categories'].get('sector_l2', []):
        if item['code'] == primary_code:
            primary_name = item['name']
            break
    if not primary_name:
        for item in style['categories'].get('thematic', []):
            if item['code'] == primary_code:
                primary_name = item['name']
                break
    
    primary_result = {
        'code': primary_code,
        'name': primary_name,
        'rs_60': primary['rs_60'],
        'rs_250': primary['rs_250'],
        'index_type': primary['type'],
    }
    
    # 查该指数属于哪个行业分组
    # 从 market_health_sector_daily 查所有组，判断 primary_code 属于哪个
    groups = db.execute(
        "SELECT * FROM market_health_sector_daily WHERE date=?", (target_date,)
    ).fetchall()
    
    sector_group = None
    for g in groups:
        gn = g['group_name']
        pool_key = gn.rsplit('_', 1)[0]  # 'l2' or 'theme'
        suffix = gn.rsplit('_', 1)[1]    # 'strong' / 'mid' / 'weak'
        
        # 获取该组的指数列表
        pool_codes = l2_set if pool_key == 'l2' else theme_set
        
        # 确定 RS 条件
        if suffix == 'strong':
            cond = lambda rs: rs >= 75
        elif suffix == 'weak':
            cond = lambda rs: rs < 30
        else:
            cond = lambda rs: 30 <= rs < 75
        
        if primary_code in pool_codes and cond(primary['rs_60']):
            sector_group = {
                'group_name': gn,
                'group_label': g['group_label'],
                'health_score': g['total_score'],
                'rating': g['rating'],
                'position': g['position'],
                'score_vs_market': g['score_vs_market'],
            }
            break
    
    db.close()
    return jsonify({
        'stock_code': stock_code,
        'stock_name': stock_name,
        'primary_index': primary_result,
        'sector_group': sector_group,
        'all_indices': all_idx,
    })


# ═══════════════════════════════════════════════
# API: GET /api/pipeline/run
# ═══════════════════════════════════════════════

@app.route('/api/pipeline/run')
def api_pipeline_run():
    """选股漏斗：按板块→RPS→形态→信号 四层过滤"""
    indices_param = request.args.get('indices', '')
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    if not indices_param:
        return jsonify({'error': '需要指定板块指数代码'})
    
    index_codes = [c.strip() for c in indices_param.split(',') if c.strip()]
    if not index_codes:
        return jsonify({'error': '板块指数为空'})
    
    db = get_db()
    
    # 从 index_constituents 获取成分股并集
    placeholders = ','.join('?' * len(index_codes))
    stocks = db.execute(f"""
        SELECT DISTINCT ic.stock_code
        FROM index_constituents ic
        WHERE ic.index_code IN ({placeholders})
          AND ic.date = (SELECT MAX(date) FROM index_constituents)
    """, index_codes).fetchall()
    
    all_codes = [r['stock_code'] for r in stocks]
    if not all_codes:
        db.close()
        return jsonify({'step3_count': 0, 'step4_count': 0, 'step5_count': 0, 'candidates': []})
    
    step3_info = f"{len(all_codes)} 只成分股"
    
    # 步骤三+四+五合并在一个批次处理
    chunk_size = 200
    results = []
    
    # 分批查 RPS 和基本数据，分开查询避免超复杂 SQL
    for chunk_start in range(0, len(all_codes), chunk_size):
        chunk = all_codes[chunk_start:chunk_start + chunk_size]
        ph = ','.join('?' * len(chunk))
        
        # 查最新价格和名称
        price_rows = db.execute(f"""
            SELECT k.stock_code, b.name, k.close
            FROM daily_kline k
            JOIN stock_basic b ON k.stock_code = b.stock_code
            WHERE k.stock_code IN ({ph}) AND k.date <= ?
              AND b.listing_status = 'normally_listed' AND b.name NOT LIKE '%ST%'
            ORDER BY k.date DESC
        """, (*chunk, target_date)).fetchall()
        
        # 按 stock_code 去重取最新
        seen = set()
        stock_map = {}
        for r in price_rows:
            if r['stock_code'] not in seen:
                seen.add(r['stock_code'])
                stock_map[r['stock_code']] = {'name': r['name'], 'close': r['close']}
        
        codes_with_data = list(stock_map.keys())
        if not codes_with_data:
            continue
        ph2 = ','.join('?' * len(codes_with_data))
        
        # 查 RPS
        rps_rows = db.execute(f"""
            SELECT stock_code, rps_20, rps_250
            FROM stock_rs_daily
            WHERE stock_code IN ({ph2}) AND date <= ?
            ORDER BY date DESC
        """, (*codes_with_data, target_date)).fetchall()
        rps_map = {}
        seen2 = set()
        for r in rps_rows:
            if r['stock_code'] not in seen2:
                seen2.add(r['stock_code'])
                rps_map[r['stock_code']] = {'rps20': r['rps_20'] or 0, 'rps250': r['rps_250'] or 0}
        
        # 查 MW 信号
        mw_rows = db.execute(f"""
            SELECT stock_code, b1_date, b2_date
            FROM mw_signal_daily
            WHERE stock_code IN ({ph2}) AND b2_date >= date(?, '-15 days')
            GROUP BY stock_code
        """, (*codes_with_data, target_date)).fetchall()
        mw_set = {r['stock_code'] for r in mw_rows}
        
        # 查口袋支点
        pp_rows = db.execute(f"""
            SELECT DISTINCT stock_code FROM pocket_pivot_daily
            WHERE stock_code IN ({ph2}) AND engine_version='V2' AND date >= date(?, '-10 days')
        """, (*codes_with_data, target_date)).fetchall()
        pp_set = {r['stock_code'] for r in pp_rows}
        
        # 查形态信号
        sig_rows = db.execute(f"""
            SELECT stock_code, signals_json FROM pattern_scan_signals
            WHERE stock_code IN ({ph2}) AND date >= date(?, '-5 days')
            GROUP BY stock_code
        """, (*codes_with_data, target_date)).fetchall()
        sig_map = {r['stock_code']: r['signals_json'] for r in sig_rows}
        
        for code, info in stock_map.items():
            rps = rps_map.get(code, {'rps20': 0, 'rps250': 0})
            if rps['rps250'] < 80:
                continue
            
            signals = []
            if code in mw_set:
                signals.append('MW')
            if code in pp_set:
                signals.append('PP')
            if code in sig_map:
                try:
                    sig_data = json.loads(sig_map[code])
                    if isinstance(sig_data, list):
                        for s in sig_data:
                            if isinstance(s, dict) and s.get('signal_name'):
                                signals.append(s['signal_name'][:4])
                except:
                    pass
            
            results.append({
                'code': code,
                'name': info['name'],
                'close': round(info['close'], 2) if info['close'] else None,
                'rps250': rps['rps250'],
                'rps20': rps['rps20'],
                'signals': ','.join(signals[:3]) if signals else '-',
                'step4': code in mw_set or code in pp_set,
                'step5': code in pp_set or code in mw_set,
            })
    
    step3_count = len(results)
    step4_list = [r for r in results if r['step4']]
    step5_list = [r for r in results if r['step5']]
    
    db.close()
    
    # 取最终候选前 20
    final = sorted(step5_list, key=lambda x: -x['rps250'])[:20]
    
    return jsonify({
        'step3_count': step3_count,
        'step3_info': step3_info,
        'step4_count': len(step4_list),
        'step5_count': len(step5_list),
        'candidates': final,
    })


# ═══════════════════════════════════════════════
# API: GET /api/market-health/breakouts
# ═══════════════════════════════════════════════

@app.route('/api/market-health/breakouts')
def api_market_breakouts():
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    db = get_db()
    rows = db.execute("""
        SELECT mb.*, sb.name
        FROM market_breakout_daily mb
        LEFT JOIN stock_basic sb ON mb.stock_code = sb.stock_code
        WHERE mb.date = (SELECT MAX(date) FROM market_breakout_daily WHERE date <= ?)
        ORDER BY mb.volume DESC
    """, (target_date,)).fetchall()
    stocks = [{
        'stock_code': r['stock_code'],
        'name': r['name'] or '',
        'close': r['close'],
        'change_pct': r['change_pct'],
        'volume': r['volume'],
        'amount': r['amount'],
        'vol_ma50': r['vol_ma50'] if 'vol_ma50' in r.keys() else 0,
        'amt_ma50': r['amt_ma50'] if 'amt_ma50' in r.keys() else 0,
        'vol_ratio': r['vol_ratio'] if 'vol_ratio' in r.keys() else 0,
        'break_ma': r['break_ma'] if 'break_ma' in r.keys() else '',
    } for r in rows]
    return jsonify({'date': rows[0]['date'] if rows else target_date, 'count': len(stocks), 'stocks': stocks})

# ═══════════════════════════════════════════════
# API: GET /api/market-scan/dividend-advice
# ═══════════════════════════════════════════════

DIVIDEND_INDICES = [
    ('000922', '中证红利', 'pure'),
    ('H30269', '红利低波', 'lowvol'),
    ('931468', '红利质量', 'quality'),
    ('000015', '红利指数', 'pure'),
    ('931848', '800红利低波', 'lowvol'),
]

# 全收益指数映射：价格指数代码 -> 全收益代码（回撤计算优先用全收益，分红再投资更真实）
FULL_RETURN_MAP = {
    '000922': 'H00922',  # 中证红利 -> 中证红利全收益
}

# 红利 250日回撤买点阈值（%）：回测 32 次触发/20日胜率75% vs 15% 仅9次小样本假象（PRD §2.1 v1.1）
DD_BUY_THRESHOLD = 10


def _dd_from_full_return(db, code, target_date, days=300, window=250):
    """250日回撤计算：优先用全收益指数（index_full_return_daily），无数据时回退价格指数。
    返回 (dd_250, high_250, current, source) 或 None。"""
    tri_code = FULL_RETURN_MAP.get(code)
    source = 'price'
    rows = None
    if tri_code:
        tri_rows = db.execute("""
            SELECT date, close FROM index_full_return_daily
            WHERE stock_code=? AND date<=?
            ORDER BY date DESC LIMIT ?
        """, (tri_code, target_date, days)).fetchall()
        tri_rows = list(reversed(tri_rows))
        if len(tri_rows) >= window:
            rows = tri_rows
            source = 'full_return'
    if rows is None:
        rows = db.execute("""
            SELECT date, close FROM index_daily_kline
            WHERE stock_code=? AND kline_type='normal' AND date<=?
            ORDER BY date DESC LIMIT ?
        """, (code, target_date, days)).fetchall()
        rows = list(reversed(rows))
    if len(rows) < window:
        return None
    closes = [r['close'] for r in rows]
    current = closes[-1]
    high = max(closes[-window:])
    dd = (high - current) / high * 100
    return {'dd_250': round(dd, 1), 'high_250': round(high, 2), 'current': round(current, 2),
            'source': source, 'date': rows[-1]['date']}



@app.route('/api/market-scan/dividend-advice')
def api_market_dividend_advice():
    """红利指数操作建议：信号检测+建议合成（支持历史回看）"""
    target_date = request.args.get('date', '')
    db = get_db()
    if not target_date:
        r = db.execute("SELECT MAX(date) FROM index_daily_kline").fetchone()
        target_date = r[0]

    results = []
    for code, name, cat in DIVIDEND_INDICES:
        # 250日回撤：优先全收益（H00922），回退价格（000922）—— PRD §3.2
        ddinfo = _dd_from_full_return(db, code, target_date)
        if ddinfo is None:
            continue
        dd_250 = ddinfo['dd_250']
        high250 = ddinfo['high_250']
        current = ddinfo['current']

        # 估值
        v = db.execute("""
            SELECT pe_ttm, pe_ttm_pct, pb, pb_pct, dyr, dyr_pct
            FROM index_fundamental_daily
            WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1
        """, (code, target_date)).fetchone()
        val = None
        if v:
            val = {
                'pe': round(v['pe_ttm'], 1) if v['pe_ttm'] else None,
                'pe_pct': round(v['pe_ttm_pct'] * 100) if v['pe_ttm_pct'] is not None else None,
                'pb': round(v['pb'], 2) if v['pb'] else None,
                'pb_pct': round(v['pb_pct'] * 100) if v['pb_pct'] is not None else None,
                'dyr': round(v['dyr'] * 100, 2) if v['dyr'] else None,
                'dyr_pct': round(v['dyr_pct'] * 100) if v['dyr_pct'] is not None else None,
            }

        # 信号（v1.1：250日回撤买点阈值 15%→10%；v1.2：卖出警示规则）
        signals = []
        if dd_250 >= DD_BUY_THRESHOLD:
            signals.append('gold_buy')
        if val and val['dyr_pct'] is not None and val['dyr_pct'] > 90:
            signals.append('high_div')
        if dd_250 >= DD_BUY_THRESHOLD and val and val['dyr_pct'] is not None and val['dyr_pct'] > 80:
            signals.append('double_confirm')
        if val and val['pe_pct'] is not None and val['pe_pct'] > 80:
            signals.append('pe_warn')
        if val and val['dyr_pct'] is not None and val['dyr_pct'] < 10:
            signals.append('low_div')

        # v1.2 卖出警示信号（卖点研究，PRD §5.4）
        # 涨幅基于全收益收盘（与回撤同口径）；ddinfo 已含全收益 closes 派生数据，需额外取涨幅
        chg20 = chg60 = None
        if ddinfo:
            # 从全收益表取 20/60 日涨幅（ddinfo 内部数据不可得，直接查）
            tri_rows_adv = db.execute("""
                SELECT close FROM index_full_return_daily
                WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 61
            """, (FULL_RETURN_MAP.get(code, code), target_date)).fetchall()
            tri_rows_adv = list(reversed(tri_rows_adv))
            if len(tri_rows_adv) >= 21:
                chg20 = (tri_rows_adv[-1]['close'] / tri_rows_adv[-21]['close'] - 1) * 100
            if len(tri_rows_adv) >= 61:
                chg60 = (tri_rows_adv[-1]['close'] / tri_rows_adv[-61]['close'] - 1) * 100
        if chg20 is not None and chg20 > 10 or chg60 is not None and chg60 > 15:
            signals.append('surge_sell')
        if val and val['pe_pct'] is not None and val['pe_pct'] > 80:
            signals.append('pe_high_sell')

        # 建议（v1.2：卖出警示优先于买入）
        advice, level = '持有/观望', 'hold'
        if 'pe_high_sell' in signals and 'surge_sell' in signals:
            advice, level = '估值高位+涨幅过大双确认，建议强减仓', 'strong_reduce'
        elif 'pe_high_sell' in signals:
            advice, level = '估值高位（10年PE分位>80%），建议减仓', 'reduce'
        elif 'surge_sell' in signals:
            advice, level = '短期涨幅过大（20日>10%/60日>15%），建议减仓', 'reduce'
        elif 'double_confirm' in signals:
            advice, level = '分批买入（回撤+高息双确认）', 'strong_buy'
        elif 'gold_buy' in signals or 'high_div' in signals:
            advice, level = '观察买入（单信号触发）', 'buy'
        elif 'pe_warn' in signals and dd_250 < 10:
            advice, level = '估值偏高（PE分位>80%），建议减仓', 'reduce'
        elif 'low_div' in signals:
            advice, level = '股息保护不足（股息率分位<10%），谨慎', 'caution'

        results.append({
            'code': code, 'name': name, 'cat': cat,
            'close': ddinfo['current'], 'date': ddinfo['date'], 'dd_source': ddinfo['source'],
            'dd_250': dd_250,
            'high_250': high250,
            'valuation': val,
            'signals': signals,
            'advice': advice,
            'advice_level': level,
        })

    # 整体汇总（v1.2：含 strong_reduce）
    buy_count = sum(1 for r in results if r['advice_level'] in ('strong_buy', 'buy'))
    reduce_count = sum(1 for r in results if r['advice_level'] in ('reduce', 'strong_reduce'))
    if buy_count >= 3:
        summary = f"{buy_count}/5 指数处于买入区，红利整体低估"
    elif reduce_count >= 3:
        summary = f"{reduce_count}/5 指数建议减仓，警惕高位回落"
    else:
        summary = f"买入区 {buy_count}/5 · 减仓区 {reduce_count}/5，整体中性"

    return jsonify({'date': target_date, 'summary': summary, 'indices': results})


@app.route('/api/market-scan/dividend-advice-detail')
def api_market_dividend_detail():
    """红利指数详情：历史回撤曲线/估值分位/信号时间线/历史类似情况统计"""
    code = request.args.get('code', '000922')
    target_date = request.args.get('date', '')
    db = get_db()
    if not target_date:
        r = db.execute("SELECT MAX(date) FROM index_daily_kline").fetchone()
        target_date = r[0]

    # 近3年K线
    rows = db.execute("""
        SELECT date, close FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>=date(?,'-3 years') AND date<=?
        ORDER BY date
    """, (code, target_date, target_date)).fetchall()
    dates = [r['date'] for r in rows]
    closes = [r['close'] for r in rows]
    if not dates:
        return jsonify({'error': 'no data'})

    # 全收益数据（回撤曲线/类似事件统计优先用全收益；PRD §3.2）
    tri_code = FULL_RETURN_MAP.get(code)
    tri_rows = None
    if tri_code:
        tri_rows = db.execute("""
            SELECT date, close FROM index_full_return_daily
            WHERE stock_code=? AND date>=date(?,'-3 years') AND date<=?
            ORDER BY date
        """, (tri_code, target_date, target_date)).fetchall()
    # 回撤计算基准：有全收益用全收益，否则价格
    dd_base = [r['close'] for r in tri_rows] if tri_rows and len(tri_rows) >= 250 else closes
    dd_dates = [r['date'] for r in tri_rows] if tri_rows and len(tri_rows) >= 250 else dates

    # 全历史全收益（类似事件统计用，防未来截断）
    hist_tri = None
    hist_tri_dates = None
    if tri_code:
        hist_tri_rows = db.execute("""
            SELECT date, close FROM index_full_return_daily
            WHERE stock_code=? ORDER BY date
        """, (tri_code,)).fetchall()
        # 注意：故意不含 date<=target_date——类似事件统计需要事件后的实际收益（回测式统计，非信号检测），
        # 回看历史日期时用全历史可得到"当时触发后实际涨了没"，避免幸存者偏差（与 hist_rows 同理）
        if len(hist_tri_rows) >= 250:
            hist_tri = [r['close'] for r in hist_tri_rows]
            hist_tri_dates = [r['date'] for r in hist_tri_rows]

    # 全历史K线（用于类似事件统计，避免目标日期截断造成幸存者偏差）
    hist_rows = db.execute("""
        SELECT date, close FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>='2016-01-01'
        ORDER BY date
    """, (code,)).fetchall()
    hist_dates = [r['date'] for r in hist_rows]
    hist_closes = [r['close'] for r in hist_rows]

    # 回撤曲线（250日滚动最高 · 优先全收益口径，PRD §3.2）
    dd_series = []
    for i in range(len(dd_base)):
        w = dd_base[max(0, i-249):i+1]
        hi = max(w)
        dd_series.append(round((hi - dd_base[i]) / hi * 100, 2))
    # 回撤曲线按价格日期对齐（全收益与价格交易日一致；全收益缺失日如 2018 前用 None 占位）
    if len(dd_dates) != len(dates):
        dd_map = dict(zip(dd_dates, dd_series))
        dd_series = [dd_map.get(d) for d in dates]

    # 估值分位（近3年，按K线日期对齐）
    val_rows = db.execute("""
        SELECT date, pe_ttm_pct, pb_pct, dyr_pct FROM index_fundamental_daily
        WHERE stock_code=? AND date>=date(?,'-3 years') AND date<=?
        ORDER BY date
    """, (code, target_date, target_date)).fetchall()
    val_map = {r['date']: r for r in val_rows}
    pe_series = [round(val_map[d]['pe_ttm_pct']*100) if d in val_map and val_map[d]['pe_ttm_pct'] is not None else None for d in dates]
    pb_series = [round(val_map[d]['pb_pct']*100) if d in val_map and val_map[d]['pb_pct'] is not None else None for d in dates]
    dyr_series = [round(val_map[d]['dyr_pct']*100) if d in val_map and val_map[d]['dyr_pct'] is not None else None for d in dates]

    # 信号时间线：历史上 250日回撤>=10% 的事件（合并20日，全收益口径）
    events = []
    last_trig = -999
    for i in range(250, len(dd_series)):
        if dd_series[i] is not None and dd_series[i] >= DD_BUY_THRESHOLD:  # v1.1：买点阈值 15%→10%
            if i - last_trig >= 20:
                events.append({'date': dates[i], 'dd': dd_series[i]})
                last_trig = i

    # 历史类似情况统计：当前回撤幅度下的所有事件（全收益优先，回退价格）
    cur_dd = dd_series[-1] if dd_series and dd_series[-1] is not None else 0
    # 全历史回撤序列
    hist_base = hist_tri if hist_tri else hist_closes
    hist_dd = []
    for i in range(len(hist_base)):
        w = hist_base[max(0, i-249):i+1]
        hi = max(w)
        hist_dd.append((hi - hist_base[i]) / hi * 100)
    similar = []
    last_s = -999
    for i in range(250, len(hist_base)):
        ddv = hist_dd[i]
        if ddv >= cur_dd and cur_dd > 0 and i - last_s >= 20:
            fwd20 = (hist_base[min(i+20, len(hist_base)-1)] - hist_base[i]) / hist_base[i] * 100 if i+20 < len(hist_base) else None
            fwd60 = (hist_base[min(i+60, len(hist_base)-1)] - hist_base[i]) / hist_base[i] * 100 if i+60 < len(hist_base) else None
            peak = max(hist_base[i:min(i+120, len(hist_base))])
            bounce = (peak - hist_base[i]) / hist_base[i] * 100
            days_to_peak = None
            seg = hist_base[i:min(i+120, len(hist_base))]
            if seg:
                days_to_peak = seg.index(max(seg))
            similar.append({
                'date': (hist_tri_dates[i] if hist_tri_dates else hist_dates[i]), 'dd': round(ddv, 1),
                'fwd20': round(fwd20, 1) if fwd20 is not None else None,
                'fwd60': round(fwd60, 1) if fwd60 is not None else None,
                'bounce': round(bounce, 1),
                'days_to_peak': days_to_peak,
            })
            last_s = i

    # 统计摘要
    stats = {}
    if similar:
        fwd20s = [s['fwd20'] for s in similar if s['fwd20'] is not None]
        fwd60s = [s['fwd60'] for s in similar if s['fwd60'] is not None]
        bounces = [s['bounce'] for s in similar]
        days = [s['days_to_peak'] for s in similar if s['days_to_peak'] is not None]
        def med(xs):
            s = sorted(xs); n = len(s)
            return s[n//2] if n % 2 else (s[n//2-1]+s[n//2])/2
        stats = {
            'count': len(similar),
            'fwd20_median': round(med(fwd20s), 1) if fwd20s else None,
            'fwd20_winrate': round(sum(1 for x in fwd20s if x > 0) / len(fwd20s) * 100, 1) if fwd20s else None,
            'fwd60_median': round(med(fwd60s), 1) if fwd60s else None,
            'fwd60_winrate': round(sum(1 for x in fwd60s if x > 0) / len(fwd60s) * 100, 1) if fwd60s else None,
            'bounce_median': round(med(bounces), 1) if bounces else None,
            'bounce_avg': round(sum(bounces)/len(bounces), 1) if bounces else None,
            'days_to_peak_median': round(med(days), 0) if days else None,
        }

    # 历史类似估值分位区间统计（当前PE/PB/DYR分位 ±10pp 窗口内的历史事件）
    # 用全历史估值数据 + 全历史K线，统计这些日子的未来20/60日表现
    val_similar = {}
    if val_map and hist_dates:
        # 当前估值分位
        cur_pe = pe_series[-1] if pe_series else None
        cur_pb = pb_series[-1] if pb_series else None
        cur_dyr = dyr_series[-1] if dyr_series else None
        # 全历史估值（按日期对齐 hist_dates；注意：val_similar 用价格指数口径统计估值区间收益，
        # 与 similar 的全收益回撤口径是两套独立统计，各有用途——估值分位来自价格指数基本面）
        hist_val_rows = db.execute("""
            SELECT date, pe_ttm_pct, pb_pct, dyr_pct FROM index_fundamental_daily
            WHERE stock_code=? AND date>='2016-01-01' ORDER BY date
        """, (code,)).fetchall()
        hist_val_map = {r['date']: (r['pe_ttm_pct'], r['pb_pct'], r['dyr_pct']) for r in hist_val_rows}
        hist_date_idx = {d: i for i, d in enumerate(hist_dates)}

        def _val_window_stats(metric_cur, metric_name):
            """统计历史估值分位在 [cur-10, cur+10] 区间的日子，未来20/60日表现"""
            if metric_cur is None:
                return None
            lo, hi = max(0, metric_cur - 10), min(100, metric_cur + 10)
            fwd20s, fwd60s = [], []
            count = 0
            for dt, vals in hist_val_map.items():
                if dt not in hist_date_idx:
                    continue
                v = vals[0] if metric_name == 'pe' else (vals[1] if metric_name == 'pb' else vals[2])
                if v is None:
                    continue
                pct = v * 100
                if lo <= pct <= hi:
                    count += 1
                    i = hist_date_idx[dt]
                    if i + 20 < len(hist_closes):
                        fwd20s.append((hist_closes[i+20] / hist_closes[i] - 1) * 100)
                    if i + 60 < len(hist_closes):
                        fwd60s.append((hist_closes[i+60] / hist_closes[i] - 1) * 100)
            if not fwd20s:
                return {'count': count, 'range': [lo, hi]}
            def _med(xs):
                s = sorted(xs); n = len(s)
                return s[n//2] if n % 2 else (s[n//2-1]+s[n//2])/2
            return {
                'count': count, 'range': [lo, hi],
                'fwd20_median': round(_med(fwd20s), 1),
                'fwd20_winrate': round(sum(1 for x in fwd20s if x > 0) / len(fwd20s) * 100, 1),
                'fwd60_median': round(_med(fwd60s), 1) if fwd60s else None,
                'fwd60_winrate': round(sum(1 for x in fwd60s if x > 0) / len(fwd60s) * 100, 1) if fwd60s else None,
            }

        val_similar = {
            'pe': _val_window_stats(cur_pe, 'pe'),
            'pb': _val_window_stats(cur_pb, 'pb'),
            'dyr': _val_window_stats(cur_dyr, 'dyr'),
        }

    name = code
    try:
        import yaml as _yaml
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'index_style.yaml'), 'r', encoding='utf-8') as _f:
            _cfg = _yaml.safe_load(_f)
        for _cat, _items in _cfg.get('categories', {}).items():
            for _it in _items:
                if isinstance(_it, dict) and _it.get('code') == code:
                    name = _it.get('name', code)
                    break
            if name != code:
                break
    except Exception as _e:
        print(f'[dividend-detail] index_style 名称查找失败 {code}: {type(_e).__name__}: {_e}', flush=True)

    # ── 价格 vs 全收益 对比线（PRD Ticket 03）──
    # 归一化：近3年起点=100（dates/closes 为价格，tri_rows 为全收益，日期对齐）
    price_norm = None
    tri_norm = None
    tri_diff = None
    if tri_rows and len(tri_rows) >= 2 and len(tri_rows) == len(dates):
        tri_closes = [r['close'] for r in tri_rows]
        base_p = closes[0] if closes[0] else 1
        base_t = tri_closes[0] if tri_closes[0] else 1
        price_norm = [round(c / base_p * 100, 1) for c in closes]
        tri_norm = [round(c / base_t * 100, 1) for c in tri_closes]
        tri_diff = [round(t - p, 1) for p, t in zip(price_norm, tri_norm)]

    # 沪深300 对比线（近3年，与 dates 对齐，归一化起点=100）
    hs300_norm = None
    hs_rows = db.execute("""
        SELECT date, close FROM index_daily_kline
        WHERE stock_code='000300' AND kline_type='normal' AND date>=? AND date<=?
        ORDER BY date
    """, (dates[0], target_date)).fetchall()
    if hs_rows:
        hs_map = {r['date']: r['close'] for r in hs_rows}
        hs_closes = [hs_map.get(d) for d in dates]
        if hs_closes[0]:
            base_h = hs_closes[0]
            hs300_norm = [round(c / base_h * 100, 1) if c else None for c in hs_closes]

    # ── 年度最大回撤（PRD §2.2 展示用，非规则；用全历史 H00922 近8年，Ticket 03）──
    annual_dd = []
    if hist_tri and hist_tri_dates:
        cur_year = None
        year_hi = None
        year_map = {}
        for i in range(len(hist_tri_dates)):
            y = hist_tri_dates[i][:4]
            if y != cur_year:
                cur_year = y
                year_hi = hist_tri[i]
            if hist_tri[i] > year_hi:
                year_hi = hist_tri[i]
            dd = (year_hi - hist_tri[i]) / year_hi * 100
            year_map.setdefault(y, 0)
            year_map[y] = max(year_map[y], dd)
        annual_dd = [{'year': y, 'dd': round(d, 1)} for y, d in sorted(year_map.items())]

    # ── 近10年K线（价格走势长图用；其他图保持近3年 dates/closes）──
    dates_long = []
    closes_long = []
    tri_long = []
    rows_long = db.execute("""
        SELECT date, close FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>=date(?,'-10 years') AND date<=?
        ORDER BY date
    """, (code, target_date, target_date)).fetchall()
    if rows_long:
        dates_long = [r['date'] for r in rows_long]
        closes_long = [r['close'] for r in rows_long]
        if tri_code:
            tri_map = dict((r['date'], r['close']) for r in db.execute(
                "SELECT date, close FROM index_full_return_daily WHERE stock_code=? AND date<=?",
                (tri_code, target_date)).fetchall())
            tri_long = [tri_map.get(d) for d in dates_long]

    return jsonify({
        'code': code, 'name': name, 'date': target_date,
        'dates': dates, 'closes': closes, 'dd_series': dd_series,
        'pe_series': pe_series, 'pb_series': pb_series, 'dyr_series': dyr_series,
        'events': events, 'similar': similar, 'stats': stats, 'val_similar': val_similar,
        'current_dd': round(cur_dd, 1),
        'price_norm': price_norm, 'tri_norm': tri_norm, 'tri_diff': tri_diff, 'hs300_norm': hs300_norm,
        'annual_dd': annual_dd,
        'dates_long': dates_long, 'closes_long': closes_long, 'tri_long': tri_long,
        'dd_source': 'full_return' if (tri_rows and len(tri_rows) >= 250) else 'price',
    })


# ═══════════════════════════════════════════════
# API: GET /api/market-scan/capital-flow
# ═══════════════════════════════════════════════

def _stock_name(db, code):
    """查股票名称，查不到返回原代码"""
    try:
        r = db.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
        return r['name'] if r and r['name'] else code
    except Exception:
        return code

@app.route('/api/market-scan/capital-flow')
def api_market_capital_flow():
    """资金情绪：两融趋势/龙虎榜/大宗交易"""
    target_date = request.args.get('date', '')
    if not target_date:
        target_date = datetime.now().strftime('%Y-%m-%d')
    db = get_db()
    
    # 两融趋势（近60日）
    trend_rows = db.execute('''
        SELECT date, margin_balance, margin_net_buy
        FROM daily_review_summary
        WHERE margin_balance IS NOT NULL AND margin_balance != 0
        ORDER BY date DESC LIMIT 250
    ''').fetchall()
    trend = [{'date': r['date'], 'balance': r['margin_balance'], 'net_buy': r['margin_net_buy']} for r in reversed(trend_rows)]
    latest_margin = trend_rows[0] if trend_rows else None
    
    # 龙虎榜：精确匹配请求日期
    lhb_rows = db.execute(
        "SELECT stock_code, stock_name, reason, net_amount FROM daily_review_lhb WHERE date=? ORDER BY net_amount DESC",
        (target_date,)
    ).fetchall()
    total_buy = sum(r['net_amount'] or 0 for r in lhb_rows if (r['net_amount'] or 0) > 0)
    total_sell = abs(sum(r['net_amount'] or 0 for r in lhb_rows if (r['net_amount'] or 0) < 0))
    net_amount = total_buy - total_sell
    lhb_summary = {
        'total_buy': round(total_buy, 2), 'total_sell': round(total_sell, 2),
        'net_amount': round(net_amount, 2), 'count': len(lhb_rows),
    }
    
    # 大宗交易：精确匹配请求日期
    bt_rows = db.execute(
        "SELECT stock_code, trading_amount, discount_rate FROM daily_review_block_trade WHERE date=? ORDER BY trading_amount DESC",
        (target_date,)
    ).fetchall()
    bt_discounts = [r['discount_rate'] for r in bt_rows if r['discount_rate']]
    bt_summary = {
        'total_count': len(bt_rows),
        'total_amount': round(sum(r['trading_amount'] for r in bt_rows), 2),
        'avg_discount': round(sum(bt_discounts)/len(bt_discounts), 2) if bt_discounts else 0,
        'max_discount': round(min(bt_discounts), 2) if bt_discounts else 0,
        'details': [{
            'code': r['stock_code'],
            'name': _stock_name(db, r['stock_code']),
            'amount': r['trading_amount'], 'discount': r['discount_rate']
        } for r in bt_rows],
    }
    
    # 龙虎榜Top
    top_buy = [{'code': r['stock_code'], 'name': r['stock_name'], 'net': r['net_amount'] or 0, 'reason': (r['reason'] or '')[:20]}
               for r in lhb_rows if (r['net_amount'] or 0) > 0][:10]
    top_sell = [{'code': r['stock_code'], 'name': r['stock_name'], 'net': abs(r['net_amount'] or 0), 'reason': (r['reason'] or '')[:20]}
                for r in lhb_rows if (r['net_amount'] or 0) < 0][:10]
    
    return jsonify({
        'date': target_date, 'data_date': target_date,
        'margin': {
            'balance': round(latest_margin['margin_balance'], 0) if latest_margin and latest_margin['margin_balance'] is not None else None,
            'net_buy': round(latest_margin['margin_net_buy'], 0) if latest_margin and latest_margin['margin_net_buy'] is not None else None,
            'trend': trend,
        },
        'lhb': {**lhb_summary, 'top_buy': top_buy, 'top_sell': top_sell},
        'block_trade': bt_summary,
    })


# ═══════════════════════════════════════════════
# API: GET /api/market-scan/fcf-advice（自由现金流指数操作建议）
# ═══════════════════════════════════════════════

FCF_INDICES = [
    ('980092', '国证自由现金流'),
    ('932365', '中证现金流'),
]
FCF_DATA_NOTE = '估值分位基于 2024-09 以来数据（约2年），短窗口统计仅供参考'


def _fcf_signals_and_advice(val):
    """估值分位三层规则（PRD §5.1）：返回 (signals, advice, level)"""
    signals = []
    if val:
        pe_p = val['pe_pct'] if val['pe_pct'] is not None else None
        pb_p = val['pb_pct'] if val['pb_pct'] is not None else None
        dy_p = val['dyr_pct'] if val['dyr_pct'] is not None else None
        # fcf_buy：任一指标进入买入区
        if (pe_p is not None and pe_p < 33) or (pb_p is not None and pb_p < 33) \
                or (dy_p is not None and dy_p > 66):
            signals.append('fcf_buy')
        # fcf_strong：≥2 指标同向
        n_buy = sum([pe_p is not None and pe_p < 33,
                     pb_p is not None and pb_p < 33,
                     dy_p is not None and dy_p > 66])
        if n_buy >= 2:
            signals.append('fcf_strong')
        # fcf_warn：PE/PB 双高
        if pe_p is not None and pb_p is not None and pe_p > 66 and pb_p > 66:
            signals.append('fcf_warn')
    advice, level = '持有/观望', 'hold'
    if 'fcf_strong' in signals:
        advice, level = '分批买入（估值双确认）', 'strong_buy'
        signals = [s for s in signals if s != 'fcf_buy']  # 只保留最高级信号（Spec W-3）
    elif 'fcf_buy' in signals:
        advice, level = '观察买入（估值单信号触发）', 'buy'
    elif 'fcf_warn' in signals:
        advice, level = '估值偏高（PE/PB双高），谨慎', 'caution'
    return signals, advice, level


@app.route('/api/market-scan/fcf-advice')
def api_market_fcf_advice():
    """自由现金流指数操作建议：估值分位信号检测+建议合成（支持历史回看，多指数）"""
    target_date = request.args.get('date', '')
    db = get_db()
    if not target_date:
        r = db.execute("SELECT MAX(date) FROM index_daily_kline").fetchone()
        if r is None or r[0] is None:
            return jsonify({'error': 'no data', 'date': ''})
        target_date = r[0]

    results = []
    for code, name in FCF_INDICES:
        # K线 300 日窗口
        rows = db.execute("""
            SELECT date, close FROM index_daily_kline
            WHERE stock_code=? AND kline_type='normal' AND date<=?
            ORDER BY date DESC LIMIT 300
        """, (code, target_date)).fetchall()
        rows = list(reversed(rows))
        if len(rows) < 250:
            continue
        closes = [r['close'] for r in rows]
        current = closes[-1]
        high250 = max(closes[-250:])
        dd_250 = (high250 - current) / high250 * 100

        # 估值
        v = db.execute("""
            SELECT pe_ttm, pe_ttm_pct, pb, pb_pct, dyr, dyr_pct
            FROM index_fundamental_daily
            WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1
        """, (code, target_date)).fetchone()
        val = None
        if v:
            val = {
                'pe': round(v['pe_ttm'], 1) if v['pe_ttm'] else None,
                'pe_pct': round(v['pe_ttm_pct'] * 100) if v['pe_ttm_pct'] is not None else None,
                'pb': round(v['pb'], 2) if v['pb'] else None,
                'pb_pct': round(v['pb_pct'] * 100) if v['pb_pct'] is not None else None,
                'dyr': round(v['dyr'] * 100, 2) if v['dyr'] else None,
                'dyr_pct': round(v['dyr_pct'] * 100) if v['dyr_pct'] is not None else None,
            }

        signals, advice, level = _fcf_signals_and_advice(val)
        results.append({
            'code': code, 'name': name,
            'close': round(current, 2), 'dd_250': round(dd_250, 1),
            'high_250': round(high250, 2),
            'valuation': val,
            'signals': signals, 'advice': advice, 'advice_level': level,
            'data_note': FCF_DATA_NOTE,
        })

    return jsonify({'date': target_date, 'indices': results})


@app.route('/api/market-scan/fcf-advice-detail')
def api_market_fcf_detail():
    """自由现金流指数详情：净值回撤曲线/估值分位走势/信号时间线/历史类似情况统计"""
    target_date = request.args.get('date', '')
    code = request.args.get('code', FCF_INDICES[0][0])
    name = next((n for c, n in FCF_INDICES if c == code), code)
    db = get_db()
    if not target_date:
        r = db.execute("SELECT MAX(date) FROM index_daily_kline").fetchone()
        if r is None or r[0] is None:
            return jsonify({'error': 'no data', 'date': ''})
        target_date = r[0]

    # 近3年K线
    rows = db.execute("""
        SELECT date, close FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>=date(?,'-3 years') AND date<=?
        ORDER BY date
    """, (code, target_date, target_date)).fetchall()
    dates = [r['date'] for r in rows]
    closes = [r['close'] for r in rows]
    if not dates:
        return jsonify({'error': 'no data'})

    # 回撤曲线（250日滚动最高 · 仅展示，不参与信号生成；2年窗口回测无统计规律，供人工参考）
    dd_series = []
    for i in range(len(closes)):
        w = closes[max(0, i-249):i+1]
        hi = max(w)
        dd_series.append(round((hi - closes[i]) / hi * 100, 2))

    # 估值分位（近2年窗口：数据从 2024-09 起自然截断，与图表标题"数据自2024-09起"一致；Spec O-4）
    val_rows = db.execute("""
        SELECT date, pe_ttm_pct, pb_pct, dyr_pct FROM index_fundamental_daily
        WHERE stock_code=? AND date>=date(?,'-2 years') AND date<=?
        ORDER BY date
    """, (code, target_date, target_date)).fetchall()
    val_map = {r['date']: r for r in val_rows}
    pe_series = [round(val_map[d]['pe_ttm_pct']*100) if d in val_map and val_map[d]['pe_ttm_pct'] is not None else None for d in dates]
    pb_series = [round(val_map[d]['pb_pct']*100) if d in val_map and val_map[d]['pb_pct'] is not None else None for d in dates]
    dyr_series = [round(val_map[d]['dyr_pct']*100) if d in val_map and val_map[d]['dyr_pct'] is not None else None for d in dates]

    # 信号时间线：估值分位触发点（近2年，20交易日去重，窗口内取信号最强日——v1.1 修复）
    # 原实现取窗口内第一个满足日，弱信号会占位吞掉更极端的强信号（2026-06-30 案例）
    # 语义：替换即新冷却起点（同一波低估信号不中断，合并为一个代表点）
    def _score(d):
        return (d.get('pe_pct') if d.get('pe_pct') is not None else 100) + \
               (d.get('pb_pct') if d.get('pb_pct') is not None else 100) + \
               (100 - (d.get('dyr_pct') if d.get('dyr_pct') is not None else 0))
    events = []
    last_trig = -999
    for i, d in enumerate(dates):
        if d not in val_map:
            continue
        vm = val_map[d]
        pe_p = vm['pe_ttm_pct'] if vm['pe_ttm_pct'] is not None else None
        pb_p = vm['pb_pct'] if vm['pb_pct'] is not None else None
        dy_p = vm['dyr_pct'] if vm['dyr_pct'] is not None else None
        n_buy = sum([pe_p is not None and pe_p < 0.33,
                     pb_p is not None and pb_p < 0.33,
                     dy_p is not None and dy_p > 0.66])
        if n_buy < 1:
            continue
        if i - last_trig >= 20:
            # 新窗口：标记当前日
            events.append({'date': d, 'signal': 'fcf_strong' if n_buy >= 2 else 'fcf_buy',
                           'pe_pct': round(pe_p*100) if pe_p is not None else None,
                           'pb_pct': round(pb_p*100) if pb_p is not None else None,
                           'dyr_pct': round(dy_p*100) if dy_p is not None else None,
                           'n_buy': n_buy})
            last_trig = i
        else:
            # 窗口内：n_buy 更高则替换（升级为强信号）；同 n_buy 保留更极端者（_score 三项分位和更低胜）
            cur = events[-1]
            cur_n = cur.get('n_buy', 0)
            if n_buy > cur_n:
                events[-1] = {'date': d, 'signal': 'fcf_strong' if n_buy >= 2 else 'fcf_buy',
                              'pe_pct': round(pe_p*100) if pe_p is not None else None,
                              'pb_pct': round(pb_p*100) if pb_p is not None else None,
                              'dyr_pct': round(dy_p*100) if dy_p is not None else None,
                              'n_buy': n_buy}
                last_trig = i
            elif n_buy == cur_n:
                # 注意：不能用 x or 100（PB=0 是合法值且 falsy，or 会误判为 100）——v1.1 二级修复
                nw = {'date': d, 'signal': 'fcf_strong' if n_buy >= 2 else 'fcf_buy',
                      'pe_pct': round(pe_p*100) if pe_p is not None else None,
                      'pb_pct': round(pb_p*100) if pb_p is not None else None,
                      'dyr_pct': round(dy_p*100) if dy_p is not None else None,
                      'n_buy': n_buy}
                if _score(nw) < _score(cur):
                    events[-1] = nw
                    last_trig = i

    # 历史类似情况统计：满足 fcf_buy 条件的交易日（全窗口 2024-09 起，20日去重）
    similar = []
    last_s = -999
    for i, d in enumerate(dates):
        if d not in val_map:
            continue
        vm = val_map[d]
        pe_p = vm['pe_ttm_pct'] if vm['pe_ttm_pct'] is not None else None
        pb_p = vm['pb_pct'] if vm['pb_pct'] is not None else None
        dy_p = vm['dyr_pct'] if vm['dyr_pct'] is not None else None
        n_buy = sum([pe_p is not None and pe_p < 0.33,
                     pb_p is not None and pb_p < 0.33,
                     dy_p is not None and dy_p > 0.66])
        if n_buy < 1 or i - last_s < 20:
            continue
        last_s = i
        # 次日收盘买入，20/60日收益（与红利 detail 口径一致；回测矩阵用次日开盘，两者差异小）
        entry = None
        fwd20 = fwd60 = None
        if i + 1 < len(closes):
            entry = closes[i + 1]
        if entry and entry > 0:
            if i + 1 + 20 < len(closes):
                fwd20 = (closes[i + 1 + 20] / entry - 1) * 100
            if i + 1 + 60 < len(closes):
                fwd60 = (closes[i + 1 + 60] / entry - 1) * 100
        similar.append({
            'date': d,
            'pe_pct': round(pe_p*100) if pe_p is not None else None,
            'pb_pct': round(pb_p*100) if pb_p is not None else None,
            'dyr_pct': round(dy_p*100) if dy_p is not None else None,
            'fwd20': round(fwd20, 1) if fwd20 is not None else None,
            'fwd60': round(fwd60, 1) if fwd60 is not None else None,
        })

    # 统计摘要
    stats = {}
    if similar:
        def _med(xs):
            s = sorted(xs); n = len(s)
            return s[n//2] if n % 2 else (s[n//2-1]+s[n//2])/2
        fwd20s = [s['fwd20'] for s in similar if s['fwd20'] is not None]
        fwd60s = [s['fwd60'] for s in similar if s['fwd60'] is not None]
        stats = {
            'count': len(similar),
            'fwd20_median': round(_med(fwd20s), 1) if fwd20s else None,
            'fwd20_winrate': round(sum(1 for x in fwd20s if x > 0) / len(fwd20s) * 100, 1) if fwd20s else None,
            'fwd60_median': round(_med(fwd60s), 1) if fwd60s else None,
            'fwd60_winrate': round(sum(1 for x in fwd60s if x > 0) / len(fwd60s) * 100, 1) if fwd60s else None,
        }

    current_dd = dd_series[-1] if dd_series else 0
    return jsonify({
        'code': code, 'name': name, 'date': target_date,
        'dates': dates, 'closes': closes, 'dd_series': dd_series,
        'pe_pct_series': pe_series, 'pb_pct_series': pb_series, 'dyr_pct_series': dyr_series,
        'events': events, 'similar': similar, 'stats': stats,
        'current_dd': current_dd,
        'data_note': FCF_DATA_NOTE,
    })


# ═══════════════════════════════════════════════
# API: GET /api/market-scan/coal-advice（中证煤炭网格投资建议）
# ═══════════════════════════════════════════════

COAL_INDEX = ('399998', '中证煤炭')
COAL_GRID_STEP = 10  # 网格间距 %（int/floor 档位语义回测最优 10% +31.6pp，analysis/grid_step_sens.py）
COAL_DATA_NOTE = '网格回测未计滑点/手续费（10%间距约500次交易）；超额与趋势强度负相关，煤炭若开启大牛市网格将跑输持有'


def _grid_backtest(closes, step_pct, cash=100000):
    """百分比间距网格回测：每 step_pct% 一档，初始买1/3，档位跌买涨卖，返回(总资产, 交易次数)"""
    if len(closes) < 10:
        return None
    c, s = cash, 0.0
    low = min(closes) * 0.95
    if low <= 0:
        return None
    s = (cash / 3) / closes[0]
    c -= cash / 3
    cg = int((closes[0] - low) / (low * step_pct / 100))
    trades = 0
    per = cash / 10
    for i in range(1, len(closes)):
        p = closes[i]
        g = int((p - low) / (low * step_pct / 100))
        if g < cg:
            for _ in range(cg - g):
                if c > per:
                    s += per / p
                    c -= per
                    trades += 1
            cg = g
        elif g > cg:
            for _ in range(g - cg):
                if s > 0:
                    amt = min(s * p, per)
                    s -= amt / p
                    c += amt
                    trades += 1
            cg = g
    return c + s * closes[-1], trades


@app.route('/api/market-scan/grid-advice')
def api_market_grid_advice():
    """网格策略自动回测推荐：输入指数代码 → 间距扫描(3/5/8/10/12%) + 箱体检测 + 适配性判断"""
    code = request.args.get('code', '399998')
    target_date = request.args.get('date', '')
    db = get_db()

    # 指数身份（yaml：名称 + 分类，etf 分类数据不可靠）
    try:
        import yaml
        style = yaml.safe_load(open(INDEX_RS_CONFIG, encoding='utf-8'))
        name, category = code, ''
        for cat, items in style.get('categories', {}).items():
            for it in items:
                if it.get('code') == code:
                    name = it.get('name', code)
                    category = cat
    except Exception:
        name, category = code, ''
    if category == 'etf':
        return jsonify({'error': 'ETF 数据不可靠（index_daily_kline 中 ETF 价格字段量级错误），请输入对应指数代码（如 515220→399998 中证煤炭）', 'code': code})

    if not target_date:
        r = db.execute("SELECT MAX(date) FROM index_daily_kline").fetchone()
        if r is None or r[0] is None:
            return jsonify({'error': 'no data', 'date': ''})
        target_date = r[0]

    rows = db.execute("""
        SELECT date, close FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date<=?
        ORDER BY date
    """, (code, target_date)).fetchall()
    if len(rows) < 120:
        return jsonify({'error': '数据不足（<120天）', 'code': code})
    dates = [r['date'] for r in rows]
    closes = [r['close'] for r in rows]
    cur = closes[-1]

    # 回测窗口：优先 2018 起（与煤炭研究一致），不足则用全部数据并标注
    start8 = '2018-01-01'
    idx8 = [i for i, d in enumerate(dates) if d >= start8]
    if len(idx8) >= 300:
        closes_bt = closes[idx8[0]:]
        window = '2018-01-01 ~ ' + dates[-1]
        short = False
    else:
        closes_bt = closes
        window = dates[0] + ' ~ ' + dates[-1]
        short = True

    # 间距扫描
    import math
    scan = []
    best = None
    best_bt = None
    hold_bt = 100000 / closes_bt[0] * closes_bt[-1]
    for st in (3, 5, 8, 10, 12):
        r = _grid_backtest(closes_bt, st)
        if not r:
            continue
        excess = (r[0] / hold_bt - 1) * 100
        scan.append({
            'step': st,
            'grid_ret': round((r[0] / 100000 - 1) * 100, 1),
            'hold_ret': round((hold_bt / 100000 - 1) * 100, 1),
            'excess': round(excess, 1),
            'trades': r[1],
        })
        if r[1] >= 20 and (best is None or excess > best['excess']):
            best = {'step': st, 'excess': round(excess, 1), 'trades': r[1]}
            best_bt = r
    if best is None:
        best = {'step': 8, 'excess': 0, 'trades': 0, 'trades_ok': False}
    else:
        best['trades_ok'] = True

    # 箱体：下沿=近250日最低×0.95（网格回测基准），上沿=近3年最高
    seg250 = closes[-250:]
    lo250 = min(seg250)
    hi250 = max(seg250)
    # 近3年窗口（B2 修复：原实现误用当年年初至今）
    from datetime import datetime as _dt, timedelta as _td
    d3 = (_dt.strptime(dates[-1], '%Y-%m-%d') - _td(days=3 * 365)).strftime('%Y-%m-%d')
    seg3 = [c for c, d in zip(closes, dates) if d >= d3]
    lo3, hi3 = min(seg3), max(seg3)
    base = lo250 * 0.95
    top = hi3
    pos = (cur - lo250) / (hi250 - lo250) * 100 if hi250 > lo250 else 50

    # 适配性判断
    max_ex = max(s['excess'] for s in scan) if scan else 0
    if max_ex > 10:
        fit = 'good'
        fit_note = '高波动/横盘特征明显，网格大概率赚取正超额（%s起 %s%%间距 %+.1fpp）' % (window[:4], best['step'], best['excess'])
    elif max_ex > 0:
        fit = 'neutral'
        fit_note = '超额有限（最高 %+.1fpp），网格可做但利润薄，需控制仓位' % max_ex
    else:
        fit = 'bad'
        fit_note = '所有间距超额≤0（最高 %+.1fpp），趋势太强，网格会大幅跑输持有，不建议' % max_ex

    # 分年度（推荐间距）
    annual = []
    for y in sorted(set(d[:4] for d in dates)):
        idx = [i for i, d in enumerate(dates) if d.startswith(y)]
        if len(idx) < 80:
            continue
        seg = closes[idx[0]:idx[-1] + 1]
        r = _grid_backtest(seg, best['step'])
        if not r:
            continue
        hold = 100000 / seg[0] * seg[-1]
        annual.append({'year': y, 'idx_ret': round((seg[-1] / seg[0] - 1) * 100, 1),
                       'grid_ret': round((r[0] / 100000 - 1) * 100, 1),
                       'excess': round((r[0] / hold - 1) * 100, 1)})

    # 波动特征
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    mean_r = sum(rets) / len(rets) if rets else 0
    ann_vol = (sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 * math.sqrt(252) * 100

    return jsonify({
        'code': code, 'name': name, 'date': target_date,
        'cur': round(cur, 2),
        'window': window, 'short_window': short,
        'range': {'lo250': round(lo250, 2), 'hi250': round(hi250, 2),
                  'lo3y': round(lo3, 2), 'hi3y': round(hi3, 2), 'pos_250': round(pos)},
        'suggest': {'step': best['step'], 'base': round(base, 2), 'top': round(top, 2),
                    'excess': best['excess'], 'trades': best['trades'], 'trades_ok': best.get('trades_ok', True)},
        'scan': scan,
        'fit': fit, 'fit_note': fit_note,
        'annual': annual[-8:],
        'stats': {'ann_vol': round(ann_vol, 1),
                  'grid_ret': round((best_bt[0] / 100000 - 1) * 100, 1) if best_bt else None,
                  'hold_ret': round((hold_bt / 100000 - 1) * 100, 1)},
        'data_note': '回测未计滑点/手续费；网格超额与趋势强度负相关，趋势市会跑输持有' + ('；本指数数据窗口较短，结论仅供参考' if short else ''),
    })


# ═══════════════════════════════════════════════
# API: GET /api/market-scan/hk-etf（港股红利ETF 观察）
# ═══════════════════════════════════════════════

HK_ETFS = [
    ('513820', '港股通高股息', 0.60, '中证港股通高股息', '汇添富'),
    ('159691', '港股通高股息精选', 0.52, '中证港股通高股息精选', '工银瑞信'),
    ('513630', '标普港股红利低波', 0.60, '标普港股通低波红利', '摩根'),
    ('159545', '恒生港股通高息低波', 0.20, '恒生港股通高股息低波动', '易方达'),
    ('512000', '券商ETF', 0.50, '证券公司(399975)', '华宝'),
]


@app.route('/api/market-scan/hk-etf')
def api_market_hk_etf():
    """港股红利ETF 观察：最新行情/涨跌/成立来年化/费率（静态信息在配置）"""
    db = get_db()
    result = []
    for code, name, fee, index_name, mgr in HK_ETFS:
        rows = db.execute("""
            SELECT date, close FROM hk_etf_daily
            WHERE stock_code=? ORDER BY date
        """, (code,)).fetchall()
        if len(rows) < 2:
            continue
        dates = [r['date'] for r in rows]
        closes = [r['close'] for r in rows]
        cur = closes[-1]
        prev = closes[-2]
        chg = (cur / prev - 1) * 100 if prev else 0
        # 成立以来年化（自然日）
        from datetime import datetime as _dt
        y0 = (_dt.strptime(dates[-1], '%Y-%m-%d') - _dt.strptime(dates[0], '%Y-%m-%d')).days / 365.25
        total = cur / closes[0] - 1
        ann = (1 + total) ** (1 / y0) - 1 if total > -1 else -1
        # ── 信号（回测依据 analysis/hk_div_buypoint.py）──
        seg = closes[-250:]
        hi250 = max(seg)
        dd250 = (hi250 - cur) / hi250 * 100
        pos250 = (cur - min(seg)) / (hi250 - min(seg)) * 100 if hi250 > min(seg) else 50
        # 信号覆盖范围：仅 513820(930914)/159691(930839) 有回测依据（hk_div_buypoint.py）；
        # 513630/159545 数据年限不足无结论 → 降级 hold 观察；512000(type=a) 是券商网格标的 → 不套港股信号
        if code in ('513630', '159545'):
            advice_level, advice = 'hold', '观察（数据年限不足，暂无回测结论；买点规则待验证）'
        elif code == '512000':
            advice_level, advice = 'hold', '券商网格标的（见⛳券商指数 tab 档位表）'
        elif dd250 >= (20 if code == '159691' else 15):
            advice_level, advice = 'buy', '买入（深回撤' + str(20 if code == '159691' else 15) + '%触发，60日胜率' + ('67%' if code == '159691' else '72%') + '）'
        elif pos250 > 85:
            advice_level, advice = 'caution', '高位区（250日位置' + str(round(pos250)) + '%），勿追高'
        else:
            advice_level, advice = 'hold', '观望（回撤' + str(round(dd250, 1)) + '%未到' + str(20 if code == '159691' else 15) + '%买点）'
        result.append({
            'code': code, 'name': name,
            'fee': fee, 'index_name': index_name, 'mgr': mgr,
            'date': dates[-1], 'close': round(cur, 3), 'chg': round(chg, 2),
            'ann': round(ann * 100, 2), 'total': round(total * 100, 1),
            'start_date': dates[0], 'days': len(dates),
            'dd_250': round(dd250, 1), 'pos_250': round(pos250),
            'lo_250': round(min(seg), 3), 'hi_250': round(max(seg), 3),
            'type': 'a' if code == '512000' else 'hk',
            'advice_level': advice_level, 'advice': advice,
        })
    return jsonify({'date': result[0]['date'] if result else '', 'etfs': result})


# ═══════════════════════════════════════════════
# API: GET /api/market-scan/full-return-compare（红利标的全收益对比）
# ═══════════════════════════════════════════════

@app.route('/api/market-scan/full-return-compare')
def api_market_full_return_compare():
    """多红利标的全收益归一化对比（起点=100）"""
    db = get_db()
    pool = [
        # (code, name, source表, 类型)
        ('H00922', '中证红利·全收益', 'index_full_return_daily', 'tri'),
        ('000922', '中证红利·价格', 'index_daily_kline', 'price'),
        ('980092', '国证自由现金流', 'index_daily_kline', 'price'),
        ('H30269', '红利低波·价格', 'index_daily_kline', 'price'),
        ('931468', '红利质量·价格', 'index_daily_kline', 'price'),
        ('000015', '红利指数·价格', 'index_daily_kline', 'price'),
        ('931848', '800红利低波·价格', 'index_daily_kline', 'price'),
        ('000300', '沪深300', 'index_daily_kline', 'price'),
    ]
    series = []
    for code, name, table, stype in pool:
        if table == 'index_full_return_daily':
            rows = db.execute(f"""SELECT date, close FROM {table}
                WHERE stock_code=? AND date>='2018-01-01' ORDER BY date""", (code,)).fetchall()
        else:
            rows = db.execute(f"""SELECT date, close FROM {table}
                WHERE stock_code=? AND kline_type='normal' AND date>='2018-01-01' ORDER BY date""", (code,)).fetchall()
        if not rows:
            continue
        dates = [r['date'] for r in rows]
        closes = [r['close'] for r in rows]
        base = closes[0]
        series.append({'code': code, 'name': name, 'type': stype,
                       'dates': dates, 'values': [round(c / base * 100, 1) for c in closes]})
    # 港股四只（全收益 hfq）
    hk_names = {'513820': '港股通高股息', '159691': '港股通高股息精选',
                '513630': '标普港股红利低波', '159545': '恒生港股通高息低波'}
    for code, name in hk_names.items():
        rows = db.execute("SELECT date, close FROM hk_etf_full_return WHERE stock_code=? ORDER BY date", (code,)).fetchall()
        if not rows:
            continue
        dates = [r['date'] for r in rows]
        closes = [r['close'] for r in rows]
        base = closes[0]
        series.append({'code': code, 'name': name + '·全收益', 'type': 'hk_etf',
                       'dates': dates, 'values': [round(c / base * 100, 1) for c in closes],
                       'start': dates[0]})
    return jsonify({'series': series, 'note': '全收益=含分红再投资（H00922/港股ETF）；带·价格为价格口径（未计分红，H30269/931468/000015/931848 暂无可靠全收益源）；各自起点=100，窗口不同（港股自2023-2024起）'})


# ═══════════════════════════════════════════════
# API: GET /api/market-scan/div-sustainability（个股分红可持续性）
# ═══════════════════════════════════════════════

@app.route('/api/market-scan/div-sustainability')
def api_market_div_sustainability():
    """个股分红可持续性：连续年限/派息率/FCF覆盖率/股息增长/历年序列"""
    code = request.args.get('code', '')
    if not code:
        return jsonify({'error': 'code 必填'}), 400
    db = get_db()

    rows = db.execute("""SELECT * FROM dividend_records
        WHERE code=? AND kind='stock' AND status='implemented' AND dividend > 0
        ORDER BY ex_date""", (code,)).fetchall()
    if not rows:
        return jsonify({'error': '无分红数据（需先运行 scripts/fetch_dividends.py）', 'code': code})

    # 历年每股分红（按 ex_date 年份聚合）
    year_map = {}
    for r in rows:
        y = r['ex_date'][:4]
        year_map[y] = year_map.get(y, 0) + (r['dividend'] or 0)
    years = sorted(year_map.keys())

    # 连续分红年限（W1 修复：从最大有分红年份回推，避免当年未分红时误判为 0）
    streak = 0
    if years:
        for y in range(int(years[-1]), int(years[-1]) - 20, -1):
            if str(y) in year_map:
                streak += 1
            else:
                break

    # 最新派息率（最后一次有值的）
    payout = None
    for r in reversed(rows):
        if r['payout_ratio'] is not None:
            payout = round(r['payout_ratio'] * 100, 1)
            break

    # 现金流覆盖（W4 修复：报告期取 ex_date 之前最近年报，避免错配一年）
    ocf_coverage = None
    div_amount = None
    div_year = None
    for r in reversed(rows):
        if r['total_amount']:
            div_amount = r['total_amount']
            div_year = r['ex_date'][:4]
            break
    f = None
    if div_year:
        f = db.execute("""SELECT operating_cash_flow FROM stock_financials_annual
            WHERE stock_code=? AND report_date<=? ORDER BY report_date DESC LIMIT 1""", (code, div_year + '-12-31')).fetchone()
    if f and div_amount and div_amount > 0:
        ocf = f['operating_cash_flow'] or 0
        ocf_coverage = round(ocf / div_amount, 2) if ocf else None

    # 股息增长（W2 修复：近 5 个年度区间 CAGR）
    div_growth = None
    if len(years) >= 6:
        y0, y1 = years[-6], years[-1]
        d0, d1 = year_map[y0], year_map[y1]
        n = int(y1) - int(y0)
        if d0 > 0 and d1 > 0 and n > 0:
            div_growth = round(((d1 / d0) ** (1 / n) - 1) * 100, 1)

    # 分红贡献度（W3：tr_dri 已入库，息 vs 涨）
    dri = db.execute("SELECT cagr_y10, cagr_fs, p_r_fs FROM stock_dri_metrics WHERE stock_code=?", (code,)).fetchone()
    contribution = None
    if dri and dri['cagr_y10'] is not None:
        contribution = {'cagr_y10': round(dri['cagr_y10'] * 100, 2),
                        'cagr_fs': round(dri['cagr_fs'] * 100, 2) if dri['cagr_fs'] is not None else None,
                        'p_r_fs': round(dri['p_r_fs'] * 100, 1) if dri['p_r_fs'] is not None else None}

    return jsonify({
        'code': code, 'dividend_count': len(rows),
        'streak_years': streak, 'payout_ratio': payout,
        'ocf_coverage': ocf_coverage, 'div_growth': div_growth,
        'contribution': contribution,
        'yearly': [{'year': y, 'dividend': round(year_map[y], 4)} for y in years],
        'note': '派息率/FCF覆盖率仅个股（理杏仁+本地财务）；ETF/基金无派息率概念',
    })


@app.route('/api/market-scan/coal-advice')
def api_market_coal_advice():
    """中证煤炭网格投资建议：网格档位位置 + 网格回测摘要（支持历史回看）"""
    target_date = request.args.get('date', '')
    db = get_db()
    if not target_date:
        r = db.execute("SELECT MAX(date) FROM index_daily_kline").fetchone()
        if r is None or r[0] is None:
            return jsonify({'error': 'no data', 'date': ''})
        target_date = r[0]

    code, name = COAL_INDEX
    rows = db.execute("""
        SELECT date, close FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date<=?
        ORDER BY date DESC LIMIT 300
    """, (code, target_date)).fetchall()
    rows = list(reversed(rows))
    if len(rows) < 250:
        return jsonify({'error': 'no data', 'date': target_date})
    closes = [r['close'] for r in rows]
    current = closes[-1]
    high250 = max(closes[-250:])
    dd_250 = (high250 - current) / high250 * 100
    pos_250 = (current - min(closes[-250:])) / (max(closes[-250:]) - min(closes[-250:])) * 100 if max(closes[-250:]) > min(closes[-250:]) else 50

    # 8% 网格当前档位（以近250日最低*0.95为基准；floor 保证现价落在档位区间内）
    base = min(closes[-250:]) * 0.95
    step = base * COAL_GRID_STEP / 100
    level = int((current - base) / step)
    grid_low = base + level * step
    grid_high = grid_low + step

    # 2018 起网格回测摘要
    rows8 = db.execute("""
        SELECT close FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>=? AND date<=?
        ORDER BY date
    """, (code, '2018-01-01', target_date)).fetchall()
    closes8 = [r['close'] for r in rows8]
    g_ret = None
    h_ret = None
    trades = None
    if len(closes8) >= 300:
        r = _grid_backtest(closes8, COAL_GRID_STEP)
        if r:
            g_ret = round((r[0] / 100000 - 1) * 100, 1)
            trades = r[1]
        h_ret = round((closes8[-1] / closes8[0] - 1) * 100, 1)

    # 建议（网格法：250日位置 + 回撤）；level 保留档位整数，建议等级用 advice_lvl（B1 修复）
    advice_lvl = 'hold'
    if pos_250 >= 75:
        advice = '网格高位区（250日位置' + str(round(pos_250)) + '%），留意减仓档，谨慎新增'
        advice_lvl = 'reduce'
    elif pos_250 <= 35 or dd_250 >= 15:
        advice = '网格低位区，可执行加仓档，分批买入'
        advice_lvl = 'buy'
    else:
        advice = '网格中位区，按既定间距运转'

    return jsonify({
        'date': target_date, 'code': code, 'name': name,
        'close': round(current, 2), 'dd_250': round(dd_250, 1), 'pos_250': round(pos_250),
        'grid': {
            'step_pct': COAL_GRID_STEP, 'level': level,
            'grid_low': round(grid_low, 2), 'grid_high': round(grid_high, 2),
            'ret_2018': g_ret, 'hold_2018': h_ret,
            'excess_2018': round((g_ret - h_ret) / (1 + h_ret / 100), 1) if g_ret is not None and h_ret is not None else None,
            'trades': trades,
        },
        'advice': advice, 'advice_level': advice_lvl,
        'data_note': COAL_DATA_NOTE,
    })


@app.route('/api/market-scan/coal-advice-detail')
def api_market_coal_detail():
    """中证煤炭网格详情：近10年K线 + 网格线 + 年度网格vs持有 + 间距敏感性"""
    target_date = request.args.get('date', '')
    db = get_db()
    if not target_date:
        r = db.execute("SELECT MAX(date) FROM index_daily_kline").fetchone()
        if r is None or r[0] is None:
            return jsonify({'error': 'no data', 'date': ''})
        target_date = r[0]

    code, name = COAL_INDEX
    rows = db.execute("""
        SELECT date, close FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>=date(?,'-10 years') AND date<=?
        ORDER BY date
    """, (code, target_date, target_date)).fetchall()
    dates = [r['date'] for r in rows]
    closes = [r['close'] for r in rows]
    if len(dates) < 300:
        return jsonify({'error': 'no data'})
    current = closes[-1]

    # 网格线（10年最低*0.95 为基准，8% 一档；当前档位 floor 保证在现价下方）
    base = min(closes) * 0.95
    step = base * COAL_GRID_STEP / 100
    lvl0 = int((current - base) / step)
    grid_lines = []
    for lv in range(max(0, lvl0 - 15), lvl0 + 16):
        v = base + lv * step
        if v <= max(closes) * 1.02 and v >= min(closes) * 0.98:
            grid_lines.append({'value': round(v, 1), 'level': lv, 'active': lv == lvl0})

    # 年度：指数涨跌 / 网格 / 持有 / 超额 / 交易次数
    annual = []
    years = sorted(set(d[:4] for d in dates))
    low_all = min(closes) * 0.95
    for y in years:
        idx = [i for i, d in enumerate(dates) if d.startswith(y)]
        if len(idx) < 80:
            continue
        seg = closes[idx[0]:idx[-1] + 1]
        r = _grid_backtest(seg, COAL_GRID_STEP)
        if not r:
            continue
        hold = 100000 / seg[0] * seg[-1]
        crosses = 0
        cg = int((seg[0] - low_all) / (low_all * COAL_GRID_STEP / 100))
        for p in seg[1:]:
            g = round((p - low_all) / (low_all * COAL_GRID_STEP / 100))
            crosses += abs(g - cg)
            cg = g
        annual.append({
            'year': y,
            'idx_ret': round((seg[-1] / seg[0] - 1) * 100, 1),
            'grid_ret': round((r[0] / 100000 - 1) * 100, 1),
            'hold_ret': round((hold / 100000 - 1) * 100, 1),
            'excess': round((r[0] / hold - 1) * 100, 1),
            'trades': r[1], 'crosses': crosses,
        })

    # 间距敏感性（2018起）
    rows8 = db.execute("""
        SELECT close FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>=? AND date<=?
        ORDER BY date
    """, (code, '2018-01-01', target_date)).fetchall()
    closes8 = [r['close'] for r in rows8]
    step_sens = []
    hold8 = 100000 / closes8[0] * closes8[-1] if closes8 else 0
    for st in (5, 8, 10, 12):
        r = _grid_backtest(closes8, st) if len(closes8) >= 300 else None
        if r:
            step_sens.append({
                'step': st,
                'grid_ret': round((r[0] / 100000 - 1) * 100, 1),
                'hold_ret': round((hold8 / 100000 - 1) * 100, 1),
                'excess': round((r[0] / hold8 - 1) * 100, 1),
                'trades': r[1],
            })

    # 波动特征
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    import math
    mean_r = sum(rets) / len(rets) if rets else 0
    ann_vol = (sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 * math.sqrt(252) * 100

    return jsonify({
        'code': code, 'name': name, 'date': target_date,
        'close': round(current, 2),
        'dates_long': dates, 'closes_long': closes,
        'grid_lines': grid_lines, 'grid_step': COAL_GRID_STEP,
        'annual': annual, 'step_sens': step_sens,
        'stats': {
            'ann_vol': round(ann_vol, 1),
            'grid_ret_2018': round((_grid_backtest(closes8, COAL_GRID_STEP)[0] / 100000 - 1) * 100, 1) if len(closes8) >= 300 and _grid_backtest(closes8, COAL_GRID_STEP) else None,
            'hold_2018': round((hold8 / 100000 - 1) * 100, 1) if closes8 else None,
        },
        'data_note': COAL_DATA_NOTE,
    })

# ═══════════════════════════════════════════════
# API: GET /api/market-scan/red-metrics（红利指数温度计：拥挤度/恐慌贪婪/股债息差）
# ═══════════════════════════════════════════════

@app.route('/api/market-scan/red-metrics')
def api_market_red_metrics():
    """红利指数三维指标 + 温度计：拥挤度 / 恐慌贪婪 / 股债息差（实时计算，支持历史回看）"""
    code = request.args.get('code', '000922')
    target_date = request.args.get('date', '')
    if not target_date:
        r = get_db().execute("SELECT MAX(date) FROM index_daily_kline").fetchone()
        if r is None or r[0] is None:
            return jsonify({'error': 'no data', 'date': ''})
        target_date = r[0]
    try:
        from scanners.red_dividend_metrics import compute_all
        result = compute_all(code, target_date)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    if not result.get('spread'):
        return jsonify({'error': '数据不足', 'code': code, 'date': target_date})
    # 指数名称（index_style.yaml，与 grid-advice 同源）
    name = code
    try:
        import yaml
        style = yaml.safe_load(open(INDEX_RS_CONFIG, encoding='utf-8'))
        for cat, items in style.get('categories', {}).items():
            for it in items:
                if it.get('code') == code:
                    name = it.get('name', code)
                    break
    except Exception:
        pass
    result['name'] = name
    return jsonify(result)


# ═══════════════════════════════════════════════
# API: GET /api/market-scan/dividend-lab（回撤实验室）
# ═══════════════════════════════════════════════

@app.route('/api/market-scan/dividend-lab')
def api_market_dividend_lab():
    """回撤实验室：口径切换（250日滚动/年度）+ 阈值可调，全收益口径统计触发点收益。
    参数：mode=250|annual（默认250）、threshold=0.10（默认0.10，回测最优）"""
    mode = request.args.get('mode', '250')
    try:
        threshold = float(request.args.get('threshold', '0.10'))
    except (TypeError, ValueError):
        return jsonify({'error': 'threshold 必须为数字'}), 400
    if not 0 < threshold <= 1:
        return jsonify({'error': 'threshold 必须在 (0,1] 区间'}), 400
    if mode not in ('250', 'annual'):
        return jsonify({'error': 'mode 必须为 250 或 annual'}), 400
    db = get_db()

    # 全收益数据（H00922，2018 起）
    rows = db.execute("""
        SELECT date, close FROM index_full_return_daily
        WHERE stock_code='H00922' ORDER BY date
    """).fetchall()
    if len(rows) < 260:
        return jsonify({'error': 'no data'})
    dates = [r['date'] for r in rows]
    closes = [r['close'] for r in rows]

    # 回撤序列
    dds = []
    if mode == 'annual':
        cur_year = None
        year_hi = None
        for i in range(len(closes)):
            y = dates[i][:4]
            if y != cur_year:
                cur_year = y
                year_hi = closes[i]
            if closes[i] > year_hi:
                year_hi = closes[i]
            dds.append((year_hi - closes[i]) / year_hi * 100)
        start_i = 0
    else:
        for i in range(len(closes)):
            w = closes[max(0, i-249):i+1]
            hi = max(w)
            dds.append((hi - closes[i]) / hi * 100)
        start_i = 250

    # 触发事件（20日去重）
    events = []
    last = -999
    for i in range(start_i, len(closes)):
        if dds[i] >= threshold * 100 and i - last >= 20:
            events.append({'date': dates[i], 'dd': round(dds[i], 1), 'close': round(closes[i], 2)})
            last = i

    # 20/60 日收益（次日收盘买入）
    def _med(xs):
        s = sorted(xs); n = len(s)
        return s[n//2] if n % 2 else (s[n//2-1]+s[n//2])/2
    stats = {}
    for w in (20, 60):
        rets = []
        for ev in events:
            i = dates.index(ev['date'])
            if i + 1 + w >= len(closes):
                continue
            entry = closes[i+1]
            rets.append((closes[i+1+w] / entry - 1) * 100)
        if rets:
            stats[w] = {
                'n': len(rets),
                'winrate': round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                'median': round(_med(rets), 2),
                'avg': round(sum(rets) / len(rets), 2),
            }

    return jsonify({
        'mode': mode, 'threshold': threshold,
        'data_range': [dates[0], dates[-1]],
        'events': events, 'stats': stats,
    })


# ═══════════════════════════════════════════════
# API: GET /api/strongest-index
# ═══════════════════════════════════════════════

@app.route('/api/strongest-index')
def api_strongest_index():
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    params_str = request.args.get('params', '{}')
    try: params = json.loads(params_str)
    except: params = {}

    db = get_db()
    pools_cfg = load_index_pools()
    conditions = params.get('conditions', {})
    auto_relax = params.get('auto_relax', True)
    relax_step = params.get('relax_step', 5)
    pool_top_n = {'market': 3, 'sector_l1': 5, 'sector_l2': 10, 'thematic': 50, 'strategy': 20}
    pool_labels = {'market': '市场指数', 'sector_l1': '一级行业', 'sector_l2': '二级行业', 'thematic': '主题指数', 'strategy': '策略指数'}

    def check(r, rs20_thr):
        if conditions.get('rs_250',{}).get('enabled',True) and (r['rs_20']or 0) >= 0 and (r['rs_250']or 0) < conditions['rs_250'].get('threshold',80): return False
        if conditions.get('rs_60',{}).get('enabled',True) and (r['rs_60']or 0) < conditions['rs_60'].get('threshold',85): return False
        if conditions.get('rs_20',{}).get('enabled',True) and (r['rs_20']or 0) < rs20_thr: return False
        if conditions.get('ma_align',{}).get('enabled',True) and not ((r['ma50']or 0) > (r['ma150']or 0) > (r['ma200']or 0)): return False
        if conditions.get('ad_slope',{}).get('enabled',True) and (r['ad_slope_20d']or 0) <= 0: return False
        return True

    result_pools = {}
    for pn, codes in pools_cfg.items():
        if pn not in pool_top_n: continue
        tn = pool_top_n[pn]
        ph = ','.join(['?' for _ in codes])
        # 先取最新有数据的日期
        latest = db.execute("SELECT MAX(date) as d FROM index_rs_daily WHERE date <= ?", (target_date,)).fetchone()
        if not latest or not latest['d']: continue
        ldate = latest['d']
        rows = db.execute(f"SELECT * FROM index_rs_daily WHERE date = ? AND stock_code IN ({ph})", [ldate] + codes).fetchall()

        rs20_thr = conditions.get('rs_20',{}).get('threshold', 90)
        flt = [r for r in rows if check(r, rs20_thr)]
        relaxed = False
        if auto_relax and len(flt) < tn:
            r2 = rs20_thr - relax_step
            if r2 >= 60:
                flt = [r for r in rows if check(r, r2)]
                rs20_thr = r2; relaxed = True
        flt.sort(key=lambda x: (x['rs_20']or 0, x['rs_60']or 0, x['rs_250']or 0), reverse=True)
        flt = flt[:tn]
        result_pools[pn] = {'top_n': tn, 'total': len(rows), 'relaxed': relaxed, 'applied_rs20': int(rs20_thr),
            'indices': [{'code': r['stock_code'], 'name': '', 'rs_20': r['rs_20'], 'rs_60': r['rs_60'],
            'rs_250': r['rs_250'], 'ma50': r['ma50'], 'ma150': r['ma150'], 'ma200': r['ma200'],
            'ad_slope': round(r['ad_slope_20d']or 0,1)} for r in flt]}

    idx_names = load_index_names()
    for pd in result_pools.values():
        for s in pd['indices']: s['name'] = idx_names.get(s['code'], s['code'])

    # ── 全量指数数据（供复核表格） ──
    # ── 全量指数数据（供复核表格） ──
    all_rows = db.execute(f"""
        SELECT * FROM index_rs_daily
        WHERE date = (SELECT MAX(date) FROM index_rs_daily WHERE date <= ?)
        ORDER BY rs_20 DESC
    """, (target_date,)).fetchall()

    # code→pool_type 映射
    code_pool = {}
    for pn, codes in pools_cfg.items():
        for c in codes: code_pool[c] = pool_labels.get(pn, pn)

    all_indices = []
    for r in all_rows:
        all_indices.append({
            'code': r['stock_code'], 'name': idx_names.get(r['stock_code'], r['stock_code']),
            'pool': code_pool.get(r['stock_code'], ''),
            'rs_20': r['rs_20'], 'rs_60': r['rs_60'], 'rs_250': r['rs_250'],
            'ret_20': round(r['ret_20'] or 0, 2), 'ret_60': round(r['ret_60'] or 0, 2),
            'ma50': round(r['ma50'] or 0, 0), 'ma150': round(r['ma150'] or 0, 0), 'ma200': round(r['ma200'] or 0, 0),
            'ad_slope': round(r['ad_slope_20d'] or 0, 1),
        })

    return jsonify({'date': target_date, 'pools': result_pools, 'all_indices': all_indices})

# ═══════════════════════════════════════════════
# API: GET /api/stock-name
# ═══════════════════════════════════════════════
@app.route('/api/market-scan/margin-by-sector')
def api_margin_by_sector():
    """两融数据：按L1行业拆解 (日频)"""
    start = request.args.get('start', '2026-01-01')
    end = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))
    db = get_db()
    try:
        import yaml
        cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'index_style.yaml'), 'r', encoding='utf-8'))
        sectors = cfg['categories']['sector_l1']
    except:
        sectors = []
    sector_stocks = {}
    for sec in sectors:
        code = sec['code']
        name = sec['name'].replace('全指','')
        stocks = db.execute('SELECT stock_code FROM index_constituents WHERE index_code=?', (code,)).fetchall()
        codes = [s['stock_code'] for s in stocks]
        if codes: sector_stocks[name] = codes
    dates = [r[0] for r in db.execute('SELECT DISTINCT date FROM daily_margin_history WHERE date>=? AND date<=? ORDER BY date', (start, end)).fetchall()]
    result = {'dates': dates, 'sectors': {}}
    for sec_name, codes in sector_stocks.items():
        bal = []; net = []
        for dt in dates:
            ph = ','.join(['?']*len(codes))
            row = db.execute(f'SELECT SUM(financing_balance)/1e8 as fb, SUM(net_purchase)/1e8 as nt FROM daily_margin_history WHERE date=? AND stock_code IN ({ph})', (dt,)+tuple(codes)).fetchone()
            bal.append(round(row[0],0) if row and row[0] else 0)
            net.append(round(row[1],0) if row and row[1] else 0)
        result['sectors'][sec_name] = {'balance': bal, 'net_buy': net}
    total_bal = []; total_net = []
    for dt in dates:
        row = db.execute('SELECT SUM(financing_balance)/1e8 as fb, SUM(net_purchase)/1e8 as nt FROM daily_margin_history WHERE date=?', (dt,)).fetchone()
        total_bal.append(round(row[0],0) if row and row[0] else 0)
        total_net.append(round(row[1],0) if row and row[1] else 0)
    # 用一致口径：以首日为基准，后续余额 = 首日余额 + 累计净买入
    base_bal = total_bal[0] if total_bal else 0
    consistent_bal = [base_bal]
    for i in range(1, len(total_net)):
        consistent_bal.append(round(consistent_bal[-1] + total_net[i], 0))
    # 同时保留原始口径
    result['total'] = {'balance': consistent_bal, 'net_buy': total_net, 'balance_raw': total_bal}
    return jsonify(result)


@app.route('/api/stock-name')
def api_stock_name():
    code = request.args.get('code', '')
    mode = request.args.get('mode', '')  # 'stock'|'index'|''=auto
    if not code: return jsonify({})
    db = get_db()
    if mode != 'index':
        r = db.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
        if r: return jsonify({'code': code, 'name': r['name']})
    # fallback: index names from index_style.yaml
    idx_names = load_index_names()
    nm = idx_names.get(code, '')
    return jsonify({'code': code, 'name': nm})

# ═══════════════════════════════════════════════
# API: POST /api/pocket-pivot
# ═══════════════════════════════════════════════

@app.route('/api/market-scan/margin-history')
def api_margin_history():
    db = get_db()
    # daily_margin_history 是全A股融资格局最全的（~3835只/日），逐日更新
    rows = db.execute('''
        SELECT date, SUM(financing_balance)/1e8 as fb, SUM(COALESCE(securities_balance,0))/1e8 as sb
        FROM daily_margin_history WHERE date >= '2026-01-01' GROUP BY date ORDER BY date
    ''').fetchall()
    return jsonify({
        'dates': [r['date'] for r in rows],
        'financing': [round(r['fb'], 0) for r in rows],
        'securities': [round(r['sb'], 2) for r in rows]
    })

@app.route('/api/market-scan/margin-top-flow')
def api_margin_top_flow():
    db = get_db()
    date = request.args.get('date', db.execute('SELECT MAX(date) FROM daily_margin_history').fetchone()[0])
    # 净流入 TOP20
    top_in = db.execute('''
        SELECT m.stock_code, b.name, m.net_purchase
        FROM daily_margin_history m
        LEFT JOIN stock_basic b ON m.stock_code=b.stock_code
        WHERE m.date=? AND m.net_purchase IS NOT NULL
        ORDER BY m.net_purchase DESC LIMIT 20
    ''', (date,)).fetchall()
    # 净流出 TOP20
    top_out = db.execute('''
        SELECT m.stock_code, b.name, m.net_purchase
        FROM daily_margin_history m
        LEFT JOIN stock_basic b ON m.stock_code=b.stock_code
        WHERE m.date=? AND m.net_purchase IS NOT NULL
        ORDER BY m.net_purchase ASC LIMIT 20
    ''', (date,)).fetchall()
    return jsonify({
        'date': date,
        'top_in': [{'code': r['stock_code'], 'name': r['name'] or r['stock_code'], 'net': round(r['net_purchase']/1e8, 2)} for r in top_in],
        'top_out': [{'code': r['stock_code'], 'name': r['name'] or r['stock_code'], 'net': round(r['net_purchase']/1e8, 2)} for r in top_out],
    })

@app.route('/api/pocket-pivot', methods=['POST', 'OPTIONS'])
def api_pocket_pivot():
    if request.method == 'OPTIONS': return '', 204
    data = request.get_json()
    stock_code = data.get('stock_code', '600519')
    start = data.get('start', '2023-01-01')
    end = data.get('end', datetime.now().strftime('%Y-%m-%d'))
    params = data.get('params', {})
    mode = data.get('mode', 'stock')
    period = data.get('period', 'day')  # day/week/month

    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if mode == 'index' else ''
    # 月线需要更长历史
    extra = '-600 days' if period == 'month' else '-300 days'
    rows = db.execute(f"""SELECT date, open, high, low, close, volume, amount FROM {table}
        WHERE stock_code=? {kf} AND date>=date(?,?) AND date<=? ORDER BY date""",
        (stock_code, start, extra, end)).fetchall()
    if not rows: return jsonify({'klines':[],'signals':[]})

    klines_full = [dict(r) for r in rows]

    # ── 日→周/月聚合 ──
    if period != 'day':
        klines_full = _aggregate_klines(klines_full, period)

    merged = {}
    cfg_path = os.path.join(PROJECT_DIR, 'config', 'market', 'pocket_pivot.yaml')
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        merged.update(cfg.get('pocket_pivot', {}))
    merged.update(params.get('pocket_pivot', params))

    from scanners.pocket_pivot import detect, get_rs
    rs_info = get_rs(db, stock_code, end, mode)
    signals = detect(klines_full, merged, rs_info)
    # 为每个信号日补上当日真实的 RS 值
    for s in signals:
        sd_rs = get_rs(db, stock_code, s['date'], mode)
        if sd_rs:
            s['rs_20'] = sd_rs['rs_20']
            s['rs_250'] = sd_rs['rs_250']
    klines_out = [k for k in klines_full if start <= k['date'] <= end]
    signals_out = [s for s in signals if start <= s['date'] <= end]
    return jsonify({'klines': klines_out, 'signals': signals_out})


def _ensure_adj_prices(klines):
    """前复权：用 change_pct 逆向推算 adj_close，按比例同步缩放 OHLC。
    
    步骤：
    1. 从最后一天向前递推 adj_close：prev_adj_close = curr_adj_close / (1 + chg)
    2. 每晚用 adj_close / raw_close 的比例同步缩放当天的 open/high/low

    这样 OHLC 的比例关系和影线形态完全保留。
    """
    if not klines or len(klines) < 2:
        return
    n = len(klines)
    
    # Step 1: 从后往前推算 adj_close
    for i in range(n - 2, -1, -1):
        curr = klines[i + 1]
        prev = klines[i]
        chg = curr.get('change_pct')
        if chg is None:
            continue
        if abs(chg) > 1:
            chg = chg / 100
        factor = 1 + chg
        if factor <= 0:
            continue
        if curr.get('close') is not None:
            prev['_adj_close'] = curr.get('_adj_close', curr['close']) / factor
    
    # Step 2: 用 adj_close / raw_close 比例同步 OHLC
    for i in range(n):
        k = klines[i]
        raw_close = k['close']
        adj_close = k.get('_adj_close', raw_close)
        if raw_close and raw_close > 0:
            ratio = adj_close / raw_close
            if k.get('open'): k['open'] *= ratio
            if k.get('high'): k['high'] *= ratio
            if k.get('low'): k['low'] *= ratio
            k['close'] = adj_close
        k.pop('_adj_close', None)


def _aggregate_klines(klines, period):
    """日K线聚合为周K或月K"""
    if not klines: return []
    result = []
    group_key = None; current = None
    for k in klines:
        d = k['date']
        if period == 'week':
            from datetime import datetime
            dt = datetime.strptime(d, '%Y-%m-%d')
            iso = dt.isocalendar()
            gk = f"{iso[0]}-W{iso[1]:02d}"
        else:
            gk = d[:7]
        if gk != group_key:
            if current: result.append(current)
            current = {'date': d, 'open': k['open'], 'high': k['high'], 'low': k['low'], 'close': k['close'], 'volume': k['volume'] or 0, 'amount': k.get('amount') or 0}
            group_key = gk
        else:
            current['high'] = max(current['high'], k['high'])
            current['low'] = min(current['low'], k['low'])
            current['close'] = k['close']
            current['volume'] = (current['volume'] or 0) + (k['volume'] or 0)
            current['amount'] = (current['amount'] or 0) + (k.get('amount') or 0)
    if current: result.append(current)
    return result


@app.route('/api/flat-base', methods=['POST', 'OPTIONS'])
def api_flat_base():
    if request.method == 'OPTIONS': return '', 204
    data = request.get_json()
    stock_code = data.get('stock_code', '600519')
    start = data.get('start', '2023-01-01')
    end = data.get('end', datetime.now().strftime('%Y-%m-%d'))
    period = data.get('period', 'day')
    params = data.get('params', {})
    db = get_db()
    extra = '-900 days' if period == 'month' else '-400 days'
    rows = db.execute(f"""SELECT date, open, high, low, close, volume, amount FROM daily_kline
        WHERE stock_code=? AND date>=date(?,?) AND date<=? ORDER BY date""",
        (stock_code, start, extra, end)).fetchall()
    if not rows: return jsonify({'klines':[],'signals':[]})
    klines_full = [dict(r) for r in rows]
    if period != 'day': klines_full = _aggregate_klines(klines_full, period)
    from scanners.flat_base import detect, load_params
    merged = load_params()
    cfg_path = os.path.join(PROJECT_DIR, 'config', 'market', 'flat_base.yaml')
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        merged.update(cfg.get('flat_base', {}))
    merged.update(params.get('flat_base', params))
    signals = detect(klines_full, merged)
    klines_out = [k for k in klines_full if start <= k['date'] <= end]
    signals_out = [s for s in signals if start <= s['date'] <= end]
    return jsonify({'klines': klines_out, 'signals': signals_out})


@app.route('/api/double-bottom', methods=['POST', 'OPTIONS'])
def api_double_bottom():
    if request.method == 'OPTIONS': return '', 204
    data = request.get_json()
    stock_code = data.get('stock_code', '600519')
    start = data.get('start', '2023-01-01')
    end = data.get('end', datetime.now().strftime('%Y-%m-%d'))
    period = data.get('period', 'day')
    mode = data.get('mode', 'stock')
    params = data.get('params', {})
    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if mode == 'index' else ''
    extra = '-600 days' if period == 'month' else '-400 days'
    rows = db.execute(f"""SELECT date, open, high, low, close, volume, amount FROM {table}
        WHERE stock_code=? {kf} AND date>=date(?,?) AND date<=? ORDER BY date""",
        (stock_code, start, extra, end)).fetchall()
    if not rows: return jsonify({'klines':[],'signals':[]})
    klines_full = [dict(r) for r in rows]
    if period != 'day': klines_full = _aggregate_klines(klines_full, period)
    from scanners.double_bottom import detect, load_params
    merged = load_params()
    cfg_path = os.path.join(PROJECT_DIR, 'config', 'market', 'double_bottom.yaml')
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        merged.update(cfg.get('double_bottom', {}))
    merged.update(params.get('double_bottom', params))
    signals = detect(klines_full, merged)
    klines_out = [k for k in klines_full if start <= k['date'] <= end]
    signals_out = [s for s in signals if start <= s['date'] <= end]
    return jsonify({'klines': klines_out, 'signals': signals_out})


@app.route('/api/pocket-pivot-rs')
def api_pocket_pivot_rs():
    code = request.args.get('code', '')
    date = request.args.get('date', '')
    mode = request.args.get('mode', 'stock')
    if not code or not date: return jsonify({})
    db = get_db()
    from scanners.pocket_pivot import get_rs
    rs = get_rs(db, code, date, mode)
    return jsonify(rs or {'rs_20': None, 'rs_250': None})

# ═══════════════════════════════════════════════
# API: GET /api/pocket-pivot-v3 — 口袋支点V3信号查询
# ═══════════════════════════════════════════════

@app.route('/api/pocket-pivot-v2')
def api_pocket_pivot_v2():
    code = request.args.get('code', '')
    start = request.args.get('start', '2026-01-01')
    end = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))
    
    db = get_db()
    rows = db.execute("""
        SELECT date, stock_code, stock_name, pivot_type, b1_overlap,
               gain_pct, vol_ratio, close_position, rps_20, rps_250,
               sma10, sma60, pct_from_ma10, base_depth, close, volume,
               h_date, l_date, c_days
        FROM pocket_pivot_daily
        WHERE stock_code = ? AND date >= ? AND date <= ?
        ORDER BY date
    """, (code, start, end)).fetchall()
    
    signals = []
    for r in rows:
        signals.append({
            'date': r['date'],
            'stock_code': r['stock_code'],
            'stock_name': r['stock_name'],
            'pivot_type': r['pivot_type'],
            'b1_overlap': bool(r['b1_overlap']),
            'gain_pct': r['gain_pct'],
            'vol_ratio': r['vol_ratio'],
            'close_position': r['close_position'],
            'rps_20': r['rps_20'],
            'rps_250': r['rps_250'],
            'sma10': r['sma10'],
            'sma60': r['sma60'],
            'pct_from_ma10': r['pct_from_ma10'],
            'base_depth': r['base_depth'],
            'close': r['close'],
            'volume': r['volume'],
            'h_date': r['h_date'],
            'l_date': r['l_date'],
            'c_days': r['c_days'],
            'signal_type': 'pocket_pivot_v2'
        })
    
    return jsonify({'signals': signals, 'count': len(signals)})

# ═══════════════════════════════════════════════
# API: GET /api/saucer-base?stock=XXX&date=YYYY-MM-DD
# ═══════════════════════════════════════════════

@app.route('/api/saucer-base')
def api_saucer_base():
    """单股票碟形基部检测，同时返回K线供图表渲染"""
    try:
        code = request.args.get('stock', '600519')
        date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
        start = request.args.get('start', None)
        
        db = get_db()
        
        klines = db.execute("""
            SELECT date, open, high, low, close, volume
            FROM daily_kline
            WHERE stock_code = ? AND date <= ? AND date >= date(?, '-400 days')
            ORDER BY date
        """, (code, date_str, date_str)).fetchall()
        
        if not klines:
            return jsonify({'signals': [], 'klines': [], 'error': 'No kline data'})
        
        daily = [dict(r) for r in klines]
        
        mkt = db.execute("""
            SELECT value FROM fundamental_indicator
            WHERE stock_code = ? AND metric_code = 'mc' AND date <= ?
            ORDER BY date DESC LIMIT 1
        """, (code, date_str)).fetchone()
        market_cap = float(mkt['value']) / 1e8 if mkt else None
        
        from scanners.saucer_base import detect, load_params
        params = load_params()
        signals = detect(daily, params, market_cap)
        
        filtered = [s for s in signals if s['signal_date'] == date_str]
        for s in filtered:
            s['stock_code'] = code
        
        klines_out = daily
        if start:
            klines_out = [k for k in daily if k['date'] >= start]
        
        return jsonify({'signals': filtered, 'klines': klines_out})
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/saucer-base/scan')
def api_saucer_base_scan():
    """批量扫描碟形基部"""
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    pool = request.args.get('pool', 'all')
    
    from scanners.saucer_base import scan_batch, load_params
    params = load_params()
    results = scan_batch(date_str, pool, params)
    
    return jsonify({'results': results, 'count': len(results), 'date': date_str, 'pool': pool})

# ═══════════════════════════════════════════════
# API: GET /api/cup-handle?stock=XXX&date=YYYY-MM-DD&start=YYYY-MM-DD&mode=stock|index
# ═══════════════════════════════════════════════

@app.route('/api/cup-handle')
def api_cup_handle():
    """杯柄形态检测"""
    code = request.args.get('stock', '600519')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    start = request.args.get('start', None)
    mode = request.args.get('mode', 'stock')
    
    if not start:
        start = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=600)).strftime('%Y-%m-%d')
    
    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if mode == 'index' else ''
    code_col = 'stock_code'
    extra_days = 400  # 给形态切割 + 前置上涨回溯留足够空间
    
    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE {code_col}=? {kf} AND date>=date(?, '-{extra_days} days') AND date<=?
        ORDER BY date""",
        (code, start, date_str)).fetchall()
    
    if not klines:
        return jsonify({'signals': [], 'klines': [], 'error': 'No data'})
    
    daily = [dict(r) for r in klines]
    
    from scanners.cup_handle import detect, load_params
    params = load_params()
    signals = detect(daily, params)
    
    # 返回全部信号（前端会按日期范围过滤）
    for s in signals:
        s['stock_code'] = code
    
    klines_out = [k for k in daily if k['date'] >= start]
    
    return jsonify({'signals': signals, 'klines': klines_out})

# ═══════════════════════════════════════════════
# API: GET /api/cup-handle/diag?stock=XXX&date=YYYY-MM-DD&mode=stock|index
# 单日排查 — 逐条件返回通过/不通过详情
# ═══════════════════════════════════════════════

@app.route('/api/cup-handle/diag')
def api_cup_handle_diag():
    code = request.args.get('stock', '600519')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    mode = request.args.get('mode', 'stock')
    
    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if mode == 'index' else ''
    code_col = 'stock_code'
    
    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE {code_col}=? {kf} AND date<=? AND date>=date(?, '-500 days')
        ORDER BY date""", (code, date_str, date_str)).fetchall()
    
    if len(klines) < 170:
        return jsonify({'error': f'K线不足 ({len(klines)}条, 需要≥170)'})
    
    daily = [dict(r) for r in klines]
    target_idx = len(daily) - 1
    target = daily[target_idx]
    
    from scanners.cup_handle import load_params, _aggregate_weekly, _find_prior_high_and_bottom_cutting, _find_prior_high_and_bottom_simple
    from scanners.cup_handle import _check_prior_advance, _check_recovery, _check_volume, _find_handle, _check_breakout, _check_false_breakout
    from scanners.cup_handle import _sma, _sma_before
    
    params = load_params()
    results = []
    
    def ok(cond, val, thresh, note=''): return {'condition': cond, 'value': str(val), 'threshold': str(thresh), 'pass': True, 'note': note}
    def fail(cond, val, thresh, note=''): return {'condition': cond, 'value': str(val), 'threshold': str(thresh), 'pass': False, 'note': note}
    
    n_daily = len(daily)
    # 0.1 数据充分
    data_ok = n_daily >= params['lookback'] + 50
    results.append(ok('数据充分', f'{n_daily}条', f'≥{params["lookback"]+50}条') if data_ok
                   else fail('数据充分', f'{n_daily}条', f'≥{params["lookback"]+50}条'))
    
    if not data_ok:
        return jsonify({'date': date_str, 'stock': code, 'results': results, 'all_pass': False})
    
    # 0.2 SMA50
    sma50c = _sma([k['close'] for k in daily], 50)
    sma50_ok = target['close'] > sma50c
    results.append(ok('SMA50趋势', f'C={target["close"]:.2f}', f'SMA50={sma50c:.2f}', 'C>SMA50') if sma50_ok
                   else fail('SMA50趋势', f'C={target["close"]:.2f}', f'SMA50={sma50c:.2f}'))
    
    # 1. 形态切割法找前高+杯底
    weekly = _aggregate_weekly(daily)
    mode_ph = params.get('prior_high_mode', 'cutting')
    if mode_ph == 'simple':
        cup = _find_prior_high_and_bottom_simple(weekly, daily, date_str, params)
    else:
        cup = _find_prior_high_and_bottom_cutting(weekly, daily, date_str, params)
    
    if cup is None:
        results.append(fail('① 前高+杯底', '未找到', f'回调∈[{params["cup_drawdown_min"]*100}%,{params["cup_drawdown_max"]*100}%]', '形态切割法无法定位'))
        return jsonify({'date': date_str, 'stock': code, 'ohlc': {'open': target['open'], 'high': target['high'], 'low': target['low'], 'close': target['close']}, 'cup': None, 'handle': None, 'results': results, 'all_pass': False})
    
    cup_ok = True
    dd_pct = cup['drawdown'] * 100
    results.append(ok('① 前高+杯底', f'前高={cup["prior_high"]:.2f} 杯底={cup["bottom"]:.2f} 回调={dd_pct:.1f}%',
                      f'回调∈[{params["cup_drawdown_min"]*100}%,{params["cup_drawdown_max"]*100}%]'))
    
    # 0.3 前置上涨
    pa = _check_prior_advance(daily, cup['prior_high_date'], cup['prior_high'], params)
    pa_ok = pa >= params['min_prior_advance']
    results.append(ok(f'前置上涨', f'{pa*100:.1f}%', f'≥{params["min_prior_advance"]*100}%') if pa_ok
                   else fail(f'前置上涨', f'{pa*100:.1f}%', f'≥{params["min_prior_advance"]*100}%'))
    
    # 1.4 杯底平坦性
    if params.get('cup_bottom_check', True):
        b_idx = cup['bottom_idx']
        b_start = max(0, b_idx - 5)
        b_end = min(len(daily), b_idx + 6)
        b_zone = daily[b_start:b_end]
        z_hi = max(k['close'] for k in b_zone)
        z_lo = min(k['close'] for k in b_zone)
        flat_amp = (z_hi - z_lo) / z_hi if z_hi > 0 else 0
        flat_ok = flat_amp <= params['cup_bottom_flatness']
        results.append(ok(f'① 杯底平坦性', f'振幅={flat_amp*100:.1f}%', f'≤{params["cup_bottom_flatness"]*100}%', '±5天') if flat_ok
                       else fail(f'① 杯底平坦性', f'振幅={flat_amp*100:.1f}%', f'≤{params["cup_bottom_flatness"]*100}%'))
        cup_ok = cup_ok and flat_ok
    else:
        results.append(ok('① 杯底平坦性', '已关闭', '—'))
    
    # 2. 杯身回升
    rec_ok = _check_recovery(daily, cup, target_idx, params)
    recovery_val = max(k['close'] for k in daily[cup['bottom_idx']:target_idx+1]) / cup['prior_high']
    results.append(ok('② 杯身回升', f'回升到{recovery_val*100:.0f}%', f'≥{params["cup_recovery"]*100}%') if rec_ok
                   else fail('② 杯身回升', f'回升到{recovery_val*100:.0f}%', f'≥{params["cup_recovery"]*100}%'))
    
    # 3. 成交量
    vr = _check_volume(daily, cup, target_idx, params)
    vol_ok = vr is not None
    if vol_ok:
        vb, vc = vr
        results.append(ok('③ 成交量', f'杯底量/50均={vb:.2f} 缩量={vc:.2f}',
                          f'杯底≤{params["vol_bottom_max"]} 缩量≤{params["vol_contraction"]}'))
    else:
        results.append(fail('③ 成交量', '不满足', f'杯底≤{params["vol_bottom_max"]} 缩量≤{params["vol_contraction"]}'))
    
    # 4. 柄部
    handle = _find_handle(daily, cup, target_idx, params)
    has_handle = handle is not None
    handle_req = params.get('handle_required', True)
    if has_handle:
        h_dd = handle['handle_drawdown'] * 100
        results.append(ok('④ 柄部', f'有柄 回撤={h_dd:.1f}% 量比={handle["handle_vol_ratio"]:.2f}',
                          f'回撤≤{params["handle_max_drawdown"]*100}%'))
    else:
        if handle_req:
            results.append(fail('④ 柄部', '无柄', '需要柄部'))
        else:
            results.append(ok('④ 柄部', '无柄（已放行）', '不要求柄部'))
    
    # 5. 突破
    buy_pt = _check_breakout(daily, target_idx, cup, handle, params)
    bo_ok = buy_pt is not None
    sma50v = _sma_before([k['volume'] for k in daily], 50, target_idx)
    vol_ratio_bo = target['volume'] / sma50v if sma50v > 0 else 0
    results.append(ok('⑤ 突破', f'收盘={target["close"]:.2f} 量比={vol_ratio_bo:.2f}',
                      f'买点={buy_pt if bo_ok else "—"} 量比≥{params["breakout_vol_ratio"]}') if bo_ok
                   else fail('⑤ 突破', f'收盘={target["close"]:.2f} 量比={vol_ratio_bo:.2f}',
                             f'买点>收盘 或 量比<{params["breakout_vol_ratio"]}'))
    
    # 6. 假突破
    fb = _check_false_breakout(daily, target_idx, handle, params)
    results.append(ok('⑥ 假突破排除', '通过', '无假突破信号') if not fb
                   else fail('⑥ 假突破排除', '检测到假突破', '假突破信号'))
    
    all_pass = cup_ok and pa_ok and rec_ok and vol_ok and (has_handle or not handle_req) and bo_ok and not fb
    
    return jsonify({
        'date': date_str,
        'stock': code,
        'ohlc': {'open': target['open'], 'high': target['high'], 'low': target['low'], 'close': target['close']},
        'cup': {'prior_high': cup['prior_high'], 'prior_high_date': cup['prior_high_date'],
                'bottom': cup['bottom'], 'bottom_date': cup['bottom_date'],
                'drawdown_pct': round(cup['drawdown']*100, 1), 'descent_days': cup['descent_days']},
        'handle': {'found': has_handle, 'high': handle['handle_high_price'] if has_handle else None,
                   'low': handle['handle_low_price'] if has_handle else None,
                   'drawdown_pct': round(handle['handle_drawdown']*100, 1) if has_handle else None} if has_handle else {'found': False},
        'results': results,
        'all_pass': all_pass,
    })

# ═══════════════════════════════════════════════
# API: GET /api/base-breakout
# ═══════════════════════════════════════════════

@app.route('/api/base-breakout')
def api_base_breakout():
    code = request.args.get('stock', '600519')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    start = request.args.get('start', None)
    mode = request.args.get('mode', 'stock')
    
    if not start:
        start = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=730)).strftime('%Y-%m-%d')
    
    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if mode == 'index' else ''
    code_col = 'stock_code'
    
    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE {code_col}=? {kf} AND date>=date(?, '-500 days') AND date<=?
        ORDER BY date""", (code, start, date_str)).fetchall()
    
    if not klines:
        return jsonify({'signals': [], 'klines': [], 'error': 'No data'})
    
    daily = [dict(r) for r in klines]
    
    from scanners.base_breakout import detect, load_params
    params = load_params()
    signals = detect(daily, params)
    
    for s in signals:
        s['stock_code'] = code
    
    # Layer 2 标注
    from scanners.base_tags import tag_signal
    vcp_params = {}
    for k in ['vcp_min_toc','vcp_max_toc','vcp_contraction_ratio','vcp_vol_contraction',
              'vcp_terminal_amp','vcp_dryup_ratio']:
        if request.args.get(k):
            vcp_params[k] = float(request.args.get(k))
    
    for s in signals:
        s['tags'] = tag_signal(daily, s, vcp_params)
    
    klines_out = [k for k in daily if k['date'] >= start]
    
    return jsonify({'signals': signals, 'klines': klines_out})


# ═══════════════════════════════════════════════
# API: GET /api/base-breakout/diag
# ═══════════════════════════════════════════════

@app.route('/api/base-breakout/diag')
def api_base_breakout_diag():
    code = request.args.get('stock', '600519')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    mode = request.args.get('mode', 'stock')
    
    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if mode == 'index' else ''
    code_col = 'stock_code'
    
    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE {code_col}=? {kf} AND date<=? AND date>=date(?, '-500 days')
        ORDER BY date""", (code, date_str, date_str)).fetchall()
    
    if len(klines) < 170:
        return jsonify({'error': f'K线不足 ({len(klines)}条, 需要 >= 170)'})
    
    daily = [dict(r) for r in klines]
    target_idx = len(daily) - 1
    target = daily[target_idx]
    
    from scanners.base_breakout import load_params, _find_trough_and_prior_high, _check_prior_advance
    from scanners.base_breakout import _sma, _sma_before, _linear_slope
    
    params = load_params()
    results = []
    
    def ok(cond, val, thresh, note=''): return {'condition': cond, 'value': str(val), 'threshold': str(thresh), 'pass': True, 'note': note}
    def fail(cond, val, thresh, note=''): return {'condition': cond, 'value': str(val), 'threshold': str(thresh), 'pass': False, 'note': note}
    
    n_daily = len(daily)
    data_ok = n_daily >= params['lookback'] + 50
    results.append(ok('数据充分', f'{n_daily}条', f'≥{params["lookback"]+50}条') if data_ok
                   else fail('数据充分', f'{n_daily}条', f'≥{params["lookback"]+50}条'))
    if not data_ok:
        return jsonify({'date': date_str, 'results': results, 'all_pass': False, 'prior_high': None, 'trough': None})
    
    closes = [k['close'] for k in daily]
    sma50c = _sma(closes, 50)
    sma50_ok = target['close'] > sma50c if params.get('sma50_check', True) else True
    results.append(ok('SMA50趋势', f'C={target["close"]:.2f}', f'SMA50={sma50c:.2f}') if sma50_ok
                   else fail('SMA50趋势', f'C={target["close"]:.2f}', f'SMA50={sma50c:.2f}'))
    
    base = _find_trough_and_prior_high(daily, target_idx, params)
    if base is None:
        results.append(fail('① 谷+前高', '未找到', f'回调≥{params["drawdown_min"]*100}%'))
        return jsonify({'date': date_str, 'ohlc': {'open': target['open'], 'high': target['high'], 'low': target['low'], 'close': target['close']},
                        'results': results, 'all_pass': False, 'prior_high': None, 'trough': None})
    
    dd = base['drawdown']; dd_pct = dd * 100
    results.append(ok('① 谷+前高', f'前高={base["prior_high"]:.2f} 谷={base["trough"]:.2f} 回调={dd_pct:.1f}%',
                      f'回调∈[{params["drawdown_min"]*100}%,{params["drawdown_max"]*100}%]'))
    
    dd_ok = params['drawdown_min'] <= dd <= params['drawdown_max']
    if dd_ok:
        results.append(ok('② 回调深度', f'{dd_pct:.1f}%', f'[{params["drawdown_min"]*100}%,{params["drawdown_max"]*100}%]'))
    else:
        results.append(fail('② 回调深度', f'{dd_pct:.1f}%', f'[{params["drawdown_min"]*100}%,{params["drawdown_max"]*100}%]'))
    
    max_rec = max(k['close'] for k in daily[base['trough_idx']:target_idx+1])
    rec_pct = max_rec / base['prior_high']
    rec_ok = rec_pct >= params['min_recovery']
    asc_ok = _linear_slope([k['close'] for k in daily[base['trough_idx']:target_idx+1]]) > 0
    rec_all = rec_ok and asc_ok
    results.append(ok('③ 回升', f'回升到{rec_pct*100:.0f}%', f'≥{params["min_recovery"]*100}%') if rec_all
                   else fail('③ 回升', f'回升到{rec_pct*100:.0f}%', f'≥{params["min_recovery"]*100}%'))
    
    pa = _check_prior_advance(daily, base['prior_high_idx'], base['prior_high'], params['lookback'], params['min_prior_advance'])
    pa_ok = pa >= params['min_prior_advance']
    results.append(ok('④ 前置上涨', f'{pa*100:.1f}%', f'≥{params["min_prior_advance"]*100}%') if pa_ok
                   else fail('④ 前置上涨', f'{pa*100:.1f}%', f'≥{params["min_prior_advance"]*100}%'))
    
    buy_pt = base['prior_high'] + 0.01
    bo_close = target['close'] >= buy_pt
    volumes = [k['volume'] for k in daily]
    sma50v = _sma_before(volumes, 50, target_idx)
    bo_vol = sma50v > 0 and target['volume'] >= sma50v * params['breakout_vol_ratio']
    bo_green = not params.get('require_green', True) or target['close'] > target['open']
    pos_v = 1
    if target['high'] > target['low']:
        pos_v = (target['close'] - target['low']) / (target['high'] - target['low'])
    bo_pos = pos_v >= params.get('close_position_min', 0.5)
    bo_all = bo_close and bo_vol and bo_green and bo_pos
    results.append(ok('⑤ 突破', f'收盘={target["close"]:.2f} 量比={target["volume"]/sma50v:.2f}' if sma50v>0 else f'收盘={target["close"]:.2f}',
                      f'买点={buy_pt:.2f} 量比≥{params["breakout_vol_ratio"]}') if bo_all
                   else fail('⑤ 突破', f'收盘={target["close"]:.2f}',
                             f'买点={buy_pt:.2f} ({"✅" if bo_close else "❌"}突破 {"✅" if bo_vol else "❌"}量 {"✅" if bo_green else "❌"}阳线 {"✅" if bo_pos else "❌"}位置)'))
    
    all_pass = data_ok and sma50_ok and dd_ok and rec_all and pa_ok and bo_all
    
    return jsonify({
        'date': date_str,
        'ohlc': {'open': target['open'], 'high': target['high'], 'low': target['low'], 'close': target['close']},
        'prior_high': base['prior_high'], 'prior_high_date': base['prior_high_date'],
        'trough': base['trough'], 'trough_date': base['trough_date'],
        'drawdown_pct': round(dd_pct, 1),
        'results': results, 'all_pass': all_pass,
    })


# ═══════════════════════════════════════════════
# API: GET /api/breakout-failure
# ═══════════════════════════════════════════════

@app.route('/api/breakout-failure')
def api_breakout_failure():
    code = request.args.get('stock', '600519')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    start = request.args.get('start', None)
    mode = request.args.get('mode', 'stock')

    if not start:
        start = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=730)).strftime('%Y-%m-%d')

    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if mode == 'index' else ''
    code_col = 'stock_code'

    from scanners.breakout_failure import detect as detect_failure, load_params
    from scanners.base_breakout import load_params as load_bp_params

    params = load_params()
    monitor_days = params['monitor_days']

    # 方案A：静默扩展 end 最多 monitor_days 天，但不超过今天
    today_str = datetime.now().strftime('%Y-%m-%d')
    display_end = date_str
    extended_end = date_str
    if date_str < today_str:
        extended_dt = datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=monitor_days * 2)
        max_dt = min(extended_dt, datetime.now())
        extended_end = max_dt.strftime('%Y-%m-%d')

    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE {code_col}=? {kf} AND date>=date(?, '-500 days') AND date<=?
        ORDER BY date""", (code, start, extended_end)).fetchall()

    if not klines:
        return jsonify({'signals': [], 'klines': [], 'klines_display_end': display_end, 'breakout_signals': [], 'failure_signals': [], 'stats': {}})

    daily = [dict(r) for r in klines]

    # 解析可选参数覆盖 YAML
    bp_params = load_bp_params()
    for k in ['lookback', 'min_base_days', 'min_descent_days', 'drawdown_min', 'drawdown_max',
              'min_recovery', 'min_prior_advance', 'breakout_vol_ratio', 'close_position_min']:
        if request.args.get(k):
            bp_params[k] = float(request.args.get(k))
    for k in ['require_green', 'sma50_check']:
        if request.args.get(k) is not None:
            bp_params[k] = request.args.get(k).lower() in ('true', '1', 'yes')

    # 失败参数覆盖
    for k in params:
        if request.args.get(k) is not None:
            v = request.args.get(k)
            if isinstance(params[k], bool):
                params[k] = v.lower() in ('true', '1', 'yes')
            elif isinstance(params[k], int):
                params[k] = int(v)
            elif isinstance(params[k], float):
                params[k] = float(v)

    # 获取突破信号和失败信号
    bp_signals_raw = []
    from scanners.base_breakout import detect as detect_breakout
    bp_signals_raw = detect_breakout(daily, bp_params)

    # 只保留 display_end 之前的突破
    bp_signals = [s for s in bp_signals_raw if s['signal_date'] <= display_end]

    # 运行失败检测
    failure_signals = detect_failure(daily, bp_params=bp_params)

    # 过滤失败信号：只保留其 breakout_date <= display_end 且 date <= display_end 的
    failure_signals = [f for f in failure_signals
                       if f['breakout_date'] <= display_end and f['date'] <= display_end]

    # K线输出
    klines_out = [k for k in daily if k['date'] >= start]

    # 统计
    total_breakouts = len(bp_signals)
    total_failures = len(failure_signals)
    failed_bo_dates = set(f['breakout_date'] for f in failure_signals)
    failed_breakouts = len(failed_bo_dates)
    failure_rate = round(failed_breakouts / total_breakouts * 100, 1) if total_breakouts > 0 else 0
    by_severity = {'mild': 0, 'confirmed': 0, 'severe': 0}
    for f in failure_signals:
        sev = f.get('severity', 'mild')
        by_severity[sev] = by_severity.get(sev, 0) + 1

    return jsonify({
        'code': code,
        'klines': klines_out,
        'klines_display_end': display_end,
        'breakout_signals': bp_signals,
        'failure_signals': failure_signals,
        'stats': {
            'total_breakouts': total_breakouts,
            'total_failures': total_failures,
            'failed_breakouts': failed_breakouts,
            'failure_rate_pct': failure_rate,
            'by_severity': by_severity,
        }
    })


# ═══════════════════════════════════════════════
# API: GET /api/breakout-failure/diag
# ═══════════════════════════════════════════════

@app.route('/api/breakout-failure/diag')
def api_breakout_failure_diag():
    code = request.args.get('stock', '600519')
    breakout_date = request.args.get('breakout_date', '')
    check_date = request.args.get('check_date', datetime.now().strftime('%Y-%m-%d'))
    mode = request.args.get('mode', 'stock')

    if not breakout_date:
        return jsonify({'error': '缺少 breakout_date 参数'})

    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if mode == 'index' else ''
    code_col = 'stock_code'

    from scanners.breakout_failure import diagnose, load_params
    params = load_params()
    monitor_days = params['monitor_days']

    # 获取从 breakout_date 前 500 天到 check_date 后 monitor_days 天的数据
    extended_end = (datetime.strptime(check_date, '%Y-%m-%d') + timedelta(days=monitor_days * 2)).strftime('%Y-%m-%d')
    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE {code_col}=? {kf} AND date>=date(?, '-500 days') AND date<=?
        ORDER BY date""", (code, breakout_date, extended_end)).fetchall()

    if not klines:
        return jsonify({'error': '无K线数据'})

    daily = [dict(r) for r in klines]

    result = diagnose(daily, breakout_date, check_date)

    # 获取股票名称
    name = code
    if mode != 'index':
        name_row = db.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
        if name_row:
            name = name_row['name']

    result['code'] = code
    result['name'] = name

    return jsonify(result)


@app.route('/api/climax-top')
def api_climax_top():
    code = request.args.get('stock', '600519')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    mode = request.args.get('mode', 'stock')
    start = request.args.get('start', None)
    
    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if mode == 'index' else ''
    code_col = 'stock_code'
    
    lb_date = start if start else date_str
    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE {code_col}=? {kf} AND date<=? AND date>=date(?,'-600 days')
        ORDER BY date""", (code, date_str, lb_date)).fetchall()
    
    if len(klines) < 390:
        return jsonify({'weekly': [], 'signals': [], 'error': f'K线不足 ({len(klines)}条, 需要 >= 390)'})
    
    daily = [dict(r) for r in klines]
    
    from scanners.climax_top import detect, load_params, _aggr_weekly, _find_baseline
    params = load_params()
    
    weekly = _aggr_weekly(daily)
    baseline = _find_baseline(weekly, daily, date_str, DB_PATH)
    
    signals = detect(daily, params, baseline_price=baseline, stock_code=code)
    if start:
        weekly = [w for w in weekly if w['date'] >= start]
    weekly_out = weekly[-120:]  # 最近120周
    
    return jsonify({'daily': daily, 'weekly': weekly_out, 'signals': signals, 'baseline': baseline})


@app.route('/api/climax-top/diag')
def api_climax_top_diag():
    code = request.args.get('stock', '600519')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    mode = request.args.get('mode', 'stock')
    
    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if mode == 'index' else ''
    code_col = 'stock_code'
    
    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE {code_col}=? {kf} AND date<=? AND date>=date(?, '-600 days')
        ORDER BY date""", (code, date_str, date_str)).fetchall()
    
    if len(klines) < 390:
        return jsonify({'error': f'K线不足 ({len(klines)}条, 需要 >= 390)'})
    
    daily = [dict(r) for r in klines]
    
    from scanners.climax_top import detect, load_params, _aggr_weekly, _find_baseline
    from scanners.climax_top import _check_daily_accel, _check_daily_reversal
    params = load_params()
    
    weekly = _aggr_weekly(daily)
    baseline = _find_baseline(weekly, daily, date_str, DB_PATH)
    
    signals = detect(daily, params, baseline_price=baseline, stock_code=code)
    
    results = []
    def ok(cond, val, thresh, note=''): return {'condition':cond,'value':str(val),'threshold':str(thresh),'pass':True,'note':note}
    def fail(cond, val, thresh, note=''): return {'condition':cond,'value':str(val),'threshold':str(thresh),'pass':False,'note':note}
    
    if baseline:
        results.append(ok('① 基准点', f'{baseline:.2f}', '—'))
    else:
        results.append(fail('① 基准点', '未找到', '需要突破信号或52周低点'))
    
    for s in signals[:5]:
        results.append(ok(f'信号 {s["signal_date"]}',
            f'{s["climax_start"]}~{s["climax_end"]} +{s["climax_gain_pct"]}% 得分={s["score"]}',
            f'≥{params["score_warning"]}分'))
    
    all_pass = len(signals) > 0
    
    return jsonify({'date': date_str, 'baseline': baseline, 'signals_count': len(signals),
                    'results': results, 'all_pass': all_pass})

@app.route('/api/distribution-day')
def api_distribution_day():
    code = request.args.get('index', '000985')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    start = request.args.get('start', None)
    if not start:
        start = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=730)).strftime('%Y-%m-%d')
    db = get_db()
    klines = db.execute("""SELECT date, open, high, low, close, volume FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>=? AND date<=?
        ORDER BY date""", (code, start, date_str)).fetchall()
    if len(klines) < 26:
        return jsonify({'error': f'K线不足 ({len(klines)}条, 需要 >= 26)'})
    daily = [dict(r) for r in klines]
    from scanners.distribution_day import detect, load_params
    params = load_params()
    result = detect(daily, params)
    return jsonify(result)
@app.route('/api/distribution-day/diag')
@app.route('/api/distribution-day/joint')
def api_distribution_day_joint():
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    start = request.args.get('start', None)
    if not start:
        start = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=730)).strftime('%Y-%m-%d')
    db = get_db()
    k985 = db.execute("""SELECT date, open, high, low, close, volume FROM index_daily_kline
        WHERE stock_code='000985' AND kline_type='normal' AND date>=? AND date<=? ORDER BY date""",
        (start, date_str)).fetchall()
    k300 = db.execute("""SELECT date, open, high, low, close, volume FROM index_daily_kline
        WHERE stock_code='000300' AND kline_type='normal' AND date>=? AND date<=? ORDER BY date""",
        (start, date_str)).fetchall()
    from scanners.distribution_day import detect_multi, load_params
    result = detect_multi({'000985': [dict(r) for r in k985], '000300': [dict(r) for r in k300]})
    return jsonify(result)


@app.route('/api/railroad-tracks')
def api_railroad_tracks():
    code = request.args.get('stock', '600519')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    start = request.args.get('start', None)
    mode = request.args.get('mode', 'stock')
    if not start:
        start = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=730)).strftime('%Y-%m-%d')
    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if mode == 'index' else ''
    code_col = 'stock_code'
    lb_date = start
    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE {code_col}=? {kf} AND date<=? AND date>=date(?,'-600 days')
        ORDER BY date""", (code, date_str, lb_date)).fetchall()
    if len(klines) < 250:
        return jsonify({'error': f'K线不足 ({len(klines)}条, 需要 >= 250)'})
    daily = [dict(r) for r in klines]
    from scanners.railroad_tracks import detect_all, load_params
    params = load_params()
    result = detect_all(daily, params, stock_code=code)
    # 过滤 daily/weekly 到起止区间
    if start:
        result['daily'] = [k for k in result['daily'] if k['date'] >= start]
    if len(result['daily']) > 2000:
        result['daily'] = result['daily'][-2000:]
    result['weekly'] = [k for k in result['weekly'] if k['date'] >= start][:120] if start else result['weekly'][:120]
    return jsonify(result)


@app.route('/api/railroad-tracks/diag')
def api_railroad_tracks_diag():
    code = request.args.get('stock', '600519')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    mode = request.args.get('mode', 'stock')
    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if mode == 'index' else ''
    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE stock_code=? {kf} AND date<=? AND date>=date(?,'-600 days') ORDER BY date""",
        (code, date_str, date_str)).fetchall()
    daily = [dict(r) for r in klines]
    from scanners.railroad_tracks import detect_all, load_params
    result = detect_all(daily, stock_code=code)
    return jsonify({'date': date_str, 'stock': code, 'total_signals': len(result['all_signals']),
                    'by_type': {'S': len(result['signals_weekly']), 'A': len(result['signals_daily_double']),
                    'B': len(result['signals_daily_single'])},
                    'latest': [{'date': s['signal_date'], 'label': s['label']} for s in result['all_signals'][-5:]]})


# ═══════════════════════════════════════════════
# API: GET /api/top-pattern (头部形态)
# ═══════════════════════════════════════════════

@app.route('/api/top-pattern')
def api_top_pattern():
    code = request.args.get('stock', '600519')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    start = request.args.get('start', None)
    mode = request.args.get('mode', 'stock')
    if not start:
        start = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=730)).strftime('%Y-%m-%d')
    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    code_col = 'stock_code'
    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE {code_col}=? AND date<=? AND date>=?
        ORDER BY date""", (code, date_str, start)).fetchall()
    if len(klines) < 60:
        return jsonify({'error': f'K线不足 ({len(klines)}条, 需要 >= 60)'})
    daily = [dict(r) for r in klines]
    from scanners.top_pattern import detect_all, load_params
    freq = request.args.get('freq', 'D')
    params = load_params()
    result = detect_all(daily, params, freq=freq, stock_code=code)
    return jsonify(result)


@app.route('/api/top-pattern/diag')
def api_top_pattern_diag():
    code = request.args.get('stock', '600519')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    start = request.args.get('start', None)
    mode = request.args.get('mode', 'stock')
    if not start:
        start = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=730)).strftime('%Y-%m-%d')
    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    code_col = 'stock_code'
    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE {code_col}=? AND date<=? AND date>=?
        ORDER BY date""", (code, date_str, start)).fetchall()
    if len(klines) < 60:
        return jsonify({'error': f'K线不足 ({len(klines)}条, 需要 >= 60)'})
    daily = [dict(r) for r in klines]
    from scanners.top_pattern import get_diag, load_params
    freq = request.args.get('freq', 'D')
    params = load_params()
    diag = get_diag(daily, params, stock_code=code)
    return jsonify(diag)

# ═══════════════════════════════════════════════
# API: GET /api/volume-divergence (量价背离)
# ═══════════════════════════════════════════════

@app.route('/api/volume-divergence')
def api_volume_divergence():
    code = request.args.get('stock', '600519')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    start = request.args.get('start', None)
    mode = request.args.get('mode', 'stock')
    if not start:
        start = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=730)).strftime('%Y-%m-%d')
    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    code_col = 'stock_code'
    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE {code_col}=? AND date<=? AND date>=?
        ORDER BY date""", (code, date_str, start)).fetchall()
    if len(klines) < 60:
        return jsonify({'error': f'K线不足 ({len(klines)}条)'})
    daily = [dict(r) for r in klines]
    from scanners.volume_divergence import detect_range, load_params
    params = load_params()
    result = detect_range(daily, params, stock_code=code)
    return jsonify({'daily': daily, 'signals': result, 'stock_code': code})


@app.route('/api/volume-divergence/diag')
def api_volume_divergence_diag():
    code = request.args.get('stock', '600519')
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    start = request.args.get('start', None)
    mode = request.args.get('mode', 'stock')
    if not start:
        start = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=730)).strftime('%Y-%m-%d')
    db = get_db()
    table = 'index_daily_kline' if mode == 'index' else 'daily_kline'
    code_col = 'stock_code'
    klines = db.execute(f"""SELECT date, open, high, low, close, volume FROM {table}
        WHERE {code_col}=? AND date<=? AND date>=?
        ORDER BY date""", (code, date_str, start)).fetchall()
    if len(klines) < 60:
        return jsonify({'error': f'K线不足 ({len(klines)}条)'})
    daily = [dict(r) for r in klines]
    from scanners.volume_divergence import get_diag, load_params
    params = load_params()
    diag = get_diag(daily, params, stock_code=code)
    return jsonify(diag)


# ═══════════════════════════════════════════════
# API: GET /api/market-panorama
# ═══════════════════════════════════════════════

@app.route('/api/market-panorama')
def api_market_panorama():
    """大盘扫描看板全景数据，从 market_snapshot_daily 读取"""
    db = get_db()

    snap = db.execute("SELECT * FROM market_snapshot_daily WHERE double_strong IS NOT NULL ORDER BY date DESC LIMIT 1").fetchone()
    if not snap:
        return jsonify({'status': 'no_data', 'date': datetime.now().strftime('%Y-%m-%d')})

    # 抛盘日
    dc = snap['dist_30d_count'] or 0
    dd = snap['dist_30d_dates'] or ''
    if dc >= 5:
        dist = {'level': 'danger', 'label': f'{dc}个', 'desc': snap['dist_detail'] or f'近30天{dc}个抛盘日({dd})'}
    elif dc >= 3:
        dist = {'level': 'warning', 'label': f'{dc}个', 'desc': snap['dist_detail'] or f'近30天{dc}个抛盘日({dd})'}
    elif dc >= 1:
        dist = {'level': 'ok', 'label': f'{dc}个', 'desc': snap['dist_detail'] or f'近30天{dc}个抛盘日({dd})'}
    else:
        dist = {'level': 'ok', 'label': '0', 'desc': snap['dist_detail'] or '近30天无抛盘日'}
    dist['dates'] = dd

    core = {
        'distribution': dist,
        'ftd': {'level': 'ok' if (snap['ftd_30d_count'] or 0) > 0 else 'warning',
                'label': str(snap['ftd_30d_count'] or 0), 'count': snap['ftd_30d_count'] or 0,
                'dates': snap['ftd_30d_dates'] or '', 'desc': snap['ftd_detail'] or ''},
        'accumulation': {'level': 'ok' if (snap['acc_30d_count'] or 0) > 0 else 'warning',
                        'label': str(snap['acc_30d_count'] or 0), 'count': snap['acc_30d_count'] or 0,
                        'dates': snap['acc_30d_dates'] or '', 'desc': snap['acc_detail'] or ''},
    }

    ch = snap['crowd_high_count'] or 0
    ct = snap['crowd_total'] or 0
    crowd = {
        'high_count': ch, 'total': ct,
        'desc': f'拥挤度≥70的指数{ch}个/{ct}个。' + (
            '多个指数过热，追高风险加大。' if ch>=3 else '指数拥挤度正常。' if ch<=1 else '个别指数偏热，注意区分趋势与泡沫。'
        ),
    }

    stocks = {
        'double_strong': snap['double_strong'] or 0,
        'steady_leader': snap['steady_leader'] or 0,
        'burst': snap['burst'] or 0,
    }

    return jsonify({'date': snap['date'], 'core': core, 'crowding': crowd, 'stocks': stocks,
                    'ad': {'positive': snap['ad_positive_count'] or 0, 'total': snap['ad_total'] or 0,
                           'desc': snap['ad_detail'] or ''},
                    'divergence': {'count': snap['diverge_count'] or 0, 'desc': snap['diverge_detail'] or ''}})

# ═══════════════════════════════════════════════
    klines_out = [k for k in klines_full if start <= k['date'] <= end]
    signals_out = [s for s in signals if start <= s['date'] <= end]
    return jsonify({'klines': klines_out, 'signals': signals_out})


@app.route('/api/market-panorama/compute', methods=['POST'])
def api_market_panorama_compute():
    """手动触发快照计算"""
    import subprocess
    target = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    try:
        r = subprocess.run(
            ['python', 'scripts/compute_market_snapshot.py', '--date', target],
            cwd=PROJECT_DIR, capture_output=True, text=True, timeout=120
        )
        return jsonify({'ok': r.returncode == 0, 'output': r.stdout[-500:] if r.stdout else r.stderr[-200:]})
    except Exception as e:
        return jsonify({'ok': False, 'output': str(e)})

def _save_ad_snapshot(db, result, as_of_date):
    """从AD计算结果中提取摘要，存入 market_snapshot_daily"""
    try:
        positive = 0; total = 0
        for pname, pdata in result.get('pools', {}).items():
            for item in pdata.get('rankings', []):
                r = item.get('rating', '')
                if r:
                    total += 1
                    if r[0] in ('A', 'B'):
                        positive += 1
        if total == 0: return
        pct = round(positive / total * 100)
        desc = f'{positive}/{total}个指数AD评级为正(A/B)，' + (
            '机构资金积极流入，市场支撑强。' if pct >= 60 else
            '机构资金中性偏多，可适度参与。' if pct >= 30 else
            '机构资金偏向流出，谨慎操作。')
        db.execute("""
            INSERT INTO market_snapshot_daily (date, ad_positive_count, ad_total, ad_detail)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET ad_positive_count=excluded.ad_positive_count,
            ad_total=excluded.ad_total, ad_detail=excluded.ad_detail
        """, (as_of_date, positive, total, desc))
        db.commit()
    except Exception as e:
        print(f"[AD snapshot] save error: {e}")


def _save_divergence_snapshot(db, results, as_of_date):
    """从背离计算结果中提取摘要，存入 market_snapshot_daily"""
    try:
        count = sum(1 for r in results if r.get('resonance'))
        if count == 0:
            desc = '当前无指数出现背离共振，市场趋势一致性强。'
        elif count <= 2:
            desc = f'{count}个指数出现背离共振，关注趋势转折可能。'
        else:
            desc = f'{count}个指数出现背离共振，多项指标预警，建议降低仓位。'
        db.execute("""
            INSERT INTO market_snapshot_daily (date, diverge_count, diverge_detail)
            VALUES (?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET diverge_count=excluded.diverge_count,
            diverge_detail=excluded.diverge_detail
        """, (as_of_date, count, desc))
        db.commit()
    except Exception as e:
        print(f"[Divergence snapshot] save error: {e}")


# ═══════════════════════════════════════════════
# API: POST /api/backtest/save
# ═══════════════════════════════════════════════

@app.route('/api/backtest/save', methods=['POST', 'OPTIONS'])
def api_backtest_save():
    if request.method == 'OPTIONS': return '', 204

    data = request.get_json()
    name = data.get('name', f"Backtest {datetime.now().strftime('%Y%m%d_%H%M')}")
    stock_code = data.get('stock_code')
    start = data.get('start')
    end = data.get('end')
    signal_type = data.get('signal_type', 'distribution_day')
    params = data.get('params', {})
    signals = data.get('signals', [])
    stats = data.get('stats', {})

    db = get_db()
    cur = db.cursor()
    cur.execute("""INSERT INTO backtest_runs (name, signal_type, stock_code, start_date, end_date, params)
        VALUES (?,?,?,?,?,?)""", (name, signal_type, stock_code, start, end, json.dumps(params)))
    run_id = cur.lastrowid

    for s in signals:
        cur.execute("""INSERT INTO backtest_signals (run_id,stock_code,date,signal_type,score,open,high,low,close,
            change_pct,volume,amount,vol_5d,vol_10d,vol_20d,ma5,ma10,ma20,ma50,ma120,ma250,
            volume_score,decline_score,position_score,gap_score,special_score,total_score,
            close_position,upper_shadow_pct,lower_shadow_pct,volume_ratio,volume_ratio_ma5)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, stock_code, s.get('date'), s.get('signal_type'), s.get('total_score', 0),
             s.get('open'), s.get('high'), s.get('low'), s.get('close'), s.get('change_pct'),
             s.get('volume'), s.get('amount', 0), s.get('vol_5d'), s.get('vol_10d'), s.get('vol_20d'),
             s.get('ma5'), s.get('ma10'), s.get('ma20'), s.get('ma50'), s.get('ma120'), s.get('ma250'),
             0,0,0,0,0,0, s.get('close_position'), s.get('upper_shadow_pct'), s.get('lower_shadow_pct'),
             s.get('volume_ratio'), s.get('volume_ratio_ma5')))

    cur.execute("""INSERT INTO backtest_stats (run_id,total_days,signal_count,standard_count,
        heavy_count,stealth_count,reversal_count,weighted_count,avg_vol_10d,avg_volume_ratio)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (run_id, stats.get('total_days', 0), stats.get('signal_count', 0),
         stats.get('standard_count', 0), stats.get('heavy_count', 0),
         stats.get('special_count', 0), stats.get('reversal_count', 0),
         stats.get('weighted_count', 0), stats.get('avg_vol_10d'), stats.get('avg_volume_ratio')))

    db.commit()
    return jsonify({'ok': True, 'run_id': run_id})

# ═══════════════════════════════════════════════
# API: GET /api/backtest/list
# ═══════════════════════════════════════════════

@app.route('/api/backtest/list')
def api_backtest_list():
    db = get_db()
    signal_type = request.args.get('signal_type', 'distribution_day')
    rows = db.execute("""SELECT r.*, s.signal_count, s.weighted_count
        FROM backtest_runs r LEFT JOIN backtest_stats s ON r.id=s.run_id
        WHERE r.signal_type=? ORDER BY r.created_at DESC""", (signal_type,)).fetchall()
    return jsonify([dict(r) for r in rows])

# ═══════════════════════════════════════════════
# API: GET /api/backtest/compare
# ═══════════════════════════════════════════════

@app.route('/api/backtest/compare')
def api_backtest_compare():
    id1, id2 = request.args.get('id1'), request.args.get('id2')
    db = get_db()

    def get_run(rid):
        run = db.execute("SELECT * FROM backtest_runs WHERE id=?", (rid,)).fetchone()
        stats = db.execute("SELECT * FROM backtest_stats WHERE run_id=?", (rid,)).fetchone()
        signals = db.execute("SELECT * FROM backtest_signals WHERE run_id=? ORDER BY date", (rid,)).fetchall()
        return {'run': dict(run) if run else None, 'stats': dict(stats) if stats else None,
                'signals': [dict(s) for s in signals]}

    return jsonify({'run1': get_run(id1), 'run2': get_run(id2)})

# ═══════════════════════════════════════════════
# API: GET /api/backtest/<id>/signals
# ═══════════════════════════════════════════════

@app.route('/api/backtest/<int:run_id>/signals')
def api_backtest_signals(run_id):
    db = get_db()
    rows = db.execute("SELECT * FROM backtest_signals WHERE run_id=? ORDER BY date", (run_id,)).fetchall()
    return jsonify([dict(r) for r in rows])

# ═══════════════════════════════════════════════
# API: 指数拥挤度回测
# ═══════════════════════════════════════════════

@app.route('/api/crowding/config', methods=['GET', 'POST', 'OPTIONS'])
def api_crowding_config():
    if request.method == 'OPTIONS': return '', 204

    config_path = os.path.join(PROJECT_DIR, 'config', 'index_crowding.yaml')

    if request.method == 'POST':
        raw = request.get_data(as_text=True)
        if raw:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(raw)
            return jsonify({'ok': True})
        return jsonify({'ok': False, 'error': 'empty body'}), 400

    # GET
    if os.path.exists(config_path):
        with open(config_path, encoding='utf-8') as f:
            return f.read(), 200, {'Content-Type': 'text/yaml; charset=utf-8'}
    return jsonify({'weights': {}, 'levels': {}})


@app.route('/api/crowding/backtest', methods=['POST', 'OPTIONS'])
def api_crowding_backtest():
    if request.method == 'OPTIONS': return '', 204

    data = request.get_json() or {}
    weights = data.get('weights', {})
    levels_raw = data.get('levels', {})
    index_codes = data.get('index_codes', [])
    start_date = data.get('start_date', '2025-01-01')
    end_date = data.get('end_date', '2026-05-05')

    # 取前100个指数
    if not index_codes:
        index_codes = DEFAULT_INDEX_CODES[:100] if 'DEFAULT_INDEX_CODES' in dir() else []
    else:
        index_codes = index_codes[:100]

    # 构建权重和等级
    w = {
        'turnover_ratio': weights.get('turnover_ratio', 0.25),
        'turnover_rate': weights.get('turnover_rate', 0.10),
        'margin_balance': weights.get('margin_balance', 0.15),
        'margin_buy': weights.get('margin_buy', 0.10),
        'pe_pct': weights.get('pe_pct', 0.15),
        'pb_pct': weights.get('pb_pct', 0.05),
        'dyr_pct': weights.get('dyr_pct', 0.05),
        'fund_holding': weights.get('fund_holding', 0.15),
    }
    levels = [
        (0, levels_raw.get('low_max', 30), '低拥挤'),
        (levels_raw.get('low_max', 30), levels_raw.get('normal_max', 60), '正常'),
        (levels_raw.get('normal_max', 60), levels_raw.get('elevated_max', 80), '偏高'),
        (levels_raw.get('elevated_max', 80), 101, '高拥挤'),
    ]

    from scanners.index_crowding import compute_for_api
    results = compute_for_api(index_codes, start_date, end_date, w, levels)

    return jsonify({
        'results': results,
        'params': {'weights': w, 'levels': levels_raw},
        'count': len(results),
    })


@app.route('/api/crowding/indices', methods=['GET'])
def api_crowding_indices():
    """返回指数池列表"""
    yaml_path = os.path.join(PROJECT_DIR, 'config', 'index_style.yaml')
    if not os.path.exists(yaml_path):
        return jsonify([])
    try:
        import yaml
        with open(yaml_path, encoding='utf-8') as f:
            data = yaml.safe_load(f)
        indices = []
        for cat_name, idx_list in data.get('categories', {}).items():
            for item in idx_list:
                indices.append({
                    'code': item['code'],
                    'name': item['name'],
                    'category': cat_name
                })
        return jsonify(indices)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/crowding/latest', methods=['GET'])
def api_crowding_latest():
    """返回所有指数最新拥挤度数据（从 index_crowding_daily 表直接取）"""
    db = get_db()
    date = request.args.get('date', None)
    try:
        if not date:
            date = db.execute("SELECT MAX(date) FROM index_crowding_daily").fetchone()[0]
        # 找到不晚于请求日期且记录最多的快照
        snap = db.execute("""SELECT date, COUNT(*) as cnt FROM index_crowding_daily
            WHERE date <= ? GROUP BY date ORDER BY cnt DESC LIMIT 1""", (date,)).fetchone()
        if not snap or not snap['date']:
            return jsonify({'results': [], 'date': date, 'count': 0})
        date = snap['date']
        rows = db.execute('''
            SELECT stock_code, composite_score, crowd_level,
                   heat_score, flow_score, valuation_score,
                   pe_pct, turnover_ratio_pct
            FROM index_crowding_daily
            WHERE date = ?
            ORDER BY composite_score DESC
        ''', (date,)).fetchall()
        results = [{
            'stock_code': r['stock_code'],
            'composite_score': r['composite_score'],
            'crowd_level': r['crowd_level'],
            'heat_score': r['heat_score'],
            'flow_score': r['flow_score'],
            'valuation_score': r['valuation_score'],
            'pe_pct': r['pe_pct'],
            'turnover_ratio_pct': r['turnover_ratio_pct'],
        } for r in rows]
        return jsonify({'results': results, 'date': date, 'count': len(results)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════
# API: 个股RS强度
# ═══════════════════════════════════════════════

# ── 个股RS计算缓存 ──
_rs_cache = {}  # {date: polars DataFrame}

@app.route('/api/stock-rs', methods=['GET'])
def api_stock_rs():
    date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 200))
    try:
        from scanners.stock_rs import compute
        import polars as pl
        # 缓存：同一天不重复计算
        if date not in _rs_cache:
            _rs_cache.clear()  # 只保留最新一天
            _rs_cache[date] = compute(target_date=date, start_date=None)
        df = _rs_cache[date]
        latest_date = df["date"].max()
        # 过滤到最新日期，并把 null rps 和有效 rps 分开——防止排序参数导致 null 排前面
        latest = df.filter(pl.col("date") == latest_date)
        valid_rps = latest.filter(pl.col("rps_250").is_not_null()).sort("rps_250", descending=True)
        total_valid = len(valid_rps)
        total_pages = (total_valid + page_size - 1) // page_size if page_size > 0 else 1
        start = (page - 1) * page_size
        page_data = valid_rps.slice(start, page_size)

        results = []
        for row in page_data.iter_rows(named=True):
            results.append({
                'stock_code': row['stock_code'],
                'name': row.get('name', ''),
                'close': row['adj_close'],
                'rps_250': row['rps_250'],
                'rps_120': row['rps_120'],
                'rps_60': row['rps_60'],
                'rps_20': row['rps_20'],
                'double_strong': row['double_strong'],
                'rs_line': round(row['rs_line_norm'], 2) if row['rs_line_norm'] is not None else None,
            })

        stats = {
            'total': len(latest),
            'valid_rps250': total_valid,
            'double_strong_count': latest.filter(pl.col("double_strong").is_not_null()).shape[0],
            'page': page,
            'page_size': page_size,
            'total_pages': total_pages,
        }

        return jsonify({'date': str(latest_date), 'results': results, 'stats': stats})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stock-rs/double-strong', methods=['GET'])
def api_stock_rs_double_strong():
    date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    try:
        from scanners.stock_rs import compute, get_double_strong
        import polars as pl
        if date not in _rs_cache:
            _rs_cache.clear()
            _rs_cache[date] = compute(target_date=date, start_date=None)
        df = _rs_cache[date]
        ds = get_double_strong(df)
        latest_date = df["date"].max()
        ds = ds.filter(pl.col("date") == latest_date).filter(pl.col("rps_250").is_not_null()).sort("rps_250", descending=True)

        results = []
        for row in ds.iter_rows(named=True):
            results.append({
                'stock_code': row['stock_code'],
                'name': row.get('name', ''),
                'close': row['adj_close'],
                'rps_250': row['rps_250'],
                'rps_20': row['rps_20'],
                'pattern': row['double_strong'],
            })

        return jsonify({'date': str(latest_date), 'results': results, 'count': len(results)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stock-rs/rs-line', methods=['GET'])
def api_stock_rs_line():
    """单只股票RS线历史序列"""
    code = request.args.get('code', '600519')
    start = request.args.get('start', '2024-01-01')
    end = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))
    try:
        from scanners.stock_rs import compute
        import polars as pl
        cache_key = end + '_' + (start or 'full')
        if cache_key not in _rs_cache:
            if len(_rs_cache) > 2:
                _rs_cache.clear()
            _rs_cache[cache_key] = compute(target_date=end, start_date=start)
        df = _rs_cache[cache_key]
        stock = df.filter((pl.col("stock_code")==code) & (pl.col("date")>=start) & (pl.col("date")<=end)).sort("date")
        if stock.shape[0] == 0:
            return jsonify({'error': f'{code} 无数据'}), 404
        return jsonify({
            'code': code,
            'dates': stock["date"].to_list(),
            'rs_line': [round(x, 2) if x else None for x in stock["rs_line_norm"].to_list()],
            'close': stock["adj_close"].to_list(),
            'rps_250': stock["rps_250"].to_list(),
            'rps_20': stock["rps_20"].to_list(),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════
# API: GET /api/valuation — 指数估值分位数据
# ═══════════════════════════════════════════════

@app.route('/api/valuation')
def api_valuation():
    code = request.args.get('code', '000300')
    start = request.args.get('start', '2016-01-01')
    end = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))
    db = get_db()
    rows = db.execute('''
        SELECT date, pe_ttm, pe_ttm_pct, pb, pb_pct, dyr, dyr_pct
        FROM index_fundamental_daily
        WHERE stock_code = ? AND date >= ? AND date <= ?
        ORDER BY date
    ''', (code, start, end)).fetchall()
    return jsonify({
        'code': code,
        'dates': [r['date'] for r in rows],
        'pe': [r['pe_ttm'] for r in rows],
        'pe_pct': [r['pe_ttm_pct'] for r in rows],
        'pb': [r['pb'] for r in rows],
        'pb_pct': [r['pb_pct'] for r in rows],
        'dyr': [r['dyr'] for r in rows],
        'dyr_pct': [r['dyr_pct'] for r in rows],
    })


@app.route('/api/valuation/fs')
def api_valuation_fs():
    """指数财务数据（年报）"""
    code = request.args.get('code', '000300')
    try:
        import sys, os
        sys.path.insert(0, os.path.join(PROJECT_DIR, 'scripts'))
        from common import api_post
        metrics = [
            'y.m.npatoshopc_ps.t', 'y.m.roe.t',
            'y.ps.oi.t', 'y.ps.op.t', 'y.ps.op_s_r.t',
            'y.ps.np.t', 'y.ps.np_s_r.t',
            'y.ps.da_om.t', 'y.ps.tas.t',
        ]
        raw = api_post('/index/fs/hybrid', {
            'stockCodes': [code], 'metricsList': metrics,
            'startDate': '2016-01-01',
            'endDate': datetime.now().strftime('%Y-%m-%d'),
        })
        raw_sorted = sorted(raw, key=lambda x: x.get('date', ''))
        result = {'code': code, 'dates': [],
            'eps': [], 'roe': [], 'revenue': [], 'op_profit': [],
            'op_margin': [], 'net_profit': [], 'net_margin': [],
            'dividend': [], 'tax': [], 'peg': []}
        for item in raw_sorted:
            dt = item.get('date', '')[:10]
            result['dates'].append(dt[:4] if len(dt)>4 else dt)
            y = item.get('y', {})
            ps = y.get('ps', {})
            m = y.get('m', {})
            def get_nested(d, path):
                for k in path:
                    if not isinstance(d, dict): return d
                    d = d.get(k, {})
                return d if not isinstance(d, dict) else None
            result['eps'].append(get_nested(m, ['npatoshopc_ps','t']))
            result['roe'].append(get_nested(m, ['roe','t']))
            result['revenue'].append(get_nested(ps, ['oi','t']))
            result['op_profit'].append(get_nested(ps, ['op','t']))
            result['op_margin'].append(get_nested(ps, ['op_s_r','t']))
            result['net_profit'].append(get_nested(ps, ['np','t']))
            result['net_margin'].append(get_nested(ps, ['np_s_r','t']))
            result['dividend'].append(get_nested(ps, ['da_om','t']))
            result['tax'].append(get_nested(ps, ['tas','t']))
            result['peg'].append(None)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════
# API: 个股全维度看板
# ═══════════════════════════════════════════════

@app.route('/api/stock-valuation')
def api_stock_valuation():
    """个股估值指标历史：PE/PB/PS/股息率/市值"""
    code = request.args.get('code', '600519')
    start = request.args.get('start', '2016-01-01')
    end = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))
    db = get_db()
    metrics = ['pe_ttm','pb','ps_ttm','dyr','mc']
    result = {'code': code, 'dates': [], 'pe': [], 'pb': [], 'ps': [], 'dyr': [], 'mc': []}
    rows = db.execute('''
        SELECT date, metric_code, value FROM fundamental_indicator
        WHERE stock_code=? AND date>=? AND date<=?
        AND metric_code IN (?,?,?,?,?)
        ORDER BY date, metric_code
    ''', (code, start, end, *metrics)).fetchall()
    # 按日期聚合
    by_date = {}
    for r in rows:
        d = r['date']
        if d not in by_date: by_date[d] = {}
        by_date[d][r['metric_code']] = r['value']
    for d in sorted(by_date.keys()):
        v = by_date[d]
        result['dates'].append(d)
        result['pe'].append(v.get('pe_ttm'))
        result['pb'].append(v.get('pb'))
        result['ps'].append(v.get('ps_ttm'))
        result['dyr'].append(v.get('dyr'))
        result['mc'].append(v.get('mc'))
    # 股票名称
    name_row = db.execute('SELECT name FROM stock_basic WHERE stock_code=?', (code,)).fetchone()
    result['name'] = name_row['name'] if name_row else code

    # ── 计算十年分位 ──
    def calc_pct(arr, ascending=True):
        """计算每个值在历史中的百分位(0~1)"""
        valid = [(i, v) for i, v in enumerate(arr) if v is not None]
        if len(valid) < 2: return [None]*len(arr)
        sorted_vals = sorted(valid, key=lambda x: x[1], reverse=not ascending)
        n = len(sorted_vals)
        pcts = [None]*len(arr)
        for rank, (idx, _) in enumerate(sorted_vals):
            pcts[idx] = round(rank/(n-1), 4)
        return pcts

    result['pe_pct'] = calc_pct(result['pe'], ascending=True)    # PE越低越便宜
    result['pb_pct'] = calc_pct(result['pb'], ascending=True)
    result['ps_pct'] = calc_pct(result['ps'], ascending=True)
    result['dyr_pct'] = calc_pct(result['dyr'], ascending=False)  # 股息率越高越好

    return jsonify(result)


@app.route('/api/quarterly-fcf')
def api_quarterly_fcf():
    """个股季度自由现金流（单季拆分）"""
    code = request.args.get('code', '600519')
    db = get_db()
    rows = db.execute('''
        SELECT report_date, free_cash_flow
        FROM stock_financials_quarterly
        WHERE stock_code=? AND free_cash_flow IS NOT NULL
        ORDER BY report_date
    ''', (code,)).fetchall()
    if len(rows) > 40:
        rows = rows[-40:]

    # 累计→单季拆分：Q1用原值，Q2~Q4减去上一季累计值
    dates, fcf_single = [], []
    for i, r in enumerate(rows):
        d = r['report_date']
        v = r['free_cash_flow']
        month = int(d[5:7])
        if month == 3:  # Q1 = 单季 = 累计
            single = v
        elif i > 0:  # Q2/Q3/Q4 = 累计 - 上年同季度累计
            prev = rows[i-1]['free_cash_flow']
            single = v - prev if prev is not None else v
        else:
            single = v
        dates.append(d)
        fcf_single.append(round(single, 2))

    return jsonify({
        'code': code,
        'dates': dates,
        'fcf': fcf_single,
    })


@app.route('/api/stock-financials')
def api_stock_financials():
    """个股年度财务数据：ROE/毛利率/净利率/EPS/营收增速/净利增速/FCF/资产负债率等"""
    code = request.args.get('code', '600519')
    db = get_db()
    rows = db.execute('''
        SELECT report_date, revenue, revenue_yoy, net_profit, net_profit_yoy,
               gross_margin, roe,
               free_cash_flow, asset_liability_ratio, interest_bearing_debt_ratio,
               current_ratio, quick_ratio, receivables_turnover, inventory_turnover,
               total_liabilities, interest_bearing_debt, cabb
        FROM stock_financials_annual
        WHERE stock_code=? AND report_date >= '2016-12-31'
        ORDER BY report_date
    ''', (code,)).fetchall()
    result = {'code': code, 'dates': [], 'revenue': [], 'revenue_yoy': [],
              'net_profit': [], 'net_profit_yoy': [], 'gross_margin': [],
              'roe': [], 'eps': [], 'fcf': [], 'debt_ratio': [],
              'interest_debt_ratio': [], 'current_ratio': [], 'quick_ratio': [],
              'receivables_turnover': [], 'inventory_turnover': [],
              'total_debt': [], 'interest_debt': [], 'interest_free_debt': [],
              'cabb': [], 'interest_bearing_debt': []}
    for r in rows:
        result['dates'].append(r['report_date'][:4])
        result['revenue'].append(r['revenue'])
        result['revenue_yoy'].append(r['revenue_yoy'])
        result['net_profit'].append(r['net_profit'])
        result['net_profit_yoy'].append(r['net_profit_yoy'])
        result['gross_margin'].append(r['gross_margin'])
        result['roe'].append(r['roe'])
        result['cabb'].append(r['cabb'] if r['cabb'] else None)
        result['interest_bearing_debt'].append(r['interest_bearing_debt'] if r['interest_bearing_debt'] else None)
        # EPS = 净利润 / 总股本
        cap_row = db.execute('''SELECT capitalization FROM stock_equity_change
            WHERE stock_code=? AND change_date <= ? ORDER BY change_date DESC LIMIT 1''',
            (code, r['report_date'])).fetchone()
        cap = cap_row['capitalization'] if cap_row and cap_row['capitalization'] else None
        eps = (r['net_profit'] / cap) if (r['net_profit'] and cap) else None
        result['eps'].append(eps)
        result['fcf'].append(r['free_cash_flow'])
        result['debt_ratio'].append(r['asset_liability_ratio'])
        result['interest_debt_ratio'].append(r['interest_bearing_debt_ratio'])
        result['current_ratio'].append(r['current_ratio'])
        result['quick_ratio'].append(r['quick_ratio'])
        result['receivables_turnover'].append(r['receivables_turnover'])
        result['inventory_turnover'].append(r['inventory_turnover'])
        # 负债绝对值（直接使用理杏仁提供的值，单位与总资产一致）
        tl = r['total_liabilities']
        ibd = r['interest_bearing_debt']
        if tl is not None:
            result['total_debt'].append(tl)
            result['interest_debt'].append(ibd if ibd is not None else 0)
            result['interest_free_debt'].append(tl - (ibd or 0))
        else:
            result['total_debt'].append(None)
            result['interest_debt'].append(None)
            result['interest_free_debt'].append(None)
        # 年度PE = 市值 / 净利润 = (股本 × 年末收盘价) / 净利润
        year_end_price = None
        if cap and r['net_profit']:
            yr = r['report_date'][:4]
            k_row = db.execute('''SELECT close FROM daily_kline
                WHERE stock_code=? AND date >= ? AND date <= ?
                ORDER BY date DESC LIMIT 1''',
                (code, yr+'-12-01', yr+'-12-31')).fetchone()
            if k_row:
                year_end_price = k_row['close']
        annual_pe = (cap * year_end_price / r['net_profit']) if (cap and year_end_price and r['net_profit']) else None
        result.setdefault('annual_pe', []).append(annual_pe)
    return jsonify(result)


# ═══════════════════════════════════════════════
# API: GET /api/index-rs
# ═══════════════════════════════════════════════

def load_index_pools():
    """从 config/index_style.yaml 加载指数分类池定义"""
    if not os.path.exists(INDEX_RS_CONFIG):
        return {}
    if HAS_YAML:
        with open(INDEX_RS_CONFIG, encoding='utf-8') as f:
            data = yaml.safe_load(f)
        categories = data.get('categories', {})
        pools = {}
        for cat_name, indices in categories.items():
            pools[cat_name] = [item['code'] for item in indices]
        return pools
    else:
        # 回退到简易解析
        return _parse_index_yaml_simple()


def _parse_index_yaml_simple():
    """简易YAML解析（无PyYAML时回退）"""
    pools = {}
    current_cat = None
    with open(INDEX_RS_CONFIG, encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if ':' in stripped and not stripped.startswith('-') and 'code:' not in stripped.lower():
                cat_name = stripped.split(':')[0].strip().split('#')[0].strip()
                if cat_name in ('categories', 'meta'):
                    continue
                pools[cat_name] = []
                current_cat = cat_name
            elif current_cat and '- {code:' in stripped:
                m = re.search(r"code:\s*['\"]?([^'\",}]+)", stripped)
                if m:
                    pools[current_cat].append(m.group(1).strip())
    return pools


INDEX_NAMES_MAP = {}

def load_index_names():
    """从 config/index_style.yaml 加载指数代码→名称映射"""
    global INDEX_NAMES_MAP
    if INDEX_NAMES_MAP:
        return INDEX_NAMES_MAP
    if not os.path.exists(INDEX_RS_CONFIG):
        return {}
    if HAS_YAML:
        with open(INDEX_RS_CONFIG, encoding='utf-8') as f:
            data = yaml.safe_load(f)
        for cat_name, indices in data.get('categories', {}).items():
            for item in indices:
                INDEX_NAMES_MAP[item['code']] = item['name']
    else:
        with open(INDEX_RS_CONFIG, encoding='utf-8') as f:
            for line in f:
                m_code = re.search(r"code:\s*['\"]?([^'\",}]+)", line)
                m_name = re.search(r"name:\s*['\"]?([^'\",}]+)", line)
                if m_code and m_name:
                    INDEX_NAMES_MAP[m_code.group(1).strip()] = m_name.group(1).strip()
    return INDEX_NAMES_MAP


@app.route('/api/index-rs')
def api_index_rs():
    """指数RS强度 — 从 index_rs_daily 读取预计算结果"""
    as_of_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    pool_name = request.args.get('pool', None)

    db = get_db()
    all_pools = load_index_pools()
    if not all_pools:
        return jsonify({'error': 'index_style.yaml not found'}), 500

    if pool_name and pool_name in all_pools:
        pools = {pool_name: all_pools[pool_name]}
    else:
        pools = all_pools

    index_names = load_index_names()
    tier_config = load_config('index_rs') or {}
    tier_params = tier_config.get('tiers', {})

    result = {'pools': {}}

    for pname, codes in pools.items():
        ph = ','.join(['?' for _ in codes])
        rows = db.execute(f"""
            SELECT * FROM index_rs_daily WHERE date <= ? AND stock_code IN ({ph})
            AND date = (SELECT MAX(date) FROM index_rs_daily WHERE date <= ?)
        """, [as_of_date] + codes + [as_of_date]).fetchall()

        # 获取当日涨跌幅（index_daily_kline.change 是百分比小数）
        daily_changes = {}
        if rows:
            krows = db.execute(f"""
                SELECT stock_code, change, date FROM index_daily_kline
                WHERE stock_code IN ({ph}) AND kline_type='normal' AND date<=?
                ORDER BY date DESC
            """, codes + [as_of_date]).fetchall()
            # 取每个 code 的最新一条
            seen = set()
            for kr in krows:
                if kr['stock_code'] not in seen:
                    seen.add(kr['stock_code'])
                    daily_changes[kr['stock_code']] = round((kr['change'] or 0) * 100, 2)

        rankings = []
        for r in rows:
            rankings.append({
                'code': r['stock_code'], 'name': index_names.get(r['stock_code'], r['stock_code']),
                'close': r['close'],
                'change_pct': round(daily_changes.get(r['stock_code'], 0) or 0, 2),
                'RET_20': r['ret_20'], 'RET_60': r['ret_60'], 'RET_120': r['ret_120'], 'RET_250': r['ret_250'],
                'RS_20': r['rs_20'], 'RS_60': r['rs_60'], 'RS_120': r['rs_120'], 'RS_250': r['rs_250'],
            })

        # L1/L2/L3 筛选
        l1, l2, l3 = [], [], []
        l1_cfg = tier_params.get('L1', {}) if tier_params else {}
        l2_cfg = tier_params.get('L2', {}) if tier_params else {}
        l3_cfg = tier_params.get('L3', {}) if tier_params else {}

        for item in rankings:
            if l1_cfg:
                if (item['RS_120'] or 0) >= l1_cfg.get('rs_120', 90) and \
                   (item['RS_250'] or 0) >= l1_cfg.get('rs_250', 85) and \
                   (item['RS_60'] or 0) >= l1_cfg.get('rs_60', 80):
                    l1.append(item)
            if l2_cfg:
                if (item['RS_20'] or 0) >= l2_cfg.get('rs_20', 90):
                    l2.append(item)
            if l3_cfg:
                if (item['RS_60'] or 0) >= l3_cfg.get('rs_60', 70):
                    l3.append(item)

        rankings.sort(key=lambda x: x['RS_120'] or 0, reverse=True)
        top10 = rankings[:10]

        result['pools'][pname] = {
            'rankings': rankings,
            'tiers': {'L1': l1, 'L2': l2, 'L3': l3},
            'top10': top10,
        }

    return jsonify(result)

# ═══════════════════════════════════════════════
# API: GET /api/index-constituents
# ═══════════════════════════════════════════════

@app.route('/api/index-constituents')
def api_index_constituents():
    index_code = request.args.get('index_code', '')
    date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))

    if not index_code:
        return jsonify({'error': 'index_code required'}), 400

    db = get_db()

    # 找到指定日期之前最近一个月份快照
    snap = db.execute("""SELECT date FROM index_constituents
        WHERE index_code=? AND date <= ?
        ORDER BY date DESC LIMIT 1""", (index_code, date)).fetchone()

    if not snap:
        return jsonify({'constituents': [], 'snapshot_date': None, 'count': 0})

    snap_date = snap['date']

    # 拉取成分股及权重 + 涨跌幅
    rows = db.execute("""SELECT ic.stock_code, sb.name,
        (SELECT icw.weighting FROM index_constituent_weightings icw
         WHERE icw.index_code = ic.index_code AND icw.stock_code = ic.stock_code
         ORDER BY icw.date DESC LIMIT 1) as weighting,
        (SELECT close FROM daily_kline WHERE stock_code=ic.stock_code AND date<=? ORDER BY date DESC LIMIT 1) as close,
        (SELECT close FROM daily_kline WHERE stock_code=ic.stock_code AND date<=(SELECT date FROM daily_kline WHERE stock_code=ic.stock_code AND date<=? ORDER BY date DESC LIMIT 1 OFFSET 1) ORDER BY date DESC LIMIT 1) as prev_close,
        (SELECT close FROM daily_kline WHERE stock_code=ic.stock_code AND date<=(SELECT date FROM daily_kline WHERE stock_code=ic.stock_code AND date<=? ORDER BY date DESC LIMIT 1 OFFSET 4) ORDER BY date DESC LIMIT 1) as close_5d_ago,
        (SELECT close FROM daily_kline WHERE stock_code=ic.stock_code AND date<=(SELECT date FROM daily_kline WHERE stock_code=ic.stock_code AND date<=? ORDER BY date DESC LIMIT 1 OFFSET 9) ORDER BY date DESC LIMIT 1) as close_10d_ago,
        (SELECT close FROM daily_kline WHERE stock_code=ic.stock_code AND date<=(SELECT date FROM daily_kline WHERE stock_code=ic.stock_code AND date<=? ORDER BY date DESC LIMIT 1 OFFSET 19) ORDER BY date DESC LIMIT 1) as close_20d_ago
        FROM index_constituents ic
        LEFT JOIN stock_basic sb ON ic.stock_code = sb.stock_code
        WHERE ic.index_code = ? AND ic.date = ?
        ORDER BY weighting DESC NULLS LAST""",
        (date, date, date, date, date, index_code, snap_date)).fetchall()

    constituents = []
    for r in rows:
        c = {'stock_code': r['stock_code'], 'name': r['name'], 'weighting': r['weighting']}
        if r['close'] and r['prev_close']:
            c['chg_d1'] = round((r['close']/r['prev_close']-1)*100, 2)
        if r['close'] and r['close_5d_ago']:
            c['chg_5d'] = round((r['close']/r['close_5d_ago']-1)*100, 2)
        if r['close'] and r['close_10d_ago']:
            c['chg_10d'] = round((r['close']/r['close_10d_ago']-1)*100, 2)
        if r['close'] and r['close_20d_ago']:
            c['chg_20d'] = round((r['close']/r['close_20d_ago']-1)*100, 2)
        constituents.append(c)

    return jsonify({
        'index_code': index_code,
        'snapshot_date': snap_date,
        'count': len(constituents),
        'constituents': constituents,
    })

# ═══════════════════════════════════════════════
# API: GET /api/index-ad
# ═══════════════════════════════════════════════

@app.route('/api/index-ad')
def api_index_ad():
    as_of_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    pool_name = request.args.get('pool', None)
    window_days = int(request.args.get('window', 65))
    method = request.args.get('method', 'raw')  # raw | zscore

    db = get_db()

    # zscore需要250天历史基线，增加查询窗口
    if method == 'zscore':
        lookback = 500  # 250天基线 + 65天窗口 ≈ 315个交易日 ≈ 500个日历日
    else:
        lookback = window_days * 2 + 30

    # 加载指数分类池
    all_pools = load_index_pools()
    if not all_pools:
        return jsonify({'error': 'index_style.yaml not found or empty'}), 500

    if pool_name and pool_name in all_pools:
        pools = {pool_name: all_pools[pool_name]}
    else:
        pools = all_pools

    # 加载指数名称
    index_names = load_index_names()

    # 收集所有需要的指数代码
    all_codes = set()
    for codes in pools.values():
        all_codes.update(codes)
    code_list = list(all_codes)
    if not code_list:
        return jsonify({'error': 'no indices in pool'}), 400

    # 批量查询K线
    placeholders = ','.join(['?' for _ in code_list])
    rows = db.execute(f"""SELECT k.stock_code, k.date, k.open, k.high, k.low, k.close, k.volume, k.amount, k.change,
        COALESCE(f.to_r, CASE WHEN f.mc > 0 THEN k.amount / f.mc ELSE 0 END) as to_r
        FROM index_daily_kline k
        LEFT JOIN index_fundamental_daily f ON k.stock_code = f.stock_code AND k.date = f.date
        WHERE k.kline_type='normal'
        AND k.stock_code IN ({placeholders})
        AND k.date >= date(?, '-{lookback} days')
        ORDER BY k.stock_code, k.date""",
        code_list + [as_of_date]).fetchall()

    # 按指数代码分组
    pool_klines = {}
    for r in rows:
        code = r['stock_code']
        if code not in pool_klines:
            pool_klines[code] = []
        pool_klines[code].append({
            'date': r['date'],
            'open': r['open'],
            'high': r['high'],
            'low': r['low'],
            'close': r['close'],
            'to_r': r['to_r'],
            'change': r['change'],
        })

    # 调用引擎
    result = detect_index_ad(pool_klines, pools, as_of_date, window_days, method)

    # 补充指数名称和评级含义
    for pname, pdata in result['pools'].items():
        for item in pdata.get('rankings', []):
            item['name'] = index_names.get(item['code'], item['code'])
            if item.get('rating'):
                from detectors.index_ad import RATING_MEANINGS
                item['meaning'] = RATING_MEANINGS.get(item['rating'], '')

    # ── 保存摘要到 market_snapshot_daily ──
    _save_ad_snapshot(db, result, as_of_date)

    return jsonify(result)

# ═══════════════════════════════════════════════
# API: GET /api/stock-analysis
# ═══════════════════════════════════════════════

@app.route('/api/stock-analysis')
def api_stock_analysis():
    stock_code = request.args.get('code', '')
    if not stock_code:
        return jsonify({'error': 'code required'}), 400
    try:
        from analysis.financial import dcf_valuation, comps_analysis, earnings_analysis, three_statement_projection
        def safe(fn, *args):
            try: return fn(*args)
            except Exception as e: return {'error': str(e)}
        dcf = safe(dcf_valuation, stock_code, {'exit_multiple': 8})
        comps = safe(comps_analysis, stock_code)
        earnings = safe(earnings_analysis, stock_code)
        model = safe(three_statement_projection, stock_code)
        return jsonify({'dcf': dcf, 'comps': comps, 'earnings': earnings, 'model': model})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ═══════════════════════════════════════════════
# API: GET /api/fundamental-deterioration
# ═══════════════════════════════════════════════

@app.route('/api/fundamental-deterioration')
def api_fundamental_deterioration():
    code = request.args.get('code', '')
    if not code:
        return jsonify({'error': 'code required'}), 400
    try:
        from analysis.fundamental_deterioration import check_fundamental_deterioration
        result = check_fundamental_deterioration(code)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════
# API: GET /api/index-divergence
# ═══════════════════════════════════════════════

@app.route('/api/index-divergence')
def api_index_divergence():
    as_of_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    pool_name = request.args.get('pool', 'market')
    sensitivity = request.args.get('sensitivity', 'long')

    db = get_db()

    all_pools = load_index_pools()
    if pool_name not in all_pools:
        return jsonify({'error': 'invalid pool'}), 400
    pool_codes = all_pools[pool_name]
    index_names = load_index_names()

    # 如带 _t 参数则跳过缓存，强制重算
    force_refresh = bool(request.args.get('_t', ''))

    # 先查缓存（跳过强制刷新时）
    cached = []
    placeholders = ','.join(['?' for _ in pool_codes])
    if not force_refresh:
        cached = db.execute(f'''SELECT * FROM index_divergence_daily
            WHERE stock_code IN ({placeholders}) AND date = ?''',
            pool_codes + [as_of_date]).fetchall()

    cached_map = {r['stock_code']: r for r in cached}
    all_codes = set(pool_codes)
    missing = all_codes - set(cached_map.keys())

    # 如果全部缓存命中，直接返回
    if not missing:
        results = []
        for r in cached:
            results.append(build_div_result(r, index_names))
        return jsonify({'as_of_date': as_of_date, 'pool': pool_name, 'cached': True, 'indices': results})

    # 批量查询K线
    lookback = 500
    code_list = list(missing)
    ph = ','.join(['?' for _ in code_list])
    rows = db.execute(f'''SELECT stock_code, date, open, high, low, close, volume, amount, change
        FROM index_daily_kline WHERE kline_type='normal'
        AND stock_code IN ({ph})
        AND date >= date(?, '-{lookback} days')
        ORDER BY stock_code, date''',
        code_list + [as_of_date]).fetchall()

    pool_klines = {}
    for r in rows:
        code = r['stock_code']
        if code not in pool_klines:
            pool_klines[code] = []
        pool_klines[code].append(dict(r))

    # 加载配置
    cfg = load_config('divergence')
    vp_cfg = cfg.get('volume_price', {}) if cfg else {}
    rsi_cfg = cfg.get('rsi', {}) if cfg else {}
    macd_cfg = cfg.get('macd', {}) if cfg else {}
    breadth_cfg = cfg.get('breadth', {}) if cfg else {}
    confirm_window = cfg.get('confirm_window', 20) if cfg else 20

    if sensitivity == 'short':
        rsi_cfg = {**rsi_cfg, 'period': 7, 'lookback': 10}
        macd_cfg = {**macd_cfg, 'lookback': 10}
        confirm_window = 10

    # ── 成分股上涨比例预计算 ──
    # 仅对 market/sector_l1/sector_l2 计算（86个指数），其余跳过
    advance_ratios_map = {}
    if pool_name in ('market', 'sector_l1', 'sector_l2'):
        advance_ratios_map = compute_advance_ratios(db, pool_codes, as_of_date)

    results = []
    for code in sorted(missing):
        klines = pool_klines.get(code, [])
        if not klines: continue

        as_of_idx = None
        for i, k in enumerate(klines):
            if k['date'] == as_of_date: as_of_idx = i; break
        if as_of_idx is None:
            for i in range(len(klines)-1, -1, -1):
                if klines[i]['date'] <= as_of_date: as_of_idx = i; break
        if as_of_idx is None: continue

        actual_date = klines[as_of_idx]['date']
        close_val = klines[as_of_idx]['close']

        div_vp = detect_volume_price_divergence(klines, vp_cfg, as_of_idx)
        div_rsi = detect_rsi_divergence(klines, rsi_cfg, as_of_idx)
        div_macd = detect_macd_divergence(klines, macd_cfg, as_of_idx)

        # 成分股背离（仅对有预计算数据的指数）
        div_breadth = None
        if code in advance_ratios_map and advance_ratios_map[code]:
            ar_dict = advance_ratios_map[code]  # {date: ratio}
            div_breadth = detect_breadth_divergence(klines, ar_dict, as_of_idx, breadth_cfg)

        div_vp = confirm_divergence(klines, as_of_idx, div_vp, confirm_window)
        div_rsi = confirm_divergence(klines, as_of_idx, div_rsi, confirm_window)
        div_macd = confirm_divergence(klines, as_of_idx, div_macd, confirm_window)
        div_breadth = confirm_divergence(klines, as_of_idx, div_breadth, min(confirm_window, 10))

        divergences = {'vp': div_vp, 'rsi': div_rsi, 'macd': div_macd, 'breadth': div_breadth}
        resonance_level, alert_text = compute_resonance(divergences, None, None, None)

        db.execute('''INSERT OR REPLACE INTO index_divergence_daily
            (stock_code, date, div_vp_type, div_vp_level, div_vp_strength,
             div_rsi_type, div_rsi_level, div_macd_type, div_macd_level,
             div_breadth_type, div_breadth_level,
             resonance_level, alert_text, close, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime("now","localtime"))''',
            (code, actual_date,
             div_vp['type'] if div_vp else None, div_vp['level'] if div_vp else None, div_vp.get('strength') if div_vp else None,
             div_rsi['type'] if div_rsi else None, div_rsi['level'] if div_rsi else None,
             div_macd['type'] if div_macd else None, div_macd['level'] if div_macd else None,
             div_breadth['type'] if div_breadth else None, div_breadth['level'] if div_breadth else None,
             resonance_level, alert_text, close_val))

        results.append({
            'code': code, 'name': index_names.get(code, code), 'close': close_val,
            'div_vp': {'type': div_vp['type'], 'level': div_vp['level'], 'strength': div_vp.get('strength')} if div_vp else None,
            'div_rsi': {'type': div_rsi['type'], 'level': div_rsi['level']} if div_rsi else None,
            'div_macd': {'type': div_macd['type'], 'level': div_macd['level']} if div_macd else None,
            'div_breadth': {'type': div_breadth['type'], 'level': div_breadth['level']} if div_breadth else None,
            'resonance_level': resonance_level, 'alert_text': alert_text,
        })

    db.commit()

    # 合并缓存结果
    for r in cached:
        if r['stock_code'] not in missing:
            results.append(build_div_result(r, index_names))

    results.sort(key=lambda x: x['code'])
    # ── 保存摘要到 market_snapshot_daily ──
    _save_divergence_snapshot(db, results, as_of_date)

    return jsonify({'as_of_date': as_of_date, 'pool': pool_name, 'indices': results})
    return {
        'code': r['stock_code'], 'name': index_names.get(r['stock_code'], r['stock_code']),
        'close': r['close'],
        'div_vp': {'type': r['div_vp_type'], 'level': r['div_vp_level'], 'strength': r['div_vp_strength']} if r['div_vp_type'] else None,
        'div_rsi': {'type': r['div_rsi_type'], 'level': r['div_rsi_level']} if r['div_rsi_type'] else None,
        'div_macd': {'type': r['div_macd_type'], 'level': r['div_macd_level']} if r['div_macd_type'] else None,
        'div_breadth': {'type': r['div_breadth_type'], 'level': r['div_breadth_level']} if r['div_breadth_type'] else None,
        'resonance_level': r['resonance_level'], 'alert_text': r['alert_text'],
        'rs_rating': r['rs_rating'], 'ad_rating': r['ad_rating'], 'crowd_level': r['crowd_level'],
    }


def compute_advance_ratios(db, index_codes, as_of_date):
    """
    预计算每个指数的每日成分股上涨比例。
    返回 {code: [ratio, ...]} 与 index_daily_kline 等长。
    """
    advance_map = {}

    # 1. 找到最近一次成分股快照
    placeholders = ','.join(['?' for _ in index_codes])
    snapshots = db.execute(f'''SELECT index_code, MAX(date) as snap_date
        FROM index_constituents WHERE index_code IN ({placeholders})
        AND date <= ? GROUP BY index_code''',
        index_codes + [as_of_date]).fetchall()

    snap_map = {r['index_code']: r['snap_date'] for r in snapshots}

    # 2. 对每个指数，取成分股列表 → 查询近65天的日涨跌
    lookback_date = (datetime.strptime(as_of_date, '%Y-%m-%d') - timedelta(days=100)).strftime('%Y-%m-%d')

    for code in index_codes:
        snap_date = snap_map.get(code)
        if not snap_date:
            advance_map[code] = None
            continue

        # 取成分股列表
        constituents = db.execute('''SELECT stock_code FROM index_constituents
            WHERE index_code = ? AND date = ?''', (code, snap_date)).fetchall()
        if not constituents:
            advance_map[code] = None
            continue

        c_codes = [r['stock_code'] for r in constituents]
        c_ph = ','.join(['?' for _ in c_codes])

        # 查询这些成分股的日涨跌
        rows = db.execute(f'''SELECT date, AVG(CASE WHEN change_pct > 0 THEN 1.0 ELSE 0.0 END) as up_ratio
            FROM daily_kline WHERE stock_code IN ({c_ph})
            AND date >= ? AND date <= ?
            GROUP BY date ORDER BY date''',
            c_codes + [lookback_date, as_of_date]).fetchall()

        if not rows:
            advance_map[code] = None
            continue

        # 构建与index_daily_kline对齐的日期列表
        idx_dates = db.execute('''SELECT date FROM index_daily_kline
            WHERE stock_code = ? AND kline_type="normal"
            AND date >= ? AND date <= ? ORDER BY date''',
            (code, lookback_date, as_of_date)).fetchall()

        date_to_ratio = {r['date']: r['up_ratio'] for r in rows}
        advance_map[code] = date_to_ratio  # {date: ratio}

    return advance_map

# ═══════════════════════════════════════════════
# 缠论分析 API — /api/chanlun
# ═══════════════════════════════════════════════

@app.route('/api/chanlun/analyze', methods=['GET'])
def api_chanlun_analyze():
    """缠论分析：分型→笔→中枢→信号"""
    code = request.args.get('code', '000985')
    freq = request.args.get('freq', 'D')
    limit = int(request.args.get('limit', 300))
    data_mode = request.args.get('mode', 'auto')
    try:
        from scanners.chanlun import analyze
        result = analyze(code, freq, limit, data_mode=data_mode)
        import json
        json.dumps(result)
        return jsonify(result)
    except Exception as e:
        import traceback, sys
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        return jsonify({"error": str(e)}), 500

@app.route('/api/chanlun/echarts', methods=['GET'])
def api_chanlun_echarts():
    """缠论 ECharts 图表配置（可直接用于前端渲染）"""
    code = request.args.get('code', '000985')
    freq = request.args.get('freq', 'D')
    limit = int(request.args.get('limit', 300))
    data_mode = request.args.get('mode', 'auto')
    try:
        from scanners.chanlun import get_echarts_option
        theme = request.args.get('theme', 'dark')
        option = get_echarts_option(code, freq, limit, theme, data_mode=data_mode)
        return jsonify(option)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500

@app.route('/api/chanlun/multi-period', methods=['GET'])
def api_chanlun_multi_period():
    """缠论多周期联立分析（日/周/月）"""
    code = request.args.get('code', '000985')
    limit = int(request.args.get('limit', 400))
    data_mode = request.args.get('mode', 'auto')
    try:
        from scanners.chanlun import multi_period_analyze
        result = multi_period_analyze(code, limit, data_mode=data_mode)
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route('/api/chanlun/cascade', methods=['GET'])
def api_chanlun_cascade():
    """区间套分析：日线→60分钟→15分钟三级级联"""
    code = request.args.get('code', '600519')
    date = request.args.get('date', None)
    side = request.args.get('side', None)
    try:
        from scanners.chanlun import cascade_analyze
        result = cascade_analyze(code, date, side)
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route('/api/chanlun/cascade/echarts', methods=['GET'])
def api_chanlun_cascade_echarts():
    """区间套分钟级 K 线图 ECharts 配置"""
    code = request.args.get('code', '600519')
    freq = request.args.get('freq', '60')
    limit = int(request.args.get('limit', 300))
    try:
        from scanners.chanlun import get_minute_echarts_option
        theme = request.args.get('theme', 'dark')
        option = get_minute_echarts_option(code, freq, limit, theme)
        return jsonify(option)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


# ═══════════════════════════════════════════════
# 统一形态扫描 API — /api/pattern-scan
# ═══════════════════════════════════════════════

@app.route('/api/pattern-scan', methods=['GET', 'OPTIONS'])
def api_pattern_scan():
    if request.method == 'OPTIONS':
        return '', 204

    # ── 参数解析 ──
    code = request.args.get('code', '600519')
    start = request.args.get('start', None)
    end = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))
    period = request.args.get('period', 'daily')
    mode = request.args.get('mode', '')  # 'stock' | 'index' | ''=auto

    db = get_db()

    # ── 确定是股票还是指数 ──
    if mode == 'index':
        is_index = True
    elif mode == 'stock':
        is_index = False
    else:
        is_index = bool(re.match(r'^(sh|sz|cs|cy|)\d{6}$', code) and (
            code.startswith('sh') or code.startswith('sz') or
            code.startswith('cs') or code.startswith('cy')
        ))
    table = 'index_daily_kline' if is_index else 'daily_kline'
    kf = "AND kline_type='normal'" if is_index else ''

    # 获取足够的历史K线（至少2年）
    # 个股优先用前复权价（adj_*），NULL 时退回不复权
    chg_col = 'change' if is_index else 'change_pct'
    if is_index:
        ohlc = "open, high, low, close"
    else:
        ohlc = "COALESCE(adj_open, open) as open, COALESCE(adj_high, high) as high, COALESCE(adj_low, low) as low, COALESCE(adj_close, close) as close"
    if start:
        rows = db.execute(f"""SELECT date, {ohlc}, volume, amount, {chg_col} as change_pct
            FROM {table} WHERE stock_code=? {kf}
            AND date>=date(?, '-750 days') AND date<=?
            ORDER BY date""", (code, start, end)).fetchall()
    else:
        rows = db.execute(f"""SELECT date, open, high, low, close, volume, amount, {chg_col} as change_pct
            FROM {table} WHERE stock_code=? {kf}
            AND date<=?
            ORDER BY date""", (code, end)).fetchall()

    if not rows:
        return jsonify({'code': code, 'error': 'no_data'})

    klines_full = [dict(r) for r in rows]

    # ── 前复权：用 change_pct 逆向推算（adj_*/complex_factor 大量缺失后的兜底方案）──
    if not is_index:
        _ensure_adj_prices(klines_full)

    # 获取股票名称
    name = code
    if not is_index:
        name_row = db.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
        if name_row:
            name = name_row['name']
    if name == code:
        idx_names = load_index_names()
        nm = idx_names.get(code, '')
        if nm:
            name = nm

    # ── 日→周/月聚合（如需要） ──
    if period == 'monthly':
        klines_full = _aggregate_klines(klines_full, 'month')
    elif period == 'weekly':
        klines_full = _aggregate_klines(klines_full, 'week')

    # ── 计算 TA-Lib 指标（供前端和引擎使用） ──
    indicators = _compute_indicators(klines_full)

    # ── 注入 stock_code 到每条 K 线（供引擎获取缠论数据）──
    for k in klines_full:
        k['stock_code'] = code

    # ── 运行全部引擎 ──
    signals = run_all_engines(klines=klines_full, indicators=indicators)

    # ── 过滤到请求的日期范围 ──
    if start:
        klines_out = [k for k in klines_full if k['date'] >= start]
        signals_out = [s for s in signals if s['date'] >= start]
    else:
        klines_out = klines_full
        signals_out = signals

    # box_breakdown：只输出活跃事件（failed 已清除的事件仅保留在 details 供统计，不上 K 线）
    signals_out = [s for s in signals_out
                   if not (s.get('source') == 'box_breakdown' and s.get('signal_level') is None)]

    # ── 信号统计 ──
    by_source = {}
    bullish = 0
    bearish = 0
    for s in signals_out:
        # 归一化：补齐缺失字段
        if 'type' not in s:
            s['type'] = 'bullish'  # 兜底：无type字段默认看涨（顶部形态等引擎已自行标注bearish）
        if 'confidence' not in s:
            s['confidence'] = 'medium'
        if 'pivot' not in s:
            s['pivot'] = None
        if 'details' not in s:
            s['details'] = {}

        src = s['source']
        if src not in by_source:
            by_source[src] = 0
        by_source[src] += 1
        if s['type'] == 'bullish':
            bullish += 1
        else:
            bearish += 1

    # ── 引擎列表 ──
    engine_list = get_engine_list()
    
    # ── 为 MW B1 信号补充技术置信度分 ──
    mw_ts = {}
    try:
        ts_rows = db.execute(
            "SELECT b1_date, tech_score FROM mw_signal_daily WHERE stock_code=? AND tech_score>0",
            (code,)
        ).fetchall()
        for r in ts_rows:
            mw_ts[r['b1_date']] = r['tech_score']
    except Exception as e:
        # 表不存在/字段变更时不阻塞扫描，但留痕（review W4：禁止静默吞错）
        print(f"[pattern-scan] mw_ts fetch failed for {code}: {type(e).__name__}: {e}", flush=True)
    
    for s in signals_out:
        if s.get('source') == 'mw_signal' and s.get('type') == 'bullish':
            ts = mw_ts.get(s['date'], 0)
            if ts > 0:
                s['details']['tech_score'] = ts

    return jsonify({
        'code': code,
        'name': name,
        'period': period,
        'date_range': {
            'start': klines_out[0]['date'] if klines_out else start,
            'end': klines_out[-1]['date'] if klines_out else end,
        },
        'klines': klines_out,
        'indicators': _sanitize_indicators(indicators, len(klines_out)),
        'engines': engine_list,
        'signals': signals_out,
        'signal_stats': {
            'by_source': by_source,
            'total': len(signals_out),
            'bullish': bullish,
            'bearish': bearish,
        },
        'recommendation': generate_recommendation(
            signals_out, indicators, klines_out, name
        ),
    })


def _compute_indicators(klines):
    """计算 TA-Lib 技术指标，返回 dict of lists"""
    n = len(klines)
    if n < 5:
        return {}

    close = np.array([k.get('close') or np.nan for k in klines], dtype=np.float64)
    high = np.array([k.get('high') or np.nan for k in klines], dtype=np.float64)
    low = np.array([k.get('low') or np.nan for k in klines], dtype=np.float64)
    open_ = np.array([k.get('open') or np.nan for k in klines], dtype=np.float64)
    vol = np.array([k.get('volume') or 0 for k in klines], dtype=np.float64)

    result = {}

    # SMA
    for p in [5, 10, 20, 50, 120, 250]:
        sma = talib.SMA(close, p)
        result[f'sma{p}'] = [float(x) if not np.isnan(x) else None for x in sma]

    # BBANDS
    bb_u, bb_m, bb_l = talib.BBANDS(close, 20, 2, 2, 0)
    result['bb_upper'] = [float(x) if not np.isnan(x) else None for x in bb_u]
    result['bb_middle'] = [float(x) if not np.isnan(x) else None for x in bb_m]
    result['bb_lower'] = [float(x) if not np.isnan(x) else None for x in bb_l]

    # ATR
    atr = talib.ATR(high, low, close, 14)
    result['atr14'] = [float(x) if not np.isnan(x) else None for x in atr]

    # RSI
    rsi = talib.RSI(close, 14)
    result['rsi14'] = [float(x) if not np.isnan(x) else None for x in rsi]

    # MACD
    macd, macd_sig, macd_hist = talib.MACD(close, 12, 26, 9)
    result['macd'] = [float(x) if not np.isnan(x) else None for x in macd]
    result['macd_signal'] = [float(x) if not np.isnan(x) else None for x in macd_sig]
    result['macd_hist'] = [float(x) if not np.isnan(x) else None for x in macd_hist]

    # VOL_MA50
    vol_ma = talib.SMA(vol, 50)
    result['vol_ma50'] = [float(x) if not np.isnan(x) else None for x in vol_ma]

    return result


def _sanitize_indicators(indicators, target_len):
    """确保 indicators 长度和 klines_out 对齐。
    klines_out 是 klines_full 按 start 日期过滤后的尾部，
    因此 indicators 也取尾部 target_len 个元素。"""
    result = {}
    for key, arr in indicators.items():
        arr = list(arr)
        if len(arr) < target_len:
            arr = [None] * (target_len - len(arr)) + arr
        else:
            arr = arr[-target_len:]  # 取尾部，与 klines_out 对齐
        result[key] = arr
    return result

# ═══════════════════════════════════════════════
# CAN SLIM 评分 API — /api/canslim-score
# ═══════════════════════════════════════════════

@app.route('/api/canslim-score', methods=['GET', 'POST', 'OPTIONS'])
def api_canslim_score():
    if request.method == 'OPTIONS':
        return '', 204

    if request.method == 'POST':
        # 保存结果到数据库
        data = request.get_json()
        stock_code = data.get('stock_code', '600519')
        target_date = data.get('date', datetime.now().strftime('%Y-%m-%d'))
        result = canslim_score_stock(stock_code, target_date, save=True)
        return jsonify({'saved': True, 'score': result['score'], 'grade': result['grade']})

    # GET: 计算并返回评分
    code = request.args.get('code', '600519')
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    result = canslim_score_stock(code, target_date, save=False)
    return jsonify(result)

# ═══════════════════════════════════════════════
# CAN SLIM 评分查询 API — /api/canslim-scores
# ═══════════════════════════════════════════════

@app.route('/api/canslim-scores', methods=['GET', 'OPTIONS'])
def api_canslim_scores():
    if request.method == 'OPTIONS':
        return '', 204

    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    db = get_db()

    # 如果没有指定日期的数据，取有足够数据的最新日期（至少1000条）
    check = db.execute("""
        SELECT date, COUNT(*) as cnt FROM cansim_scores 
        GROUP BY date HAVING cnt >= 1000
        ORDER BY date DESC LIMIT 1
    """).fetchone()
    if not check:
        return jsonify({'scores': [], 'date': target_date, 'count': 0})

    latest = check['date']
    if target_date != latest:
        target_date = latest

    rows = db.execute("""
        SELECT s.stock_code, b.name, s.score, s.grade,
               s.score_c, s.score_a, s.score_n, s.score_s, s.score_l, s.score_i
        FROM cansim_scores s
        JOIN stock_basic b ON s.stock_code = b.stock_code
        WHERE s.date = ?
        ORDER BY s.score DESC, s.stock_code
    """, (target_date,)).fetchall()

    scores = []
    for i, r in enumerate(rows):
        scores.append({
            'rank': i + 1,
            'stock_code': r['stock_code'],
            'name': r['name'],
            'score': r['score'],
            'grade': r['grade'],
            'c': r['score_c'],
            'a': r['score_a'],
            'n': r['score_n'],
            's': r['score_s'],
            'l': r['score_l'],
            'i': r['score_i'],
        })

    return jsonify({'scores': scores, 'date': target_date, 'count': len(scores)})

# ═══════════════════════════════════════════════
# 缠论每日精选
# ═══════════════════════════════════════════════

@app.route('/api/chanlun-daily-selection', methods=['GET'])
def api_chanlun_daily_selection():
    """返回最新观察池中当日有缠论买入信号的股票"""
    target_date = request.args.get('date', '')
    db = get_db()
    
    # 确定日期
    if not target_date:
        row = db.execute("SELECT MAX(date) FROM discipline_observation_pool").fetchone()
        if not row or not row[0]:
            return jsonify({'items': [], 'date': '', 'count': 0})
        target_date = row[0]
    
    # 关联观察池 + 缠论扫描结果，筛选当日有买入信号的
    pool_count = db.execute("SELECT COUNT(*) FROM discipline_observation_pool WHERE date = ?", (target_date,)).fetchone()[0]
    scan_count = db.execute("SELECT COUNT(*) FROM chanlun_scan_daily WHERE scan_date = ? AND latest_trade_side = 'buy'", (target_date,)).fetchone()[0]
    
    rows = db.execute("""
        SELECT o.stock_code, o.stock_name, o.industry_name, o.rs_category,
               o.rps_20, o.rps_250, o.canslim_total, o.composite_score,
               c.latest_trade_type, c.latest_trade_price, c.bi_count, c.zs_count,
               c.latest_bi_dir, c.latest_bi_power
        FROM discipline_observation_pool o
        JOIN chanlun_scan_daily c ON o.stock_code = c.stock_code AND o.date = c.scan_date
        WHERE o.date = ? AND c.latest_trade_side = 'buy'
        ORDER BY o.composite_score DESC
    """, (target_date,)).fetchall()
    
    items = []
    for r in rows:
        items.append({
            'stock_code': r['stock_code'],
            'stock_name': r['stock_name'],
            'industry_name': r['industry_name'],
            'rs_category': r['rs_category'],
            'rps_20': r['rps_20'],
            'rps_250': r['rps_250'],
            'canslim_total': r['canslim_total'],
            'composite_score': round(r['composite_score'],1) if r['composite_score'] else None,
            'trade_type': r['latest_trade_type'],
            'trade_price': r['latest_trade_price'],
            'bi_count': r['bi_count'],
            'zs_count': r['zs_count'],
            'latest_bi_dir': r['latest_bi_dir'],
            'latest_bi_power': round(r['latest_bi_power'],1) if r['latest_bi_power'] else None,
        })
    
    return jsonify({'items': items, 'date': target_date, 'count': len(items), 'pool_count': pool_count, 'scan_count': scan_count})


# ═══════════════════════════════════════════════
# 突破形态结构识别
# ═══════════════════════════════════════════════

@app.route('/api/pattern/structure', methods=['GET', 'OPTIONS'])
def api_pattern_structure():
    if request.method == 'OPTIONS':
        return '', 204
    code = request.args.get('code', '').strip()
    start = request.args.get('start', '').strip()
    end = request.args.get('end', '').strip()
    if not code or not start or not end:
        return jsonify({'error': '缺少参数 code/start/end'}), 400
    from scanners.pattern_structure import analyze_structure
    result = analyze_structure(code, start, end)
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


# ═══════════════════════════════════════════════
# MW 信号
# ═══════════════════════════════════════════════

@app.route('/api/mw/scan', methods=['POST', 'OPTIONS'])
def api_mw_scan():
    if request.method == 'OPTIONS':
        return '', 204
    data = request.get_json() or {}
    start = data.get('start', '')
    end = data.get('end', '')
    if not start:
        return jsonify({'error': '缺少 start 参数'}), 400
    if not end:
        end = start
    
    from datetime import datetime, timedelta
    from scanners.mw_signal import run_scan
    
    s_dt = datetime.strptime(start, '%Y-%m-%d')
    e_dt = datetime.strptime(end, '%Y-%m-%d')
    results = []
    dt = s_dt
    while dt <= e_dt:
        ds = dt.strftime('%Y-%m-%d')
        print(f'[MW Scan] {ds}...', flush=True)
        try:
            run_scan(ds, fast=False)
            results.append(ds)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'{ds}: {str(e)}'}), 500
        dt += timedelta(days=1)
    
    return jsonify({'ok': True, 'scanned': results, 'count': len(results)})


@app.route('/api/mw/signals', methods=['GET', 'OPTIONS'])
def api_mw_signals():
    if request.method == 'OPTIONS':
        return '', 204
    target_date = request.args.get('date', None)
    start_date = request.args.get('start', None)
    end_date = request.args.get('end', None)
    signal_type = request.args.get('type', 'b2')  # b1 | b2
    date_col = 'b1_date' if signal_type == 'b1' else 'b2_date'
    db = get_db()
    
    if start_date and end_date:
        rows = db.execute(
            f"SELECT * FROM mw_signal_daily WHERE {date_col} >= ? AND {date_col} <= ? AND stock_code != '_sentinel_' ORDER BY {date_col} DESC, score DESC",
            (start_date, end_date)
        ).fetchall()
    elif target_date:
        rows = db.execute(
            f"SELECT * FROM mw_signal_daily WHERE {date_col}=? AND stock_code != '_sentinel_' ORDER BY score DESC", (target_date,)
        ).fetchall()
    else:
        row = db.execute(f"SELECT MAX({date_col}) FROM mw_signal_daily WHERE {date_col} IS NOT NULL AND stock_code != '_sentinel_'").fetchone()
        if not row or not row[0]:
            return jsonify({'date': None, 'signals': [], 'count': 0, 'dates': [], 'type': signal_type})
        target_date = row[0]
        rows = db.execute(
            f"SELECT * FROM mw_signal_daily WHERE {date_col}=? AND stock_code != '_sentinel_' ORDER BY score DESC", (target_date,)
        ).fetchall()
    
    signals = [dict(r) for r in rows]
    
    # 附加信号共振详情（B1 查 B1 日，B2 查 B2 日）
    for s in signals:
        code = s['stock_code']
        sig_date = s[date_col]
        row = db.execute("SELECT signals_json FROM pattern_scan_signals WHERE stock_code=? AND date=?", (code, sig_date)).fetchone()
        s['sig_details'] = []
        if row and row[0]:
            try:
                import json as _json
                sigs = _json.loads(row[0]) if isinstance(row[0], str) else row[0]
                for sig in (sigs if isinstance(sigs, list) else []):
                    src = sig.get('source','')
                    tp = sig.get('type','')
                    det = sig.get('details',{})
                    desc = det.get('cdl_name') or det.get('description') or det.get('signal_type') or ''
                    s['sig_details'].append({'source': src, 'type': tp, 'desc': desc})
            except:
                pass
    
    # 可用日期列表
    avail = db.execute(f"SELECT DISTINCT {date_col} FROM mw_signal_daily WHERE {date_col} IS NOT NULL AND stock_code != '_sentinel_' ORDER BY {date_col} DESC").fetchall()
    dates = [r[0] for r in avail]
    
    return jsonify({
        'date': target_date, 'start': start_date, 'end': end_date,
        'signals': signals, 'count': len(signals), 'dates': dates, 'type': signal_type
    })


# ═══════════════════════════════════════════════
# MW 信号回测分析
# ═══════════════════════════════════════════════

@app.route('/api/mw/backtest', methods=['GET', 'OPTIONS'])
def api_mw_backtest():
    if request.method == 'OPTIONS':
        return '', 204
    start = request.args.get('start', '2026-01-01')
    end = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))
    from analytics.mw_backtest import run
    result = run(start, end)
    return jsonify(result)


# ═══════════════════════════════════════════════
# 投资决策驾驶舱 API
# ═══════════════════════════════════════════════

@app.route('/api/cockpit/latest', methods=['GET', 'OPTIONS'])
def api_cockpit_latest():
    """获取最近一次管道运行结果"""
    if request.method == 'OPTIONS':
        return '', 204
    db = get_db()
    latest = db.execute("SELECT MAX(run_date) FROM cockpit_daily").fetchone()
    if not latest or not latest[0]:
        return jsonify({'candidates': [], 'run_date': None, 'market': {}, 'pipeline_stats': {}})
    run_date = latest[0]
    rows = db.execute(
        "SELECT * FROM cockpit_daily WHERE run_date=? ORDER BY rank",
        (run_date,)
    ).fetchall()
    candidates = [dict(r) for r in rows]
    # 解析 JSON 字段
    for c in candidates:
        for field in ['signal_types', 'theme_indices']:
            if c.get(field) and isinstance(c[field], str):
                try:
                    c[field] = json.loads(c[field])
                except json.JSONDecodeError:
                    c[field] = []

    # 获取大盘环境
    from cockpit.briefing import BriefingEngine
    engine = BriefingEngine(db)
    market = engine._module_market()
    engine.close()

    return jsonify({
        'candidates': candidates,
        'run_date': run_date,
        'market': market,
        'pipeline_stats': {'total_candidates': len(candidates)}
    })


@app.route('/api/cockpit/run', methods=['POST', 'OPTIONS'])
def api_cockpit_run():
    """手动触发管道运行"""
    if request.method == 'OPTIONS':
        return '', 204
    from cockpit.pipeline import run_pipeline, load_config as cockpit_load_config
    config = cockpit_load_config()
    db = get_db()
    try:
        candidates, stats = run_pipeline(
            target_date=datetime.now().strftime('%Y-%m-%d'),
            config=config, db=db, save=True
        )
        return jsonify({'success': True, 'candidates': candidates, 'stats': stats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cockpit/status', methods=['GET', 'OPTIONS'])
def api_cockpit_status():
    """管道运行状态"""
    if request.method == 'OPTIONS':
        return '', 204
    db = get_db()
    row = db.execute("SELECT MAX(run_date) as last_run, COUNT(*) as count FROM cockpit_daily").fetchone()
    # 检查是否在运行中
    import os
    lock_file = os.path.join(PROJECT_DIR, 'data', '.cockpit_running')
    running = os.path.exists(lock_file)
    return jsonify({
        'last_run': row['last_run'] if row else None,
        'total_records': row['count'] if row else 0,
        'running': running
    })


@app.route('/api/cockpit/config', methods=['GET', 'OPTIONS'])
def api_cockpit_config_get():
    """获取管道配置"""
    if request.method == 'OPTIONS':
        return '', 204
    from cockpit.pipeline import load_config as cockpit_load_config
    config = cockpit_load_config()
    return jsonify(config)


@app.route('/api/cockpit/config', methods=['POST'])
def api_cockpit_config_post():
    """保存管道配置 → config/cockpit.yaml"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400
    import yaml
    config_path = os.path.join(PROJECT_DIR, 'config', 'cockpit.yaml')
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return jsonify({'success': True, 'message': '配置已保存'})


@app.route('/api/cockpit/briefing', methods=['GET', 'OPTIONS'])
def api_cockpit_briefing():
    """单只股票完整简报"""
    if request.method == 'OPTIONS':
        return '', 204
    code = request.args.get('code', '')
    date = request.args.get('date', '')
    if not code:
        return jsonify({'error': 'stock_code required'}), 400

    db = get_db()
    from cockpit.briefing import BriefingEngine
    from cockpit.pipeline import load_config as _load_config
    engine = BriefingEngine(db)

    # 从数据库取候选数据
    if date:
        row = db.execute(
            "SELECT * FROM cockpit_daily WHERE stock_code=? AND run_date=?",
            (code, date)
        ).fetchone()
    else:
        row = db.execute(
            "SELECT * FROM cockpit_daily WHERE stock_code=? ORDER BY run_date DESC LIMIT 1",
            (code,)
        ).fetchone()

    if not row:
        engine.close()
        return jsonify({'error': 'No data for this stock'}), 404

    candidate = dict(row)
    # 获取市场数据
    market = engine._module_market()
    briefing = engine.generate(candidate, market_data=market)

    # 仓位计算
    from cockpit.position import calculate_position
    sl = briefing.get('stop_loss', {})
    entry = sl.get('entry_price_ref', 10)
    stop = sl.get('stop_loss_price', entry * 0.92)
    pos_cfg = _load_config().get('position', {})
    pos = calculate_position(
        entry, stop,
        account_size=pos_cfg.get('account_size', 1000000),
        max_loss_pct=pos_cfg.get('max_loss_pct', 0.02),
        kelly_fraction=pos_cfg.get('kelly_fraction', 0.25)
    )
    briefing['position'] = pos

    engine.close()
    return jsonify(briefing)




@app.route('/api/start-vibe', methods=['POST', 'OPTIONS'])
def api_start_vibe():
    """启动 vibe-trading MCP 服务（端口 8781）"""
    if request.method == 'OPTIONS': return '', 204
    import subprocess, socket
    # 先检查是否已在运行
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(('127.0.0.1', 8781))
        s.close()
        return jsonify({'ok': True, 'message': 'already running'})
    except:
        pass
    try:
        # vibe-trading.exe 可能的安装路径
        vibe_paths = [
            os.path.join(os.path.dirname(sys.executable), 'Scripts', 'vibe-trading.exe'),
            os.path.join(os.environ.get('APPDATA', ''), '..', 'Local', 'Programs', 'Python', 'Python312', 'Scripts', 'vibe-trading.exe'),
            os.path.join(os.environ.get('USERPROFILE', ''), 'AppData', 'Roaming', 'Python', 'Python312', 'Scripts', 'vibe-trading.exe'),
            'vibe-trading',  # 最后尝试 PATH
        ]
        vibe_exe = 'vibe-trading'
        for p in vibe_paths:
            if os.path.exists(p):
                vibe_exe = p
                break
        subprocess.Popen(
            [vibe_exe, 'serve', '--port', '8781'],
            cwd=PROJECT_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return jsonify({'ok': True, 'message': 'started'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


# 调研任务存储（内存）
_research_tasks = {}

def _run_vibe(tid, code, name, prompt):
    import urllib.request, time
    _research_tasks[tid]['status']='running'
    try:
        r=json.loads(urllib.request.urlopen(urllib.request.Request(
            'http://localhost:8781/sessions',
            data=json.dumps({'title':f'research-{code}'}).encode(),
            headers={'Content-Type':'application/json'}),timeout=10).read())
        sid=r.get('session_id','')
        if not sid: _research_tasks[tid].update({'status':'done','research':'会话创建失败','source':'error'});return
        urllib.request.urlopen(urllib.request.Request(
            f'http://localhost:8781/sessions/{sid}/messages',
            data=json.dumps({'content':prompt}).encode(),
            headers={'Content-Type':'application/json'}),timeout=15).read()
        deadline=time.time()+600
        while time.time()<deadline:
            time.sleep(8)
            try:
                m=json.loads(urllib.request.urlopen(f'http://localhost:8781/sessions/{sid}/messages?limit=5',timeout=15).read())
                for x in m:
                    if x.get('role')=='assistant' and x.get('content'):
                        _research_tasks[tid].update({'status':'done','research':x['content'],'source':'vibe-trading'});return
            except:pass
        _research_tasks[tid].update({'status':'done','research':'（调研超时）','source':'timeout'})
    except Exception as e:
        _research_tasks[tid].update({'status':'done','research':f'（调研异常: {e}）','source':'error'})


# ═══════════════════════════════════════════════
# API: GET /api/four-masters/analyze
# ═══════════════════════════════════════════════

@app.route('/api/four-masters/prepare', methods=['GET'])
def api_four_masters_prepare():
    """四大师分析准备：提取数据 + 生成每位大师的调研问题"""
    code = request.args.get('code', '').strip()
    if not code:
        return jsonify({'error': 'code required'}), 400
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    
    db = None
    try:
        db = get_db()
        name_row = db.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
        if not name_row:
            return jsonify({'error': '股票代码不存在'}), 404
        stock_name = name_row['name']

        rs_row = db.execute("""SELECT rps_20, rps_60, rps_120, rps_250 FROM stock_rs_daily
            WHERE stock_code=? ORDER BY date DESC LIMIT 1""", (code,)).fetchone()
        cs_row = db.execute("""SELECT score_c, score_a, score_l FROM cansim_scores
            WHERE stock_code=? ORDER BY date DESC LIMIT 1""", (code,)).fetchone()
        fs_rows = db.execute("""SELECT asset_liability_ratio FROM stock_financials_annual
            WHERE stock_code=? ORDER BY report_date DESC LIMIT 1""", (code,)).fetchall()
        pe_val = None; pb_val = None; roe_val = None; eps_val = None; revenue_val = None
        net_profit_val = None; net_margin_val = None; op_margin_val = None
        try:
            for mc in ['pe_ttm','pb','roe_ttm','eps_ttm']:
                row = db.execute('SELECT value FROM fundamental_indicator WHERE stock_code=? AND metric_code=? ORDER BY date DESC LIMIT 1', (code, mc)).fetchone()
                v = round(row['value'], 2) if row else None
                if mc == 'pe_ttm': pe_val = v
                elif mc == 'pb': pb_val = v
                elif mc == 'roe_ttm': roe_val = v
                elif mc == 'eps_ttm': eps_val = v
        except:
            pass
        # 从年报补充财务概览
        try:
            fa = db.execute("""SELECT revenue, net_profit, gross_margin, roe, operating_cash_flow
                FROM stock_financials_annual WHERE stock_code=? ORDER BY report_date DESC LIMIT 1""", (code,)).fetchone()
            if fa:
                r = fa['revenue']
                if r: revenue_val = round(r/1e8, 1)
                np = fa['net_profit']
                if np: net_profit_val = round(np/1e8, 1)
                gm = fa['gross_margin']
                if gm: net_margin_val = round(gm, 1)
                roe_v = fa['roe']
                if roe_v: roe_val = round(roe_v, 1)
        except:
            pass
        has_buy = False; has_sell = False
        try:
            sig_rows = db.execute("""SELECT signals_json FROM pattern_scan_signals
                WHERE stock_code=? AND date>=date(?, '-10 days') LIMIT 5""", (code, target_date)).fetchall()
            for r in sig_rows:
                import json as _j
                try:
                    sigs = _j.loads(r['signals_json'])
                    if isinstance(sigs, list):
                        for s in sigs:
                            st = s.get('signal_type','') if isinstance(s, dict) else ''
                            if st in ('buy','bullish','pp','bo','b1','b2'): has_buy = True
                            if st in ('sell','bearish','top','rule'): has_sell = True
                except: pass
        except:
            pass
        ind_row = db.execute("SELECT industry_name FROM stock_industry WHERE stock_code=? LIMIT 1", (code,)).fetchone()
        if not ind_row or not ind_row['industry_name']:
            ind_row = db.execute("SELECT ind_name as industry_name FROM mw_signal_daily WHERE stock_code=? AND ind_name IS NOT NULL LIMIT 1", (code,)).fetchone()
        debt_ratio = fs_rows[0]['asset_liability_ratio'] if fs_rows else None

        rps250 = rs_row['rps_250'] if rs_row else 0
        rps20 = rs_row['rps_20'] if rs_row else 0
        c_score = cs_row['score_c'] if cs_row else 0
        l_score = cs_row['score_l'] if cs_row else 0
        # has_buy/has_sell 已在上方的 signals_json 解析中设置
        masters = {
            'duan': {'name':'段永平','focus':'生意本质+管理层','questions':[]},
            'buffett': {'name':'巴菲特','focus':'护城河+估值','questions':[]},
            'munger': {'name':'芒格','focus':'风险+逆向','questions':[]},
            'li': {'name':'李录','focus':'长期趋势','questions':[]},
        }
        if c_score and c_score >= 50:
            masters['duan']['questions'].append(f"{stock_name}的当季收益增长很好，核心驱动力是什么？")
        masters['duan']['questions'] += [f"{stock_name}的管理层持股和创始人情况？", f"用一句话说清{stock_name}赚的是什么钱？"]
        if pe_val and pb_val:
            masters['buffett']['questions'].append(f"当前PE={pe_val} PB={pb_val}，估值合理吗？利润在周期底部？")
        masters['buffett']['questions'] += [f"{stock_name}的经济护城河是什么？", f"{stock_name}的资本配置记录如何？"]
        if debt_ratio and debt_ratio > 60:
            masters['munger']['questions'].append(f"负债率{debt_ratio:.0f}%偏高，风险在哪？")
        if has_sell:
            masters['munger']['questions'].append("近期卖出信号的原因？")
        masters['munger']['questions'] += [f"{stock_name}最可能的失败路径？", "管理层激励是否合理？"]
        if rps250 >= 80:
            masters['li']['questions'].append("行业RS很高，20年后还在吗？会被颠覆？")
        masters['li']['questions'] += [f"{stock_name}所在行业处于什么发展阶段？", "技术路线风险如何？"]

        all_q = []
        for mk, mv in masters.items():
            for q in mv['questions'][:3]:
                all_q.append(f"[{mv['name']}] {q}")

        # 财务表格数据（直接从数据库查询）
        fin_tables = {'pe':[],'pb':[],'ps':[],'dyr':[],'financials':[],'rs':[]}
        try:
            for mc in ['pe_ttm','pb','ps_ttm','dyr']:
                rows = db.execute('SELECT date, value FROM fundamental_indicator WHERE stock_code=? AND metric_code=? AND value IS NOT NULL ORDER BY date', (code, mc)).fetchall()
                fin_tables[mc.replace('_ttm','').replace('dyr','dyr')] = [{'d':r['date'], 'v':round(r['value'],2)} for r in rows]
            fa_rows = db.execute('SELECT * FROM stock_financials_quarterly WHERE stock_code=? AND report_date<="2026-06-30" ORDER BY report_date DESC LIMIT 10', (code,)).fetchall()
            for r in fa_rows:
                rev = r['revenue_single'] or 0
                np = r['net_profit_single'] or 0
                fin_tables['financials'].append({
                    'year': r['report_date'][:4]+'Q'+str((int(r['report_date'][5:7])-1)//3+1) if r['report_date'] else '',
                    'revenue': round(rev/1e8,1),
                    'rev_yoy': round(r['revenue_yoy'],1) if r['revenue_yoy'] else None,
                    'net_profit': round(np/1e8,2),
                    'np_yoy': round(r['net_profit_yoy'],1) if r['net_profit_yoy'] else None,
                    'gross_margin': round(r['gross_margin_single'] or 0,1),
                    'roe': round(r['roe_single'] or 0,1),
                    'fcf': round((r['free_cash_flow'] or 0)/1e8,1),
                    'debt_ratio': round(r['asset_liability_ratio'] or 0,1),
                    'current_ratio': round(r['current_ratio'] or 0,2),
                    'quick_ratio': round(r['quick_ratio'] or 0,2),
                })
            rs_rows = db.execute('SELECT date, rps_20, rps_250 FROM stock_rs_daily WHERE stock_code=? AND rps_250 IS NOT NULL ORDER BY date', (code,)).fetchall()
            fin_tables['rs'] = [{'d':r['date'],'rps20':r['rps_20'] or 0,'rps250':r['rps_250'] or 0} for r in rs_rows][-500:]
        except:
            pass

        return jsonify({
            'code': code, 'name': stock_name, 'date': target_date,
            'data_snapshot': {
                'rps250': rps250, 'rps20': rps20, 'c_score': c_score, 'l_score': l_score,
                'pe': pe_val, 'pb': pb_val, 'roe': roe_val, 'eps': eps_val,
                'revenue': revenue_val, 'net_profit': net_profit_val, 'gross_margin': net_margin_val,
                'has_buy': has_buy, 'has_sell': has_sell,
                'industry': ind_row['industry_name'] if ind_row else '',
                'debt_ratio': debt_ratio,
            },
            'masters': masters,
            'all_questions': all_q,
            'fin_tables': fin_tables,
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500
    finally:
        if db:
            try: db.close()
            except: pass


@app.route('/api/research/status', methods=['GET'])
def api_research_status():
    tid=request.args.get('task_id','')
    if not tid or tid not in _research_tasks:
        return jsonify({'status':'not_found'})
    t=_research_tasks[tid]; r={'task_id':tid,'code':t['code'],'name':t['name'],'status':t['status'],'source':t['source'],'questions':t['questions']}
    if t['status']=='done' and t['research']:
        r['research']=t['research']
        del _research_tasks[tid]
    return jsonify(r)


@app.route('/api/research', methods=['GET', 'POST', 'OPTIONS'])
def api_research():
    """外部调研：优先 vibe-trading MCP（端口 8781），降级到 DDGS 网络搜索"""
    if request.method == 'OPTIONS': return '', 204
    
    code = request.args.get('code', '')
    custom_queries = []
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        custom_queries = data.get('queries', [])
        if not code:
            code = data.get('code', '')
    if not code: return jsonify({'error': 'code required'}), 400
    
    db = get_db()
    name_row = db.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
    stock_name = name_row['name'] if name_row else code
    
    # 获取 RS 和信号数据用于自动生成问题
    rs_row = db.execute("""SELECT rps_20, rps_60, rps_120, rps_250 FROM stock_rs_daily
        WHERE stock_code=? ORDER BY date DESC LIMIT 1""", (code,)).fetchone()
    cs_row = db.execute("""SELECT score_c, score_a FROM cansim_scores
        WHERE stock_code=? ORDER BY date DESC LIMIT 1""", (code,)).fetchone()
    # 检查是否有 MW 信号
    mw_row = db.execute("""SELECT COUNT(*) as cnt FROM mw_signal_daily
        WHERE stock_code=? AND b2_date>=date('now','-15 days')""", (code,)).fetchone()
    has_buy = mw_row and mw_row['cnt'] > 0
    db.close()
    
    has_sell = False  # 简化为仅基于RS和CANSLIM判断
    rs250 = rs_row['rps_250'] if rs_row else 0
    c_score = cs_row['score_c'] if cs_row else 0
    
    # 生成调研问题：合并自定义问题 + 自动生成问题
    questions = list(custom_queries) if custom_queries else []
    # 自动生成问题
    if not custom_queries or True:  # 始终补充自动问题
        auto_qs = []
        auto_qs.append(f"{stock_name}({code})近期有什么重大新闻、公告或管理层变动？")
        auto_qs.append(f"{stock_name}最近一期财报的关键数据如何？营收和利润增长趋势？")
        if rs250 >= 90:
            auto_qs.append(f"{stock_name}(RS_250={rs250})长期强势的驱动力是什么？是否可持续？")
        if has_buy:
            auto_qs.append(f"{stock_name}近期出现买入信号，是否有业绩或事件催化剂支撑？")
        if c_score >= 50:
            auto_qs.append(f"{stock_name}当季收益增长的主要来源是什么？")
        questions.extend(auto_qs)
    
    # 异步启动 vibe-trading 调研（后台线程，不阻塞）
    combined = '请调研以下问题，逐一回答并标注来源：\n' + '\n'.join(f'{i+1}. {q}' for i, q in enumerate(questions[:8]))
    tid = f'{code}_{int(datetime.now().timestamp())}'
    _research_tasks[tid] = {'status':'pending','code':code,'name':stock_name,'questions':questions,'research':'','source':''}
    import threading
    threading.Thread(target=_run_vibe, args=(tid, code, stock_name, combined), daemon=True).start()
    return jsonify({'task_id':tid,'code':code,'name':stock_name,'source':'vibe-trading','questions':questions,'status':'started','message':'调研已启动(最长10分钟)，请稍候...'})


@app.route('/api/prompt-generator', methods=['GET', 'OPTIONS'])
def api_prompt_generator():
    """生成欧奈尔分析 prompt"""
    if request.method == 'OPTIONS': return '', 204
    code = request.args.get('code', '')
    date = request.args.get('date', '')
    if not code: return jsonify({'error': 'code required'}), 400
    from cockpit.oneil_deep import ONeilDeepAnalyzer
    db = get_db()
    a = ONeilDeepAnalyzer(db)
    name_row = db.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
    stock_name = name_row['name'] if name_row else code
    if not date:
        mw = db.execute("SELECT b2_date FROM mw_signal_daily WHERE stock_code=? AND b2_date >= date('now','-5 days') ORDER BY b2_date DESC LIMIT 1", (code,)).fetchone()
        if mw: date = mw['b2_date']
        else: date = datetime.now().strftime('%Y-%m-%d')
    info = a._build_rich_profile(code, {'stock_name': stock_name, 'signal_date': date})
    prompt = a._build_rich_prompt(code, info)
    # 财务表格数据
    fin_tables = {'pe':[],'pb':[],'ps':[],'financials':[],'rs':[]}
    try:
        for mc in ['pe_ttm','pb','ps_ttm']:
            rows = db.execute('SELECT date, value FROM fundamental_indicator WHERE stock_code=? AND metric_code=? AND value IS NOT NULL ORDER BY date', (code, mc)).fetchall()
            fin_tables[mc.replace('_ttm','')] = [{'d':r['date'], 'v':round(r['value'],2)} for r in rows]
        # 从季度财报补充财务概览（近10个季度）
        fa_rows = db.execute('SELECT * FROM stock_financials_quarterly WHERE stock_code=? AND report_date<="2026-06-30" ORDER BY report_date DESC LIMIT 10', (code,)).fetchall()
        for r in fa_rows:
            rev = r['revenue_single'] or 0
            np = r['net_profit_single'] or 0
            fin_tables['financials'].append({
                'year': r['report_date'][:4]+'Q'+str((int(r['report_date'][5:7])-1)//3+1) if r['report_date'] else '',
                'revenue': round(rev/1e8,1),
                'rev_yoy': round(r['revenue_yoy'],1) if r['revenue_yoy'] else None,
                'net_profit': round(np/1e8,2),
                'np_yoy': round(r['net_profit_yoy'],1) if r['net_profit_yoy'] else None,
                'gross_margin': round(r['gross_margin_single'] or 0,1),
                'roe': round(r['roe_single'] or 0,1),
                'fcf': round((r['free_cash_flow'] or 0)/1e8,1),
                'debt_ratio': round(r['asset_liability_ratio'] or 0,1),
                'current_ratio': round(r['current_ratio'] or 0,2),
                'quick_ratio': round(r['quick_ratio'] or 0,2),
            })
        rs_rows = db.execute('SELECT date, rps_20, rps_250 FROM stock_rs_daily WHERE stock_code=? AND rps_250 IS NOT NULL ORDER BY date', (code,)).fetchall()
        fin_tables['rs'] = [{'d':r['date'],'rps20':r['rps_20'] or 0,'rps250':r['rps_250'] or 0} for r in rs_rows][-500:]
    except:
        pass
    return jsonify({'code': code, 'name': stock_name, 'date': date, 'prompt': prompt, 'fin_tables': fin_tables, 'length': len(prompt)})


@app.route('/api/prompt-generator/analyze', methods=['POST', 'OPTIONS'])
def api_prompt_generator_analyze():
    """发送 prompt 给 DeepSeek CLI"""
    if request.method == 'OPTIONS': return '', 204
    data = request.get_json()
    if not data or not data.get('prompt'): return jsonify({'error': 'prompt required'}), 400
    import subprocess
    DEEPSEEK_EXE = r'D:\dstui\deepseek-tui-windows-x64.exe'
    try:
        result = subprocess.run([DEEPSEEK_EXE, 'exec', '--model', 'deepseek-v4-flash', data['prompt']],
            capture_output=True, text=True, timeout=180, encoding='utf-8', errors='replace',
            cwd=r'D:\dstui', shell=False)
        output = result.stdout.strip() or result.stderr.strip()
        if not output: output = 'AI 未返回内容'
    except subprocess.TimeoutExpired:
        output = 'AI 分析超时（180秒）'
    except Exception as e:
        output = f'调用失败: {str(e)}'
    # Markdown → HTML
    import re
    html = ''
    in_code = False
    in_ul = False
    in_ol = False
    for line in output.split('\n'):
        line_raw = line
        # 先替换行内格式
        line = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
        line = re.sub(r'`(.+?)`', r'<code>\1</code>', line)
        if line.startswith('```'):
            if in_code: html += '</code></pre>'
            else: html += '<pre class="md-code"><code>'
            in_code = not in_code
            in_ul = False; in_ol = False
            continue
        if in_code:
            html += line + '\n'
            continue
        if not line.strip():
            if in_ul: html += '</ul>'; in_ul = False
            if in_ol: html += '</ol>'; in_ol = False
            html += '<br>'
            continue
        m = re.match(r'^(#{1,6})\s+(.+)$', line)
        if m:
            if in_ul: html += '</ul>'; in_ul = False
            if in_ol: html += '</ol>'; in_ol = False
            lvl = len(m.group(1))
            html += f'<h{lvl}>{m.group(2)}</h{lvl}>'
            continue
        m = re.match(r'^[-*]\s+(.+)$', line)
        if m:
            if in_ol: html += '</ol>'; in_ol = False
            if not in_ul: html += '<ul>'; in_ul = True
            html += f'<li>{m.group(1)}</li>'
            continue
        m = re.match(r'^\d+\.\s+(.+)$', line)
        if m:
            if in_ul: html += '</ul>'; in_ul = False
            if not in_ol: html += '<ol>'; in_ol = True
            html += f'<li>{m.group(1)}</li>'
            continue
        m = re.match(r'^>\s+(.+)$', line)
        if m:
            if in_ul: html += '</ul>'; in_ul = False
            if in_ol: html += '</ol>'; in_ol = False
            html += f'<blockquote>{m.group(1)}</blockquote>'
            continue
        if in_ul: html += '</ul>'; in_ul = False
        if in_ol: html += '</ol>'; in_ol = False
        html += f'<p>{line}</p>'
    if in_ul: html += '</ul>'
    if in_ol: html += '</ol>'
    if in_code: html += '</code></pre>'
    return jsonify({'result': html})

@app.route('/api/cockpit/oneil-deep', methods=['GET', 'OPTIONS'])
def api_cockpit_oneil_deep():
    """欧奈尔深度分析 — 调用 DeepSeek CLI 实时生成"""
    if request.method == 'OPTIONS':
        return '', 204
    code = request.args.get('code', '')
    if not code:
        return jsonify({'error': 'code required'}), 400

    db = get_db()
    from cockpit.oneil_deep import ONeilDeepAnalyzer
    from cockpit.briefing import BriefingEngine

    analyzer = ONeilDeepAnalyzer(db)
    engine = BriefingEngine(db)

    # 收集股票数据
    name_row = db.execute("SELECT name FROM stock_basic WHERE stock_code=?", (code,)).fetchone()
    stock_name = name_row['name'] if name_row else code

    # 获取候选数据（从 cockpit_daily 或构建基础候选）
    candidate = {'stock_code': code, 'stock_name': stock_name}
    row = db.execute(
        "SELECT * FROM cockpit_daily WHERE stock_code=? ORDER BY run_date DESC LIMIT 1",
        (code,)
    ).fetchone()
    if row:
        candidate.update(dict(row))

    # 收集股票全维度数据
    stock_info = analyzer._build_stock_profile(code, candidate)
    market_info = engine._module_market()
    prompt = analyzer._build_prompt(code, stock_info, market_info)

    # 构建完整 prompt（含技能框架）
    skill = analyzer._load_skill()
    full_prompt = f"""你是欧奈尔交易顾问，严格遵循《像欧奈尔信徒一样交易》的框架。
请根据以下股票全维度数据，写一篇连贯的深度分析文章。
叙事风格，像一位严师在跟你对话，引用欧奈尔名言。
最后给出明确的综合结论（推荐买入/谨慎买入/观望/不建议）。

{skill[:2000]}

---

{prompt}

---
请用HTML格式输出（h3标题、p段落、blockquote引用名言）。"""

    # 调用 DeepSeek CLI
    import subprocess, os
    DEEPSEEK_EXE = r'D:\dstui\codewhale-tui-windows-x64.exe'

    try:
        result = subprocess.run(
            [DEEPSEEK_EXE, 'exec', full_prompt],
            capture_output=True, text=True, timeout=180, encoding='utf-8', errors='replace',
            cwd=r'D:\dstui', shell=False
        )
        output = result.stdout.strip() or result.stderr.strip()
        if not output:
            output = '<p>AI 未返回内容，请稍后重试。</p>'
    except subprocess.TimeoutExpired:
        output = '<p style="color:#F44336">AI 分析超时（180秒），请稍后重试。</p>'
    except Exception as e:
        output = f'<p style="color:#F44336">调用失败：{str(e)}</p>'

    # 缓存到文件
    run_date = datetime.now().strftime('%Y-%m-%d')
    report_dir = os.path.join(PROJECT_DIR, 'data', 'cockpit', 'oneil', run_date)
    os.makedirs(report_dir, exist_ok=True)
    html_content = analyzer._text_to_html(code, stock_info, output, run_date)
    with open(os.path.join(report_dir, f'{code}.html'), 'w', encoding='utf-8') as f:
        f.write(html_content)

    return jsonify({
        'code': code,
        'stock_name': stock_name,
        'html': output,
        'cached': f'data/cockpit/oneil/{run_date}/{code}.html'
    })


@app.route('/api/cockpit/oneil-report', methods=['GET', 'OPTIONS'])
def api_cockpit_oneil_report():
    """获取欧奈尔深度分析 HTML 报告"""
    if request.method == 'OPTIONS':
        return '', 204
    code = request.args.get('code', '')
    date = request.args.get('date', '')
    if not code or not date:
        return jsonify({'error': 'code and date required'}), 400

    report_path = os.path.join(PROJECT_DIR, 'data', 'cockpit', 'oneil', date, f'{code}.html')
    if not os.path.exists(report_path):
        return jsonify({'error': 'not found'}), 404

    with open(report_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return content, 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route('/api/discipline/trades/adjust-cost', methods=['POST', 'OPTIONS'])
def api_trade_adjust_cost():
    """调整未平仓交易的成本价（buy_price），写审计日志"""
    if request.method == 'OPTIONS':
        return '', 204
    body = request.get_json(silent=True) or {}
    trade_id = body.get('trade_id')
    new_cost = body.get('new_cost')
    reason = (body.get('reason') or '').strip()
    if not trade_id or not new_cost or float(new_cost) <= 0:
        return jsonify({'error': 'trade_id 与合法 new_cost 必填'}), 400
    new_cost = float(new_cost)
    db = get_db()
    row = db.execute("SELECT buy_price, sell_date FROM discipline_trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        return jsonify({'error': '交易记录不存在'}), 404
    if row['sell_date']:
        return jsonify({'error': '已平仓交易不可调整成本'}), 400
    old = row['buy_price']
    db.execute("UPDATE discipline_trades SET buy_price=? WHERE id=?", (new_cost, trade_id))
    db.execute("""INSERT INTO discipline_trade_adjustments(trade_id, old_cost, new_cost, reason, adjusted_at)
        VALUES(?,?,?,?,?)""", (trade_id, old, new_cost, reason or '(无理由)',
                                 datetime.now().strftime('%Y-%m-%d %H:%M')))
    db.commit()
    return jsonify({'ok': True, 'trade_id': trade_id, 'old_cost': old, 'new_cost': new_cost})


@app.route('/api/discipline/trade-stats', methods=['GET', 'OPTIONS'])
def api_trade_stats():
    """交易记录统计（心理关数据源）"""
    if request.method == 'OPTIONS':
        return '', 204
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM discipline_trades ORDER BY buy_date DESC LIMIT 20"
        ).fetchall()
        trades = [dict(r) for r in rows]

        # 计算胜率
        closed = [t for t in trades if t.get('sell_price') is not None]
        wins = [t for t in closed if (t.get('sell_price', 0) - t.get('buy_price', 0)) > 0]
        recent_5 = closed[:5]
        recent_wins = [t for t in recent_5 if (t.get('sell_price', 0) - t.get('buy_price', 0)) > 0]

        # 连续亏损检测
        streak = 0
        for t in closed:
            if (t.get('sell_price', 0) - t.get('buy_price', 0)) < 0:
                streak += 1
            else:
                break

        return jsonify({
            'total_trades': len(trades),
            'closed_trades': len(closed),
            'win_rate_all': round(len(wins) / len(closed) * 100, 1) if closed else None,
            'win_rate_5': round(len(recent_wins) / len(recent_5) * 100, 1) if recent_5 else None,
            'consecutive_losses': streak,
            'last_trade_date': trades[0]['buy_date'] if trades else None,
        })
    except sqlite3.OperationalError as e:
        return jsonify({
            'total_trades': 0, 'closed_trades': 0,
            'win_rate_all': None, 'win_rate_5': None,
            'consecutive_losses': 0, 'last_trade_date': None,
            'note': '暂无交易记录'
        })


# ═══════════════════════════════════════════════
# 回测实验室 API
# ═══════════════════════════════════════════════

SIGNAL_BITS_LAB = {
    'MW_B1': 0, 'MW_B2': 1, 'MW_PLUS': 2,
    'PP_V1': 3, 'PP_V2': 4, 'BO_V2': 5,
}

# 各信号的因子列映射
SIGNAL_FACTORS = {
    'MW_B1':  {'rs_col': 'mw_b1_h_rs250',     'vr_col': 'mw_b1_vol_ratio', 'ind_rs_col': None},
    'MW_B2':  {'rs_col': None,                 'vr_col': None,              'ind_rs_col': None},
    'MW_PLUS':{'rs_col': None,                 'vr_col': None,              'ind_rs_col': None},
    'PP_V1':  {'rs_col': 'pp_v1_rps_250',     'vr_col': 'pp_v1_vol_ratio', 'ind_rs_col': None},
    'PP_V2':  {'rs_col': 'pp_v2_rps_250',     'vr_col': 'pp_v2_vol_ratio', 'ind_rs_col': None},
    'BO_V2':  {'rs_col': None,                 'vr_col': 'bo_v2_vol_ratio', 'ind_rs_col': 'bo_v2_ind_rs250'},
}

@app.route('/api/backtest-lab/query', methods=['POST', 'OPTIONS'])
def api_backtest_lab_query():
    if request.method == 'OPTIONS':
        return '', 204
    
    params = request.get_json() or {}
    signals = params.get('signals', ['PP_V2'])
    start_date = params.get('start_date', '2024-01-01')
    end_date = params.get('end_date', '2026-06-22')
    entry_method = params.get('entry_method', 'T+1_O')
    hold_days = int(params.get('hold_days', 10))
    market_regime = params.get('market_regime', 'all')
    filters = params.get('filters', {})
    
    db = get_db()
    
    # ── 计算信号mask ──
    mask = 0
    for s in signals:
        if s in SIGNAL_BITS_LAB:
            mask |= (1 << SIGNAL_BITS_LAB[s])
    
    # ── 构建质量过滤条件 ──
    filter_clauses = []
    filter_params = []
    
    rs_min = filters.get('rs_min')
    rs_max = filters.get('rs_max')
    vr_min = filters.get('vol_ratio_min')
    vr_max = filters.get('vol_ratio_max')
    ind_rs_min = filters.get('ind_rs_min')
    ind_rs_max = filters.get('ind_rs_max')
    turnover_min = filters.get('turnover_min')
    
    for s in signals:
        if s not in SIGNAL_BITS_LAB:
            continue
        bit = 1 << SIGNAL_BITS_LAB[s]
        fac = SIGNAL_FACTORS.get(s, {})
        
        conditions = []
        if rs_min is not None and fac.get('rs_col'):
            conditions.append(f"(e.{fac['rs_col']} IS NULL OR e.{fac['rs_col']} >= ?)")
            filter_params.append(rs_min)
        if rs_max is not None and fac.get('rs_col'):
            conditions.append(f"(e.{fac['rs_col']} IS NULL OR e.{fac['rs_col']} <= ?)")
            filter_params.append(rs_max)
        if vr_min is not None and fac.get('vr_col'):
            conditions.append(f"(e.{fac['vr_col']} IS NULL OR e.{fac['vr_col']} >= ?)")
            filter_params.append(vr_min)
        if vr_max is not None and fac.get('vr_col'):
            conditions.append(f"(e.{fac['vr_col']} IS NULL OR e.{fac['vr_col']} <= ?)")
            filter_params.append(vr_max)
        if ind_rs_min is not None and fac.get('ind_rs_col'):
            conditions.append(f"(e.{fac['ind_rs_col']} IS NULL OR e.{fac['ind_rs_col']} >= ?)")
            filter_params.append(ind_rs_min)
        if ind_rs_max is not None and fac.get('ind_rs_col'):
            conditions.append(f"(e.{fac['ind_rs_col']} IS NULL OR e.{fac['ind_rs_col']} <= ?)")
            filter_params.append(ind_rs_max)
        
        if conditions:
            filter_clauses.append(
                f"((br.signal_mask & {bit}) = 0 OR ((br.signal_mask & {bit}) > 0 AND {' AND '.join(conditions)}))"
            )
    
    # 成交额过滤
    if turnover_min is not None:
        filter_clauses.append(
            f"""(
                NOT EXISTS (
                    SELECT 1 FROM daily_kline dk 
                    WHERE dk.stock_code=br.stock_code AND dk.date=br.signal_date
                )
                OR EXISTS (
                    SELECT 1 FROM daily_kline dk 
                    WHERE dk.stock_code=br.stock_code AND dk.date=br.signal_date
                      AND dk.amount >= ?
                )
            )"""
        )
        filter_params.append(turnover_min)
    
    filter_sql = ' AND '.join(filter_clauses) if filter_clauses else '1=1'
    
    # ── 基础WHERE ──
    base_params = [entry_method, hold_days, start_date, end_date]
    regime_sql = ''
    if market_regime != 'all':
        regime_sql = ' AND br.market_regime = ?'
        base_params.append(market_regime)
    
    # ── 查询1: 汇总 ──
    sql_summary = f"""
        SELECT COUNT(*) as samples,
               ROUND(AVG(is_win), 4) as win_rate,
               ROUND(AVG(net_ret_pct), 2) as mean_ret,
               ROUND(AVG(ret_pct), 2) as mean_gross
        FROM backtest_results br
        JOIN signal_events e ON br.stock_code=e.stock_code AND br.signal_date=e.date
        WHERE br.pool_mode='full'
          AND br.entry_method=? AND br.hold_days=?
          AND br.signal_date BETWEEN ? AND ?
          AND br.signal_mask & {mask} > 0
          {regime_sql}
          AND {filter_sql}
    """
    row = db.execute(sql_summary, base_params + filter_params).fetchone()
    
    # 计算其他指标
    sql_extra = f"""
        SELECT net_ret_pct, is_win,
               ROUND(AVG(CASE WHEN net_ret_pct>0 THEN net_ret_pct END),2) as avg_win,
               ROUND(AVG(CASE WHEN net_ret_pct<0 THEN ABS(net_ret_pct) END),2) as avg_loss
        FROM backtest_results br
        JOIN signal_events e ON br.stock_code=e.stock_code AND br.signal_date=e.date
        WHERE br.pool_mode='full'
          AND br.entry_method=? AND br.hold_days=?
          AND br.signal_date BETWEEN ? AND ?
          AND br.signal_mask & {mask} > 0
          {regime_sql}
          AND {filter_sql}
    """
    extra_row = db.execute(sql_extra, base_params + filter_params).fetchone()
    
    samples = row['samples']
    wr = row['win_rate'] or 0
    avg_win = extra_row['avg_win'] or 0.01
    avg_loss = extra_row['avg_loss'] or 0.01
    plr = avg_win / avg_loss if avg_loss > 0 else 1.0
    kelly = max(0, min(0.5, wr - (1-wr)/plr)) if plr > 0 else 0
    
    # ── 查询2: 按信号分 ──
    by_signal = []
    for s in signals:
        if s not in SIGNAL_BITS_LAB:
            continue
        bit = 1 << SIGNAL_BITS_LAB[s]
        
        # 该信号的过滤条件
        s_filter_sql = filter_sql  # 复用全局过滤
        
        sql_sig = f"""
            SELECT COUNT(*) as samples,
                   ROUND(AVG(is_win),4) as win_rate,
                   ROUND(AVG(net_ret_pct),2) as mean_ret,
                   ROUND(AVG(CASE WHEN net_ret_pct>0 THEN net_ret_pct END),2) as avg_win,
                   ROUND(AVG(CASE WHEN net_ret_pct<0 THEN ABS(net_ret_pct) END),2) as avg_loss
            FROM backtest_results br
            JOIN signal_events e ON br.stock_code=e.stock_code AND br.signal_date=e.date
            WHERE br.pool_mode='full'
              AND br.entry_method=? AND br.hold_days=?
              AND br.signal_date BETWEEN ? AND ?
              AND br.signal_mask & {bit} > 0
              {regime_sql}
              AND {s_filter_sql}
        """
        r = db.execute(sql_sig, base_params + filter_params).fetchone()
        if r['samples'] > 0:
            aw = r['avg_win'] or 0.01
            al = r['avg_loss'] or 0.01
            p = aw / al if al > 0 else 1
            k = max(0, min(0.5, r['win_rate'] - (1-r['win_rate'])/p)) if p > 0 else 0
            by_signal.append({
                'signal': s, 'samples': r['samples'],
                'win_rate': round(r['win_rate'], 4),
                'mean_ret': r['mean_ret'],
                'profit_loss_ratio': round(p, 2),
                'kelly': round(k, 4),
            })
    
    # ── 查询3: 按组合分（含共震）──
    by_combo = []
    sql_combo = f"""
        SELECT br.combo_label, br.signal_count, COUNT(*) as samples,
               ROUND(AVG(br.is_win),4) as win_rate,
               ROUND(AVG(br.net_ret_pct),2) as mean_ret
        FROM backtest_results br
        JOIN signal_events e ON br.stock_code=e.stock_code AND br.signal_date=e.date
        WHERE br.pool_mode='full'
          AND br.entry_method=? AND br.hold_days=?
          AND br.signal_date BETWEEN ? AND ?
          AND br.signal_mask & {mask} > 0
          {regime_sql}
          AND {filter_sql}
        GROUP BY br.combo_label
        ORDER BY COUNT(*) DESC
    """
    for r in db.execute(sql_combo, base_params + filter_params):
        by_combo.append({
            'combo_label': r['combo_label'],
            'signal_count': r['signal_count'],
            'samples': r['samples'],
            'win_rate': round(r['win_rate'], 4),
            'mean_ret': r['mean_ret'],
        })
    
    # ── 查询4: 月度 ──
    monthly = []
    sql_monthly = f"""
        SELECT substr(br.signal_date,1,7) as month,
               COUNT(*) as samples,
               ROUND(AVG(is_win),4) as win_rate,
               ROUND(AVG(net_ret_pct),2) as mean_ret
        FROM backtest_results br
        JOIN signal_events e ON br.stock_code=e.stock_code AND br.signal_date=e.date
        WHERE br.pool_mode='full'
          AND br.entry_method=? AND br.hold_days=?
          AND br.signal_date BETWEEN ? AND ?
          AND br.signal_mask & {mask} > 0
          {regime_sql}
          AND {filter_sql}
        GROUP BY month ORDER BY month
    """
    for r in db.execute(sql_monthly, base_params + filter_params):
        monthly.append({
            'month': r['month'], 'samples': r['samples'],
            'win_rate': round(r['win_rate'], 4), 'mean_ret': r['mean_ret'],
        })
    
    # ── 查询5: 收益分布 ──
    distribution = []
    buckets = [(-100, -15), (-15, -10), (-10, -7), (-7, -5), (-5, -3), (-3, -1),
               (-1, 1), (1, 3), (3, 5), (5, 7), (7, 10), (10, 15), (15, 100)]
    for lo, hi in buckets:
        cnt = db.execute(f"""
            SELECT COUNT(*) FROM backtest_results br
            JOIN signal_events e ON br.stock_code=e.stock_code AND br.signal_date=e.date
            WHERE br.pool_mode='full'
              AND br.entry_method=? AND br.hold_days=?
              AND br.signal_date BETWEEN ? AND ?
              AND br.signal_mask & {mask} > 0
              {regime_sql}
              AND {filter_sql}
              AND br.net_ret_pct >= ? AND br.net_ret_pct < ?
        """, base_params + filter_params + [lo, hi]).fetchone()[0]
        label = f'{lo}~{hi}%' if lo > -100 and hi < 100 else (f'<{hi}%' if lo == -100 else f'>{lo}%')
        distribution.append({'bucket': label, 'range': [lo, hi], 'count': cnt})
    
    return jsonify({
        'summary': {
            'samples': samples,
            'win_rate': round(wr, 4),
            'mean_ret': row['mean_ret'],
            'mean_gross': row['mean_gross'],
            'profit_loss_ratio': round(plr, 2),
            'kelly': round(kelly, 4),
            'params': {
                'signals': signals, 'entry_method': entry_method,
                'hold_days': hold_days, 'market_regime': market_regime,
                'start_date': start_date, 'end_date': end_date,
            }
        },
        'by_signal': by_signal,
        'by_combo': by_combo,
        'monthly': monthly,
        'distribution': distribution,
    })


# ═══════════════════════════════════════════════
# CORS
# ═══════════════════════════════════════════════

@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response


# ═══════════════════════════════════════════════
# 静态文件服务（web/ 目录下的 HTML 页面）
# ═══════════════════════════════════════════════

WEB_DIR = os.path.join(PROJECT_DIR, 'web')
from flask import send_from_directory

@app.route('/<path:subpath>')
def serve_web(subpath):
    """服务 web/ 目录下任意文件（HTML/JS/CSS/图片等）"""
    full = os.path.join(WEB_DIR, subpath)
    if os.path.isfile(full):
        return send_from_directory(WEB_DIR, subpath)
    idx = os.path.join(full, 'index.html')
    if os.path.isfile(idx):
        return send_from_directory(WEB_DIR, os.path.join(subpath, 'index.html'))
    return jsonify({'error': 'Not found', 'path': subpath}), 404


# ═══════════════════════════════════════════════
# API: 自选池日报（watchlist-report）
# ═══════════════════════════════════════════════

@app.route('/api/watchlist-report/data')
def api_watchlist_report_data():
    """日报数据（daily_update 步骤 32 落盘后读取）"""
    d = request.args.get('date', '')
    db = get_db()
    if not d:
        r = db.execute("SELECT MAX(date) d FROM watchlist_report_daily").fetchone()
        d = r['d'] if r and r['d'] else ''
    if not d:
        return jsonify({'error': 'no_report', 'date': None})
    r = db.execute("SELECT report_json, created_at FROM watchlist_report_daily WHERE date=?", (d,)).fetchone()
    if not r:
        return jsonify({'error': 'no_report', 'date': d})
    try:
        data = json.loads(r['report_json'])
    except Exception:
        return jsonify({'error': 'corrupt_report', 'date': d})
    data['created_at'] = r['created_at']
    data['date'] = d
    # 注入 last_view 供前端错过检测高亮
    lv = db.execute("SELECT value FROM watchlist_review_state WHERE key='last_view'").fetchone()
    data['last_view'] = lv['value'] if lv else None
    return jsonify(data)


@app.route('/api/watchlist-report/view', methods=['POST', 'OPTIONS'])
def api_watchlist_report_view():
    """记录用户查看日期（错过检测锚点）"""
    if request.method == 'OPTIONS':
        return '', 204
    body = request.get_json(silent=True) or {}
    d = body.get('date', '')
    if not d:
        return jsonify({'error': 'date required'}), 400
    db = get_db()
    cur = db.execute("SELECT value FROM watchlist_review_state WHERE key='last_view'").fetchone()
    if cur and cur['value'] and cur['value'] >= d:
        return jsonify({'ok': True, 'last_view': cur['value'], 'unchanged': True})  # 不倒退
    db.execute("INSERT OR REPLACE INTO watchlist_review_state(key, value) VALUES('last_view', ?)", (d,))
    db.commit()
    return jsonify({'ok': True, 'last_view': d})


@app.route('/api/watchlist-report/index')
def api_watchlist_report_index():
    """历史日报日期列表（日历选择器）"""
    db = get_db()
    rows = db.execute("SELECT date FROM watchlist_report_daily ORDER BY date").fetchall()
    return jsonify({'dates': [r['date'] for r in rows]})


# ═══════════════════════════════════════════════
# API: 商品监控（豆粕/有色）
# ═══════════════════════════════════════════════

COMMODITY_WATCH = [
    {'type': 'bean_meal', 'codes': ['159985'], 'name': '豆粕', 'note': '厄尔尼诺/天气逻辑标的（高波动，预期差观察）'},
    {'type': 'commodity', 'codes': ['510170'], 'name': '大宗商品', 'note': '阶梯箱体（13-20箱0.30-0.75→21-25箱0.73-1.25→26突破1.25新台阶）；箱体内网格+突破转趋势，当前持有窗口，跌破1.25回箱体转网格'},
    {'type': 'nonferrous', 'codes': ['512400', '930708', '000819'], 'name': '有色', 'note': 'AI基建+电网+供给约束'},
]


def _comm_metrics(db, code, target_date):
    """商品标的指标：close/20日/60日涨幅/位置/回撤/估值（510170 映射 000066）"""
    rows = db.execute("""SELECT date, close FROM index_daily_kline
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 261""", (code, target_date)).fetchall()
    if len(rows) < 21:
        return None
    out = {'date': rows[0]['date'], 'close': rows[0]['close']}
    out['chg_20'] = round((rows[0]['close'] / rows[20]['close'] - 1) * 100, 1)
    if len(rows) >= 61:
        out['chg_60'] = round((rows[0]['close'] / rows[60]['close'] - 1) * 100, 1)
    else:
        out['chg_60'] = None
    seg = rows[:250]
    lo, hi = min(r['close'] for r in seg), max(r['close'] for r in seg)
    out['pos_250'] = round((rows[0]['close'] - lo) / (hi - lo) * 100) if hi > lo else 50
    out['dd_250'] = round((hi - rows[0]['close']) / hi * 100, 1)
    # 估值（ETF → 跟踪指数映射）
    fund_code = {'510170': '000066'}.get(code, code)
    v = db.execute("""SELECT pe_ttm, pe_ttm_pct, pb, pb_pct, dyr, dyr_pct
        FROM index_fundamental_daily WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1""",
                   (fund_code, target_date)).fetchone()
    if v:
        out['pe'] = round(v['pe_ttm'], 1) if v['pe_ttm'] else None
        out['pe_pct'] = round(v['pe_ttm_pct'] * 100) if v['pe_ttm_pct'] is not None else None
        out['pb'] = round(v['pb'], 2) if v['pb'] else None
        out['pb_pct'] = round(v['pb_pct'] * 100) if v['pb_pct'] is not None else None
        out['dyr'] = round(v['dyr'] * 100, 2) if v['dyr'] else None
        out['dyr_pct'] = round(v['dyr_pct'] * 100) if v['dyr_pct'] is not None else None
    return out


@app.route('/api/market-scan/commodity')
def api_market_commodity():
    """商品监控（豆粕/有色）：价格/涨幅/位置/回撤 + 简版建议"""
    target_date = request.args.get('date', '')
    db = get_db()
    if not target_date:
        r = db.execute("SELECT MAX(date) FROM index_daily_kline").fetchone()
        target_date = r[0]
    result = []
    for item in COMMODITY_WATCH:
        entry = {'type': item['type'], 'name': item['name'], 'note': item['note'], 'targets': []}
        for code in item['codes']:
            m = _comm_metrics(db, code, target_date)
            if not m:
                continue
            name = code
            try:
                names = load_index_names()
                name = names.get(code, code)
            except Exception:
                pass
            entry['targets'].append({'code': code, 'name': name, **m})
        # 简版建议（按主标的）
        t = entry['targets'][0] if entry['targets'] else None
        if t:
            if item['type'] == 'bean_meal':
                if t['pos_250'] < 30 and t['dd_250'] > 15:
                    entry['level'], entry['advice'] = 'buy', f"布局窗口：位置 {t['pos_250']}% 低位 + 回撤 {t['dd_250']}%（厄尔尼诺预期，注意高波动）"
                elif t['pos_250'] > 85:
                    entry['level'], entry['advice'] = 'wait', f"高位 {t['pos_250']}%，天气行情兑现后勿追"
                else:
                    entry['level'], entry['advice'] = 'hold', f"观望（位置 {t['pos_250']}%，豆粕 20日 {t['chg_20']}% 尚未启动）"
            elif item['type'] == 'commodity':
                # 510170 大宗商品：回撤买点(15%胜率61%) + 估值分位(PB<20%胜率80%) + 阶梯箱体
                pb_pct = t.get('pb_pct')
                pe_pct = t.get('pe_pct')
                dyr_pct = t.get('dyr_pct')
                dd = t.get('dd_250')
                if pb_pct is not None and pb_pct > 90:
                    entry['level'], entry['advice'] = 'wait', f"估值高位警示：PB {t.get('pb')} 分位 {pb_pct}% >90%（回测：PB分位<20%买入80%胜率）——不追高，等回撤≥15%或PB分位回落"
                elif dd is not None and dd >= 15 and (pb_pct is None or pb_pct < 60):
                    entry['level'], entry['advice'] = 'buy', f"回撤买点：250日回撤 {dd}% ≥15%（回测 60日61%/120日63%）——买"
                elif pb_pct is not None and pb_pct < 20:
                    entry['level'], entry['advice'] = 'buy', f"估值买点：PB 分位 {pb_pct}% <20%（回测 60日80%）——买"
                elif dyr_pct is not None and dyr_pct > 90:
                    entry['level'], entry['advice'] = 'buy', f"股息买点：股息率分位 {dyr_pct}% >90%（回测 64%）——买"
                elif t['pos_250'] > 85:
                    entry['level'], entry['advice'] = 'wait', f"高位 {t['pos_250']}%——突破后持有窗口，勿追高"
                else:
                    entry['level'], entry['advice'] = 'hold', f"持有/观望（回撤 {dd}% / PB分位 {pb_pct}%——未到买点）"
            else:
                if t['chg_20'] > 10 and t['pos_250'] < 70:
                    entry['level'], entry['advice'] = 'buy', f"趋势启动：20日 {t['chg_20']}% + 位置 {t['pos_250']}% 未过热（AI基建+供给约束逻辑）"
                elif t['pos_250'] > 85:
                    entry['level'], entry['advice'] = 'wait', f"高位 {t['pos_250']}%，不追"
                else:
                    entry['level'], entry['advice'] = 'hold', f"观望（20日 {t['chg_20']}% / 位置 {t['pos_250']}%）"
        result.append(entry)
    return jsonify({'date': target_date, 'items': result})


@app.route('/api/market-scan/commodity-detail')
def api_market_commodity_detail():
    """商品标的详情：价格/回撤/估值分位全景（510170 → 估值映射 000066）"""
    code = request.args.get('code', '510170')
    target_date = request.args.get('date', '')
    fund_code = {'510170': '000066'}.get(code, code)
    db = get_db()
    if not target_date:
        r = db.execute("SELECT MAX(date) FROM index_daily_kline").fetchone()
        target_date = r[0]

    # 3 年价格（后复权）
    rows = db.execute("""SELECT date, close FROM index_daily_kline
        WHERE stock_code=? AND date>=date(?,'-3 years') AND date<=? ORDER BY date""",
                      (code, target_date, target_date)).fetchall()
    dates = [r['date'] for r in rows]
    closes = [r['close'] for r in rows]

    # 250 日回撤序列
    dd_series = []
    dd_buy_events = []
    for i in range(len(closes)):
        win = closes[max(0, i - 249):i + 1]
        hi = max(win)
        dd_series.append(round((hi - closes[i]) / hi * 100, 1))
        if i >= 250 and dd_series[i] >= 15 and dd_series[i - 1] < 15:
            dd_buy_events.append({'date': dates[i], 'dd': dd_series[i], 'price': round(closes[i], 3)})

    # 估值（000066）：3 年分位 + 实际值
    val_rows = db.execute("""SELECT date, pe_ttm, pe_ttm_pct, pb, pb_pct, dyr, dyr_pct
        FROM index_fundamental_daily WHERE stock_code=?
        AND date>=date(?,'-3 years') AND date<=? ORDER BY date""",
                          (fund_code, target_date, target_date)).fetchall()
    vmap = {r['date']: r for r in val_rows}
    pe_s = [round(vmap[d]['pe_ttm_pct'] * 100) if d in vmap and vmap[d]['pe_ttm_pct'] is not None else None for d in dates]
    pb_s = [round(vmap[d]['pb_pct'] * 100) if d in vmap and vmap[d]['pb_pct'] is not None else None for d in dates]
    dyr_s = [round(vmap[d]['dyr_pct'] * 100) if d in vmap and vmap[d]['dyr_pct'] is not None else None for d in dates]
    pe_v = [round(vmap[d]['pe_ttm'], 1) if d in vmap and vmap[d]['pe_ttm'] else None for d in dates]
    pb_v = [round(vmap[d]['pb'], 2) if d in vmap and vmap[d]['pb'] else None for d in dates]
    dyr_v = [round(vmap[d]['dyr'] * 100, 2) if d in vmap and vmap[d]['dyr'] else None for d in dates]

    cur = vmap.get(dates[-1]) if dates else None
    cur_dd = dd_series[-1] if dd_series else None
    return jsonify({
        'code': code, 'name': '大宗商品ETF', 'index': '上证大宗商品(000066)', 'date': dates[-1] if dates else target_date,
        'dates': dates, 'closes': closes, 'dd_series': dd_series, 'dd_buy_events': dd_buy_events,
        'pe_series': pe_s, 'pb_series': pb_s, 'dyr_series': dyr_s,
        'pe_values': pe_v, 'pb_values': pb_v, 'dyr_values': dyr_v,
        'current': {'close': closes[-1] if closes else None, 'dd_250': cur_dd,
                    'pe': round(cur['pe_ttm'], 1) if cur and cur['pe_ttm'] else None,
                    'pe_pct': round(cur['pe_ttm_pct'] * 100) if cur and cur['pe_ttm_pct'] is not None else None,
                    'pb': round(cur['pb'], 2) if cur and cur['pb'] else None,
                    'pb_pct': round(cur['pb_pct'] * 100) if cur and cur['pb_pct'] is not None else None,
                    'dyr': round(cur['dyr'] * 100, 2) if cur and cur['dyr'] else None,
                    'dyr_pct': round(cur['dyr_pct'] * 100) if cur and cur['dyr_pct'] is not None else None},
        'rules': {
            'buy_dd': '回撤≥15%（60日61%）且PB分位<60%',
            'buy_pb': 'PB分位<20%（80%胜率）',
            'buy_dyr': '股息率分位>90%（64%）',
            'sell_pb': 'PB分位>90%（高位警示，当前97%）',
            'box': '阶梯箱体：箱内网格/突破转趋势，当前台阶1.23-1.65，关键位1.25',
        },
    })


if __name__ == '__main__':
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    init_schema()
    print("🦊 O'Neil Backtest API Server starting on http://localhost:8788")
    print(f"   Config dir: {CONFIG_DIR}")
    print(f"   Detectors: distribution_day, follow_through_day, accumulation, index_rs")
    app.run(host='0.0.0.0', port=8788, debug=False)