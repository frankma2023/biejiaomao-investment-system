"""
缠论 vs 欧奈尔回测对比引擎 v2.0

每次运行对一个交易日进行三组对比评估：
  A. 全池基准：观察池全部股票买入
  B. 缠论叠加：池内 + 同日缠论买入信号
  C. O'Neil进阶：池内 + O'Neil精选（discipline_screening_daily）

用法：
  python src/scanners/chanlun_backtest_compare.py --date 2026-04-01           # 单日
  python src/scanners/chanlun_backtest_compare.py --date 2026-04-01 --filter  # 单日+过滤
  python src/scanners/chanlun_backtest_compare.py --start 2026-05-21 --end 2026-06-02 --filter  # 批量历史回填
"""

import sys, os, sqlite3, json, random
from datetime import datetime, timedelta

SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
PROJECT_ROOT = os.path.join(SRC_DIR, '..')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')
OUT_DIR = os.path.join(PROJECT_ROOT, 'web', 'chanlun-backtest-compare', 'data')
MANIFEST_PATH = os.path.join(PROJECT_ROOT, 'web', 'chanlun-backtest-compare', 'manifest.json')

HORIZONS = [5, 10, 20]
RANDOM_SAMPLES = 50
PRICE_LOOKBACK_DAYS = 180


# ══════════════════════════════════════════════════════
# 数据获取
# ══════════════════════════════════════════════════════

def get_obs_stocks(obs_date):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT DISTINCT stock_code FROM discipline_observation_pool WHERE date = ?",
        (obs_date,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_oneil_advanced(obs_date):
    """获取当日O'Neil精选股票，全量纳入不设阈值"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT DISTINCT stock_code, stock_name, oneil_score FROM discipline_screening_daily WHERE date = ? ORDER BY rank",
        (obs_date,)
    ).fetchall()
    conn.close()
    return [{'code': r[0], 'name': r[1], 'score': r[2]} for r in rows]


def get_close_prices(code, since_date):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT date, close FROM daily_kline
        WHERE stock_code = ? AND date >= ?
        ORDER BY date
    """, (code, since_date)).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def get_stock_names(codes, obs_date):
    conn = sqlite3.connect(DB_PATH)
    names = {}
    placeholders = ','.join('?' * len(codes))
    for r in conn.execute(
        f"SELECT stock_code, stock_name FROM discipline_observation_pool WHERE date = ? AND stock_code IN ({placeholders})",
        [obs_date] + codes
    ).fetchall():
        names[r[0]] = r[1]
    missing = [c for c in codes if c not in names]
    if missing:
        mp = ','.join('?' * len(missing))
        for r in conn.execute(
            f"SELECT stock_code, name FROM stock_basic WHERE stock_code IN ({mp})",
            missing
        ).fetchall():
            names[r[0]] = r[1] if r[1] else r[0]
    for c in codes:
        if c not in names:
            names[c] = c
    conn.close()
    return names


def get_market_context(obs_date):
    """获取当日市场上下文（4个维度）"""
    conn = sqlite3.connect(DB_PATH)
    ctx = {
        'oneil_phase': None,
        'nh_nl_status': None,
        'trend': None,
        'strongest_index_rs': None
    }

    # 1) O'Neil 大盘状态
    row = conn.execute(
        "SELECT market_phase FROM market_direction_daily WHERE date = ?", (obs_date,)
    ).fetchone()
    if row:
        ctx['oneil_phase'] = row[0]

    # 2) NH-NL 市场广度 (来自 market_direction_daily 的 new_high_new_low_ratio)
    row = conn.execute(
        "SELECT new_high_new_low_ratio FROM market_direction_daily WHERE date = ?", (obs_date,)
    ).fetchone()
    if row and row[0] is not None:
        ratio = row[0]
        if ratio > 1.2:
            ctx['nh_nl_status'] = '广度健康'
        elif ratio > 0.8:
            ctx['nh_nl_status'] = '广度分化'
        else:
            ctx['nh_nl_status'] = '广度恶化'

    # 3) 均线趋势（上证指数）
    rows = conn.execute("""
        SELECT close FROM index_daily_kline
        WHERE stock_code = '000001' AND date <= ? ORDER BY date DESC LIMIT 60
    """, (obs_date,)).fetchall()
    if rows and len(rows) >= 3:
        closes = [r[0] for r in rows]
        ma20 = sum(closes[:20]) / min(20, len(closes)) if len(closes) >= 20 else sum(closes)/len(closes)
        ma50 = sum(closes[:min(50, len(closes))]) / min(50, len(closes)) if len(closes) >= 3 else 0
        current = closes[0]
        if current > ma20 > ma50:
            ctx['trend'] = '多头排列'
        elif current < ma20 < ma50:
            ctx['trend'] = '空头排列'
        else:
            ctx['trend'] = '震荡'

    # 4) 最强指数 RS
    row = conn.execute(
        "SELECT rs_250 FROM index_rs_daily WHERE date = ? ORDER BY rs_250 DESC LIMIT 1",
        (obs_date,)
    ).fetchone()
    if row and row[0] is not None:
        rs = row[0]
        if rs >= 80:
            ctx['strongest_index_rs'] = '动量强劲'
        elif rs >= 40:
            ctx['strongest_index_rs'] = '动量温和'
        else:
            ctx['strongest_index_rs'] = '动量疲弱'

    conn.close()
    return ctx


# ══════════════════════════════════════════════════════
# 回测计算
# ══════════════════════════════════════════════════════

def compute_forward_returns(prices, price_dates, entry_dt, entry_price):
    try:
        idx = price_dates.index(entry_dt)
    except ValueError:
        return None
    result = {}
    for h in HORIZONS:
        fut_idx = idx + h
        if fut_idx < len(price_dates):
            fut_close = prices[price_dates[fut_idx]]
            result[h] = round((fut_close - entry_price) / entry_price * 100, 2)
        else:
            result[h] = None
    if any(v is not None for v in result.values()):
        return result
    return None


def calc_stats(ret_list, n=None):
    """从收益率列表计算统计量"""
    valid = [v for v in ret_list if v is not None]
    if not valid:
        return {'avg': 0, 'win': 0, 'n': n if n else 0}
    return {
        'avg': round(sum(valid) / len(valid), 2),
        'win': round(sum(1 for v in valid if v > 0) / len(valid) * 100, 1),
        'n': n if n else len(valid)
    }


def run_equal_random(pool_codes, n_pick, obs_date, price_since, samples=RANDOM_SAMPLES):
    """等额随机采样：从全池随机抽n_pick只，重复samples次取均值"""
    # 先获取所有池内股票的close价格和forward returns
    pool_results = []
    for code in pool_codes:
        prices = get_close_prices(code, price_since)
        if len(prices) < 30:
            continue
        price_dates = sorted(prices.keys())
        entry_price = prices.get(obs_date)
        if entry_price is None:
            continue
        rets = compute_forward_returns(prices, price_dates, obs_date, entry_price)
        if rets and any(v is not None for v in rets.values()):
            pool_results.append({'code': code, 'returns': rets})

    if len(pool_results) < n_pick:
        return None

    random.seed(42)
    agg = {}
    for h in HORIZONS:
        sample_avgs = []
        sample_wins = []
        for _ in range(samples):
            sampled = random.sample(pool_results, min(n_pick, len(pool_results)))
            rets = [s['returns'].get(h) for s in sampled if s['returns'].get(h) is not None]
            if rets:
                sample_avgs.append(sum(rets) / len(rets))
                sample_wins.append(sum(1 for v in rets if v > 0) / len(rets) * 100)
        if sample_avgs:
            agg[h] = {
                'avg': round(sum(sample_avgs) / len(sample_avgs), 2),
                'win': round(sum(sample_wins) / len(sample_wins), 1),
                'samples': samples,
                'n_per_sample': n_pick
            }
        else:
            agg[h] = {'avg': 0, 'win': 0, 'samples': samples, 'n_per_sample': n_pick}
    return agg


def filter_by_rules(analyze_result):
    """三步规则链过滤"""
    if "error" in analyze_result:
        return []
    tc = analyze_result.get("trend_classification", {})
    trade_sigs = analyze_result.get("trade_signals", [])
    outcomes = analyze_result.get("divergence_outcomes", [])
    trend_type = tc.get("trend_type", "无中枢")

    # Step 1: 结构过滤
    if trend_type not in ("上涨趋势", "盘整"):
        return []

    # Step 2: 信号匹配（有买入信号即可）
    buy_signals = [s for s in trade_sigs if s["side"] == "buy"]
    if not buy_signals:
        return []

    # Step 3: 历史质量
    if outcomes:
        total = len(outcomes)
        if total > 0:
            strong = sum(1 for o in outcomes if o["outcome"] == "反趋势")
            quality = round(strong / total * 100)
            if quality < 30:
                return []

    signal_grades = {"三买": 3, "二买": 2, "类一买": 1, "一买": 1}
    return [dict(s) for s in buy_signals if signal_grades.get(s.get("type", ""), 0) >= 1]


# ══════════════════════════════════════════════════════
# 单日回测核心
# ══════════════════════════════════════════════════════

def run_single_day(obs_date, use_filter=False):
    from scanners.chanlun import analyze

    stocks = get_obs_stocks(obs_date)
    if not stocks:
        print(f'  {obs_date}: 无观察池数据，跳过')
        return None

    advanced_list = get_oneil_advanced(obs_date)
    price_since = (datetime.strptime(obs_date, "%Y-%m-%d") - timedelta(days=PRICE_LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    market_ctx = get_market_context(obs_date)

    print(f'  {obs_date}: 池{len(stocks)}只 ONeil精选{len(advanced_list)}只')

    chanlun_raw = []        # 全部缠论信号（过滤前）
    chanlun_filtered = []   # 三步规则链过滤后
    chanlun_signals_out = []  # 过滤后明细
    chanlun_raw_out = []    # 原始明细
    full_basket_returns = {'5d': [], '10d': [], '20d': []}
    oneil_returns = {'5d': [], '10d': [], '20d': []}
    oneil_signals_out = []
    names_cache = get_stock_names(stocks, obs_date)

    grade_map = {'三买': 3, '二买': 2, '类一买': 1, '一买': 1}

    for i, code in enumerate(stocks):
        prices = get_close_prices(code, price_since)
        if len(prices) < 30:
            continue
        price_dates = sorted(prices.keys())

        # 全池基准
        entry_price = prices.get(obs_date)
        if entry_price:
            rets = compute_forward_returns(prices, price_dates, obs_date, entry_price)
            if rets:
                for h in HORIZONS:
                    if rets.get(h) is not None:
                        full_basket_returns[f'{h}d'].append(rets[h])

        # 缠论分析
        try:
            r = analyze(code, 'D', 500, data_mode='stock')
        except Exception:
            continue

        t_signals = [s for s in r.get('trade_signals', []) if s['side'] == 'buy' and s['dt'] == obs_date]
        if not t_signals:
            continue

        best_signal = max(t_signals, key=lambda s: grade_map.get(s['type'], 0))
        signal_types = ','.join(sorted(set(s['type'] for s in t_signals)))

        if entry_price is None:
            continue

        rets = compute_forward_returns(prices, price_dates, obs_date, entry_price)
        if rets is None:
            continue

        record = {
            'code': code,
            'name': names_cache.get(code, code),
            'type': best_signal['type'],
            'confidence': best_signal.get('confidence', '低'),
            'ret_5d': rets.get(5),
            'ret_10d': rets.get(10),
            'ret_20d': rets.get(20),
            'signal_types': signal_types
        }

        chanlun_raw.append(record)
        chanlun_raw_out.append(dict(record))

        if use_filter:
            allowed = filter_by_rules(r)
            if not allowed:
                continue
            filtered_types = {a['type'] for a in allowed}
            if best_signal['type'] not in filtered_types:
                continue

        chanlun_filtered.append(record)
        chanlun_signals_out.append(dict(record))

        if (i + 1) % 100 == 0:
            print(f'    进度: {i+1}/{len(stocks)} (信号: {len(chanlun_raw)})')

    # O'Neil 进阶组
    for adv in advanced_list:
        code = adv['code']
        prices = get_close_prices(code, price_since)
        if len(prices) < 30:
            continue
        price_dates = sorted(prices.keys())
        entry_price = prices.get(obs_date)
        if entry_price is None:
            continue
        rets = compute_forward_returns(prices, price_dates, obs_date, entry_price)
        if rets is None:
            continue
        for h in HORIZONS:
            if rets.get(h) is not None:
                oneil_returns[f'{h}d'].append(rets[h])
        oneil_signals_out.append({
            'code': code,
            'name': adv['name'] or names_cache.get(code, code),
            'score': adv['score'],
            'ret_5d': rets.get(5),
            'ret_10d': rets.get(10),
            'ret_20d': rets.get(20)
        })

    # 等额随机采样（缠论组对照）
    cl_signals = chanlun_filtered if use_filter else chanlun_raw
    n_chanlun = len(cl_signals)
    random_equal = None
    if n_chanlun > 0:
        random_equal = run_equal_random(stocks, n_chanlun, obs_date, price_since)

    # O'Neil 等额随机
    oneil_random_equal = None
    if len(oneil_signals_out) > 0:
        oneil_random_equal = run_equal_random(stocks, len(oneil_signals_out), obs_date, price_since)

    # 组装 stats
    chanlun_ret = {'5d': [s['ret_5d'] for s in cl_signals if s['ret_5d'] is not None],
                    '10d': [s['ret_10d'] for s in cl_signals if s['ret_10d'] is not None],
                    '20d': [s['ret_20d'] for s in cl_signals if s['ret_20d'] is not None]}

    stats = {
        'chanlun': {f'{h}d': calc_stats(chanlun_ret[f'{h}d'], n_chanlun) for h in HORIZONS},
        'full_basket': {f'{h}d': calc_stats(full_basket_returns[f'{h}d']) for h in HORIZONS},
        'oneil_advanced': {f'{h}d': calc_stats(oneil_returns[f'{h}d']) for h in HORIZONS},
    }
    if random_equal:
        stats['random_equal'] = {f'{h}d': random_equal[h] for h in HORIZONS}
    if oneil_random_equal:
        stats['oneil_random_equal'] = {f'{h}d': oneil_random_equal[h] for h in HORIZONS}

    # 按信号类型分组
    by_type = {}
    for tp in ['一买', '二买', '三买', '类一买']:
        typed = [s for s in cl_signals if s['type'] == tp]
        if typed:
            by_type[tp] = {}
            for h in HORIZONS:
                hret = [s[f'ret_{h}d'] for s in typed if s[f'ret_{h}d'] is not None]
                by_type[tp][f'{h}d'] = calc_stats(hret, len(typed))

    # 按置信度分组
    by_conf = {}
    for conf in ['高', '中', '低']:
        confd = [s for s in cl_signals if s['confidence'] == conf]
        if confd:
            by_conf[conf] = {}
            for h in HORIZONS:
                hret = [s[f'ret_{h}d'] for s in confd if s[f'ret_{h}d'] is not None]
                by_conf[conf][f'{h}d'] = calc_stats(hret, len(confd))

    result = {
        'obs_date': obs_date,
        'pool_size': len(stocks),
        'run_date': datetime.now().strftime('%Y-%m-%d'),
        'pool_composition': stocks,
        'signals': chanlun_signals_out if use_filter else chanlun_raw_out,
        'signals_raw': chanlun_raw_out,
        'oneil_signals': oneil_signals_out,
        'stats': stats,
        'by_type': by_type,
        'by_confidence': by_conf,
        'market_context': market_ctx,
        'filtered': use_filter
    }

    print(f'    → {obs_date}: 缠论{len(chanlun_raw)}/' +
          (f'{len(chanlun_filtered)}(过滤) ' if use_filter else '') +
          f'池{len(stocks)}只(有效{len(full_basket_returns["5d"])}) ONeil{len(advanced_list)}只(有效{len(oneil_returns["5d"])})')

    return result


def save_result(result, out_dir=OUT_DIR):
    os.makedirs(out_dir, exist_ok=True)
    obs_date = result['obs_date']
    fname = f'{obs_date}.json' if not result['filtered'] else f'{obs_date}_filtered.json'

    # 精简版：不存 pool_composition 全量到文件（太大），仅保留摘要
    out = dict(result)
    out.pop('pool_composition', None)
    if 'signals_raw' in out and not result['filtered']:
        out.pop('signals_raw', None)

    with open(os.path.join(out_dir, fname), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'  📁 {fname}')


def save_manifest(dates, out_dir=OUT_DIR):
    # 先加载已有 manifest
    existing = {}
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
            for entry in json.load(f):
                existing[entry['obs_date']] = entry

    # 更新或新增当前批次的日期
    for obs_date in sorted(dates):
        fname = f'{obs_date}.json'
        fpath = os.path.join(out_dir, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        entry = {
            'obs_date': obs_date,
            'pool_size': data.get('pool_size', 0),
            'chanlun_n': len(data.get('signals', [])),
            'oneil_advanced_n': len(data.get('oneil_signals', [])),
            'run_date': data.get('run_date', ''),
            'market_phase': data.get('market_context', {}).get('oneil_phase', '')
        }
        # 检查是否有过滤版本
        ffname = f'{obs_date}_filtered.json'
        if os.path.exists(os.path.join(out_dir, ffname)):
            with open(os.path.join(out_dir, ffname), 'r', encoding='utf-8') as f:
                fdata = json.load(f)
            entry['chanlun_filtered_n'] = len(fdata.get('signals', []))
        existing[obs_date] = entry

    manifest = sorted(existing.values(), key=lambda x: x['obs_date'])
    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f'  📋 manifest: {len(manifest)} 个交易日')


# ══════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse as ap
    parser = ap.ArgumentParser(description='缠论vs欧奈尔回测对比 v2.0')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--date', type=str, help='单日回测日期 YYYY-MM-DD')
    group.add_argument('--start', type=str, help='批量起始日期 YYYY-MM-DD')
    parser.add_argument('--end', type=str, default=None, help='批量结束日期（默认昨天）')
    parser.add_argument('--filter', action='store_true', help='启用三步规则链过滤')
    args = parser.parse_args()

    if args.date:
        dates = [args.date]
    else:
        start_dt = datetime.strptime(args.start, '%Y-%m-%d')
        if args.end:
            end_dt = datetime.strptime(args.end, '%Y-%m-%d')
        else:
            end_dt = datetime.now() - timedelta(days=1)

        # 检查哪些日期有观察池数据
        conn = sqlite3.connect(DB_PATH)
        available = set(r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM discipline_observation_pool ORDER BY date"
        ).fetchall())
        conn.close()

        dates = []
        dt = start_dt
        while dt <= end_dt:
            ds = dt.strftime('%Y-%m-%d')
            if ds in available:
                dates.append(ds)
            dt += timedelta(days=1)

        if not dates:
            print('指定范围内无观察池数据')
            sys.exit(1)
        print(f'日期范围: {dates[0]} ~ {dates[-1]}, {len(dates)}个交易日')

    processed = []
    for obs_date in dates:
        try:
            # 原始版
            result = run_single_day(obs_date, use_filter=False)
            if result:
                save_result(result)
                # 过滤版
                if args.filter:
                    fresult = run_single_day(obs_date, use_filter=True)
                    if fresult:
                        fresult['filtered'] = True
                        save_result(fresult)
                processed.append(obs_date)
        except Exception as e:
            print(f'  ❌ {obs_date}: {e}')
            import traceback
            traceback.print_exc()

    if processed:
        save_manifest(processed)
    print(f'\n完成: {len(processed)} 个交易日')
