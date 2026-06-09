"""
PLUS信号 B2+2开盘买入5%仓位 持有10日 模拟盘
初始资金100万，2026-01-01 ~ 2026-06-05
"""
import sqlite3, os, sys
from collections import defaultdict

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')

def get_plus_signals():
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

def load_price_data(codes):
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    placeholders = ','.join('?' * len(codes))
    rows = db.execute(f"""
        SELECT stock_code, date, open, close FROM daily_kline
        WHERE stock_code IN ({placeholders}) AND date >= '2025-12-01' AND date <= '2026-07-31'
        ORDER BY stock_code, date
    """, codes).fetchall()
    db.close()
    
    cache = {}
    for r in rows:
        code = r['stock_code']
        if code not in cache:
            cache[code] = {'dates': [], 'prices': {}}
        cache[code]['dates'].append(r['date'])
        cache[code]['prices'][r['date']] = {'open': r['open'], 'close': r['close']}
    return cache

def find_nth_trading_day(dates, base_date, n):
    """找base_date之后第n个交易日"""
    try:
        idx = dates.index(base_date)
    except ValueError:
        for i, d in enumerate(dates):
            if d > base_date:
                idx = i - 1
                break
        else:
            return None
    target = idx + n
    if target >= len(dates):
        return None
    return dates[target]

def run_simulation():
    signals = get_plus_signals()
    print(f"PLUS signals: {len(signals)}")
    
    codes = list(set(s['stock_code'] for s in signals))
    prices = load_price_data(codes)
    
    # 构建所有信号的待执行买入计划: (entry_date, stock_code, exit_date)
    pending_buys = []  # (entry_date, code, exit_date, signal)
    skipped_no_entry = 0
    skipped_no_exit = 0
    
    for sig in signals:
        code = sig['stock_code']
        b2 = sig['b2_date']
        pdata = prices.get(code)
        if not pdata:
            continue
        
        entry_date = find_nth_trading_day(pdata['dates'], b2, 2)
        if not entry_date:
            skipped_no_entry += 1
            continue
        
        exit_date = find_nth_trading_day(pdata['dates'], entry_date, 10)
        if not exit_date:
            skipped_no_exit += 1
            continue
        
        pending_buys.append((entry_date, code, exit_date, sig))
    
    print(f"Pending buys: {len(pending_buys)} (no_entry={skipped_no_entry}, no_exit={skipped_no_exit})")
    
    # 按entry_date分组
    buys_by_date = defaultdict(list)
    for entry_date, code, exit_date, sig in pending_buys:
        buys_by_date[entry_date].append((code, exit_date, sig))
    
    # 全局交易日历
    all_dates = set()
    for code in codes:
        all_dates.update(prices[code]['dates'])
    all_dates = sorted(all_dates)
    sim_dates = [d for d in all_dates if '2025-12-15' <= d <= '2026-07-31']
    
    cash = 1_000_000.0
    positions = []  # [{code, shares, entry_price, entry_date, exit_date}]
    trades = []
    daily_values = []
    peak = 1_000_000
    max_dd = 0
    max_positions = 0
    total_invested = 0
    skipped_cash = 0
    
    for today in sim_dates:
        # === 卖出 ===
        for pos in positions[:]:
            if pos['exit_date'] <= today:
                code = pos['code']
                pdata = prices.get(code)
                if pdata and today in pdata['prices']:
                    exit_price = pdata['prices'][today]['close']
                else:
                    exit_price = pos['entry_price']
                
                proceeds = pos['shares'] * exit_price
                cash += proceeds
                ret = (exit_price - pos['entry_price']) / pos['entry_price'] * 100
                trades.append({
                    'code': code,
                    'stock_name': pos.get('name', ''),
                    'entry_date': pos['entry_date'],
                    'exit_date': today,
                    'entry_price': pos['entry_price'],
                    'exit_price': exit_price,
                    'return_pct': ret
                })
                positions.remove(pos)
        
        # === 买入 ===
        if today in buys_by_date:
            for code, exit_date, sig in buys_by_date[today]:
                pdata = prices.get(code)
                if not pdata:
                    continue
                
                entry_price = pdata['prices'].get(today, {}).get('open', 0)
                if not entry_price or entry_price <= 0:
                    continue
                
                invest = cash * 0.05
                if invest < 5000:
                    skipped_cash += 1
                    continue
                
                shares = invest / entry_price
                cash -= invest
                total_invested += invest
                
                positions.append({
                    'code': code,
                    'name': sig.get('stock_name', ''),
                    'shares': shares,
                    'entry_price': entry_price,
                    'entry_date': today,
                    'exit_date': exit_date
                })
        
        # === 估值 ===
        pos_value = 0
        for pos in positions:
            code = pos['code']
            pdata = prices.get(code)
            if pdata and today in pdata['prices']:
                pos_value += pos['shares'] * pdata['prices'][today]['close']
            else:
                pos_value += pos['shares'] * pos['entry_price']
        
        total_value = cash + pos_value
        daily_values.append((today, total_value, cash, pos_value, len(positions)))
        
        max_positions = max(max_positions, len(positions))
        peak = max(peak, total_value)
        dd = (total_value - peak) / peak * 100
        max_dd = min(max_dd, dd)
    
    # === 统计 ===
    if not trades:
        print("No trades executed!")
        return None
    
    wins = sum(1 for t in trades if t['return_pct'] > 0)
    win_rate = wins / len(trades) * 100
    total_return = (daily_values[-1][1] - 1_000_000) / 1_000_000 * 100
    
    rets = [t['return_pct'] for t in trades]
    rets_sorted = sorted(rets)
    median_ret = rets_sorted[len(rets_sorted)//2]
    avg_ret = sum(rets) / len(rets)
    
    # 月度
    monthly = defaultdict(lambda: {'trades': 0, 'wins': 0, 'total_ret': 0})
    for t in trades:
        month = t['entry_date'][:7]
        monthly[month]['trades'] += 1
        if t['return_pct'] > 0:
            monthly[month]['wins'] += 1
        monthly[month]['total_ret'] += t['return_pct']
    
    print(f"\n{'='*60}")
    print(f"  PLUS B2+2 5%仓位 10日持有 模拟盘")
    print(f"{'='*60}")
    print(f"  初始资金:       ¥{1_000_000:,.0f}")
    print(f"  最终市值:       ¥{daily_values[-1][1]:,.0f}")
    print(f"  总收益率:       {total_return:+.2f}%")
    print(f"  最大回撤:       {max_dd:.2f}%")
    print(f"  交易笔数:       {len(trades)}")
    print(f"  胜率:           {win_rate:.1f}%")
    print(f"  中位收益:       {median_ret:+.2f}%")
    print(f"  平均收益:       {avg_ret:+.2f}%")
    print(f"  最大同时持仓:   {max_positions}")
    print(f"  总投入:         ¥{total_invested:,.0f}")
    print(f"  剩余现金:       ¥{cash:,.0f}")
    print(f"  跳过(资金不足): {skipped_cash}")
    print(f"{'='*60}")
    
    print(f"\n月度表现:")
    print(f"  {'月份':<8} {'笔数':>4} {'胜率':>6} {'累计收益':>10}")
    for m in sorted(monthly.keys()):
        d = monthly[m]
        wr = d['wins']/d['trades']*100 if d['trades'] else 0
        print(f"  {m:<8} {d['trades']:>4} {wr:>5.1f}% {d['total_ret']:>+9.2f}%")
    
    print(f"\n收益分布:")
    buckets = [('<-10%', float('-inf'), -10), ('-10~-5%', -10, -5), ('-5~0%', -5, 0),
               ('0~5%', 0, 5), ('5~10%', 5, 10), ('10~20%', 10, 20), ('>20%', 20, float('inf'))]
    for label, lo, hi in buckets:
        cnt = sum(1 for r in rets if lo < r <= hi)
        bar = '█' * (cnt * 50 // max(1, len(rets)))
        print(f"  {label:<10} {cnt:>3} ({cnt/len(rets)*100:>5.1f}%) {bar}")
    
    print(f"\nTop5 最佳:")
    for t in sorted(trades, key=lambda x: x['return_pct'], reverse=True)[:5]:
        print(f"  {t['code']} {t['stock_name']:<8s} {t['entry_date']}→{t['exit_date']} {t['return_pct']:+.1f}%")
    
    print(f"\nTop5 最差:")
    for t in sorted(trades, key=lambda x: x['return_pct'])[:5]:
        print(f"  {t['code']} {t['stock_name']:<8s} {t['entry_date']}→{t['exit_date']} {t['return_pct']:+.1f}%")
    
    # 打印每日净值曲线关键点
    print(f"\n净值曲线(月末):")
    last_month = ''
    for date, tv, c, pv, npos in daily_values:
        month = date[:7]
        if month != last_month:
            print(f"  {date}  ¥{tv:,.0f}  (收益{tv/1_000_000*100-100:+.1f}%)  持仓{npos}只  现金¥{c:,.0f}")
            last_month = month
    # 最后一天
    print(f"  {daily_values[-1][0]}  ¥{daily_values[-1][1]:,.0f}  (收益{daily_values[-1][1]/1_000_000*100-100:+.1f}%)  持仓{daily_values[-1][4]}只")

if __name__ == '__main__':
    run_simulation()
