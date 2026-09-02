# -*- coding: utf-8 -*-
"""红利指数全景数据采集（文档用）— 全收益统计 + 当前估值"""
import sqlite3, sys, io, json, math
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
db = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db')
db.row_factory = sqlite3.Row

INDICES = [
    ('000922', '中证红利', '一代·股息率', 2004),
    ('H30269', '红利低波', '二代·股息率+低波', 2005),
    ('930955', '红利低波100', '二代·股息率+低波', 2005),
    ('931468', '红利质量', '多因子·红利+质量', None),
    ('980081', '价值100', '三代·PE+股息+现金流', 2012),
    ('980092', '自由现金流', '现金流因子', None),
    ('930914', '港股通高股息', '港股红利', None),
    ('930839', '港股通高股息精选', '港股红利', None),
    ('932305', '智选高股息', '三代·预案股息率', 2005),
    ('000015', '红利指数', '一代·沪市股息', 2005),
    ('931848', '800红利低波', '二代·红利低波', None),
]
OUT = {}

for code, name, gen, base_year in INDICES:
    # 全收益序列
    tri = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code=? ORDER BY date", (code,)).fetchall()
    if not tri:
        OUT[code] = {'name': name, 'note': '无全收益'}
        continue
    dates = [r['date'] for r in tri]
    closes = [r['close'] for r in tri]
    d = {'name': name, 'gen': gen, 'start': dates[0], 'end': dates[-1], 'n': len(closes)}
    d['cur'] = round(closes[-1], 1)
    # 起点归一化（2016-01-04 附近）
    base_i = next((i for i, x in enumerate(dates) if x >= '2016-01-04'), 0)
    if base_i < len(closes) - 1:
        c0 = closes[base_i]
        yrs = (datetime.strptime(dates[-1], '%Y-%m-%d') - datetime.strptime(dates[base_i], '%Y-%m-%d')).days / 365.25
        cagr = (closes[-1] / c0) ** (1 / yrs) - 1
        d['cagr_2016'] = round(cagr * 100, 1)
        d['norm_2016'] = round(closes[-1] / c0 * 100, 1)
    # 5年（2021-09 起）
    b5 = next((i for i, x in enumerate(dates) if x >= '2021-09-01'), 0)
    if b5 < len(closes) - 1 and dates[-1] > '2026-08-01':
        c0 = closes[b5]
        yrs = (datetime.strptime(dates[-1], '%Y-%m-%d') - datetime.strptime(dates[b5], '%Y-%m-%d')).days / 365.25
        d['cagr_5y'] = round((closes[-1] / c0) ** (1 / yrs) * 100 - 100, 1) if yrs > 0 else None
    # 年化波动（近1年日收益）
    r1y = [r for r in tri if r['date'] >= '2025-09-01']
    if len(r1y) > 30:
        rets = [(r1y[i]['close'] / r1y[i-1]['close'] - 1) for i in range(1, len(r1y))]
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / len(rets)
        d['vol_1y'] = round(math.sqrt(var) * math.sqrt(244) * 100, 1)
    # 最大回撤（2016 起全收益）
    hi = -1e18; mdd = 0
    for c in closes:
        hi = max(hi, c)
        mdd = max(mdd, (hi - c) / hi)
    d['mdd_2016'] = round(mdd * 100, 1)
    # YTD
    y0 = next((i for i, x in enumerate(dates) if x >= '2026-01-01'), None)
    if y0 is not None and y0 < len(closes) - 1 and closes[y0]:
        d['ytd'] = round((closes[-1] / closes[y0] - 1) * 100, 1)
    # 年度收益（2016-2025 全收益）
    annual = {}
    for yr in range(2016, 2027):
        ys = [r for r in tri if r['date'].startswith(str(yr))]
        if len(ys) >= 2:
            annual[str(yr)] = round((ys[-1]['close'] / ys[0]['close'] - 1) * 100, 1)
        elif ys:
            # 部分年度（如 932305 2024-09 起）
            pass
    d['annual'] = annual
    OUT[code] = d

# 当前估值（最新）
for code, name, gen, _ in INDICES:
    v = db.execute("""SELECT date, pe_ttm, pe_ttm_pct, pb, pb_pct, dyr, dyr_pct
        FROM index_fundamental_daily WHERE stock_code=? ORDER BY date DESC LIMIT 1""", (code,)).fetchone()
    if v and code in OUT:
        OUT[code]['val'] = {
            'date': v['date'],
            'pe': round(v['pe_ttm'], 1) if v['pe_ttm'] else None,
            'pe_pct': round(v['pe_ttm_pct'] * 100) if v['pe_ttm_pct'] is not None else None,
            'pb': round(v['pb'], 2) if v['pb'] else None,
            'pb_pct': round(v['pb_pct'] * 100) if v['pb_pct'] is not None else None,
            'dyr': round(v['dyr'] * 100, 2) if v['dyr'] else None,
            'dyr_pct': round(v['dyr_pct'] * 100) if v['dyr_pct'] is not None else None,
        }

json.dump(OUT, open(r'D:\hanako\investment-system\.scratch\div_panorama.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
# 打印摘要
for code, d in OUT.items():
    if 'note' in d:
        print(f"{code} {d['name']}: {d['note']}"); continue
    v = d.get('val') or {}
    print(f"{d['name']:<8}{code} 全收益{d['cur']} | 2016起年化{d.get('cagr_2016','—')}% (norm {d.get('norm_2016','—')}) | 5年{d.get('cagr_5y','—')}% | 波{d.get('vol_1y','—')}% | 回撤{d['mdd_2016']}% | YTD{d.get('ytd','—')}% | PE {v.get('pe','—')}({v.get('pe_pct','—')}%) dyr {v.get('dyr','—')}%({v.get('dyr_pct','—')}%)")
db.close()
