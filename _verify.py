#!/usr/bin/env python3
"""Verify 002483 stage distribution after fix"""
import sqlite3
conn = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db')
rows = conn.execute(
    "SELECT date, stage, prior_stage, action FROM cpa_stage_daily WHERE stock_code='002483' AND date >= '2023-11-01' ORDER BY date"
).fetchall()
stages = {}
for r in rows:
    s = r[1].rstrip('T').rstrip('w')
    stages[s] = stages.get(s, 0) + 1

print('002483 阶段分布（2023-11起）:')
for s in sorted(stages.keys(), key=lambda x: str(x)):
    print(f'  {s}: {stages[s]}')

cnt_1a = conn.execute(
    "SELECT COUNT(*) FROM cpa_stage_daily WHERE stock_code='002483' AND stage='①a' AND date >= '2020-01-01'"
).fetchone()[0]
print(f'\n2020年后 ①a 行数: {cnt_1a}')
print(f'总日线: {len(rows)}')
conn.close()