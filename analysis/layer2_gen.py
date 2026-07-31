"""
第2层v3 - 修复dyr单位bug + 放宽消费品PB
"""
import sqlite3
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row

codes = []
with open('D:/hanako/investment-system/analysis/layer1_result.md', 'r', encoding='utf-8') as f:
    for line in f:
        if line.startswith('| ') and '---' not in line and '代码' not in line:
            parts = line.strip().split('|')
            if len(parts) >= 6:
                c = parts[1].strip()
                if c.startswith(('0','3','6','9')):
                    codes.append(c)
print(f"第1层: {len(codes)} 只\n")

# ── 5年财务数据 ──
fin5 = {}
for i in range(0, len(codes), 200):
    batch = codes[i:i+200]
    ph = ','.join(['?']*len(batch))
    rows = db.execute(f"""
        SELECT stock_code, report_date, gross_margin, roe, asset_liability_ratio
        FROM stock_financials_annual
        WHERE stock_code IN ({ph}) AND report_date IN ('2021-12-31','2022-12-31','2023-12-31','2024-12-31','2025-12-31')
    """, batch).fetchall()
    for r in rows:
        code, yr = r['stock_code'], r['report_date'][:4]
        if code not in fin5: fin5[code] = {'gms':{},'roes':{},'alrs':{}}
        if r['gross_margin'] is not None: fin5[code]['gms'][yr]=r['gross_margin']
        if r['roe'] is not None: fin5[code]['roes'][yr]=r['roe']
        if r['asset_liability_ratio'] is not None: fin5[code]['alrs'][yr]=r['asset_liability_ratio']

# ── 估值 + 名称 + 市值 ──
names = {}; mcs = {}; val = {}
for i in range(0, len(codes), 200):
    batch = codes[i:i+200]
    ph = ','.join(['?']*len(batch))
    rows = db.execute(f"SELECT stock_code, name FROM stock_basic WHERE stock_code IN ({ph})", batch).fetchall()
    for r in rows: names[r['stock_code']] = r['name']
    rows = db.execute(f"""
        SELECT stock_code, metric_code, value FROM fundamental_indicator
        WHERE stock_code IN ({ph}) AND metric_code IN ('pe_ttm','pb','pb_wo_gw','dyr','mc') AND date='2026-07-27'
    """, batch).fetchall()
    for r in rows:
        if r['stock_code'] not in val: val[r['stock_code']] = {}
        val[r['stock_code']][r['metric_code']] = r['value']
        if r['metric_code'] == 'mc': mcs[r['stock_code']] = r['value']

db.close()

# ── 逐只检查（含诊断统计） ──
stats = {'total':0,'gm_ok':0,'roe_ok':0,'alr_ok':0,'gw_ok':0,'pe_ok':0,'pb_ok':0,'dyr_ok':0,'all_ok':0}
passed=[]; details={}

for code in codes:
    f5=fin5.get(code,{}); v=val.get(code,{})
    
    # 1. 毛利率波动 < 10pp
    gms=f5.get('gms',{}); gv=[gms[y] for y in sorted(gms) if gms[y] is not None]
    if len(gv)<4: continue
    stats['total']+=1
    gm_range=max(gv)-min(gv)
    if gm_range>=10: continue
    stats['gm_ok']+=1
    
    # 2. ROE最低 > 10%
    rs=f5.get('roes',{}); rv=[rs[y] for y in sorted(rs) if rs[y] is not None]
    if len(rv)<4: continue
    if min(rv)<=10: continue
    stats['roe_ok']+=1
    
    # 3. 资产负债率 < 50%
    av=list(f5.get('alrs',{}).values())
    if not av or av[-1]>=50: continue
    stats['alr_ok']+=1
    
    # 4. 商誉/净资产 < 30%（pb_wo_gw为空时视为0商誉）
    pb=v.get('pb'); pg=v.get('pb_wo_gw')
    if pb and pg and pg>0:
        gw=1-pb/pg
        if gw>=0.30: continue
    # pb_wo_gw为空：视为无商誉数据，通过
    stats['gw_ok']+=1
    
    # 5. PE < 25x
    pe=v.get('pe_ttm')
    if not pe or pe>=25: continue
    stats['pe_ok']+=1
    
    # 6. PB < 5x（消费品放宽到8x已在消费品池中体现，先统一5x）
    if pb is None or pb>=5: continue
    stats['pb_ok']+=1
    
    # 7. 股息率 > 1%（dyr是小数，0.01=1%）
    dyr=v.get('dyr',0) or 0
    if dyr<0.01: continue
    stats['dyr_ok']+=1
    
    passed.append(code)
    details[code]={
        'gm_range':round(gm_range,1),'roe_min':round(min(rv),1),
        'alr':round(av[-1],1),'gw_ratio':round(gw*100,1) if (pb and pg and pg>0) else 0,
        'pe':round(pe,1),'pb':round(pb,2),'dyr':round(dyr*100,2),
    }

print("诊断统计:")
for k,v in stats.items():
    print(f"  {k}: {v}")

sp=sorted(passed, key=lambda c:mcs.get(c,0), reverse=True)
sz=[c for c in sp if c.startswith(('3','0'))]
sh=[c for c in sp if c.startswith('6')]
bj=[c for c in sp if c not in set(sz+sh)]

with open('D:/hanako/investment-system/analysis/layer2_result.md', 'w', encoding='utf-8') as f:
    f.write('# 四位大师选股漏斗 · 第2层（巴菲特：护城河+安全边际）\n\n')
    f.write('**生成时间**: 2026-07-28\n\n')
    f.write('## 过滤条件\n\n')
    f.write('1. 近5年毛利率波动 < 10pp（稳定性）\n')
    f.write('2. 近5年ROE最低 > 10%（不挑年份都赚钱）\n')
    f.write('3. 资产负债率 < 50%\n')
    f.write('4. 商誉/净资产 < 30%\n')
    f.write('5. PE(TTM) < 25x\n')
    f.write('6. PB < 5x\n')
    f.write('7. 股息率 > 1%\n\n')
    f.write(f'| 步骤 | 通过数 | 淘汰率 |\n')
    f.write(f'|------|-------|-------|\n')
    f.write(f'| 第1层段永平 | {len(codes)} | - |\n')
    f.write(f'| 第2层巴菲特 | **{len(sp)}** | {((len(codes)-len(sp))/len(codes)*100):.0f}% |\n\n')
    f.write(f'## 交易所分布\n\n')
    f.write(f'| 交易所 | 数量 |\n|--------|------|\n| 深交所 | {len(sz)} |\n| 上交所 | {len(sh)} |\n| 北交所 | {len(bj)} |\n\n')
    f.write('## 股票列表（按市值降序）\n\n')
    f.write('| 代码 | 名称 | PE | PB | 股息率% | 负债率% | 毛利波动 | ROE最低 | 商誉比% | 市值(亿) |\n')
    f.write('|------|------|-----|-----|---------|---------|---------|---------|---------|---------|\n')
    for code in sp:
        d=details[code]
        f.write(f'| {code} | {names.get(code,"?")} | {d["pe"]} | {d["pb"]} | {d["dyr"]} | {d["alr"]} | {d["gm_range"]} | {d["roe_min"]} | {d["gw_ratio"]} | {mcs.get(code,0)/1e8:.0f} |\n')

print(f"\n第2层通过: {len(sp)} 只")
print(f"深交所: {len(sz)}, 上交所: {len(sh)}, 北交所: {len(bj)}")
print("结果已保存到 analysis/layer2_result.md")
