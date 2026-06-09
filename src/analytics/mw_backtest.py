"""
MW 信号回测分析引擎 v1.0

计算 MW 信号的 forward return，按置信度/评分维度/市场背景/市值分组统计。

用法：
    python src/analytics/mw_backtest.py --start 2026-01-01 --end 2026-06-04

输出：JSON 到 stdout
"""

import sqlite3, os, sys, json, random, argparse
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "lixinger.db")

HORIZONS = [5, 10, 20]
RANDOM_SAMPLES = 50


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def compute_ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


# ═══════════════════════════════════════════════
# 1. 市场状态
# ═══════════════════════════════════════════════

def get_market_states(db, start_date, end_date):
    """中证全指 000985 的 MA20/MA50 排列"""
    # 往前多取 60 天保证 MA50 可计算
    extended_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=90)).strftime('%Y-%m-%d')
    rows = db.execute("""
        SELECT date, close FROM index_daily_kline
        WHERE stock_code='000985' AND date >= ? AND date <= ?
        ORDER BY date
    """, (extended_start, end_date)).fetchall()

    closes = [r['close'] for r in rows]
    dates = [r['date'] for r in rows]
    states = {}

    for i in range(len(closes)):
        if i < 50:
            continue
        window = closes[:i+1]
        ma20 = compute_ma(window, 20)
        ma50 = compute_ma(window, 50)
        if ma20 is None or ma50 is None:
            continue
        cur = closes[i]
        if cur > ma20 > ma50:
            states[dates[i]] = "多头"
        elif cur < ma20 < ma50:
            states[dates[i]] = "空头"
        else:
            states[dates[i]] = "震荡"

    return states


# ═══════════════════════════════════════════════
# 2. 市值
# ═══════════════════════════════════════════════

def get_market_caps(db, stock_codes):
    """获取股票的最新总股本"""
    placeholders = ','.join('?' * len(stock_codes))
    rows = db.execute(f"""
        SELECT stock_code, MAX(capitalization) as cap
        FROM stock_equity_change
        WHERE stock_code IN ({placeholders})
        GROUP BY stock_code
    """, stock_codes).fetchall()
    return {r[0]: r[1] for r in rows if r[1]}

def cap_tier(mcap):
    if mcap < 5_000_000_000:
        return "小盘(<50亿)"
    elif mcap < 20_000_000_000:
        return "中盘(50~200亿)"
    elif mcap < 100_000_000_000:
        return "大盘(200~1000亿)"
    else:
        return "超大盘(≥1000亿)"


# ═══════════════════════════════════════════════
# 3. Forward Returns
# ═══════════════════════════════════════════════

def get_forward_returns(db, signals):
    """批量获取 B2 之后的 forward returns"""
    # 预先加载所有需要的 K 线数据
    codes = list(set(s['stock_code'] for s in signals))
    min_b2 = min(s['b2_date'] for s in signals)

    # 预加载缓存
    price_cache = {}
    for code in codes:
        rows = db.execute("""
            SELECT date, close FROM daily_kline
            WHERE stock_code=? AND date >= ?
            ORDER BY date
        """, (code, min_b2)).fetchall()
        price_cache[code] = {r['date']: r['close'] for r in rows}

    results = []
    for sig in signals:
        code = sig['stock_code']
        b2_date = sig['b2_date']
        prices = price_cache.get(code, {})
        dates = sorted(prices.keys())

        if b2_date not in prices:
            continue

        entry_price = prices[b2_date]
        try:
            idx = dates.index(b2_date)
        except ValueError:
            continue

        rets = {}
        for h in HORIZONS:
            fut_idx = idx + h
            if fut_idx < len(dates):
                rets[h] = round((prices[dates[fut_idx]] - entry_price) / entry_price * 100, 2)
            else:
                rets[h] = None

        results.append({
            'code': code,
            'b2_date': b2_date,
            'returns': rets,
            'signal': sig
        })

    return results


# ═══════════════════════════════════════════════
# 4. 统计计算
# ═══════════════════════════════════════════════

def calc_stats(returns, n=None):
    valid = [r for r in returns if r is not None]
    if not valid:
        return {'win_rate': 0, 'median_return': 0, 'avg_return': 0, 'n': 0}
    return {
        'win_rate': round(sum(1 for v in valid if v > 2.0) / len(valid) * 100, 1),
        'median_return': round(sorted(valid)[len(valid)//2], 2),
        'avg_return': round(sum(valid) / len(valid), 2),
        'n': n if n else len(valid)
    }


def group_stats(items, group_key, group_name_fn=None):
    """按 key 分组统计"""
    groups = defaultdict(lambda: {h: [] for h in HORIZONS})
    for item in items:
        key = item['signal'].get(group_key) if group_key in item['signal'] else group_name_fn(item) if group_name_fn else None
        if key is None:
            continue
        for h in HORIZONS:
            if item['returns'].get(h) is not None:
                groups[key][h].append(item['returns'][h])

    result = {}
    for key, rets in sorted(groups.items(), key=lambda x: str(x[0])):
        result[str(key)] = {f"{h}d": calc_stats(rets[h]) for h in HORIZONS}

    return result


# ═══════════════════════════════════════════════
# 5. 随机基准
# ═══════════════════════════════════════════════

def random_baseline(db, signals, price_cache):
    """等额随机采样基准 — 逐只股票统计而非组合平均"""
    by_date = defaultdict(list)
    for s in signals:
        by_date[s['b2_date']].append(s)

    all_rets = {h: [] for h in HORIZONS}
    random.seed(42)

    for b2_date, sigs in by_date.items():
        n_pick = len(sigs)
        if n_pick == 0:
            continue

        sig_codes = set(s['stock_code'] for s in sigs)
        all_codes = list(price_cache.keys())
        non_sig_codes = [c for c in all_codes if c not in sig_codes and b2_date in price_cache.get(c, {})]

        if len(non_sig_codes) < n_pick:
            continue

        for _ in range(RANDOM_SAMPLES):
            sampled = random.sample(non_sig_codes, n_pick)
            for code in sampled:
                prices = price_cache.get(code, {})
                dates = sorted(prices.keys())
                if b2_date not in prices:
                    continue
                entry = prices[b2_date]
                try:
                    idx = dates.index(b2_date)
                except ValueError:
                    continue
                for h in HORIZONS:
                    fut = idx + h
                    if fut < len(dates):
                        ret = (prices[dates[fut]] - entry) / entry * 100
                        all_rets[h].append(ret)

    result = {}
    for h in HORIZONS:
        result[f"{h}d"] = calc_stats(all_rets[h], len(all_rets[h]))

    return result


# ═══════════════════════════════════════════════
# 6. 评分维度分析
# ═══════════════════════════════════════════════

SCORE_DIMS = {
    'score_h': 'H:前高趋势',
    'score_d': 'D:调整深度',
    'score_c': 'C:横盘质量',
    'score_p': 'P:整理回撤',
    'score_i1': 'I1:行业RS250',
    'score_i2': 'I2:个股RS250',
    'score_sig': 'Sig:信号共振',
    'score_gap': 'Gap:跳空',
}


def analyze_dimensions(signals_with_rets):
    """两层分析"""
    # 层一：区分度检查
    distribution = {}
    for dim_key, dim_name in SCORE_DIMS.items():
        vals = [s['signal'].get(dim_key) for s in signals_with_rets if s['signal'].get(dim_key) is not None]
        if not vals:
            distribution[dim_key] = {'name': dim_name, 'differentiation': '无数据', 'counts': {}}
            continue
        counts = {}
        for v in vals:
            counts[v] = counts.get(v, 0) + 1
        max_pct = max(counts.values()) / len(vals) * 100
        distribution[dim_key] = {
            'name': dim_name,
            'counts': {str(k): v for k, v in sorted(counts.items())},
            'dominant_pct': round(max_pct, 1),
            'differentiation': '低' if max_pct > 90 else '正常'
        }

    # 层二：有区分度维度的胜率/收益率
    dim_stats = {}
    for dim_key, dim_name in SCORE_DIMS.items():
        if distribution[dim_key]['differentiation'] == '低':
            dim_stats[dim_key] = {'name': dim_name, 'differentiation': '低', 'detail': None}
            continue

        groups = defaultdict(lambda: {h: [] for h in HORIZONS})
        for item in signals_with_rets:
            val = item['signal'].get(dim_key)
            if val is None:
                continue
            for h in HORIZONS:
                if item['returns'].get(h) is not None:
                    groups[val][h].append(item['returns'][h])

        detail = {}
        for val, rets in sorted(groups.items()):
            detail[str(val)] = {f"{h}d": calc_stats(rets[h]) for h in HORIZONS}

        dim_stats[dim_key] = {'name': dim_name, 'differentiation': '正常', 'detail': detail}

    return distribution, dim_stats


# ═══════════════════════════════════════════════
# 7. 共性分析
# ═══════════════════════════════════════════════

def common_traits(signals_with_rets, horizon=10):
    """提取胜率/收益率 Top10% 的共性特征"""
    valid = [s for s in signals_with_rets if s['returns'].get(horizon) is not None]
    if len(valid) < 20:
        return {}

    # 排序
    sorted_by_ret = sorted(valid, key=lambda s: s['returns'][horizon], reverse=True)
    sorted_by_win = sorted(valid, key=lambda s: s['returns'][horizon], reverse=True)
    top_n = max(1, len(valid) // 10)

    top_ret = sorted_by_ret[:top_n]
    top_win = sorted_by_win[:top_n]

    def avg_field(items, field, default=0):
        vals = [i['signal'].get(field) for i in items if i['signal'].get(field) is not None]
        return round(sum(vals) / len(vals), 2) if vals else default

    def avg_ret(items, h):
        vals = [i['returns'].get(h) for i in items if i['returns'].get(h) is not None]
        return round(sum(vals) / len(vals), 2) if vals else 0

    all_avg = {
        'b2_return_pct': avg_field(valid, 'b2_return_pct'),
        'b2_close_pos': avg_field(valid, 'b2_close_pos'),
        'b2_ma_count': avg_field(valid, 'b2_ma_count'),
        'b1_return_pct': avg_field(valid, 'b1_return_pct'),
        'decline_pct': avg_field(valid, 'decline_pct'),
        'c_amplitude_pct': avg_field(valid, 'c_amplitude_pct'),
        'h_rs250': avg_field(valid, 'h_rs250'),
        'score_total': avg_field(valid, 'score'),
        'ret_10d': avg_ret(valid, 10),
        'win_rate': calc_stats([i['returns'].get(10) for i in valid])['win_rate'],
    }

    top_ret_avg = {
        'b2_return_pct': avg_field(top_ret, 'b2_return_pct'),
        'b2_close_pos': avg_field(top_ret, 'b2_close_pos'),
        'b2_ma_count': avg_field(top_ret, 'b2_ma_count'),
        'b1_return_pct': avg_field(top_ret, 'b1_return_pct'),
        'decline_pct': avg_field(top_ret, 'decline_pct'),
        'c_amplitude_pct': avg_field(top_ret, 'c_amplitude_pct'),
        'h_rs250': avg_field(top_ret, 'h_rs250'),
        'score_total': avg_field(top_ret, 'score'),
        'ret_10d': avg_ret(top_ret, 10),
        'win_rate': calc_stats([i['returns'].get(10) for i in top_ret])['win_rate'],
    }

    # 差异
    diff = {}
    for k in all_avg:
        if k in top_ret_avg:
            diff[k] = round(top_ret_avg[k] - all_avg[k], 2)

    # 找出差异最大的 5 个特征
    top_diff = sorted(diff.items(), key=lambda x: abs(x[1]), reverse=True)
    top_features = [{'feature': k, 'all': all_avg[k], 'top10': top_ret_avg.get(k, 0), 'diff': v}
                    for k, v in top_diff[:8] if abs(v) > 0.5]

    return {
        'horizon': horizon,
        'all_signals_n': len(valid),
        'top10_n': top_n,
        'all_avg': all_avg,
        'top10_avg': top_ret_avg,
        'top_features': top_features
    }


# ═══════════════════════════════════════════════
# 8. 模拟盘
# ═══════════════════════════════════════════════

def portfolio_simulation(signals, start_date, end_date, price_cache, date_index, all_dates):
    """三档策略模拟：≥90 / ≥80 / 每日TOP10不限分"""
    from collections import defaultdict
    by_date = defaultdict(list)
    for s in signals:
        by_date[s['b2_date']].append(s)

    def run_strategy(label, filter_fn):
        cash = 1_000_000.0
        positions = []
        trades = wins = 0
        totals = []
        peak = 1_000_000
        max_dd = 0

        for day_idx, today in enumerate(all_dates):
            # 卖出到期
            for pos in positions[:]:
                if pos['exit_idx'] <= day_idx:
                    ep = price_cache[pos['code']].get(today, pos['entry_price'])
                    cash += pos['shares'] * ep
                    trades += 1
                    if ep > pos['entry_price']:
                        wins += 1
                    positions.remove(pos)

            # 买入
            if today in by_date:
                picks = [s for s in by_date[today] if filter_fn(s)][:10]
                for pick in picks:
                    code = pick['stock_code']
                    ep = price_cache[code].get(today)
                    if not ep or ep <= 0:
                        continue
                    try:
                        eidx = date_index[today]
                        xidx = min(eidx + 20, len(all_dates) - 1)
                    except:
                        continue
                    invest = cash * 0.01
                    if invest < 1000 or invest > cash:
                        continue
                    cash -= invest
                    positions.append({
                        'code': code, 'shares': invest / ep,
                        'entry_price': ep, 'entry_idx': eidx, 'exit_idx': xidx
                    })

            pv = sum(p['shares'] * price_cache[p['code']].get(today, p['entry_price']) for p in positions)
            tv = cash + pv
            totals.append((today, tv))
            peak = max(peak, tv)
            max_dd = min(max_dd, (tv - peak) / peak * 100)

        if not totals:
            return None
        final = totals[-1][1]
        ret = round((final - 1_000_000) / 1_000_000 * 100, 1)
        wr = round(wins / trades * 100, 1) if trades else 0
        return {
            'label': label, 'final_value': round(final, 0), 'return_pct': ret,
            'trades': trades, 'win_rate': wr, 'max_drawdown_pct': round(max_dd, 1)
        }

    r90 = run_strategy('score≥90(TOP)', lambda s: s.get('score', 0) >= 90)
    r80 = run_strategy('score≥80(高置信)', lambda s: s.get('confidence') == '高')
    rTOP = run_strategy('每日前10不限分', lambda s: True)

    return {'score_ge_90': r90, 'score_ge_80': r80, 'daily_top10': rTOP}


# ═══════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════

def run(start_date, end_date):
    db = get_db()

    # ── 读取信号 ──
    signals_raw = db.execute("""
        SELECT *, score_v2, confidence_v2, score_m1, score_m2, score_m3
        FROM mw_signal_daily
        WHERE b2_date >= ? AND b2_date <= ?
        ORDER BY b2_date, score DESC
    """, (start_date, end_date)).fetchall()
    signals = [dict(r) for r in signals_raw]

    if not signals:
        return {'error': '无信号数据'}

    # ── 市场状态 ──
    market_states = get_market_states(db, start_date, end_date)

    # ── 市值 ──
    codes = list(set(s['stock_code'] for s in signals))
    caps = get_market_caps(db, codes)

    # ── Forward Returns ──
    results = get_forward_returns(db, signals)

    # ── 总体统计 ──
    overall = {}
    for h in HORIZONS:
        rets = [r['returns'].get(h) for r in results if r['returns'].get(h) is not None]
        overall[f"{h}d"] = calc_stats(rets, len(rets))

    # ── 置信度分组 ──
    by_conf = group_stats(results, 'confidence')
    by_conf_v2 = group_stats(results, 'confidence_v2')

    # 细粒度分档
    def score_tier(item):
        s = item['signal'].get('score', 0)
        if s >= 90: return "90~100"
        if s >= 85: return "85~90"
        if s >= 80: return "80~85"
        if s >= 75: return "75~80"
        if s >= 70: return "70~75"
        if s >= 65: return "65~70"
        if s >= 60: return "60~65"
        if s >= 55: return "55~60"
        if s >= 50: return "50~55"
        return "0~50"

    by_score_tier = group_stats(results, None, score_tier)

    # ── 市场背景分组 ──
    def market_group(item):
        state = market_states.get(item['signal'].get('b2_date', ''), '未知')
        return state

    by_market = group_stats(results, None, market_group)

    # ── 市值分组 ──
    for item in results:
        code = item['signal']['stock_code']
        cap = caps.get(code, 0)
        # 获取 B2 日收盘价来算市值
        b2_close = item['signal'].get('close', 0) or 0
        # 从 daily_kline 查一下 B2 日收盘价
        if not b2_close:
            row = db.execute("SELECT close FROM daily_kline WHERE stock_code=? AND date=?", 
                           (code, item['signal'].get('b2_date'))).fetchone()
            b2_close = row['close'] if row else 0
        mcap = cap * b2_close if cap and b2_close else 0
        item['_mcap_tier'] = cap_tier(mcap)

    by_cap = group_stats(results, None, lambda i: i.get('_mcap_tier', '未知'))

    # ── 评分维度 ──
    dist, dim_stats = analyze_dimensions(results)

    # ── 共性 ──
    traits = common_traits(results, horizon=10)

    # ── 随机基准（全量）──
    # 构建 price_cache（所有信号的股票K线）
    price_cache = {}
    for code in codes:
        rows = db.execute("""SELECT date, close FROM daily_kline WHERE stock_code=? AND date >= ? ORDER BY date""",
                        (code, start_date)).fetchall()
        price_cache[code] = {r['date']: r['close'] for r in rows}
    baseline = random_baseline(db, signals, price_cache)

    # ── 随机基准（体系1 高置信度）──
    signals_v1_high = [s for s in signals if s.get('confidence') == '高']
    baseline_v1_high = random_baseline(db, signals_v1_high, price_cache) if signals_v1_high else {}

    # ── 随机基准（体系2 高置信度）──
    signals_v2_high = [s for s in signals if s.get('confidence_v2') == '高']
    baseline_v2_high = random_baseline(db, signals_v2_high, price_cache) if signals_v2_high else {}

    # ── 模拟盘所需的日期序列 ──
    all_dates = sorted(set(d for p in price_cache.values() for d in p.keys()))
    date_index = {d: i for i, d in enumerate(all_dates)}

    # ── 模拟盘 ──
    simulation = portfolio_simulation(signals, start_date, end_date, price_cache, date_index, all_dates)

    # ── 模拟盘 ──
    simulation = portfolio_simulation(signals, start_date, end_date, price_cache, date_index, all_dates)

    db.close()

    return {
        'start': start_date,
        'end': end_date,
        'total_signals': len(signals),
        'overall': overall,
        'by_confidence': by_conf,
        'by_confidence_v2': by_conf_v2,
        'by_score_tier': by_score_tier,
        'by_market': by_market,
        'by_market_cap': by_cap,
        'score_distribution': dist,
        'score_dimension': dim_stats,
        'common_traits': traits,
        'random_baseline': baseline,
        'random_baseline_v1_high': baseline_v1_high,
        'random_baseline_v2_high': baseline_v2_high,
        'portfolio_simulation': simulation,
        'market_states': {k: market_states[k] for k in sorted(market_states.keys())[-20:]}
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2026-01-01')
    parser.add_argument('--end', default=datetime.now().strftime('%Y-%m-%d'))
    args = parser.parse_args()

    result = run(args.start, args.end)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
