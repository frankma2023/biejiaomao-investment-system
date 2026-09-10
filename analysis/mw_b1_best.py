# -*- coding: utf-8 -*-
"""补测最强组合：熊市 × 深回调 × TS 中段（复用 mw_b1_conditions 数据管道）"""
import sys, io, os, sqlite3, statistics
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\hanako\investment-system\src')
os.chdir(r'D:\hanako\investment-system\src')
exec(open(r'D:\hanako\investment-system\analysis\mw_b1_conditions.py', encoding='utf-8').read().split("print('\\n===== 全量基线")[0])

def stat(rows, name, cond=None):
    sub = [r for r in rows if (cond(r) if cond else True)]
    if len(sub) < 30:
        print(f'  {name}: n={len(sub)} 样本不足')
        return
    exs = [r['ex'] for r in sub]
    win = sum(1 for v in exs if v > 0) / len(exs)
    print(f'  {name}: n={len(sub):6d} 超额胜率={win*100:5.1f}% 平均={statistics.mean(exs)*100:+6.2f}% 中位={statistics.median(exs)*100:+6.2f}%')

print('\n===== 熊市组合叠加 =====')
stat(out, '熊 × decline>=40', lambda r: r['bull'] is False and r['decline'] is not None and abs(r['decline'] or 0) >= 40)
stat(out, '熊 × decline>=40 × TS55-85', lambda r: r['bull'] is False and r['decline'] is not None and abs(r['decline'] or 0) >= 40 and r['ts'] is not None and 55 <= r['ts'] <= 85)
stat(out, '熊 × decline>=40 × h_rs250>=70', lambda r: r['bull'] is False and r['decline'] is not None and abs(r['decline'] or 0) >= 40 and r['h_rs250'] is not None and r['h_rs250'] >= 70)
print('\n===== 分年: 熊×decline>=40 =====')
sub = [r for r in out if r['bull'] is False and r['decline'] is not None and abs(r['decline'] or 0) >= 40]
by_y = {}
for r in sub:
    by_y.setdefault(r['b1'][:4], []).append(r['ex'])
for y in sorted(by_y):
    v = by_y[y]
    win = sum(1 for x in v if x > 0) / len(v)
    print('  %s: n=%d 胜率=%.0f%% 平均=%+.2f%%' % (y, len(v), win * 100, statistics.mean(v) * 100))
print('\n===== 对照: 牛 × decline>=40 =====')
stat(out, '牛 × decline>=40', lambda r: r['bull'] is True and r['decline'] is not None and abs(r['decline'] or 0) >= 40)
