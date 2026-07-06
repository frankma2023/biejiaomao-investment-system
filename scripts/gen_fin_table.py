"""生成卫星化学财务数据表格（用于四大师分析）"""
import sqlite3, json
from datetime import datetime, timedelta

CODE = '002648'
NAME = '卫星化学'
db = sqlite3.connect('D:\\hanako\\investment-system\\data\\lixinger.db')
db.row_factory = sqlite3.Row

result = {'code': CODE, 'name': NAME, 'date': '2026-07-02'}

# ── 估值时序（近10年） ──
def get_fund_ts(metric, limit=2500):
    rows = db.execute("""SELECT date, value FROM fundamental_indicator
        WHERE stock_code=? AND metric_code=? ORDER BY date""", (CODE, metric)).fetchall()
    vals = [{'d': r['date'], 'v': round(r['value'], 2)} for r in rows if r['value']]
    return vals[-limit:] if len(vals) > limit else vals

result['pe'] = get_fund_ts('pe_ttm')
result['pb'] = get_fund_ts('pb')
result['ps'] = get_fund_ts('ps_ttm')
result['dyr'] = get_fund_ts('dyr')
result['pcf'] = get_fund_ts('pcf_ttm')
# 总市值 = mc
result['mc'] = get_fund_ts('mc')

# ── 年报财务数据 ──
fa_rows = db.execute("""SELECT * FROM stock_financials_annual
    WHERE stock_code=? ORDER BY report_date""", (CODE,)).fetchall()

financials = []
for r in fa_rows:
    rev = r['revenue'] or 0
    np = r['net_profit'] or 0
    gp = r['gross_margin'] or 0
    roe = r['roe'] or 0
    ocf = r['operating_cash_flow'] or 0
    fcf = r['free_cash_flow'] or 0
    ald = r['asset_liability_ratio'] or 0
    cr = r['current_ratio'] or 0
    qr = r['quick_ratio'] or 0
    revenue_yoy = r['revenue_yoy'] or 0
    np_yoy = r['net_profit_yoy'] or 0
    net_margin = (np / rev * 100) if rev else 0
    financials.append({
        'year': r['report_date'][:4], 'revenue': round(rev/1e8,1),
        'rev_yoy': round(revenue_yoy,1) if revenue_yoy else None,
        'net_profit': round(np/1e8, 2), 'np_yoy': round(np_yoy, 1) if np_yoy else None,
        'gross_margin': round(gp, 1), 'net_margin': round(net_margin, 1),
        'roe': round(roe, 1), 'ocf': round(ocf/1e8, 1),
        'fcf': round(fcf/1e8, 1) if fcf else None,
        'debt_ratio': round(ald, 1), 'current_ratio': round(cr, 2),
        'quick_ratio': round(qr, 2),
    })
result['financials'] = financials[-10:]  # 近10年

# ── RS强度 ──
rs_rows = db.execute("""SELECT date, rps_20, rps_250 FROM stock_rs_daily
    WHERE stock_code=? ORDER BY date""", (CODE,)).fetchall()
rs_data = [{'d': r['date'], 'rps20': r['rps_20'] or 0, 'rps250': r['rps_250'] or 0} for r in rs_rows if r['rps_250']]
result['rs'] = rs_data[-500:]  # 近500个交易日

db.close()

# 输出统计数据
ds = result
pe_vals = [x['v'] for x in ds['pe'] if x['v']]
pb_vals = [x['v'] for x in ds['pb'] if x['v']]
ps_vals = [x['v'] for x in ds['ps'] if x['v']]
dyr_vals = [x['v'] for x in ds['dyr'] if x['v']]

print(f"=== {NAME}({CODE}) 财务数据摘要 ===\n")
print(f"当前 PE={pe_vals[-1] if pe_vals else '—'}  PB={pb_vals[-1] if pb_vals else '—'}  PS={ps_vals[-1] if ps_vals else '—'}  股息率={dyr_vals[-1] if dyr_vals else '—'}%")

print(f"\n{'指标':<20}{'10年最低':>10}{'10年均值':>10}{'10年最高':>10}{'当前':>10}")
print('-'*60)
if pe_vals: print(f"{'PE-TTM':<20}{min(pe_vals):>10.2f}{sum(pe_vals)/len(pe_vals):>10.2f}{max(pe_vals):>10.2f}{pe_vals[-1]:>10.2f}")
if pb_vals: print(f"{'PB':<20}{min(pb_vals):>10.2f}{sum(pb_vals)/len(pb_vals):>10.2f}{max(pb_vals):>10.2f}{pb_vals[-1]:>10.2f}")
if ps_vals: print(f"{'PS-TTM':<20}{min(ps_vals):>10.2f}{sum(ps_vals)/len(ps_vals):>10.2f}{max(ps_vals):>10.2f}{ps_vals[-1]:>10.2f}")
if dyr_vals: print(f"{'股息率%':<20}{min(dyr_vals):>10.2f}{sum(dyr_vals)/len(dyr_vals):>10.2f}{max(dyr_vals):>10.2f}{dyr_vals[-1]:>10.2f}")

print(f"\n近10年财务数据:")
print(f"{'年份':<8}{'营收':>8}{'增速':>7}{'净利':>8}{'增速':>7}{'毛利':>6}{'净利':>6}{'ROE':>6}{'FCF':>8}{'负债率':>7}{'流动':>5}{'速动':>5}")
print('-'*85)
for f in reversed(financials[-10:]):
    rev_s = f"{f['revenue']:.0f}" if f['revenue'] else '—'
    np_s = f"{f['net_profit']:.0f}" if f['net_profit'] else '—'
    fcf_s = f"{f['fcf']:.0f}" if f['fcf'] else '—'
    print(f"{f['year']:<8}{rev_s:>8}{f['rev_yoy'] or '—':>7}{np_s:>8}{f['np_yoy'] or '—':>7}{f['gross_margin']:>5.1f}%{f['net_margin']:>5.1f}%{f['roe']:>5.1f}%{fcf_s:>8}{f['debt_ratio']:>6.1f}%{f['current_ratio']:>5.2f}{f['quick_ratio']:>5.2f}")

print(f"\nRS强度 (RPS):")
print(f"{'日期':<12}{'RPS_250':>10}{'RPS_20':>10}")
print('-'*32)
for r in rs_data[-5:]:
    print(f"{r['d']:<12}{r['rps250']:>10}{r['rps20']:>10}")

# 保存为JSON
with open('D:\\hanako\\_fin_data.json', 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, default=str)
print(f"\n✅ 完整数据已保存")
