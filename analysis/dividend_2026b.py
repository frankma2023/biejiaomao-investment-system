"""
2026年红利指数事件链细化版:
- 精确到"高点日期 → 回撤10%/15%/20%各在何时 → 低点 → 反弹进行中/已完成"
"""
import sqlite3
import json

DB = 'D:/hanako/investment-system/data/lixinger.db'

def load_indices():
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

def analyze(conn, code):
    rows = conn.execute("""
        SELECT date, close, open FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>='2024-06-01'
        ORDER BY date
    """, (code,)).fetchall()
    if len(rows) < 200:
        return None
    dates = [r['date'] for r in rows]
    closes = [r['close'] for r in rows]

    # 2026年起点
    s26 = next((i for i, d in enumerate(dates) if d >= '2026-01-01'), None)
    if s26 is None:
        return None

    n = len(rows)
    # 找2026年唯一的主要高点（250日滚动最高）
    high_idx = None
    high_val = 0
    for i in range(s26, n):
        w = max(0, i-249)
        rh = max(closes[w:i+1])
        if closes[i] >= rh * 0.998 and closes[i] > high_val:
            high_val = closes[i]
            high_idx = i

    if high_idx is None:
        return None

    # 高点后回撤10%/15%/20%日期
    dd_dates = {}
    cur_max_dd = 0
    low_idx = high_idx
    for j in range(high_idx+1, n):
        dd = (high_val - closes[j]) / high_val * 100
        if dd > cur_max_dd:
            cur_max_dd = dd
            low_idx = j
        for t in [10, 15, 20]:
            if dd >= t and t not in dd_dates:
                dd_dates[t] = dates[j]

    low_val = closes[low_idx]
    low_date = dates[low_idx]

    # 反弹分析：低点之后
    bounce_peak = low_val
    bounce_idx = low_idx
    for j in range(low_idx+1, n):
        if closes[j] > bounce_peak:
            bounce_peak = closes[j]
            bounce_idx = j
    bounce_pct = (bounce_peak - low_val) / low_val * 100

    # 是否仍在反弹中：最后一日的收盘是否接近反弹高点（未跌破反弹高点的95%）
    last_close = closes[-1]
    still_bouncing = last_close >= bounce_peak * 0.95

    return {
        'high_date': dates[high_idx], 'high_price': round(high_val, 2),
        'dd_dates': {k: v for k, v in dd_dates.items()},
        'max_dd': round(cur_max_dd, 1),
        'low_date': low_date, 'low_price': round(low_val, 2),
        'bounce_peak_date': dates[bounce_idx], 'bounce_peak': round(bounce_peak, 2),
        'bounce_pct': round(bounce_pct, 1),
        'still_bouncing': still_bouncing,
        'last_date': dates[-1], 'last_close': round(last_close, 2),
        'days_from_low_to_peak': bounce_idx - low_idx,
    }

def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    indices = load_indices()

    results = []
    for cat, code, name in indices:
        r = analyze(conn, code)
        if r and r['max_dd'] >= 8:  # 只保留有实质回撤的
            r.update({'code': code, 'name': name, 'cat': cat})
            results.append(r)
            print(f"✓ {code} {name}")
    conn.close()

    with open('D:/hanako/investment-system/analysis/dividend_2026_events.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)

    # 按分类打印
    from collections import defaultdict
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r['cat']].append(r)

    for cat, items in by_cat.items():
        print(f"\n{'='*70}\n【{cat}】{len(items)} 只")
        for r in items:
            dd_str = ' '.join(f"{t}%:{d}" for t, d in sorted(r['dd_dates'].items()))
            bounce_state = '🔴反弹中' if r['still_bouncing'] else '⚪已结束'
            print(f"\n  {r['name']}({r['code']})")
            print(f"    高点: {r['high_date']} @ {r['high_price']}")
            print(f"    回撤档位: {dd_str if dd_str else '未达10%'}")
            print(f"    最大回撤: {r['max_dd']}% @ {r['low_date']} ({r['low_price']})")
            print(f"    反弹: {r['bounce_pct']}% → {r['bounce_peak_date']} ({r['bounce_peak']}) {bounce_state} 历时{r['days_from_low_to_peak']}日")

if __name__ == '__main__':
    main()
