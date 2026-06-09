import sqlite3
conn = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')

l1 = ['000986','000987','000988','000989','000990','000991','000992','000993','000994','000995','931775']
l2 = ['000018','000036','000037','000807','000808','000819','000841','000857','000858','399965','399975','399986','399995','399998','930697','930910','930965','931479','931897','932087','932088','H30171','H30182','H30184','H30198','H30199','H30217','H30463','000935','000936','000937','000908','000909','000910','000911','000912','000913','000915','000917','000928','000929','000930','000931','000932','000933']
all_l12 = list(set(l1 + l2))

ind_stocks = set(row[0] for row in conn.execute('SELECT DISTINCT stock_code FROM stock_industry'))
idx_all = set(row[0] for row in conn.execute('SELECT DISTINCT stock_code FROM stock_index'))
sw_stocks = set(row[0] for row in conn.execute('SELECT DISTINCT stock_code FROM stock_sw_industry'))

p1 = ','.join(['?' for _ in l1])
p2 = ','.join(['?' for _ in l2])
p12 = ','.join(['?' for _ in all_l12])

idx_l1 = set(row[0] for row in conn.execute(f'SELECT DISTINCT stock_code FROM stock_index WHERE index_code IN ({p1})', l1))
idx_l2 = set(row[0] for row in conn.execute(f'SELECT DISTINCT stock_code FROM stock_index WHERE index_code IN ({p2})', l2))
idx_l12 = set(row[0] for row in conn.execute(f'SELECT DISTINCT stock_code FROM stock_index WHERE index_code IN ({p12})', all_l12))

total_basic = conn.execute("SELECT COUNT(*) FROM stock_basic WHERE listing_status='normally_listed'").fetchone()[0]

print(f'A股正常上市股票数 (stock_basic):          {total_basic}')
print(f'有行业归属的股票 (stock_industry):          {len(ind_stocks)}')
print(f'有申万一级行业映射 (stock_sw_industry):     {len(sw_stocks)}')
print(f'stock_index 表中含任意指数的股票:            {len(idx_all)}')
print()
print(f'L1指数覆盖 (11个指数):                      {len(idx_l1)}')
print(f'L2指数覆盖 (45个指数):                      {len(idx_l2)}')
print(f'L1+L2 合计覆盖:                             {len(idx_l12)}')
print()
print('--- 交叉分析 ---')
print(f'有行业归属 AND 被L1+L2覆盖:                 {len(ind_stocks & idx_l12)}')
print(f'有行业归属 BUT 未被L1+L2覆盖:               {len(ind_stocks - idx_l12)}')
print(f'有SW映射 AND 被L1+L2覆盖:                   {len(sw_stocks & idx_l12)}')
print(f'有SW映射 BUT 未被L1+L2覆盖:                 {len(sw_stocks - idx_l12)}')
print(f'被L1+L2覆盖 BUT 无行业归属:                 {len(idx_l12 - ind_stocks)}')
print(f'无行业归属 AND 未被L1+L2覆盖:               {total_basic - len(ind_stocks | idx_l12)}')

# check: L1 detail
print()
print('--- stock_index 中 L1 代码的存在情况 ---')
for code in l1:
    cnt = conn.execute('SELECT COUNT(*) FROM stock_index WHERE index_code=?', [code]).fetchone()[0]
    name = conn.execute("SELECT DISTINCT index_name FROM stock_index WHERE index_code=?", [code]).fetchone()
    nm = name[0] if name else 'NOT IN DB'
    print(f'  {code} {nm}: {cnt} 只')

print()
print('--- stock_index 中 L2 代码的存在情况 ---')
for code in l2:
    cnt = conn.execute('SELECT COUNT(*) FROM stock_index WHERE index_code=?', [code]).fetchone()[0]
    name = conn.execute("SELECT DISTINCT index_name FROM stock_index WHERE index_code=?", [code]).fetchone()
    nm = name[0] if name else 'NOT IN DB'
    print(f'  {code} {nm}: {cnt} 只')

# 0-count ones
print()
print('--- 在 stock_index 中不存在的 L1/L2 代码 ---')
for code in l1 + l2:
    cnt = conn.execute('SELECT COUNT(*) FROM stock_index WHERE index_code=?', [code]).fetchone()[0]
    if cnt == 0:
        print(f'  {code}')

conn.close()
