"""
2026年红利指数: 高点→回撤→反弹事件链分析
对每个指数: 
  1. 找2026年高点（250日滚动最高确认）
  2. 高点后回撤达到 10%/15%/20% 的日期
  3. 回撤后的反弹：最低点 → 反弹幅度（至反弹高点/固定窗口）
"""
import sqlite3
import json

DB = 'D:/hanako/investment-system/data/lixinger.db'

def load_indices():
    """从 dividend_engine 的分类结果加载"""
    import sys
    sys.path.insert(0, 'D:/hanako/investment-system/analysis')
    from dividend_engine import classify_indices
    cats = classify_indices()
    result = []
    for cat, items in cats.items():
        for code, name in items:
            if code != '510880':
                result.append((cat, code, name))
    return result

def get_kline(conn, code, start='2024-01-01'):
    rows = conn.execute("""
        SELECT date, close, open, high, low FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>=?
        ORDER BY date
    """, (code, start)).fetchall()
    return rows

def find_2026_events(rows):
    """
    找2026年事件链: 高点 → 回撤x% → 反弹
    返回事件列表: {high_date, high_price, dd_date, dd_pct, low_date, low_price, bounce_date, bounce_pct}
    """
    # 找2026年起点
    start_2026 = None
    for i, r in enumerate(rows):
        if r['date'] >= '2026-01-01':
            start_2026 = i
            break
    if start_2026 is None:
        return []
    
    # 计算250日滚动最高（用2024-2026数据确保250窗口完整）
    n = len(rows)
    events = []
    
    # 扫描2026年的局部高点（前面有足够历史）
    i = start_2026
    while i < n - 5:
        r = rows[i]
        # 250日滚动最高
        w_start = max(0, i - 249)
        window = rows[w_start:i+1]
        roll_high = max(x['close'] for x in window)
        if r['close'] >= roll_high * 0.995:  # 接近250日高点
            high_price = r['close']
            high_date = r['date']
            # 从高点向后找回撤
            max_dd = 0
            low_idx = i
            for j in range(i+1, min(n, i+250)):
                dd = (high_price - rows[j]['close']) / high_price
                if dd > max_dd:
                    max_dd = dd
                    low_idx = j
            if max_dd >= 0.10:  # 至少回撤10%
                low_price = rows[low_idx]['close']
                low_date = rows[low_idx]['date']
                # 从低点向后找反弹（60日窗口）
                bounce_peak = low_price
                bounce_idx = low_idx
                for j in range(low_idx+1, min(n, low_idx+61)):
                    if rows[j]['close'] > bounce_peak:
                        bounce_peak = rows[j]['close']
                        bounce_idx = j
                bounce_pct = (bounce_peak - low_price) / low_price * 100
                # 反弹需要至少5%才算有效
                if bounce_pct >= 5:
                    events.append({
                        'high_date': high_date, 'high_price': round(high_price,2),
                        'dd_pct': round(max_dd*100,1), 'dd_date': low_date,
                        'low_price': round(low_price,2),
                        'bounce_date': rows[bounce_idx]['date'],
                        'bounce_pct': round(bounce_pct,1),
                        'days_to_bounce': bounce_idx - low_idx,
                    })
            i = low_idx + 1  # 跳到回撤低点之后，避免重复
        else:
            i += 1
    return events

def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    indices = load_indices()
    
    all_events = []
    for cat, code, name in indices:
        rows = get_kline(conn, code)
        evs = find_2026_events(rows)
        for ev in evs:
            ev['code'] = code
            ev['name'] = name
            ev['cat'] = cat
            all_events.append(ev)
        print(f"✓ {code} {name}: {len(evs)} 个事件")
    
    conn.close()
    
    # 按分类保存
    with open('D:/hanako/investment-system/analysis/dividend_2026_events.json', 'w', encoding='utf-8') as f:
        json.dump(all_events, f, ensure_ascii=False, indent=1)
    
    print(f"\n共 {len(all_events)} 个事件")
    # 按分类统计
    from collections import defaultdict
    by_cat = defaultdict(list)
    for ev in all_events:
        by_cat[ev['cat']].append(ev)
    for cat, evs in by_cat.items():
        print(f"\n【{cat}】{len(evs)} 个事件")
        for ev in evs[:5]:
            print(f"  {ev['name']}: 高点{ev['high_date']}({ev['high_price']}) → 回撤{ev['dd_pct']}%({ev['dd_date']}) → 反弹{ev['bounce_pct']}%({ev['bounce_date']})")

if __name__ == '__main__':
    main()
