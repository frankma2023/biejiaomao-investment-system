"""
四位大师选股漏斗 - 第1层：段永平（好生意）
条件：
1. 近3年毛利率均值 > 30%
2. 近3年净利率均值 > 10%（净利率=净利润/营收）
3. 近3年ROE均值 > 15%
4. 近3年经营现金流/净利润均值 > 0.8
5. 近3年总股本扩张率 < 20%
"""
import sqlite3

db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db.row_factory = sqlite3.Row

# 读第0层股票列表
codes = []
with open('D:/hanako/investment-system/analysis/layer0_result.md', 'r', encoding='utf-8') as f:
    for line in f:
        if line.startswith('| ') and '---' not in line and '代码' not in line:
            parts = line.strip().split('|')
            if len(parts) >= 6:
                code = parts[1].strip()
                if code.startswith(('0','3','6','9')):
                    codes.append(code)

print(f"第0层: {len(codes)} 只\n")

# ── 取财务数据（2023/2024/2025年报） ──
fin_data = {}  # stock_code -> {year -> {gross_margin, net_margin, roe, ocf_np_ratio}}

chunk = 500
for i in range(0, len(codes), chunk):
    batch = codes[i:i+chunk]
    ph = ','.join(['?']*len(batch))
    rows = db.execute(f"""
        SELECT sfa.* FROM stock_financials_annual sfa
        WHERE sfa.stock_code IN ({ph})
          AND sfa.report_date IN ('2023-12-31','2024-12-31','2025-12-31')
    """, batch).fetchall()
    for r in rows:
        code = r['stock_code']
        year = r['report_date'][:4]
        if code not in fin_data:
            fin_data[code] = {}
        if r['gross_margin'] is not None and r['revenue'] and r['revenue'] > 0:
            fin_data[code][year] = {
                'gross_margin': float(r['gross_margin']),
                'net_margin': float(r['net_profit'] or 0) / float(r['revenue']) * 100,
                'roe': float(r['roe'] or 0),
                'ocf': float(r['operating_cash_flow'] or 0),
                'np': float(r['net_profit'] or 0),
            }

# ── 取股本数据 ──
# 最新总股本（取2025-12-31的outstanding_shares_a）
shares_now = {}
for i in range(0, len(codes), chunk):
    batch = codes[i:i+chunk]
    ph = ','.join(['?']*len(batch))
    rows = db.execute(f"""
        SELECT stock_code, outstanding_shares_a FROM stock_equity_change
        WHERE stock_code IN ({ph}) AND change_date='2025-12-31'
    """, batch).fetchall()
    for r in rows:
        shares_now[r['stock_code']] = r['outstanding_shares_a']

# 3年前总股本（取2022-12-31的outstanding_shares_a）
shares_3y = {}
for i in range(0, len(codes), chunk):
    batch = codes[i:i+chunk]
    ph = ','.join(['?']*len(batch))
    rows = db.execute(f"""
        SELECT stock_code, outstanding_shares_a FROM stock_equity_change
        WHERE stock_code IN ({ph}) AND change_date='2022-12-31'
    """, batch).fetchall()
    for r in rows:
        shares_3y[r['stock_code']] = r['outstanding_shares_a']

# 没有2022-12-31的用2023-12-31代替
for i in range(0, len(codes), chunk):
    batch = [c for c in codes if c not in shares_3y]
    if not batch: break
    batch = batch[:chunk]
    ph = ','.join(['?']*len(batch))
    rows = db.execute(f"""
        SELECT stock_code, outstanding_shares_a FROM stock_equity_change
        WHERE stock_code IN ({ph}) AND change_date='2023-12-31'
    """, batch).fetchall()
    for r in rows:
        shares_3y[r['stock_code']] = r['outstanding_shares_a']

db.close()

# ── 计算每个条件 ──
passed = []
details = {}

for code in codes:
    fy = fin_data.get(code, {})
    years = sorted(fy.keys())
    if len(years) < 2:  # 至少2年数据
        continue
    
    # 1. 毛利率
    gm_vals = [fy[y]['gross_margin'] for y in years if fy[y]['gross_margin'] is not None]
    gm_avg = sum(gm_vals) / len(gm_vals) if gm_vals else 0
    
    # 2. 净利率
    nm_vals = [fy[y]['net_margin'] for y in years if fy[y]['net_margin'] is not None]
    nm_avg = sum(nm_vals) / len(nm_vals) if nm_vals else 0
    
    # 3. ROE
    roe_vals = [fy[y]['roe'] for y in years if fy[y]['roe'] is not None]
    roe_avg = sum(roe_vals) / len(roe_vals) if roe_vals else 0
    
    # 4. 经营现金流/净利润（使用3年合计值避免负分母问题）
    total_ocf = sum(fy[y]['ocf'] for y in years)
    total_np = sum(fy[y]['np'] for y in years)
    ocf_np_ratio = total_ocf / total_np if total_np > 0 else 0
    
    # 5. 股本扩张率
    now_sh = shares_now.get(code)
    old_sh = shares_3y.get(code)
    share_expand = 0
    if now_sh and old_sh and old_sh > 0:
        share_expand = (now_sh - old_sh) / old_sh * 100
    
    # 全部条件判断
    cond1 = gm_avg > 30
    cond2 = nm_avg > 10
    cond3 = roe_avg > 15
    cond4 = ocf_np_ratio > 0.8
    cond5 = share_expand < 20
    
    if cond1 and cond2 and cond3 and cond4 and cond5:
        passed.append(code)
        details[code] = {
            'gm': round(gm_avg, 1),
            'nm': round(nm_avg, 1),
            'roe': round(roe_avg, 1),
            'ocf_np': round(ocf_np_ratio, 2),
            'sh_exp': round(share_expand, 1),
        }

# 关闭主DB连接后重新打开取名字和市值
db2 = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
db2.row_factory = sqlite3.Row
names = {}
for i in range(0, len(passed), chunk):
    batch = passed[i:i+chunk]
    ph = ','.join(['?']*len(batch))
    rows = db2.execute(f"SELECT stock_code, name FROM stock_basic WHERE stock_code IN ({ph})", batch).fetchall()
    for r in rows:
        names[r['stock_code']] = r['name']

# ── 输出 ──
sorted_passed = sorted(passed)
print(f"第1层通过: {len(sorted_passed)} 只\n")

# 交易所分布
sz = [c for c in sorted_passed if c.startswith(('3','0'))]
sh = [c for c in sorted_passed if c.startswith('6')]
bj = [c for c in sorted_passed if c not in set(sz+sh)]
print(f"深交所: {len(sz)}, 上交所: {len(sh)}, 北交所: {len(bj)}\n")

# 按市值排序（取市值数据）
mc_data = {}
for i in range(0, len(sorted_passed), chunk):
    batch = sorted_passed[i:i+chunk]
    ph = ','.join(['?']*len(batch))
    rows = db2.execute(f"""
        SELECT a.stock_code, a.value FROM fundamental_indicator a
        INNER JOIN (SELECT stock_code, MAX(date) AS md FROM fundamental_indicator WHERE metric_code='mc' GROUP BY stock_code) b
        ON a.stock_code=b.stock_code AND a.date=b.md
        WHERE a.metric_code='mc' AND a.stock_code IN ({ph})
    """, batch).fetchall()
    for r in rows:
        mc_data[r['stock_code']] = r['value']

sorted_by_mc = sorted(sorted_passed, key=lambda c: mc_data.get(c, 0), reverse=True)

# 写入MD
with open('D:/hanako/investment-system/analysis/layer1_result.md', 'w', encoding='utf-8') as f:
    f.write('# 四位大师选股漏斗 · 第1层（段永平：好生意）\n\n')
    f.write('**生成时间**: 2026-07-28\n\n')
    f.write('## 过滤条件\n\n')
    f.write('1. 近3年毛利率均值 > 30%\n')
    f.write('2. 近3年净利率均值 > 10%\n')
    f.write('3. 近3年ROE均值 > 15%\n')
    f.write('4. 近3年经营现金流/净利润均值 > 0.8\n')
    f.write('5. 近3年总股本扩张率 < 20%\n\n')
    f.write(f'| 步骤 | 通过数 |\n')
    f.write(f'|------|-------|\n')
    f.write(f'| 第0层基础池 | {len(codes)} |\n')
    f.write(f'| 第1层段永平 | **{len(sorted_passed)}** |\n\n')
    f.write(f'## 交易所分布\n\n')
    f.write(f'| 交易所 | 数量 |\n')
    f.write(f'|--------|------|\n')
    f.write(f'| 深交所 | {len(sz)} |\n')
    f.write(f'| 上交所 | {len(sh)} |\n')
    f.write(f'| 北交所 | {len(bj)} |\n\n')
    f.write(f'## 股票列表（按市值降序）\n\n')
    f.write('| 代码 | 名称 | 交易所 | 毛利率% | 净利率% | ROE% | OCF/净利 | 股本扩张% | 市值(亿) |\n')
    f.write('|------|------|--------|---------|---------|------|----------|----------|---------|\n')
    for code in sorted_by_mc:
        info = details.get(code, {})
        mkt = '沪' if code.startswith('6') else ('深' if code.startswith(('3','0')) else '北')
        mc_v = mc_data.get(code, 0)
        f.write(f'| {code} | {names.get(code,"?")} | {mkt} | {info.get("gm","?")} | {info.get("nm","?")} | {info.get("roe","?")} | {info.get("ocf_np","?")} | {info.get("sh_exp","?")} | {mc_v/1e8:.0f} |\n')

print(f"结果已保存到 analysis/layer1_result.md")
print(f"共 {len(sorted_passed)} 只")

db2.close()
