"""
投资决策驾驶舱 - 硬过滤管道引擎

五级管道：
  1. 观察池过滤 → 2. 市值门槛 → 3. 行业RS → 4. 个股RS(H点) → 5. 形态信号

用法：
    python -m src.cockpit.pipeline --date 2026-06-09
    python -m src.cockpit.pipeline --date 2026-06-09 --save  # 持久化到数据库
"""
import os
import sys
import sqlite3
import argparse
import json
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')

# ── 默认配置 ──
DEFAULT_CONFIG = {
    'pipeline': {
        'market_cap_min': 50,
        'industry_rs250_min': 75,
        'stock_rs250_h_min': 80,
        'signal_lookback_days': 5,
        'max_candidates': 5,
    },
    'position': {
        'max_loss_pct': 0.02,
        'kelly_fraction': 0.25,
        'account_size': 1000000,
    }
}


def load_config():
    """加载 cockpit.yaml 配置"""
    import yaml
    config_path = os.path.join(PROJECT_ROOT, 'config', 'cockpit.yaml')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return DEFAULT_CONFIG


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_cockpit_table(db):
    """创建 cockpit_daily 表"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS cockpit_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            rank INTEGER,
            signal_types TEXT,
            signal_date TEXT,
            confidence TEXT,
            h_date TEXT, h_price REAL,
            l_date TEXT, l_price REAL,
            decline_pct REAL,
            consolidation_days INTEGER,
            hist_win_rate REAL,
            hist_median_5d REAL,
            hist_median_10d REAL,
            hist_median_20d REAL,
            canslim_total INTEGER,
            canslim_c INTEGER, canslim_a INTEGER, canslim_n INTEGER,
            canslim_s INTEGER, canslim_l INTEGER, canslim_i INTEGER, canslim_m INTEGER,
            market_cap REAL,
            profit_trend TEXT,
            l1_industry TEXT,
            l1_rs250 INTEGER, l1_rs20 INTEGER, l1_pct_5d REAL,
            theme_indices TEXT,
            market_light TEXT,
            ftd_confirmed INTEGER,
            distribution_days INTEGER,
            crowding REAL,
            nhnl_diff INTEGER,
            suggested_position_pct REAL,
            max_loss_amount REAL,
            entry_price_ref REAL,
            stop_loss_price REAL,
            kelly_position_pct REAL,
            target_price REAL,
            sentiment_summary TEXT,
            oneil_analysis TEXT,
            stop_loss_rule TEXT,
            trailing_stop_rule TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(run_date, stock_code)
        )
    """)
    db.commit()


# ════════════════════════════════════════════════════════
# 第一级：观察池过滤
# ════════════════════════════════════════════════════════

def filter_observation_pool(db, target_date):
    """从 discipline_observation_pool 获取最新股票列表"""
    rows = db.execute("""
        SELECT stock_code, stock_name, industry_name, rs_category,
               rps_20, rps_60, rps_120, rps_250,
               canslim_total, canslim_c, canslim_a, canslim_n,
               canslim_s, canslim_l, canslim_i, canslim_m,
               roe, eps_yoy, revenue_yoy
        FROM discipline_observation_pool
        WHERE date = (SELECT MAX(date) FROM discipline_observation_pool WHERE date <= ?)
    """, (target_date,)).fetchall()

    return {r['stock_code']: dict(r) for r in rows}


# ════════════════════════════════════════════════════════
# 第二级：市值门槛
# ════════════════════════════════════════════════════════

def filter_market_cap(db, stock_codes, min_cap_yi=50):
    """
    筛选市值 ≥ min_cap_yi 亿的股票。
    使用 market_cap_snapshot 快照表（秒级查询）。
    快照表为空时全部通过（兜底）。
    """
    if not stock_codes:
        return set()

    # 检查快照表是否存在
    try:
        cnt = db.execute("SELECT COUNT(*) as c FROM market_cap_snapshot").fetchone()
        if not cnt or cnt['c'] == 0:
            return set(stock_codes)  # 快照表为空，全部通过
    except sqlite3.OperationalError:
        return set(stock_codes)  # 表不存在，全部通过

    placeholders = ','.join(['?'] * len(stock_codes))
    rows = db.execute(f"""
        SELECT stock_code FROM market_cap_snapshot
        WHERE stock_code IN ({placeholders})
          AND (market_cap >= ? OR market_cap IS NULL)
    """, list(stock_codes) + [min_cap_yi]).fetchall()

    return {r['stock_code'] for r in rows}


# ════════════════════════════════════════════════════════
# 第三级：行业RS强度
# ════════════════════════════════════════════════════════

def filter_industry_rs(db, stock_codes, pool_data, min_rs=75):
    """
    筛选所属行业 RS250 ≥ min_rs 的股票。
    V1 策略：优先使用 MW 引擎已计算的 ind_rs250（mw_signal_daily 表），
    兜底从 index_rs_daily 查询。无行业数据的股票默认通过。
    """
    if not stock_codes:
        return set()

    passed = set()
    placeholders = ','.join(['?'] * len(stock_codes))
    params = list(stock_codes)

    # 优先从 mw_signal_daily 获取已计算的行业RS
    mw_rows = db.execute(f"""
        SELECT DISTINCT stock_code, MAX(ind_rs250) as ind_rs250
        FROM mw_signal_daily
        WHERE stock_code IN ({placeholders})
          AND ind_rs250 IS NOT NULL
        GROUP BY stock_code
    """, params).fetchall()

    mw_rs = {r['stock_code']: r['ind_rs250'] for r in mw_rows}

    for code in stock_codes:
        rs = mw_rs.get(code)
        if rs is not None and rs >= min_rs:
            passed.add(code)
        elif rs is None:
            # 无MW数据时默认通过（行业未知无法判断，降权交给软排序）
            passed.add(code)

    return passed


# ════════════════════════════════════════════════════════
# 第四级：个股RS强度（H点时点）
# ════════════════════════════════════════════════════════

def filter_stock_rs(db, stock_codes, min_rs=80):
    """筛选前高时点 RS250 ≥ min_rs 的股票"""
    if not stock_codes:
        return set()

    passed = set()
    results = {}  # code -> h_rs250

    # 从 MW 信号取 H 点 RS
    placeholders = ','.join(['?'] * len(stock_codes))
    mw_rows = db.execute(f"""
        SELECT DISTINCT stock_code, h_rs250
        FROM mw_signal_daily
        WHERE stock_code IN ({placeholders})
          AND h_rs250 IS NOT NULL
        ORDER BY b2_date DESC
    """, list(stock_codes)).fetchall()

    mw_rs = {}
    for r in mw_rows:
        if r['stock_code'] not in mw_rs:
            mw_rs[r['stock_code']] = r['h_rs250']

    # 从 stock_rs_daily 取最近 60 天内最高 RS250（兜底）
    rs_rows = db.execute(f"""
        SELECT stock_code, MAX(rps_250) as max_rs250
        FROM stock_rs_daily
        WHERE stock_code IN ({placeholders})
          AND date >= date('now', '-60 days')
        GROUP BY stock_code
    """, list(stock_codes)).fetchall()

    rs_60d = {r['stock_code']: r['max_rs250'] for r in rs_rows if r['max_rs250']}

    for code in stock_codes:
        h_rs = mw_rs.get(code) or rs_60d.get(code, 0)
        results[code] = h_rs
        if h_rs >= min_rs:
            passed.add(code)

    return passed, results


# ════════════════════════════════════════════════════════
# 第五级：形态信号
# ════════════════════════════════════════════════════════

def filter_signals(db, stock_codes, lookback_days=5, max_candidates=5):
    """筛选最近 N 天内有买入信号的股票，按优先级排序"""
    if not stock_codes:
        return []

    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
    placeholders = ','.join(['?'] * len(stock_codes))
    params = list(stock_codes)

    candidates = {}  # code -> {signals, priority, details}

    # MW PLUS 信号（最高优先级）
    mw_rows = db.execute(f"""
        SELECT stock_code, stock_name, b2_date, score, score_v2, is_plus,
               h_date, h_price, l_date, l_price, decline_pct,
               c_start, c_end, h_rs250, ind_name, ind_rs250,
               confidence, confidence_v2, b2_return_pct, b2_is_gap
        FROM mw_signal_daily
        WHERE stock_code IN ({placeholders})
          AND b2_date >= ?
        ORDER BY score_v2 DESC
    """, params + [cutoff]).fetchall()

    for r in mw_rows:
        code = r['stock_code']
        priority = 1 if r['is_plus'] else 4
        signal_type = 'mw_plus' if r['is_plus'] else 'mw_b2'
        if code not in candidates or priority < candidates[code]['priority']:
            candidates[code] = {
                'stock_code': code,
                'stock_name': r['stock_name'],
                'signals': [signal_type],
                'priority': priority,
                'signal_date': r['b2_date'],
                'confidence': r['confidence_v2'] or r['confidence'],
                'h_date': r['h_date'], 'h_price': r['h_price'],
                'l_date': r['l_date'], 'l_price': r['l_price'],
                'decline_pct': r['decline_pct'],
                'h_rs250': r['h_rs250'],
                'ind_name': r['ind_name'],
                'ind_rs250': r['ind_rs250'],
                'is_plus': r['is_plus'],
                'mw_score': r['score_v2'] or r['score'],
                'b2_is_gap': r['b2_is_gap'],
                'c_start': r['c_start'], 'c_end': r['c_end'],
            }

    # 口袋支点信号
    pp_rows = db.execute(f"""
        SELECT stock_code, stock_name, date, pivot_type, b1_overlap,
               h_date, l_date, c_days, gain_pct, vol_ratio,
               close_position, rps_20, rps_250, base_depth, close, volume
        FROM pocket_pivot_daily
        WHERE stock_code IN ({placeholders})
          AND date >= ?
        ORDER BY date DESC
    """, params + [cutoff]).fetchall()

    for r in pp_rows:
        code = r['stock_code']
        priority = 2 if r['b1_overlap'] else 5
        signal_type = 'pocket_pivot_b1' if r['b1_overlap'] else 'pocket_pivot'
        if code not in candidates:
            candidates[code] = {
                'stock_code': code, 'stock_name': r['stock_name'],
                'signals': [signal_type], 'priority': priority,
                'signal_date': r['date'],
                'confidence': '高' if r['b1_overlap'] else '中',
                'h_date': r['h_date'], 'h_price': None,
                'l_date': r['l_date'], 'l_price': None,
                'decline_pct': r['base_depth'],
                'h_rs250': r['rps_250'],
                'pp_type': r['pivot_type'],
                'pp_b1_overlap': r['b1_overlap'],
                'pp_gain_pct': r['gain_pct'],
                'pp_vol_ratio': r['vol_ratio'],
                'consolidation_days': r['c_days'],
            }
        else:
            # 已有MW信号，追加口袋支点
            if signal_type not in candidates[code]['signals']:
                candidates[code]['signals'].append(signal_type)
                if r['b1_overlap']:
                    candidates[code]['priority'] = min(candidates[code]['priority'], 2)

    # 基部突破V2信号
    try:
        bo_rows = db.execute(f"""
            SELECT stock_code, date, close, change_pct, volume, amount
            FROM market_breakout_daily
            WHERE stock_code IN ({placeholders})
              AND date >= ?
            ORDER BY date DESC
        """, params + [cutoff]).fetchall()

        for r in bo_rows:
            code = r['stock_code']
            signal_type = 'base_breakout'
            if code not in candidates:
                candidates[code] = {
                    'stock_code': code, 'stock_name': '',
                    'signals': [signal_type], 'priority': 3,
                    'signal_date': r['date'],
                    'confidence': '中',
                }
            else:
                if signal_type not in candidates[code]['signals']:
                    candidates[code]['signals'].append(signal_type)
                candidates[code]['priority'] = min(candidates[code]['priority'], 3)
    except sqlite3.OperationalError:
        pass  # 表不存在时跳过

    # 按优先级排序，取前 N
    sorted_candidates = sorted(candidates.values(), key=lambda x: (x['priority'], -(x.get('mw_score') or 0)))
    return sorted_candidates[:max_candidates]


def filter_b1_signals(db, stock_codes, lookback_days=5, max_candidates=5):
    """筛选最近 N 天内有 B1 信号的股票"""
    if not stock_codes:
        return []

    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
    placeholders = ','.join(['?'] * len(stock_codes))
    params = list(stock_codes)

    rows = db.execute(f"""
        SELECT stock_code, stock_name, b1_date, b2_date, score, score_v2, is_plus,
               h_date, h_price, l_date, l_price, decline_pct, h_rs250, ind_name, ind_rs250
        FROM mw_signal_daily
        WHERE stock_code IN ({placeholders})
          AND b1_date >= ?
        ORDER BY b1_date DESC
    """, params + [cutoff]).fetchall()

    candidates = []
    seen = set()
    for r in rows:
        code = r['stock_code']
        if code in seen: continue
        seen.add(code)
        candidates.append({
            'stock_code': code, 'stock_name': r['stock_name'],
            'signal_type': 'b1', 'signal_date': r['b1_date'],
            'b2_date': r['b2_date'], 'has_b2': bool(r['b2_date']),
            'mw_score': r['score_v2'] or r['score'],
            'is_plus': r['is_plus'],
            'h_date': r['h_date'], 'h_price': r['h_price'],
            'l_date': r['l_date'], 'l_price': r['l_price'],
            'decline_pct': r['decline_pct'], 'h_rs250': r['h_rs250'],
            'ind_name': r['ind_name'], 'ind_rs250': r['ind_rs250'],
        })
        if len(candidates) >= max_candidates: break

    return candidates


# ════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════

def run_pipeline(target_date=None, config=None, db=None, save=False):
    """
    执行完整五级管道。
    返回: list of candidate dicts
    """
    if target_date is None:
        target_date = datetime.now().strftime('%Y-%m-%d')
    if config is None:
        config = load_config()
    if db is None:
        db = get_db()
        own_db = True
    else:
        own_db = False

    cfg = config.get('pipeline', DEFAULT_CONFIG['pipeline'])

    # 确保表存在
    init_cockpit_table(db)

    stats = {}

    # 第一级：观察池
    pool = filter_observation_pool(db, target_date)
    stats['level1_pool'] = len(pool)
    current = set(pool.keys())

    # 第二级：市值
    current = filter_market_cap(db, current, cfg.get('market_cap_min', 50))
    stats['level2_market_cap'] = len(current)

    # 第三级：行业RS
    current = filter_industry_rs(db, current, pool, cfg.get('industry_rs250_min', 75))
    stats['level3_industry_rs'] = len(current)

    # 第四级：个股RS（H点）
    current, rs_results = filter_stock_rs(db, current, cfg.get('stock_rs250_h_min', 80))
    stats['level4_stock_rs'] = len(current)

    # 第五级：形态信号（B2）
    candidates_b2 = filter_signals(db, current, cfg.get('signal_lookback_days', 5), cfg.get('max_candidates', 5))
    stats['level5_b2_signals'] = len(candidates_b2)

    # B1 信号（独立筛选，不做置信度评分）
    candidates_b1 = filter_b1_signals(db, current, cfg.get('signal_lookback_days', 5), cfg.get('max_candidates', 5))
    stats['level5_b1_signals'] = len(candidates_b1)

    # 补充基本信息（B2 + B1 合并，B1 优先排序）
    all_candidates = []
    for i, c in enumerate(candidates_b1):
        info = dict(c)
        info['tab'] = 'b1'
        info['rank'] = i + 1
        all_candidates.append(info)
    for i, c in enumerate(candidates_b2):
        info = dict(c)
        info['tab'] = 'b2'
        info['rank'] = len(candidates_b1) + i + 1
        # 避免 B1 已覆盖的重复
        if info['stock_code'] not in {x['stock_code'] for x in all_candidates}:
            all_candidates.append(info)

    enriched = []
    for info in all_candidates:
        code = info['stock_code']

        # 从观察池补充基本面
        if code in pool:
            p = pool[code]
            info['stock_name'] = info.get('stock_name') or p.get('stock_name', '')
            for k in ['canslim_total', 'canslim_c', 'canslim_a', 'canslim_n',
                       'canslim_s', 'canslim_l', 'canslim_i', 'canslim_m',
                       'roe', 'eps_yoy', 'revenue_yoy']:
                if k not in info or info[k] is None:
                    info[k] = p.get(k)

        # B1 辅助信号标签（口袋支点、基部突破等）
        if info.get('tab') == 'b1' and info.get('signal_date'):
            b1_date = info['signal_date']
            b1_signals = list(info.get('signals', []) if isinstance(info.get('signals'), list) else [])
            # 口袋支点 on/around B1
            pp_rows = db.execute("""
                SELECT date, pivot_type, b1_overlap FROM pocket_pivot_daily
                WHERE stock_code=? AND date BETWEEN date(?, '-2 days') AND date(?, '+2 days')
            """, (code, b1_date, b1_date)).fetchall()
            for pp in pp_rows:
                tag = 'pocket_pivot_b1' if pp['b1_overlap'] else 'pocket_pivot'
                if tag not in b1_signals: b1_signals.append(tag)
            # 基部突破 on/around B1
            try:
                bo_rows = db.execute("""
                    SELECT date FROM market_breakout_daily
                    WHERE stock_code=? AND date BETWEEN date(?, '-2 days') AND date(?, '+2 days')
                """, (code, b1_date, b1_date)).fetchall()
                if bo_rows and 'base_breakout' not in b1_signals:
                    b1_signals.append('base_breakout')
            except: pass
            # MW B2 (if this B1 already has a B2)
            if info.get('has_b2') and 'mw_b2' not in b1_signals:
                b1_signals.append('mw_b2')
            # pattern-scan 全量信号（TA-Lib、K线形态等）
            try:
                ps = db.execute("""
                    SELECT signals_json FROM pattern_scan_signals
                    WHERE stock_code=? AND date=?
                """, (code, b1_date)).fetchone()
                if ps and ps['signals_json']:
                    import json
                    all_sigs = json.loads(ps['signals_json'])
                    for sig in all_sigs:
                        src = sig.get('source', '')
                        if src == 'cdl' and sig.get('type') == 'bullish':
                            name = sig.get('details', {}).get('cdl_name', '')
                            if name and name not in b1_signals:
                                b1_signals.append(name)
            except: pass
            info['signals'] = b1_signals

        # 从 pysnowball 获取市值（轻量，不阻塞）
        if not info.get('market_cap'):
            try:
                from cockpit.sentiment import SentimentEngine
                se = SentimentEngine()
                quote = se._fetch_quote(code)
                if quote and quote.get('market_cap', 0) > 0:
                    info['market_cap'] = round(quote['market_cap'] / 1e8, 0)
            except Exception:
                pass

        # 从 stock_rs_daily 获取最新 RS 值
        rs_row = db.execute(
            "SELECT rps_20, rps_60, rps_120, rps_250 FROM stock_rs_daily WHERE stock_code=? ORDER BY date DESC LIMIT 1",
            (code,)
        ).fetchone()
        if rs_row:
            info['rps_20'] = rs_row['rps_20']
            info['rps_60'] = rs_row['rps_60']
            info['rps_120'] = rs_row['rps_120']
            info['rps_250'] = rs_row['rps_250']

        enriched.append(info)

    # 持久化
    if save:
        save_candidates(db, target_date, enriched, pool_data=pool)

    if own_db:
        db.close()

    return enriched, stats


def _generate_oneil_deep(db, stock_code, stock_name, run_date, briefing_engine):
    """调用 DeepSeek CLI 预生成深度分析报告"""
    import subprocess
    from cockpit.oneil_deep import ONeilDeepAnalyzer

    analyzer = ONeilDeepAnalyzer(db)
    candidate = {'stock_code': stock_code, 'stock_name': stock_name}
    stock_info = analyzer._build_stock_profile(stock_code, candidate)
    market_info = briefing_engine._module_market()
    prompt = analyzer._build_prompt(stock_code, stock_info, market_info)

    skill = analyzer._load_skill()
    full_prompt = f"""你是欧奈尔交易顾问，严格遵循《像欧奈尔信徒一样交易》框架。
请根据以下股票全维度数据，写一篇连贯的深度分析文章（1000字以上）。
叙事风格，像一位严师在跟你对话，引用欧奈尔名言。
最后给出明确的综合结论（推荐买入/谨慎买入/观望/不建议）。

{skill[:2000]}

---

{prompt}

---
请用HTML格式输出（h3标题、p段落、blockquote引用名言）。"""

    DEEPSEEK_EXE = r'D:\dstui\deepseek-tui-windows-x64.exe'
    result = subprocess.run(
        [DEEPSEEK_EXE, '-p', full_prompt],
        capture_output=True, text=True, timeout=180, encoding='utf-8', errors='replace',
        cwd=r'D:\dstui', shell=False
    )
    output = result.stdout.strip() or result.stderr.strip()
    if not output:
        print(f"  [oneil_deep] {stock_code} CLI 无输出")
        return

    # 保存
    report_dir = os.path.join(PROJECT_ROOT, 'data', 'cockpit', 'oneil', run_date)
    os.makedirs(report_dir, exist_ok=True)
    html_content = analyzer._text_to_html(stock_code, stock_info, output, run_date)
    filepath = os.path.join(report_dir, f'{stock_code}.html')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"  [oneil_deep] {stock_code} 报告已生成 ({len(html_content)} bytes)")


def save_candidates(db, run_date, candidates, pool_data=None):
    """将候选结果写入 cockpit_daily，含简报（舆情+欧奈尔分析）"""
    from cockpit.briefing import BriefingEngine
    from cockpit.sentiment import SentimentEngine
    from cockpit.oneil_eval import ONeilEvaluator

    briefing_engine = BriefingEngine(db)
    sentiment_engine = SentimentEngine()
    oneil_evaluator = ONeilEvaluator(db)

    # 获取大盘数据（所有候选共享）
    market_data = briefing_engine._module_market()

    for c in candidates:
        code = c['stock_code']
        stock_name = c.get('stock_name', '')

        # 舆情
        sentiment_summary = ''
        try:
            sent = sentiment_engine.fetch(code, stock_name)
            sentiment_summary = sent.get('summary', '')
        except Exception:
            pass

        # 获取入场参考价（在评估前计算，供评估器使用）
        signal_date = c.get('signal_date', '')
        entry_price = None
        if signal_date:
            row = db.execute(
                "SELECT close FROM daily_kline WHERE stock_code=? AND date=?",
                (code, signal_date)
            ).fetchone()
            if row:
                entry_price = row['close']
        if not entry_price:
            row = db.execute(
                "SELECT close FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 1",
                (code,)
            ).fetchone()
            entry_price = row['close'] if row else 10.0

        c['entry_price_ref'] = entry_price

        # 欧奈尔分析
        oneil_analysis = ''
        try:
            oneil = oneil_evaluator.evaluate(code, c, market_data)
            oneil_analysis = oneil.get('summary', '')
        except Exception:
            pass

        # 深度分析（管道预生成，DeepSeek CLI）
        if c.get('rank', 99) <= 5:
            try:
                _generate_oneil_deep(db, code, c.get('stock_name',''), run_date, briefing_engine)
            except Exception as e:
                print(f"  [oneil_deep] {code} 失败: {e}")

        # 止损止盈
        from cockpit.position import calculate_stop_loss, get_trailing_stop_rule_text
        signals = c.get('signals', [])
        primary = signals[0] if signals else 'mw_b2'
        signal_type_map = {
            'mw_plus': 'mw_plus', 'mw_b2': 'mw_b2',
            'pocket_pivot_b1': 'pocket_pivot_base', 'pocket_pivot': 'pocket_pivot_base',
            'base_breakout': 'base_breakout',
        }
        st = signal_type_map.get(primary, 'mw_b2')

        stop_price, stop_rule = calculate_stop_loss(
            st, entry_price,
            l_price=c.get('l_price'),
            b2_low=None,
            signal_low=entry_price * 0.92
        )

        # 注入入场价（供评估器使用）
        c['entry_price_ref'] = entry_price
        c['stop_loss_price'] = round(stop_price, 2) if stop_price else None

        signal_types_json = json.dumps(c.get('signals', []), ensure_ascii=False)

        db.execute("""
            INSERT OR REPLACE INTO cockpit_daily (
                run_date, stock_code, stock_name, rank,
                signal_types, signal_date, confidence,
                h_date, h_price, l_date, l_price,
                decline_pct, consolidation_days,
                canslim_total, canslim_c, canslim_a, canslim_n,
                canslim_s, canslim_l, canslim_i, canslim_m,
                market_cap, profit_trend,
                mw_score,
                rps_20, rps_60, rps_120, rps_250,
                l1_industry, l1_rs250, l1_rs20, l1_pct_5d,
                sentiment_summary, oneil_analysis,
                stop_loss_price, stop_loss_rule, trailing_stop_rule,
                target_price, entry_price_ref
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_date, code, stock_name, c.get('rank'),
            signal_types_json, signal_date, c.get('confidence', ''),
            c.get('h_date'), c.get('h_price'), c.get('l_date'), c.get('l_price'),
            c.get('decline_pct'), c.get('consolidation_days'),
            c.get('canslim_total'), c.get('canslim_c'), c.get('canslim_a'), c.get('canslim_n'),
            c.get('canslim_s'), c.get('canslim_l'), c.get('canslim_i'), c.get('canslim_m'),
            c.get('market_cap'), c.get('profit_trend', ''),
            c.get('mw_score'),
            c.get('rps_20'), c.get('rps_60'), c.get('rps_120'), c.get('rps_250'),
            c.get('l1_industry', ''), c.get('l1_rs250'), c.get('l1_rs20'), c.get('l1_pct_5d'),
            sentiment_summary, oneil_analysis,
            round(stop_price, 2) if stop_price else None, stop_rule,
            get_trailing_stop_rule_text(),
            c.get('h_price'), entry_price
            ))

    db.commit()


# ════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='投资决策驾驶舱管道')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--save', action='store_true', help='保存到数据库')
    parser.add_argument('--max', type=int, default=5, help='最大候选数')
    args = parser.parse_args()

    config = load_config()
    if args.max:
        config['pipeline']['max_candidates'] = args.max

    print(f"🚀 驾驶舱管道启动 — {args.date}")
    candidates, stats = run_pipeline(args.date, config, save=args.save)

    print(f"\n📊 管道统计:")
    print(f"   观察池: {stats.get('level1_pool', 0)} 只")
    print(f"   市值≥{config['pipeline']['market_cap_min']}亿: {stats.get('level2_market_cap', 0)} 只")
    print(f"   行业RS≥{config['pipeline']['industry_rs250_min']}: {stats.get('level3_industry_rs', 0)} 只")
    print(f"   个股RS(H点)≥{config['pipeline']['stock_rs250_h_min']}: {stats.get('level4_stock_rs', 0)} 只")
    print(f"   形态信号: {stats.get('level5_signals', 0)} 只")

    print(f"\n🎯 候选股票:")
    for c in candidates:
        signals_str = ', '.join(c.get('signals', []))
        print(f"   #{c['rank']} {c['stock_code']} {c.get('stock_name','')} | {signals_str} | {c.get('signal_date','')}")
