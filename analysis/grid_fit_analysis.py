# -*- coding: utf-8 -*-
"""
中证红利 网格投资适配性分析
============================
1. 波动特征：年化波动率/振幅/震荡区间——网格需要足够波动才有差价可赚
2. 估值锚：PE/PB/股息率分位——网格上下界的设计依据
3. 区间特征：历史价格在什么区间震荡、多少时间在区间内
"""
import sys, os, sqlite3
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row

# 1. 价格数据（000922 价格 + H00922 全收益）
rows = db.execute("SELECT date, close FROM index_daily_kline WHERE stock_code='000922' AND kline_type='normal' AND date>='2018-01-01' ORDER BY date").fetchall()
dates = [r['date'] for r in rows]
closes = [r['close'] for r in rows]
tri = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code='H00922' ORDER BY date").fetchall()
tri_dates = [r['date'] for r in tri]
tri_closes = [r['close'] for r in tri]

print(f'000922 价格: {len(closes)} 条 ({dates[0]} ~ {dates[-1]})')
print(f'H00922 全收益: {len(tri_closes)} 条')

# 2. 年化波动率（20日）
import math
rets = []
for i in range(1, len(tri_closes)):
    rets.append(tri_closes[i]/tri_closes[i-1] - 1)
ann_vol = (sum((r - sum(rets)/len(rets))**2 for r in rets)/(len(rets)-1))**0.5 * math.sqrt(252) * 100
print(f'\n全收益年化波动率: {ann_vol:.1f}%')
# 对比：上证指数（参考）
print('参考：沪深300 年化波动约 18-22%，中证红利通常 12-16%')

# 3. 价格区间分布（近8年）
print(f'\n000922 价格区间（2018起）:')
print(f'  最低: {min(closes):.0f} | 最高: {max(closes):.0f} | 当前: {closes[-1]:.0f}')
print(f'  最高/最低倍数: {max(closes)/min(closes):.2f}x')
# 分位数
import statistics
s = sorted(closes)
n = len(s)
print(f'  P10: {s[n//10]:.0f} | P25: {s[n//4]:.0f} | P50: {s[n//2]:.0f} | P75: {s[3*n//4]:.0f} | P90: {s[9*n//10]:.0f}')

# 4. 估值分位区间（网格锚）
fund = db.execute("SELECT pe_ttm_pct, pb_pct, dyr_pct FROM index_fundamental_daily WHERE stock_code='000922' AND date>='2018-01-01' AND pe_ttm_pct IS NOT NULL").fetchall()
pe_pcts = [r['pe_ttm_pct'] for r in fund]
pb_pcts = [r['pb_pct'] for r in fund]
dy_pcts = [r['dyr_pct'] for r in fund]
print(f'\n估值分位（2018起，10年滚动口径）:')
print(f'  PE分位: 当前={pe_pcts[-1]:.2f} | 范围 {min(pe_pcts):.2f}~{max(pe_pcts):.2f}')
print(f'  PB分位: 当前={pb_pcts[-1]:.2f} | 范围 {min(pb_pcts):.2f}~{max(pb_pcts):.2f}')
print(f'  股息率分位: 当前={dy_pcts[-1]:.2f} | 范围 {min(dy_pcts):.2f}~{max(dy_pcts):.2f}')

# 5. 震荡特征：波动幅度分布（20日区间）
ranges = []
for i in range(20, len(tri_closes)):
    w = tri_closes[i-19:i+1]
    ranges.append((max(w)-min(w))/min(w)*100)
ranges_s = sorted(ranges)
print(f'\n20日振幅（全收益）:')
print(f'  P25: {ranges_s[len(ranges_s)//4]:.1f}% | P50: {ranges_s[len(ranges_s)//2]:.1f}% | P75: {ranges_s[3*len(ranges_s)//4]:.1f}% | P90: {ranges_s[9*len(ranges_s)//10]:.1f}%')
print(f'  振幅>10% 占比: {sum(1 for r in ranges if r > 10)/len(ranges)*100:.0f}% | >15%: {sum(1 for r in ranges if r > 15)/len(ranges)*100:.0f}%')

# 6. 年度波动次数：假设 5% 网格，一年内价格穿越 5% 档位多少次
print(f'\n5% 网格穿越次数估算（全收益）:')
for year in ['2019','2020','2021','2022','2023','2024','2025','2026']:
    idx = [i for i,d in enumerate(tri_dates) if d.startswith(year)]
    if len(idx) < 50: continue
    seg = [tri_closes[i] for i in idx]
    # 从年初价格起，每 5% 一档，统计穿越次数
    level = seg[0]
    grid = round(level / 5) * 5  # 初始档位
    crosses = 0
    last_dir = 0
    for c in seg:
        g = round(c / (level * 0.05)) * (level * 0.05)
        if abs(g - grid) >= level * 0.05 * 0.5:
            crosses += 1
            grid = g
    print(f'  {year}: 约 {crosses} 次档位穿越')
db.close()
