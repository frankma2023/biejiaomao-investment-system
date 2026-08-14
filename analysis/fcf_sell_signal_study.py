# -*- coding: utf-8 -*-
"""
红利指数 卖出信号回测研究
=========================
目的：找到有统计依据的卖出信号（触发后未来收益显著低于随机基准）

数据：
  - H00922 中证红利全收益（2018 起，2092 条）——收益计算基准
  - 000922 中证红利基本面（index_fundamental_daily，2016 起）——估值分位来源

方法论：
  - 信号触发 → 次日收盘卖出 → 统计未来 20/60/120 日收益（H00922 全收益口径）
  - 卖出有效 = 未来收益显著为负 或 显著低于随机基准（避开下跌）
  - 20 日去重（同一信号区间只算一次）

候选信号：
  S1 PE分位>80% / S2 PE分位>90% / S3 PB分位>80% / S4 PB分位>90%
  S5 股息率分位<10% / S6 股息率分位<20%
  S7 20日涨幅>10% / S8 60日涨幅>15% / S9 250日涨幅>30%
  S10 PE分位>80% 且 股息率分位<20%（双高警示）
"""
import sys, os, sqlite3
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')

DB = r'data\lixinger.db'
COOLDOWN = 20
WINDOWS = [20, 60, 120]

def load():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    # H00922 全收益
    tri = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code='H00922' ORDER BY date").fetchall()
    # 000922 基本面
    fund = db.execute("""SELECT date, pe_ttm_pct, pb_pct, dyr_pct FROM index_fundamental_daily
        WHERE stock_code='000922' AND pe_ttm_pct IS NOT NULL ORDER BY date""").fetchall()
    db.close()
    return [dict(r) for r in tri], [dict(r) for r in fund]

def stat(rs):
    if not rs:
        return {'n': 0}
    n = len(rs)
    s = sorted(rs)
    neg = sum(1 for r in rs if r < 0)
    return {
        'n': n,
        'win': round(sum(1 for r in rs if r > 0) / n * 100, 1),
        'neg': round(neg / n * 100, 1),          # 下跌概率（卖出有效性核心指标）
        'avg': round(sum(rs) / n, 2),
        'med': round(s[n//2] if n % 2 else (s[n//2-1]+s[n//2])/2, 2),
        'p25': round(s[n//4], 2),
    }

def main():
    tri, fund = load()
    tri_dates = [r['date'] for r in tri]
    tri_close = [r['close'] for r in tri]
    tri_idx = {d: i for i, d in enumerate(tri_dates)}
    print(f'H00922: {len(tri)} 条 ({tri_dates[0]} ~ {tri_dates[-1]})')
    print(f'000922 基本面: {len(fund)} 条 ({fund[0]["date"]} ~ {fund[-1]["date"]})')

    # 随机基准（2018 起，与信号同区间）
    import random
    random.seed(42)
    base = {w: [] for w in WINDOWS}
    valid = list(range(260, len(tri_close) - 120))
    samples = random.sample(valid, min(2000, len(valid)))
    for i in samples:
        for w in WINDOWS:
            if i + w < len(tri_close):
                base[w].append((tri_close[i+w] / tri_close[i] - 1) * 100)
    print('\n=== 随机基准（任意日持有）===')
    for w in WINDOWS:
        s = stat(base[w])
        print(f'  {w}日: n={s["n"]} 胜率={s["win"]}% 下跌概率={s["neg"]}% 中位={s["med"]}% p25={s["p25"]}%')

    # 信号检测
    # 预构建基本面按日期排序 + 二分查找（估值信号用）
    fund_dates = [f['date'] for f in fund]
    import bisect
    def nearest_fund(d):
        pos = bisect.bisect_right(fund_dates, d) - 1
        return fund[pos] if pos >= 0 else None

    signals_def = {
        'S1 PE分位>80%': lambda v, i: v['pe_ttm_pct'] > 0.80,
        'S2 PE分位>90%': lambda v, i: v['pe_ttm_pct'] > 0.90,
        'S3 PB分位>80%': lambda v, i: v['pb_pct'] > 0.80,
        'S4 PB分位>90%': lambda v, i: v['pb_pct'] > 0.90,
        'S5 股息率分位<10%': lambda v, i: v['dyr_pct'] < 0.10,
        'S6 股息率分位<20%': lambda v, i: v['dyr_pct'] < 0.20,
        'S7 20日涨幅>10%': lambda v, i: i >= 20 and (tri_close[i]/tri_close[i-20]-1)*100 > 10,
        'S8 60日涨幅>15%': lambda v, i: i >= 60 and (tri_close[i]/tri_close[i-60]-1)*100 > 15,
        'S9 250日涨幅>30%': lambda v, i: i >= 250 and (tri_close[i]/tri_close[i-250]-1)*100 > 30,
        'S10 PE>80%且DYR<20%': lambda v, i: v['pe_ttm_pct'] > 0.80 and v['dyr_pct'] < 0.20,
    }

    print('\n=== 卖出信号回测矩阵 ===')
    print('%-20s%-5s%-8s%-8s%-8s%-8s%-8s%-8s' % ('信号', '触发', '20日中位', '20日下跌', '60日中位', '60日下跌', '120日中位', '120日下跌'))
    results = {}
    for name, fn in signals_def.items():
        events = []
        last = -999
        for i in range(250, len(tri_dates)):
            d = tri_dates[i]
            v = nearest_fund(d) if ('分位' in name or 'PE' in name) else None
            if fn(v, i) and i - last >= COOLDOWN:
                events.append(i)
                last = i
        # 收益：次日卖出后 20/60/120 日（信号日收盘价 → 未来）
        out = {w: [] for w in WINDOWS}
        for i in events:
            for w in WINDOWS:
                if i + w < len(tri_close):
                    out[w].append((tri_close[i+w] / tri_close[i] - 1) * 100)
        results[name] = (len(events), out)
        s20, s60, s120 = stat(out[20]), stat(out[60]), stat(out[120])
        print('%-20s%-5d%-8s%-8s%-8s%-8s%-8s%-8s' % (
            name, len(events),
            (str(s20['med'])+'%' if s20.get('med') is not None else '—'),
            (str(s20['neg'])+'%' if s20.get('neg') is not None else '—'),
            (str(s60['med'])+'%' if s60.get('med') is not None else '—'),
            (str(s60['neg'])+'%' if s60.get('neg') is not None else '—'),
            (str(s120['med'])+'%' if s120.get('med') is not None else '—'),
            (str(s120['neg'])+'%' if s120.get('neg') is not None else '—')))

    # 对比基准：判断有效性
    print('\n=== 有效性判断（vs 随机基准 20日: 胜率%.1f%% 中位%.2f%% | 60日: 胜率%.1f%% 中位%.2f%%）===' % (
        stat(base[20])['win'], stat(base[20])['med'], stat(base[60])['win'], stat(base[60])['med']))
    for name, (n, out) in results.items():
        s = stat(out[60])
        if n >= 5 and s['med'] is not None:
            verdict = '🔴 有效卖点' if s['med'] < stat(base[60])['med'] - 1.5 else ('🟡 弱' if s['med'] < stat(base[60])['med'] else '⚪ 无效')
            print(f'  {name}: 60日中位 {s["med"]}% vs 基准 {stat(base[60])["med"]}% → {verdict}')

if __name__ == '__main__':
    main()
