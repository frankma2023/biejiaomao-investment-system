# -*- coding: utf-8 -*-
"""
港股红利买点规律研究
====================
A. 估值分位（930914/930839 指数，2016 起 10 年）：PE/PB/股息率分位买点
B. 回撤买点（指数价格 2016 起 + ETF 自身短窗口）
C. 股息率绝对值 / 息差（港股股息率 - 10年国债）
口径：20日去重、次日收盘买入、20/60日收益、随机基准对照
"""
import sys, os, sqlite3, statistics, random
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row
random.seed(42)

def load_kl(code, start='2016-01-01'):
    rows = db.execute("SELECT date, close FROM index_daily_kline WHERE stock_code=? AND kline_type='normal' AND date>=? ORDER BY date", (code, start)).fetchall()
    return [dict(r) for r in rows]

def load_fund(code):
    rows = db.execute("SELECT date, pe_ttm_pct, pb_pct, dyr, dyr_pct FROM index_fundamental_daily WHERE stock_code=? ORDER BY date", (code,)).fetchall()
    return {r['date']: r for r in rows}

def bond_y10(d):
    r = db.execute("SELECT y10 FROM bond_yield_daily WHERE date<=? AND y10 IS NOT NULL ORDER BY date DESC LIMIT 1", (d,)).fetchone()
    return r[0] if r else None

def analyze(dates, closes, fund, pred, label, need_fund=False, cooldown=20, min_events=3):
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
        r20 = closes[i + 21] / buy - 1 if i + 21 < n else None
        r60 = closes[i + 61] / buy - 1 if i + 61 < n else None
        events.append((r20, r60))
    if len(events) < min_events:
        return f'{label:<34} {len(events):>3}次 样本不足'
    w20 = [e[0] for e in events if e[0] is not None]
    w60 = [e[1] for e in events if e[1] is not None]
    return (f'{label:<34} {len(events):>3}次 | 20日胜率{sum(1 for v in w20 if v > 0)/len(w20)*100:>5.1f}% 中位{statistics.median(w20)*100:>+6.2f}%'
            f' | 60日胜率{sum(1 for v in w60 if v > 0)/len(w60)*100:>5.1f}% 中位{statistics.median(w60)*100:>+6.2f}%')

for code, name in [('930914', '港股通高股息(513820)'), ('930839', '港股通高股息精选(159691)')]:
    data = load_kl(code)
    dates = [r['date'] for r in data]
    closes = [r['close'] for r in data]
    fund = load_fund(code)
    print(f'\n===== {code} {name} =====')
    print(f'指数 {len(dates)} 天 ({dates[0]}~{dates[-1]})')
    print('--- 回撤买点（指数价格）---')
    print(analyze(dates, closes, fund, lambda i, d, r, c: (max(c[i-249:i+1]) - c[i]) / max(c[i-249:i+1]) * 100 >= 10, '回撤 ≥10%'))
    print(analyze(dates, closes, fund, lambda i, d, r, c: (max(c[i-249:i+1]) - c[i]) / max(c[i-249:i+1]) * 100 >= 15, '回撤 ≥15%'))
    print(analyze(dates, closes, fund, lambda i, d, r, c: (max(c[i-249:i+1]) - c[i]) / max(c[i-249:i+1]) * 100 >= 20, '回撤 ≥20%'))
    print('--- 估值分位买点 ---')
    print(analyze(dates, closes, fund, lambda i, d, r, c: r is not None and r['pe_ttm_pct'] is not None and r['pe_ttm_pct'] * 100 < 33, 'PE分位<33%', need_fund=True))
    print(analyze(dates, closes, fund, lambda i, d, r, c: r is not None and r['pb_pct'] is not None and r['pb_pct'] * 100 < 33, 'PB分位<33%', need_fund=True))
    print(analyze(dates, closes, fund, lambda i, d, r, c: r is not None and r['dyr_pct'] is not None and r['dyr_pct'] * 100 > 66, '股息率分位>66%', need_fund=True))
    print(analyze(dates, closes, fund, lambda i, d, r, c: r is not None and r['pe_ttm_pct'] is not None and r['pb_pct'] is not None and r['pe_ttm_pct'] * 100 < 33 and r['pb_pct'] * 100 < 33, 'PE+PB双低', need_fund=True))
    print('--- 股息率绝对值/息差 ---')
    print(analyze(dates, closes, fund, lambda i, d, r, c: r is not None and r['dyr'] is not None and r['dyr'] * 100 >= 6, '股息率≥6%', need_fund=True))
    print(analyze(dates, closes, fund, lambda i, d, r, c: r is not None and r['dyr'] is not None and r['dyr'] * 100 >= 7, '股息率≥7%', need_fund=True))
    print(analyze(dates, closes, fund, lambda i, d, r, c: r is not None and r['dyr'] is not None and bond_y10(d) and (r['dyr'] * 100 - bond_y10(d)) >= 4, '息差≥4%(股息率-国债)', need_fund=True))
    print(analyze(dates, closes, fund, lambda i, d, r, c: random.random() < 0.05, '随机基准'))

# ETF 自身短窗口回撤（诚实标注窗口短）
print('\n===== ETF 自身回撤（窗口短 2-3.3年，样本有限）=====')
for code, name in [('513820', '港股通高股息'), ('159545', '恒生高息低波'), ('159691', '港股通高股息精选'), ('513630', '标普港股红利低波')]:
    rows = db.execute("SELECT date, close FROM hk_etf_daily WHERE stock_code=? ORDER BY date", (code,)).fetchall()
    dates = [r['date'] for r in rows]
    closes = [r['close'] for r in rows]
    if len(closes) < 250:
        print(f'{code} {name}: 数据不足 {len(closes)} 天')
        continue
    print(f'--- {code} {name} ({len(closes)}天) ---')
    print(analyze(dates, closes, {}, lambda i, d, r, c: (max(c[i-249:i+1]) - c[i]) / max(c[i-249:i+1]) * 100 >= 10, '回撤 ≥10%', min_events=2))
    print(analyze(dates, closes, {}, lambda i, d, r, c: (max(c[i-249:i+1]) - c[i]) / max(c[i-249:i+1]) * 100 >= 15, '回撤 ≥15%', min_events=2))
db.close()
