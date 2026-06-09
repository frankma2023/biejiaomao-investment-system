"""
口袋支点V3 对 MW B2 胜率的增量验证
比较三组：B1重合 / 口袋先行 / 无口袋支点
"""
import sqlite3, sys, os
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from analytics.mw_backtest import calc_stats

DB = "D:/hanako/investment-system/data/lixinger.db"

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# 1. 加载所有 MW 信号（有完整 H/L/B1/B2）
mw_signals = db.execute("""
    SELECT stock_code, stock_name, b1_date, b2_date, score, confidence,
           h_date, l_date, decline_pct
    FROM mw_signal_daily
    WHERE b2_date >= '2025-06-01' AND b2_date <= '2026-06-05'
    AND b1_date IS NOT NULL
    ORDER BY b2_date
""").fetchall()
print(f"MW signals with B1: {len(mw_signals)}")

# 2. 加载所有口袋支点V3信号
pp_signals = db.execute("""
    SELECT date, stock_code, pivot_type, b1_overlap
    FROM pocket_pivot_daily
    WHERE date >= '2025-06-01' AND date <= '2026-06-05'
    ORDER BY date
""").fetchall()

# 按 (stock_code, date) 建索引
pp_index = defaultdict(list)
for r in pp_signals:
    pp_index[(r['stock_code'], r['date'])].append(r)
print(f"Pocket pivot signals: {len(pp_signals)}")

# 3. 分类 MW 信号
group1 = []  # B1重合：B1日 = 口袋支点日
group2 = []  # 口袋先行：B1日前3~5天有口袋支点
group3 = []  # 无口袋支点：B1前后都没有口袋支点

for mw in mw_signals:
    code = mw['stock_code']
    b1 = mw['b1_date']
    
    # 检查 B1 当天是否有口袋支点
    pp_on_b1 = pp_index.get((code, b1), [])
    
    if pp_on_b1:
        group1.append(mw)
        continue
    
    # 检查 B1 前 3~5 天是否有口袋支点
    # 需要知道交易日历
    pp_before = []
    for lookback in [3, 4, 5]:
        # 简单处理：用日期减法近似
        from datetime import datetime, timedelta
        d = datetime.strptime(b1, '%Y-%m-%d') - timedelta(days=lookback)
        check_date = d.strftime('%Y-%m-%d')
        found = pp_index.get((code, check_date), [])
        if found:
            pp_before.extend(found)
    
    if pp_before:
        group2.append(mw)
    else:
        group3.append(mw)

print(f"\n分组:")
print(f"  B1重合:        {len(group1)}")
print(f"  口袋先行(3~5天): {len(group2)}")
print(f"  无口袋支点:     {len(group3)}")

# 4. 计算 B2 之后的 forward returns
# 批量加载 K 线
codes = list(set(s['stock_code'] for s in mw_signals))
price_cache = {}
for code in codes:
    rows = db.execute("""
        SELECT date, close FROM daily_kline
        WHERE stock_code=? AND date >= '2025-06-01' AND date <= '2026-07-15'
        ORDER BY date
    """, (code,)).fetchall()
    price_cache[code] = {r['date']: r['close'] for r in rows}

def get_forward_rets(signals):
    """计算 B2 日后的 5/10/20 日 forward returns"""
    ret_5, ret_10, ret_20 = [], [], []
    for s in signals:
        code = s['stock_code']
        b2 = s['b2_date']
        prices = price_cache.get(code, {})
        dates = sorted(prices.keys())
        if b2 not in prices: continue
        entry = prices[b2]
        try: idx = dates.index(b2)
        except: continue
        for h in [5, 10, 20]:
            fut = idx + h
            if fut < len(dates):
                r = (prices[dates[fut]] - entry) / entry * 100
                {5: ret_5, 10: ret_10, 20: ret_20}[h].append(r)
    return ret_5, ret_10, ret_20

print(f"\n{'='*70}")
print(f"  MW B2 胜率：口袋支点V3 增量验证")
print(f"{'='*70}")

for label, group in [("B1重合 (★最强)", group1), ("口袋先行 (3~5天)", group2), ("无口袋支点", group3)]:
    r5, r10, r20 = get_forward_rets(group)
    s5, s10, s20 = calc_stats(r5), calc_stats(r10), calc_stats(r20)
    print(f"\n{label}: {len(r5)} 有效信号")
    print(f"  5d:  胜率{s5['win_rate']:.1f}% 中位{s5['median_return']:+.2f}% 平均{s5['avg_return']:+.2f}%")
    print(f"  10d: 胜率{s10['win_rate']:.1f}% 中位{s10['median_return']:+.2f}% 平均{s10['avg_return']:+.2f}%")
    print(f"  20d: 胜率{s20['win_rate']:.1f}% 中位{s20['median_return']:+.2f}% 平均{s20['avg_return']:+.2f}%")

# 5. 口袋先行组细分：看口袋支点到 B1 的天数
print(f"\n=== 口袋先行组：按间隔天数细分 ===")
from datetime import datetime, timedelta
for gap in [3, 4, 5]:
    sub = []
    for mw in group2:
        b1 = mw['b1_date']
        check_d = datetime.strptime(b1, '%Y-%m-%d') - timedelta(days=gap)
        check_date = check_d.strftime('%Y-%m-%d')
        if pp_index.get((mw['stock_code'], check_date), []):
            sub.append(mw)
    if sub:
        r5, r10, r20 = get_forward_rets(sub)
        s10 = calc_stats(r10)
        print(f"  间隔{gap}日 ({len(sub)}个): 10d胜率{s10['win_rate']:.1f}% 中位{s10['median_return']:+.2f}%")

db.close()
