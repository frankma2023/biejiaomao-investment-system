# -*- coding: utf-8 -*-
"""
自由现金流指数买点/卖点回测（980092 国证自由现金流 / 932365 中证现金流）
==========================================================================
信号口径对齐红利研究：
- 价格类（2012/2013 起，13年）：250日回撤买点、动量卖点
- 估值类（2024-09 起，约2年短窗口）：PE/PB/股息率分位（诚实标注）
- 20日去重、次日收盘买入、20/60日收益、随机基准对照
"""
import sys, os, sqlite3, statistics, random
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row

INDICES = [('980092', '国证自由现金流'), ('932365', '中证现金流')]
random.seed(42)

def load_kl(code):
    rows = db.execute("SELECT date, close FROM index_daily_kline WHERE stock_code=? AND kline_type='normal' ORDER BY date", (code,)).fetchall()
    return [dict(r) for r in rows]

def load_fund(code):
    rows = db.execute("SELECT date, pe_ttm_pct, pb_pct, dyr_pct FROM index_fundamental_daily WHERE stock_code=? ORDER BY date", (code,)).fetchall()
    return {r['date']: r for r in rows}

def analyze(dates, closes, fund, pred, label, need_fund=False, cooldown=20):
    n = len(closes)
    events = []
    last = -999
    for i in range(250, n - 61):
        d = dates[i]
        if need_fund and (d not in fund or fund[d]['pe_ttm_pct'] is None):
            continue
        if i - last < cooldown:
            continue
        row = fund.get(d) if need_fund else None
        if not pred(i, d, row, closes):
            continue
        last = i
        buy = closes[i + 1]
        r20 = closes[i + 21] / buy - 1
        r60 = closes[i + 61] / buy - 1
        events.append((r20, r60))
    if len(events) < 3:
        return f'{label:<32} {len(events):>3}次 样本不足'
    w20 = [e[0] for e in events]
    w60 = [e[1] for e in events]
    return (f'{label:<32} {len(events):>3}次 | 20日胜率{sum(1 for v in w20 if v > 0)/len(w20)*100:>5.1f}% 中位{statistics.median(w20)*100:>+6.2f}%'
            f' | 60日胜率{sum(1 for v in w60 if v > 0)/len(w60)*100:>5.1f}% 中位{statistics.median(w60)*100:>+6.2f}%')

for code, name in INDICES:
    data = load_kl(code)
    dates = [r['date'] for r in data]
    closes = [r['close'] for r in data]
    fund = load_fund(code)
    n = len(closes)
    years = n / 244
    ann = (closes[-1] / closes[0]) ** (1 / years) - 1
    print(f'\n===== {code} {name} =====')
    print(f'K线 {n} 天 ({dates[0]}~{dates[-1]}) · 年化 {ann*100:.1f}% · 基本面自 {min(fund) if fund else "无"} (短窗口)')
    print()
    print('--- 买点 ---')
    print(analyze(dates, closes, fund, lambda i, d, r, c: (max(c[i-249:i+1]) - c[i]) / max(c[i-249:i+1]) * 100 >= 10, '250日回撤 ≥10%'))
    print(analyze(dates, closes, fund, lambda i, d, r, c: (max(c[i-249:i+1]) - c[i]) / max(c[i-249:i+1]) * 100 >= 15, '250日回撤 ≥15%'))
    print(analyze(dates, closes, fund, lambda i, d, r, c: (max(c[i-249:i+1]) - c[i]) / max(c[i-249:i+1]) * 100 >= 20, '250日回撤 ≥20%'))
    if fund:
        print(analyze(dates, closes, fund, lambda i, d, r, c: r is not None and r['pe_ttm_pct'] is not None and r['pe_ttm_pct'] * 100 < 33, 'PE分位<33% (短窗口)', need_fund=True))
        print(analyze(dates, closes, fund, lambda i, d, r, c: r is not None and r['dyr_pct'] is not None and r['dyr_pct'] * 100 > 66, '股息率分位>66% (短窗口)', need_fund=True))
        print(analyze(dates, closes, fund, lambda i, d, r, c: r is not None and r['pe_ttm_pct'] is not None and r['pb_pct'] is not None and r['pe_ttm_pct'] * 100 < 33 and r['pb_pct'] * 100 < 33, 'PE+PB双低 (短窗口)', need_fund=True))
    print()
    print('--- 卖点（动量，出现后未来收益）---')
    print(analyze(dates, closes, fund, lambda i, d, r, c: (c[i] - c[i-5]) / c[i-5] * 100 >= 10, '20日涨幅>10% → 60日'))
    print(analyze(dates, closes, fund, lambda i, d, r, c: (c[i] - c[i-21]) / c[i-21] * 100 >= 15, '60日涨幅>15% → 60日'))
    print(analyze(dates, closes, fund, lambda i, d, r, c: (c[i] - c[i-5]) / c[i-5] * 100 >= 15, '20日涨幅>15% → 60日'))
    print(analyze(dates, closes, fund, lambda i, d, r, c: random.random() < 0.05, '随机基准'))
db.close()
