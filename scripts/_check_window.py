# -*- coding: utf-8 -*-
"""自查：scan_stock_all 加载 3000 根 vs 单日 analyze 1500 根——笔窗口是否一致？
用一只历史超长股票验证（如 000338 潍柴动力 2005 上市，>4000 根）"""
import sys, time
sys.path.insert(0, r'D:\hanako\investment-system')
sys.path.insert(0, r'D:\hanako\investment-system\src')
import sqlite3
db = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db')
n = db.execute("SELECT COUNT(*) c FROM daily_kline WHERE stock_code='000338'").fetchone()[0]
print('000338 全历史 K 线:', n, '根')
db.close()

from scanners.chanlun_scan import scan_stock, scan_stock_all

dates = ['2026-08-21']
t0 = time.time()
single = scan_stock('000338', '2026-08-21')
print(f'单日(1500根): {time.time()-t0:.2f}s bi={single["bi_count"]} zs={single["zs_count"]}')

t0 = time.time()
allr = dict(scan_stock_all('000338', dates, limit=1500))
print(f'增量(3000根): {time.time()-t0:.2f}s bi={allr["2026-08-21"]["bi_count"]} zs={allr["2026-08-21"]["zs_count"]}')

a, b = single, allr['2026-08-21']
print(f'\n一致性: bi {a["bi_count"]} vs {b["bi_count"]} | zs {a["zs_count"]} vs {b["zs_count"]}')
print('结论:', '一致 ✅' if a['bi_count'] == b['bi_count'] and a['zs_count'] == b['zs_count'] else '不一致 ❌')
