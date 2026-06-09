"""
PLUS信号延迟入场分析
B2后第2天/第3天开盘买入，持有5d/10d/20d，与随机买入对比
"""
import sqlite3, os, sys, random
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
from analytics.mw_backtest import calc_stats

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')
RANDOM_SAMPLES = 50
HORIZONS = [5, 10, 20]

def get_plus_signals():
    """获取PLUS信号列表"""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    signals = db.execute("""
        SELECT * FROM mw_signal_daily
        WHERE b2_date >= '2026-01-01' AND b2_date <= '2026-06-05'
        AND score >= 80 AND score_d = 15 AND score_i1 = 15 AND score_i2 = 15
        ORDER BY b2_date
    """).fetchall()
    result = [dict(r) for r in signals]
    db.close()
    return result

def get_price_data(codes, start_date):
    """批量加载所有需要的K线数据"""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    placeholders = ','.join('?' * len(codes))
    rows = db.execute(f"""
        SELECT stock_code, date, open, close FROM daily_kline
        WHERE stock_code IN ({placeholders}) AND date >= ?
        ORDER BY stock_code, date
    """, codes + [start_date]).fetchall()
    db.close()
    
    price_cache = defaultdict(dict)
    for r in rows:
        price_cache[r['stock_code']][r['date']] = {'open': r['open'], 'close': r['close']}
    return price_cache

def get_all_codes_with_prices(start_date):
    """获取所有有K线数据的股票代码（用于随机采样）"""
    db = sqlite3.connect(DB_PATH)
    rows = db.execute("""
        SELECT DISTINCT stock_code FROM daily_kline WHERE date >= ?
    """, (start_date,)).fetchall()
    db.close()
    return [r[0] for r in rows]

def find_nth_trading_day(dates, base_date, n):
    """在dates列表中找到base_date之后的第n个交易日，返回该日期和open价"""
    if base_date not in dates:
        # 如果base_date不在dates中，找最近的
        base_idx = None
        for i, d in enumerate(dates):
            if d >= base_date:
                base_idx = i
                break
        if base_idx is None:
            return None
    else:
        base_idx = dates.index(base_date)
    
    target_idx = base_idx + n
    if target_idx >= len(dates):
        return None
    return dates[target_idx]

def run_delayed_analysis(plus_signals, price_cache, delay_days, all_codes):
    """
    延迟入场分析
    delay_days: 2 或 3，表示B2后第几天的开盘买入
    """
    results = []
    by_date = defaultdict(list)
    for s in plus_signals:
        by_date[s['b2_date']].append(s)
    
    for sig in plus_signals:
        code = sig['stock_code']
        b2_date = sig['b2_date']
        prices = price_cache.get(code, {})
        if not prices:
            continue
        dates = sorted(prices.keys())
        
        # 找B2后第delay_days个交易日
        entry_date = find_nth_trading_day(dates, b2_date, delay_days)
        if not entry_date:
            continue
        
        entry_price = prices[entry_date]['open']
        if not entry_price or entry_price <= 0:
            continue
        
        try:
            idx = dates.index(entry_date)
        except ValueError:
            continue
        
        rets = {}
        for h in HORIZONS:
            fut_idx = idx + h
            if fut_idx < len(dates):
                rets[h] = round((prices[dates[fut_idx]]['close'] - entry_price) / entry_price * 100, 2)
            else:
                rets[h] = None
        
        results.append({
            'code': code,
            'b2_date': b2_date,
            'entry_date': entry_date,
            'entry_price': entry_price,
            'returns': rets,
            'signal': sig
        })
    
    # 计算MW统计
    mw_stats = {}
    for h in HORIZONS:
        rets = [r['returns'].get(h) for r in results if r['returns'].get(h) is not None]
        mw_stats[f"{h}d"] = calc_stats(rets, len(rets))
    
    # 随机基准：每天用相同数量的随机股票，同样延迟入场
    random.seed(42)
    all_rets = {h: [] for h in HORIZONS}
    
    for b2_date, sigs in by_date.items():
        n_pick = len(sigs)
        sig_codes = set(s['stock_code'] for s in sigs)
        
        # 找到这一天可用的非信号股票
        available = []
        for c in all_codes:
            if c in sig_codes:
                continue
            prices = price_cache.get(c, {})
            if not prices:
                continue
            dates = sorted(prices.keys())
            entry_date = find_nth_trading_day(dates, b2_date, delay_days)
            if entry_date and prices[entry_date]['open'] > 0:
                available.append(c)
        
        if len(available) < n_pick:
            continue
        
        for _ in range(RANDOM_SAMPLES):
            sampled = random.sample(available, min(n_pick, len(available)))
            for code in sampled:
                prices = price_cache[code]
                dates = sorted(prices.keys())
                entry_date = find_nth_trading_day(dates, b2_date, delay_days)
                if not entry_date:
                    continue
                entry_price = prices[entry_date]['open']
                try:
                    idx = dates.index(entry_date)
                except ValueError:
                    continue
                for h in HORIZONS:
                    fut = idx + h
                    if fut < len(dates):
                        ret = (prices[dates[fut]]['close'] - entry_price) / entry_price * 100
                        all_rets[h].append(ret)
    
    rand_stats = {}
    for h in HORIZONS:
        rand_stats[f"{h}d"] = calc_stats(all_rets[h], len(all_rets[h]))
    
    return {
        'delay_days': delay_days,
        'mw_signals': len(results),
        'mw_stats': mw_stats,
        'rand_stats': rand_stats
    }

def main():
    print("Loading PLUS signals...")
    plus_signals = get_plus_signals()
    print(f"PLUS signals: {len(plus_signals)}")
    
    codes = list(set(s['stock_code'] for s in plus_signals))
    all_codes = get_all_codes_with_prices('2026-01-01')
    print(f"Plus codes: {len(codes)}, all codes: {len(all_codes)}")
    
    print("Loading price data...")
    price_cache = get_price_data(list(set(codes + all_codes)), '2025-12-01')
    
    print("\n=== B2+2日分析 ===")
    r2 = run_delayed_analysis(plus_signals, price_cache, 2, all_codes)
    print(f"MW signals with valid entry: {r2['mw_signals']}")
    for hz in ['5d', '10d', '20d']:
        mw = r2['mw_stats'][hz]
        rd = r2['rand_stats'][hz]
        print(f"  {hz}: MW胜率={mw['win_rate']:.1f}% 中位={mw['median_return']:+.2f}% | 随机胜率={rd['win_rate']:.1f}% 中位={rd['median_return']:+.2f}%")
    
    print("\n=== B2+3日分析 ===")
    r3 = run_delayed_analysis(plus_signals, price_cache, 3, all_codes)
    print(f"MW signals with valid entry: {r3['mw_signals']}")
    for hz in ['5d', '10d', '20d']:
        mw = r3['mw_stats'][hz]
        rd = r3['rand_stats'][hz]
        print(f"  {hz}: MW胜率={mw['win_rate']:.1f}% 中位={mw['median_return']:+.2f}% | 随机胜率={rd['win_rate']:.1f}% 中位={rd['median_return']:+.2f}%")

if __name__ == '__main__':
    main()
