"""
四位大师选股漏斗 - 第3层：芒格（风险排除）
条件（数据可用项）：
1. 有息负债 / 近3年均净利润 < 5年（赚5年能还清债）
2. 应收账款/营收 < 30%（由 receivables_turnover 换算：应收/营收 = 1/周转率）
3. 存货/营收 < 25%（由 inventory_turnover 换算：存货/营收 = 1/周转率，用营收口径近似）
4. 近3年营收增速：无连续2年下滑超10%（业务不萎缩）
5. 大股东质押 < 30%（无数据，跳过并标注）
6. 大股东减持（无可靠数据，跳过并标注）
"""
import sqlite3

db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row

# 读第2层股票列表
codes = []
with open('D:/hanako/investment-system/analysis/layer2_result.md', 'r', encoding='utf-8') as f:
    for line in f:
        if line.startswith('| ') and '---' not in line and '代码' not in line:
            parts = line.strip().split('|')
            if len(parts) >= 6:
                c = parts[1].strip()
                if c.startswith(('0','3','6','9')):
                    codes.append(c)
print(f"第2层: {len(codes)} 只\n")

# ── 取3年财务数据 ──
fin = {}  # code -> {year -> {ibd, np, recv_turn, inv_turn, rev_yoy}}
for i in range(0, len(codes), 200):
    batch = codes[i:i+200]
    ph = ','.join(['?']*len(batch))
    rows = db.execute(f"""
        SELECT stock_code, report_date, interest_bearing_debt, net_profit,
               receivables_turnover, inventory_turnover, revenue_yoy
        FROM stock_financials_annual
        WHERE stock_code IN ({ph})
          AND report_date IN ('2023-12-31','2024-12-31','2025-12-31')
    """, batch).fetchall()
    for r in rows:
        code, yr = r['stock_code'], r['report_date'][:4]
        if code not in fin: fin[code] = {}
        fin[code][yr] = {
            'ibd': r['interest_bearing_debt'],
            'np': r['net_profit'],
            'rt': r['receivables_turnover'],
            'it': r['inventory_turnover'],
            'ry': r['revenue_yoy'],
        }

# ── 逐只检查 ──
passed = []
details = {}
for code in codes:
    fy = fin.get(code, {})
    years = sorted(fy.keys())
    if len(years) < 2:
        continue
    
    d = {}
    
    # 1. 有息负债 / 近3年均净利润 < 5
    nps = [fy[y]['np'] for y in years if fy[y]['np'] is not None]
    ibds = [fy[y]['ibd'] for y in years if fy[y]['ibd'] is not None]
    avg_np = sum(nps)/len(nps) if nps else 0
    latest_ibd = ibds[-1] if ibds else 0
    if avg_np > 0:
        debt_years = latest_ibd / avg_np
        d['debt_years'] = round(debt_years, 1)
        if debt_years >= 5:
            continue
    else:
        d['debt_years'] = None
        continue  # 净利润为负直接排除
    
    # 2. 应收账款/营收 < 30%（1/周转率，用2025年）
    rt = fy.get('2025', {}).get('rt') or fy.get(years[-1], {}).get('rt')
    if rt and rt > 0:
        ar_ratio = 1 / rt * 100  # 周转率=营收/应收，应收占比=1/周转率
        d['ar_ratio'] = round(ar_ratio, 1)
        if ar_ratio >= 30:
            continue
    else:
        d['ar_ratio'] = None  # 数据缺失不排除，标注
    
    # 3. 存货/营收 < 25%（1/周转率，营收口径近似）
    it = fy.get('2025', {}).get('it') or fy.get(years[-1], {}).get('it')
    if it and it > 0:
        inv_ratio = 1 / it * 100
        d['inv_ratio'] = round(inv_ratio, 1)
        if inv_ratio >= 25:
            continue
    else:
        d['inv_ratio'] = None
    
    # 4. 营收增速：无连续2年下滑超10%
    rys = [fy[y]['ry'] for y in sorted(years) if fy[y]['ry'] is not None]
    bad_streak = 0
    decline_ok = True
    for ry in rys:
        if ry < -10:
            bad_streak += 1
            if bad_streak >= 2:
                decline_ok = False
                break
        else:
            bad_streak = 0
    if not decline_ok:
        continue
    d['rev_yoy_list'] = [round(x,1) for x in rys]
    
    passed.append(code)
    details[code] = d

# ── 名称+市值 ──
names = {}; mcs = {}
for i in range(0, len(passed), 200):
    batch = passed[i:i+200]
    ph = ','.join(['?']*len(batch))
    rows = db.execute(f"SELECT stock_code, name FROM stock_basic WHERE stock_code IN ({ph})", batch).fetchall()
    for r in rows: names[r['stock_code']] = r['name']
    rows = db.execute(f"""
        SELECT stock_code, value FROM fundamental_indicator
        WHERE stock_code IN ({ph}) AND metric_code='mc' AND date='2026-07-27'
    """, batch).fetchall()
    for r in rows: mcs[r['stock_code']] = r['value']
db.close()

sp = sorted(passed, key=lambda c: mcs.get(c,0), reverse=True)
sz = [c for c in sp if c.startswith(('3','0'))]
sh = [c for c in sp if c.startswith('6')]
bj = [c for c in sp if c not in set(sz+sh)]

with open('D:/hanako/investment-system/analysis/layer3_result.md', 'w', encoding='utf-8') as f:
    f.write('# 四位大师选股漏斗 · 第3层（芒格：风险排除）\n\n')
    f.write('**生成时间**: 2026-07-31\n\n')
    f.write('## 过滤条件\n\n')
    f.write('1. 有息负债 / 近3年均净利润 < 5年\n')
    f.write('2. 应收账款/营收 < 30%\n')
    f.write('3. 存货/营收 < 25%\n')
    f.write('4. 近3年营收增速无连续2年下滑超10%\n')
    f.write('5. 大股东质押 < 30%（⚠️ 数据缺失，本轮跳过）\n')
    f.write('6. 大股东减持（⚠️ 数据缺失，本轮跳过）\n\n')
    f.write(f'| 步骤 | 通过数 | 淘汰率 |\n')
    f.write(f'|------|-------|-------|\n')
    f.write(f'| 第2层巴菲特 | {len(codes)} | - |\n')
    f.write(f'| 第3层芒格 | **{len(sp)}** | {((len(codes)-len(sp))/len(codes)*100):.0f}% |\n\n')
    f.write(f'| 交易所 | 数量 |\n|--------|------|\n| 深交所 | {len(sz)} |\n| 上交所 | {len(sh)} |\n| 北交所 | {len(bj)} |\n\n')
    f.write('## 股票列表（按市值降序）\n\n')
    f.write('| 代码 | 名称 | 债务年数 | 应收占比% | 存货占比% | 近3年营收增速% | 市值(亿) |\n')
    f.write('|------|------|---------|----------|----------|---------------|---------|\n')
    for code in sp:
        d = details[code]
        ry = '/'.join(str(x) for x in d.get('rev_yoy_list', []))
        f.write(f'| {code} | {names.get(code,"?")} | {d.get("debt_years","?")} | {d.get("ar_ratio","?")} | {d.get("inv_ratio","?")} | {ry} | {mcs.get(code,0)/1e8:.0f} |\n')

print(f"第3层通过: {len(sp)} 只")
print(f"深交所: {len(sz)}, 上交所: {len(sh)}, 北交所: {len(bj)}")
print("结果已保存到 analysis/layer3_result.md")
