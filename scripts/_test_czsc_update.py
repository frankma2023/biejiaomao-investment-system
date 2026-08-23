# -*- coding: utf-8 -*-
"""验证 CZSC.update 增量：用法 + 与全量结果一致性 + 速度"""
import sys, time, sqlite3
sys.path.insert(0, r'D:\hanako\investment-system')
from datetime import datetime
from czsc import CZSC, RawBar, Freq

db = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db')
db.row_factory = sqlite3.Row
rows = db.execute("""SELECT date, open, high, low, close, volume, amount FROM daily_kline
    WHERE stock_code='300750' ORDER BY date DESC LIMIT 600""").fetchall()
rows = list(reversed(rows))
bars = []
for r in rows:
    bars.append(RawBar(symbol='300750', dt=datetime.strptime(r['date'], '%Y-%m-%d'), freq=Freq.D,
                       open=r['open'], high=r['high'], low=r['low'], close=r['close'],
                       vol=r['volume'], amount=r['amount'] or 0))

# 1. 全量（对照基准）：前 500 根
c_full = CZSC(bars[:500])
full_bis = [(b.sdt, b.edt, str(b.direction)) for b in c_full.bi_list]

# 2. 增量：先 100 根，再逐根 update 到 500
c = CZSC(bars[:100])
t0 = time.time()
for b in bars[100:500]:
    c.update(b)
inc_time = time.time() - t0
inc_bis = [(b.sdt, b.edt, str(b.direction)) for b in c.bi_list]

print(f'增量 400 根耗时: {inc_time*1000:.1f} ms')
print(f'全量笔数: {len(full_bis)} | 增量笔数: {len(inc_bis)}')
print('一致性:', full_bis == inc_bis, '(前 500 根结束态)')
if full_bis != inc_bis:
    for i, (a, b) in enumerate(zip(full_bis, inc_bis)):
        if a != b:
            print(f'  第 {i} 笔不同: 全量{a} vs 增量{b}')
            break

# 3. 中间状态一致性：增量到 300 根时 vs 全量 300 根
c2 = CZSC(bars[:300])
mid_full = [(b.sdt, b.edt) for b in c2.bi_list]
# 从 c（已到 500）无法回溯，重建到 300
c3 = CZSC(bars[:100])
for b in bars[100:300]:
    c3.update(b)
mid_inc = [(b.sdt, b.edt) for b in c3.bi_list]
print(f'中间状态(300根)一致性: {mid_full == mid_inc}')
