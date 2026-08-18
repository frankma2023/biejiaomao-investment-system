# -*- coding: utf-8 -*-
"""
红利温度计买卖指导能力回测
===========================
逐日计算温度计序列（2018-07 起，等 120 日前置窗口暖机），
检验：温度读数高低 → 未来 20/60 日收益（次日收盘买入，20日去重）

诚实性：
- 回测的是当前启发式权重（0.35/0.25/0.40）的特定温度计
- 每 3 个交易日采样一次（温度计是慢变量，采样不影响信号）
"""
import sys, os, sqlite3, statistics, time
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
from src.scanners.red_dividend_metrics import compute_all

t0 = time.time()
db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row

# 交易日序列（2018-07-01 起）
rows = db.execute("""
    SELECT date, close FROM index_daily_kline
    WHERE stock_code='000922' AND kline_type='normal' AND date>='2018-07-01' ORDER BY date
""").fetchall()
dates = [r['date'] for r in rows]
closes = [r['close'] for r in rows]
n = len(dates)

# 全收益收盘（信号回测用 H00922）
tri = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code='H00922' AND date>='2018-07-01' ORDER BY date").fetchall()
tri_map = {r['date']: r['close'] for r in tri}

print('交易日:', n, '天', dates[0], '~', dates[-1])

# 逐日温度计（每 3 天采样）
temps = []
for i in range(0, n, 3):
    d = dates[i]
    try:
        r = compute_all('000922', d)
    except Exception as e:
        print('计算失败', d, e)
        continue
    t = (r.get('temperature') or {}).get('value')
    if t is not None:
        temps.append((d, t, i))
print(f'温度计序列: {len(temps)} 个采样 ({time.time()-t0:.0f}s)')

# 温度分布
vals = [t[1] for t in temps]
print(f'温度范围: {min(vals):.0f}~{max(vals):.0f} 中位 {statistics.median(vals):.0f}')
for lo, hi in [(0, 35), (35, 50), (50, 65), (65, 100)]:
    cnt = sum(1 for v in vals if lo <= v < hi)
    print(f'  [{lo},{hi}): {cnt} 个 ({cnt/len(vals)*100:.0f}%)')

# 信号回测
def analyze(pred, label, cooldown=20):
    events = []
    last = -999
    for d, t, i in temps:
        if not pred(t):
            continue
        if i - last < cooldown:
            continue
        if i + 1 not in range(n) or i + 61 >= n:
            continue
        last = i
        buy_d = dates[i + 1]
        if buy_d not in tri_map:
            continue
        buy = tri_map[buy_d]
        d20 = dates[i + 21]
        d60 = dates[i + 61]
        r20 = tri_map.get(d20)
        r60 = tri_map.get(d60)
        if r20 and r60:
            events.append((r20 / buy - 1, r60 / buy - 1))
    if len(events) < 3:
        return f'{label:<30} 触发{len(events):>3}次 样本不足'
    w20 = [e[0] for e in events]
    w60 = [e[1] for e in events]
    return (f'{label:<30} 触发{len(events):>3}次 | 20日胜率{sum(1 for v in w20 if v > 0)/len(w20)*100:>5.1f}% 中位{statistics.median(w20)*100:>+6.2f}%'
            f' | 60日胜率{sum(1 for v in w60 if v > 0)/len(w60)*100:>5.1f}% 中位{statistics.median(w60)*100:>+6.2f}%')

print('\n=== 温度计读数 → 未来收益（H00922 全收益 · 次日买入 · 20日去重）===')
print()
print('--- 买入视角（低温度=冷=该买?）---')
print(analyze(lambda t: t <= 30, '温度 ≤30 (偏冷区)'))
print(analyze(lambda t: t <= 35, '温度 ≤35'))
print(analyze(lambda t: 30 < t <= 45, '温度 30-45'))
print(analyze(lambda t: 45 < t <= 60, '温度 45-60 (中性)'))
print()
print('--- 卖出视角（高温度=热=该卖?）---')
print(analyze(lambda t: t >= 70, '温度 ≥70 (偏热区)'))
print(analyze(lambda t: t >= 65, '温度 ≥65'))
print(analyze(lambda t: t >= 60, '温度 ≥60'))
print(analyze(lambda t: t >= 55, '温度 ≥55 (微热)'))
print()
# 单调性检查：按温度 5 分位
import numpy as np
q = np.percentile(vals, [0, 20, 40, 60, 80, 100])
print('--- 温度五分位 → 60日收益中位（单调性）---')
for k in range(5):
    lo, hi = q[k], q[k + 1]
    print(analyze(lambda t: lo <= t < hi, f'温度 {lo:.0f}-{hi:.0f}'))
db.close()
print(f'\n总耗时 {time.time()-t0:.0f}s')
