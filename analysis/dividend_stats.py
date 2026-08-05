"""
红利指数超跌触发验证 - Ticket 3: 收益统计
次日开盘买入，持有 5/20/60 日，输出每类汇总 + 全池矩阵
"""
import sqlite3
import json
from collections import defaultdict
from dividend_engine import classify_indices, detect_events

DB = 'D:/hanako/investment-system/data/lixinger.db'
THRESHOLDS = {
    'drawdown_250': [0.10, 0.15, 0.20],
    'drawdown_hist': [0.15, 0.25],
    'pe_pct': [0.10, 0.20, 0.30],
    'pb_pct': [0.10, 0.20, 0.30],
    'dyr_pct': [0.80, 0.90],
}
WINDOWS = [5, 20, 60]

COND_LABELS = {
    'dd250_10': '250日回撤10%', 'dd250_15': '250日回撤15%', 'dd250_20': '250日回撤20%',
    'ddhist_15': '历史高点回撤15%', 'ddhist_25': '历史高点回撤25%',
    'pe_pct_10': 'PE分位<10%', 'pe_pct_20': 'PE分位<20%', 'pe_pct_30': 'PE分位<30%',
    'pb_pct_10': 'PB分位<10%', 'pb_pct_20': 'PB分位<20%', 'pb_pct_30': 'PB分位<30%',
    'dyr_pct_80': '股息率分位>80%', 'dyr_pct_90': '股息率分位>90%',
}


def get_fwd_returns(conn, code, trigger_dates):
    """对每个触发日，计算次日开盘买入后 5/20/60 日收益"""
    rows = conn.execute("""
        SELECT date, open FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>=?
        ORDER BY date
    """, (code, trigger_dates[0] if trigger_dates else '2016-01-01')).fetchall()
    dates = [r['date'] for r in rows]
    opens = [r['open'] for r in rows]
    date_to_idx = {d: i for i, d in enumerate(dates)}

    results = []
    for td in trigger_dates:
        ti = date_to_idx.get(td)
        if ti is None or ti + 1 >= len(dates):
            continue
        entry = opens[ti + 1]  # 次日开盘买入
        if not entry or entry <= 0:
            continue
        row = {'trigger_date': td, 'entry': entry, 'returns': {}}
        for w in WINDOWS:
            wi = ti + 1 + w
            if wi < len(dates) and opens[wi] and opens[wi] > 0:
                row['returns'][w] = (opens[wi] / entry - 1) * 100
        results.append(row)
    return results


def stat(returns_list):
    """统计：次数/胜率/平均/中位"""
    if not returns_list:
        return {'n': 0, 'win_rate': None, 'avg': None, 'median': None}
    n = len(returns_list)
    wins = sum(1 for r in returns_list if r > 0)
    avg = sum(returns_list) / n
    sorted_r = sorted(returns_list)
    median = sorted_r[n // 2] if n % 2 else (sorted_r[n//2 - 1] + sorted_r[n//2]) / 2
    return {'n': n, 'win_rate': round(wins / n * 100, 1), 'avg': round(avg, 2), 'median': round(median, 2)}


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    cats = classify_indices()
    # 排除 ETF 510880（作为指数跟踪产品，数据可能异常）
    all_indices = []
    for cat, items in cats.items():
        all_indices.extend([(cat, c, n) for c, n in items if c != '510880'])

    # 逐指数检测 + 收益
    index_results = {}  # code -> {cond: {window: stat}}
    cat_agg = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # cat->cond->window->[returns]

    for cat, code, name in all_indices:
        events = detect_events(code, conn, THRESHOLDS)
        index_results[code] = {'name': name, 'cat': cat, 'conds': {}}
        for cond_key, tdates in events.items():
            if not tdates:
                continue
            fwd = get_fwd_returns(conn, code, tdates)
            cond_stats = {}
            for w in WINDOWS:
                returns = [r['returns'].get(w) for r in fwd if w in r['returns']]
                returns = [r for r in returns if r is not None]
                s = stat(returns)
                cond_stats[w] = s
                cat_agg[cat][cond_key][w].extend(returns)
            index_results[code]['conds'][cond_key] = cond_stats
        print(f"✓ {code} {name}")

    conn.close()

    # 保存中间结果
    with open('D:/hanako/investment-system/analysis/dividend_results.json', 'w', encoding='utf-8') as f:
        json.dump({
            'index_results': index_results,
            'cat_agg': {k: {ck: {str(w): {'n': len(v), 'data': v} for w, v in wd.items()} for ck, wd in cd.items()} for k, cd in cat_agg.items()},
            'cond_labels': COND_LABELS,
        }, f, ensure_ascii=False, indent=1)

    # 打印分类汇总
    print("\n=== 分类 × 条件 × 窗口 汇总（20日窗口） ===")
    for cat, conds in cat_agg.items():
        print(f"\n【{cat}】")
        for cond, windows in conds.items():
            s = stat(windows.get(20, []))
            if s['n']:
                print(f"  {COND_LABELS[cond]:<16} n={s['n']:>3} 胜率={s['win_rate']}% 平均={s['avg']}% 中位={s['median']}%")

    # 全池合并矩阵
    print("\n=== 全红利池合并矩阵（20日窗口） ===")
    merged = defaultdict(list)
    for cat, conds in cat_agg.items():
        for cond, windows in conds.items():
            merged[cond].extend(windows.get(20, []))
    for cond, returns in sorted(merged.items()):
        s = stat(returns)
        if s['n']:
            print(f"  {COND_LABELS[cond]:<16} n={s['n']:>3} 胜率={s['win_rate']}% 平均={s['avg']}% 中位={s['median']}%")


if __name__ == '__main__':
    main()
