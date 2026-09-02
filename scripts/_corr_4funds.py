# -*- coding: utf-8 -*-
"""4 只候选指数 + 替代指数 全收益相关性（风格重叠验证）"""
import sqlite3, sys, io
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
db = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db')
db.row_factory = sqlite3.Row

codes = {
    '980081': '价值100', '980092': '自由现金流', '930914': '港股通高股息',
    'H30269': '红利低波', '930955': '红利低波100', '931468': '红利质量',
    '000922': '中证红利', '932305': '智选高股息',
}
# 全收益序列（2020 起对齐，避免 932305 无早期数据）
series = {}
for code in codes:
    rows = db.execute("""SELECT date, close FROM index_full_return_daily
        WHERE stock_code=? AND date>='2020-01-01' ORDER BY date""", (code,)).fetchall()
    if rows:
        series[code] = {r['date']: r['close'] for r in rows}

common_dates = set.intersection(*[set(s.keys()) for s in series.values()]) if series else set()
common_dates = sorted(common_dates)
print(f'共同交易日: {len(common_dates)}（2020 起）')

# 日收益
rets = {}
for code, m in series.items():
    rets[code] = []
    for i in range(1, len(common_dates)):
        d0, d1 = common_dates[i-1], common_dates[i]
        if m.get(d0) and m.get(d1):
            rets[code].append(m[d1] / m[d0] - 1)
        else:
            rets[code].append(None)

# 相关性矩阵
import statistics
n = len(rets[codes.keys().__iter__().__next__()])
code_list = list(codes.keys())
print(f"\n相关性矩阵（全收益日收益 {len(common_dates)-1} 天）:")
print(f"{'':<10}", end='')
for c in code_list:
    print(f"{codes[c]:<8}", end='')
print()
for c1 in code_list:
    print(f"{codes[c1]:<8}", end='')
    for c2 in code_list:
        pairs = [(a, b) for a, b in zip(rets[c1], rets[c2]) if a is not None and b is not None]
        if len(pairs) < 100:
            print(f"{'—':<8}", end=''); continue
        mean1 = sum(p[0] for p in pairs) / len(pairs)
        mean2 = sum(p[1] for p in pairs) / len(pairs)
        cov = sum((p[0]-mean1)*(p[1]-mean2) for p in pairs) / len(pairs)
        s1 = (sum((p[0]-mean1)**2 for p in pairs)/len(pairs))**0.5
        s2 = (sum((p[1]-mean2)**2 for p in pairs)/len(pairs))**0.5
        r = cov / (s1*s2) if s1*s2 else 0
        print(f"{r:<8.3f}", end='')
    print()

# 用户 4 只 vs 组合内平均相关性
user4 = ['980081', '930914', '980092', 'H30269']
pairs_all = []
for i in range(len(user4)):
    for j in range(i+1, len(user4)):
        c1, c2 = user4[i], user4[j]
        p = [(a, b) for a, b in zip(rets[c1], rets[c2]) if a is not None and b is not None]
        mean1 = sum(x[0] for x in p)/len(p); mean2 = sum(x[1] for x in p)/len(p)
        cov = sum((x[0]-mean1)*(x[1]-mean2) for x in p)/len(p)
        s1 = (sum((x[0]-mean1)**2 for x in p)/len(p))**0.5
        s2 = (sum((x[1]-mean2)**2 for x in p)/len(p))**0.5
        pairs_all.append((f"{codes[c1]}-{codes[c2]}", cov/(s1*s2) if s1*s2 else 0))
print('\n用户4只组合内两两相关性:')
for name, r in pairs_all:
    print(f'  {name}: {r:.3f}')
avg = sum(r for _, r in pairs_all) / len(pairs_all)
print(f'  平均: {avg:.3f}')
db.close()
