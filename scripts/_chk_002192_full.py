# -*- coding: utf-8 -*-
"""002192 融捷股份：系统数据全维度（财报/估值/技术面/日报）供 earnings-team"""
import requests, sqlite3

API = 'http://localhost:8788'
db = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db')
db.row_factory = sqlite3.Row

# 1. 估值
r = db.execute("""SELECT metric_code, value FROM fundamental_indicator WHERE stock_code='002192'
    AND date=(SELECT MAX(date) FROM fundamental_indicator WHERE stock_code='002192')
    AND metric_code IN ('pe_ttm','pb','ps_ttm','dyr','mc','ey','ev_ebitda_r')""").fetchall()
m = {x['metric_code']: x['value'] for x in r}
print(f"估值: PE {m.get('pe_ttm')} PB {m.get('pb')} PS {m.get('ps_ttm')} 股息率 {m.get('dyr',0)*100:.2f}% 市值 {m.get('mc',0)/1e8:.0f}亿")

# 2. 季度财报（stock_financials_quarterly）
rows = db.execute("""SELECT report_date, revenue_single, net_profit_single, net_profit_yoy,
    gross_margin_single, roe_single, free_cash_flow, asset_liability_ratio
    FROM stock_financials_quarterly WHERE stock_code='002192'
    ORDER BY report_date DESC LIMIT 8""").fetchall()
print('\n季度财报（最新在前）:')
for r_ in rows:
    print(f"  {r_['report_date']}: 营收 {r_['revenue_single']/1e8 if r_['revenue_single'] else '?'}亿 | 净利 {r_['net_profit_single']/1e8 if r_['net_profit_single'] else '?'}亿 | 净利YoY {r_['net_profit_yoy']} | 毛利率 {r_['gross_margin_single']} | ROE {r_['roe_single']} | 负债率 {r_['asset_liability_ratio']}")

# 3. 日报卡（技术面）
try:
    d = requests.get(API + '/api/watchlist-report/data', timeout=30).json()
    for c in d.get('cards', []):
        if c.get('code') == '002192':
            ev = c.get('eval') or {}
            print('\n日报卡:', ev.get('level'), ev.get('level_cn'), '| 净分', ev.get('net'))
            for x in ev.get('reasons', [])[:5]:
                print('  ', x[:80])
            print('  ctx:', c.get('ctx'))
except Exception as e:
    print('日报 ERR', e)

# 4. 技术信号（pattern-scan 近 3 月）
try:
    d = requests.get(API + '/api/pattern-scan?code=002192&start=2026-06-01', timeout=60).json()
    sigs = d.get('signals') or []
    from collections import Counter
    print('\n近3月信号:', len(sigs), Counter(s.get('source') for s in sigs))
    for s in sigs[-6:]:
        print(f"  {s.get('date')} {s.get('source')} {s.get('type')}")
except Exception as e:
    print('pattern ERR', e)
db.close()
