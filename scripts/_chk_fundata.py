# -*- coding: utf-8 -*-
"""A股财报数据覆盖检查"""
import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
db = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db')
db.row_factory = sqlite3.Row

for t, label in [('stock_financials_quarterly', '季度财报'), ('stock_financials_annual', '年报'),
                 ('fundamental_indicator', '个股基本面(日)')]:
    r = db.execute(f"SELECT COUNT(*) n, COUNT(DISTINCT stock_code) c FROM {t}").fetchone()
    try:
        r2 = db.execute(f"SELECT MAX(report_date) mx, MIN(report_date) mn FROM {t}").fetchone()
        extra = f"报告期 {r2['mn']}~{r2['mx']}"
    except Exception:
        r2 = db.execute(f"SELECT MAX(date) mx, MIN(date) mn FROM {t}").fetchone()
        extra = f"日期 {r2['mn']}~{r2['mx']}"
    print(f'{label} {t}: {r["n"]} 条 / {r["c"]} 只 | {extra}')

# 卫星化学 002648 最新财报
rows = db.execute("""SELECT report_date, announce_date, revenue_single, net_profit_single, gross_margin_single
    FROM stock_financials_quarterly WHERE stock_code='002648' ORDER BY report_date DESC LIMIT 2""").fetchall()
print('\n002648 最新季度财报:')
for r in rows:
    print(f"  报告期 {r['report_date']} 公告日 {r['announce_date']} 营收 {r['revenue_single']/1e8:.0f}亿 净利 {r['net_profit_single']/1e8:.0f}亿")
db.close()
