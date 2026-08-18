# -*- coding: utf-8 -*-
"""
温度计权重标定实验
==================
改动核心：息差分位 pct_all → pct_250（250日滚动，捕捉近期息差收窄）
权重组合候选：拥挤度/恐慌贪婪/息差
目标：高温区(≥65)样本≥15 且 60日胜率<50%；低温区(≤30) 60日胜率≥65%；单调性
"""
import sys, os, sqlite3, statistics
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)
from src.scanners.red_dividend_metrics import compute_all

db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row
rows = db.execute("""
    SELECT date, close FROM index_daily_kline
    WHERE stock_code='000922' AND kline_type='normal' AND date>='2018-07-01' ORDER BY date
""").fetchall()
dates = [r['date'] for r in rows]
closes = [r['close'] for r in rows]
n = len(dates)
tri = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code='H00922' AND date>='2018-07-01' ORDER BY date").fetchall()
tri_map = {r['date']: r['close'] for r in tri}

# 预计算三维指标序列（每 3 天）
print('预计算三维指标...')
seq = []
fail = 0
for i in range(0, n, 3):
    d = dates[i]
    try:
        r = compute_all('000922', d)
    except Exception as e:
        fail += 1
        if fail <= 3:
            print(f'  警告 {d}: {e}')
        continue
    cw = r.get('crowding') or {}
    fg = r.get('fear_greed') or {}
    sp = r.get('spread') or {}
    if cw.get('score') is None or fg.get('score') is None or sp.get('value') is None:
        continue
    seq.append({'d': d, 'i': i,
                'crowd': cw['score'], 'fg': fg['score'],
                'pct_250': sp['pct_250'], 'pct_all': sp['pct_all']})
print('序列:', len(seq), '失败:', fail)

COMBOS = {
    'A 0.35/0.25/0.40+pct250': (0.35, 0.25, 0.40, 'pct_250'),
    'B 0.45/0.20/0.35+pct250': (0.45, 0.20, 0.35, 'pct_250'),
    'C 0.40/0.20/0.40+pct250': (0.40, 0.20, 0.40, 'pct_250'),
    'D 0.50/0.20/0.30+pct250': (0.50, 0.20, 0.30, 'pct_250'),
    'E 0.35/0.25/0.40+pctAll(对照)': (0.35, 0.25, 0.40, 'pct_all'),
}

def temp_of(row, w_c, w_f, w_s, pct_key):
    pct = row[pct_key]
    return 50 + (row['crowd'] - 50) * w_c + (100 - row['fg'] - 50) * w_f + (100 - pct - 50) * w_s

def analyze(pred, label, cooldown=20):
    events = []
    last = -999
    for s in seq:
        if not pred(s['t']):
            continue
        i = s['i']
        if i - last < cooldown or i + 61 >= n:
            continue
        last = i
        buy_d = dates[i + 1]
        if buy_d not in tri_map:
            continue
        buy = tri_map[buy_d]
        r20 = tri_map.get(dates[i + 21])
        r60 = tri_map.get(dates[i + 61])
        if r20 and r60:
            events.append((r20 / buy - 1, r60 / buy - 1))
    if len(events) < 3:
        return f'{label:<14} {len(events):>3}次 样本不足'
    w20 = [e[0] for e in events]
    w60 = [e[1] for e in events]
    return (f'{label:<14} {len(events):>3}次 | 20日{sum(1 for v in w20 if v > 0)/len(w20)*100:>5.1f}%/{statistics.median(w20)*100:>+5.2f}%'
            f' | 60日{sum(1 for v in w60 if v > 0)/len(w60)*100:>5.1f}%/{statistics.median(w60)*100:>+5.2f}%')

for name, (w_c, w_f, w_s, pct_key) in COMBOS.items():
    for s in seq:
        s['t'] = temp_of(s, w_c, w_f, w_s, pct_key)
    vals = [s['t'] for s in seq]
    hi65 = sum(1 for v in vals if v >= 65)
    lo30 = sum(1 for v in vals if v <= 30)
    print(f'\n=== {name} ===')
    print(f'  分布: 中位{statistics.median(vals):.0f} 范围{min(vals):.0f}~{max(vals):.0f} | ≥65: {hi65}次 | ≤30: {lo30}次')
    if hi65 >= 5:
        print('  ' + analyze(lambda t: t <= 30, '冷≤30'))
        print('  ' + analyze(lambda t: 30 < t <= 50, '中30-50'))
        print('  ' + analyze(lambda t: t >= 65, '热≥65'))
        print('  ' + analyze(lambda t: t >= 55, '热≥55'))
    else:
        print('  ' + analyze(lambda t: t <= 30, '冷≤30'))
        print('  ' + analyze(lambda t: t >= 55, '热≥55'))
db.close()
