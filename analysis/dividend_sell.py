"""
红利指数卖出信号回测 - 防损视角
信号: MA20/MA60跌破(0/1) + PE分位高危险区(>70/80/90%) + PB分位高危险区 + 股息率分位低危险区(<10/20/30%)
衡量: 信号触发后 20/60 日内最大回撤; 出现 >10%/>15% 回撤的概率(踩雷率)
窗口: 2016-2026 全周期
"""
import sqlite3
import json
from collections import defaultdict

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

def compute_ma(closes, period):
    ma = [None] * len(closes)
    s = 0
    for i in range(len(closes)):
        s += closes[i]
        if i >= period:
            s -= closes[i - period]
        if i >= period - 1:
            ma[i] = s / period
    return ma

def analyze(conn, code):
    """返回 {signal_key: [触发日索引]}"""
    rows = conn.execute("""
        SELECT date, close FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>='2015-06-01'
        ORDER BY date
    """, (code,)).fetchall()
    if len(rows) < 400:
        return None
    dates = [r['date'] for r in rows]
    closes = [r['close'] for r in rows]
    n = len(closes)

    # 估值
    val_rows = conn.execute("""
        SELECT date, pe_ttm_pct, pb_pct, dyr_pct FROM index_fundamental_daily
        WHERE stock_code=? ORDER BY date
    """, (code,)).fetchall()
    val_map = {r['date']: (r['pe_ttm_pct'], r['pb_pct'], r['dyr_pct']) for r in val_rows}

    ma20 = compute_ma(closes, 20)
    ma60 = compute_ma(closes, 60)

    signals = defaultdict(list)
    last = defaultdict(lambda: -999)

    for i in range(60, n):
        # MA 跌破信号: 今日收 < MA, 昨日收 >= MA (首次跌破)
        if ma20[i] and ma20[i-1] and closes[i] < ma20[i] and closes[i-1] >= ma20[i-1]:
            if i - last['ma20'] >= 20:
                signals['ma20'].append(i)
                last['ma20'] = i
        if ma60[i] and ma60[i-1] and closes[i] < ma60[i] and closes[i-1] >= ma60[i-1]:
            if i - last['ma60'] >= 20:
                signals['ma60'].append(i)
                last['ma60'] = i

        # 估值危险区信号
        v = val_map.get(dates[i])
        if v:
            pe_pct, pb_pct, dyr_pct = v
            for t in [70, 80, 90]:
                key = f'pe_hi_{t}'
                if pe_pct is not None and pe_pct * 100 > t:
                    if i - last[key] >= 20:
                        signals[key].append(i)
                        last[key] = i
            for t in [70, 80, 90]:
                key = f'pb_hi_{t}'
                if pb_pct is not None and pb_pct * 100 > t:
                    if i - last[key] >= 20:
                        signals[key].append(i)
                        last[key] = i
            for t in [10, 20, 30]:
                key = f'dyr_lo_{t}'
                if dyr_pct is not None and dyr_pct * 100 < t:
                    if i - last[key] >= 20:
                        signals[key].append(i)
                        last[key] = i

    return {'dates': dates, 'closes': closes, 'signals': signals}

def measure_drawdown(closes, start_idx, window):
    """从 start_idx 次日开始，window 日内最大回撤（%）"""
    if start_idx + 1 >= len(closes):
        return None
    peak = closes[start_idx]
    max_dd = 0
    for j in range(start_idx + 1, min(len(closes), start_idx + 1 + window)):
        dd = (peak - closes[j]) / peak * 100
        if dd > max_dd:
            max_dd = dd
        if closes[j] > peak:
            peak = closes[j]
    return max_dd

def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    indices = load_indices()

    # 结果: code -> signal -> {w20: [dd], w60: [dd]}
    results = {}
    for cat, code, name in indices:
        r = analyze(conn, code)
        if not r:
            continue
        closes = r['closes']
        sig_results = {}
        for sig_key, idxs in r['signals'].items():
            if not idxs:
                continue
            dd20 = [measure_drawdown(closes, i, 20) for i in idxs]
            dd60 = [measure_drawdown(closes, i, 60) for i in idxs]
            dd20 = [d for d in dd20 if d is not None]
            dd60 = [d for d in dd60 if d is not None]
            sig_results[sig_key] = {'n': len(idxs), 'dd20': dd20, 'dd60': dd60}
        results[code] = {'name': name, 'cat': cat, 'signals': sig_results}
        print(f"✓ {code} {name}")

    conn.close()

    with open('D:/hanako/investment-system/analysis/dividend_sell_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)

    # 汇总打印
    SIG_LABELS = {
        'ma20': '跌破MA20', 'ma60': '跌破MA60',
        'pe_hi_70': 'PE分位>70%', 'pe_hi_80': 'PE分位>80%', 'pe_hi_90': 'PE分位>90%',
        'pb_hi_70': 'PB分位>70%', 'pb_hi_80': 'PB分位>80%', 'pb_hi_90': 'PB分位>90%',
        'dyr_lo_10': '股息率分位<10%', 'dyr_lo_20': '股息率分位<20%', 'dyr_lo_30': '股息率分位<30%',
    }
    merged = defaultdict(lambda: {'dd20': [], 'dd60': []})
    for code, info in results.items():
        for sig, s in info['signals'].items():
            merged[sig]['dd20'].extend(s['dd20'])
            merged[sig]['dd60'].extend(s['dd60'])

    print("\n=== 全池合并: 信号 → 未来回撤（60日窗口） ===")
    for sig, s in merged.items():
        dd = s['dd60']
        if not dd:
            continue
        n = len(dd)
        avg_dd = sum(dd) / n
        rate10 = sum(1 for d in dd if d > 10) / n * 100
        rate15 = sum(1 for d in dd if d > 15) / n * 100
        print(f"  {SIG_LABELS.get(sig, sig):<14} n={n:>3} 平均回撤={avg_dd:>5.1f}% 踩雷率(>10%)={rate10:>5.1f}% 深踩率(>15%)={rate15:>5.1f}%")

if __name__ == '__main__':
    main()
