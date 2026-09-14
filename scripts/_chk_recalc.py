# -*- coding: utf-8 -*-
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
c = sqlite3.connect('data/lixinger.db'); c.row_factory = sqlite3.Row
print('进度表 (czsc101_adj):', c.execute(
    "SELECT COUNT(*) n FROM chanlun_recalc_progress WHERE version='czsc101_adj'").fetchone()['n'], '只')
print()
print('抽样 3 只在 chanlun_scan_daily 的新版本行：')
for r in c.execute("""SELECT stock_code, scan_date, algo_version, bi_count
                      FROM chanlun_scan_daily WHERE algo_version='czsc101_adj'
                      ORDER BY stock_code LIMIT 3"""):
    print('  ', dict(r))
print()
print('chanlun_scan_daily 按版本分布：')
for r in c.execute("SELECT COALESCE(algo_version,'(null)') v, COUNT(*) n, "
                   "COUNT(DISTINCT stock_code) s FROM chanlun_scan_daily GROUP BY v ORDER BY n DESC"):
    print(f'   {r["v"]:<14}{r["n"]:>12,} 行 / {r["s"]:>6,} 只')
print()
print('chanlun_bi_json 行数:', c.execute("SELECT COUNT(*) n FROM chanlun_bi_json").fetchone()['n'])
c.close()
