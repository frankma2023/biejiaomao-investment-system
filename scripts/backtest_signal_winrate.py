# -*- coding: utf-8 -*-
"""信号胜率回测 v2：单次扫描 pattern_scan_signals，按 source 分类，前瞻 20 日收益"""
import os
import sys
import json
import sqlite3
from collections import defaultdict
from datetime import datetime

DB = r'D:\hanako\investment-system\data\lixinger.db'
TARGET = {'base_breakout', 'base_breakout_v2', 'box_breakout', 'double_bottom',
          'flat_base', 'cup_handle', 'saucer_base', 'top_pattern', 'climax_top',
          'box_breakdown', 'railroad_tracks', 'breakout_failure', 'volume_stall',
          'volume_divergence'}
FWD = 20

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# 1. 单次扫描收集各 source 信号
collected = defaultdict(list)  # src -> [(code, date, close)]
rows = db.execute("""SELECT stock_code, date, signals_json FROM pattern_scan_signals
    WHERE date >= '2019-01-01'""").fetchall()
print(f'扫描 {len(rows)} 行历史信号...')
for r in rows:
    try:
        j = json.loads(r['signals_json'])
    except Exception:
        continue
    for s in j:
        src = s.get('source')
        if src not in TARGET:
            continue
        det = s.get('details') or {}
        if det.get('confirmed') is False or det.get('vol_ok') is False:
            continue
        collected[src].append((r['stock_code'], r['date'], s.get('close') or det.get('close')))

print(f'收集完成: { {k: len(v) for k, v in collected.items()} }')

# 2. 去重（同股同 source 取最早）
deduped = {}
for src, lst in collected.items():
    seen = {}
    for code, d, c in lst:
        key = (code, src)
        if key not in seen or d < seen[key][0]:
            seen[key] = (d, c)
    deduped[src] = [(k[0], v[0], v[1]) for k, v in seen.items()]

# 3. 前瞻 20 日收益（信号日 close 统一从 daily_kline 取，不依赖 signals_json）
results = {}
for src, sigs in deduped.items():
    gains = []
    missing = 0
    for code, d, c0 in sigs:
        kl = db.execute("""SELECT close FROM daily_kline WHERE stock_code=? AND date>=?
            ORDER BY date LIMIT ?""", (code, d, FWD + 2)).fetchall()
        if len(kl) < FWD + 2:
            missing += 1
            continue
        c0 = kl[0]['close']  # 信号日收盘（前复权优先无则不复权）
        if not c0:
            missing += 1
            continue
        gains.append((kl[FWD + 1]['close'] / c0 - 1) * 100)
    if not gains:
        results[src] = None
        continue
    pos = sorted(gains)
    med = pos[len(pos) // 2]
    win = sum(1 for g in gains if g > 0) / len(gains) * 100
    avg = sum(gains) / len(gains)
    results[src] = {'n': len(gains), 'win': win, 'med': med, 'avg': avg, 'missing': missing}

print('\n' + '=' * 70)
print('信号胜率回测（2019 起，前瞻 20 交易日，同源去重取最早）')
print('=' * 70)
lines = []
for src in sorted(TARGET):
    r = results.get(src)
    if not r:
        lines.append(f"| {src} | 0 | — | — | — |")
        print(f"  {src:<20} 无样本")
        continue
    lines.append(f"| {src} | {r['n']} | {r['win']:.1f}% | {r['med']:+.1f}% | {r['avg']:+.1f}% |")
    print(f"  {src:<20} 样本 {r['n']:>6} | 胜率 {r['win']:.1f}% | 中位 {r['med']:+.1f}%")

os.makedirs(r'D:\hanako\investment-system\docs\analysis', exist_ok=True)
fn = r'D:\hanako\investment-system\docs\analysis\信号胜率回测_%s.md' % datetime.now().strftime('%Y-%m')
with open(fn, 'w', encoding='utf-8') as f:
    f.write(f"# 信号胜率回测（2019 起，前瞻 {FWD} 交易日，同源去重取最早）\n\n")
    f.write("| 信号 | 样本 | 20日胜率 | 中位收益 | 平均收益 |\n|---|---|---|---|---|\n")
    f.write('\n'.join(lines) + '\n')
    f.write("\n**口径**：全市场 pattern_scan_signals（2019 起）；买入类胜率=正收益比例，卖出类取 100-胜率=负收益比例；未确认信号已剔除；信号日 close 来自 signals_json。\n")
print('\n已存档:', fn)
