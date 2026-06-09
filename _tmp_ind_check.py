import sqlite3
conn = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')

# 1. index_constituents 中 000988 的成分股数
cnt_988 = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM index_constituents WHERE index_code='000988' AND date=(SELECT MAX(date) FROM index_constituents WHERE index_code='000988')").fetchone()[0]
print(f"全指工业(000988)最新一期成分股: {cnt_988} 只")

# 2. stock_sw_industry 中有多少 "工业" 相关
sw_all = conn.execute("SELECT DISTINCT industry_name FROM stock_sw_industry ORDER BY industry_name").fetchall()
print(f"\n申万一级行业列表 ({len(sw_all)} 个):")
industrial_sw = []
for row in sw_all:
    name = row[0]
    cnt = conn.execute("SELECT COUNT(*) FROM stock_sw_industry WHERE industry_name=?", [name]).fetchone()[0]
    print(f"  {name}: {cnt} 只")
    if '工业' in name or '制造' in name or '机械' in name or '设备' in name:
        industrial_sw.append(name)

# 3. A股正常上市总股票数
total_basic = conn.execute("SELECT COUNT(*) FROM stock_basic WHERE listing_status='normally_listed'").fetchone()[0]
print(f"\nA股正常上市: {total_basic} 只")

# 4. stock_sw_industry 覆盖的股票数
sw_covered = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM stock_sw_industry").fetchone()[0]
print(f"stock_sw_industry 覆盖: {sw_covered} 只")

# 5. 申万一级中 "工业" 具体是哪类
# 中证 L1 全指工业对应哪些申万行业?
print(f"\n--- 申万行业中含'工业'关键词的 ---")
for name in industrial_sw:
    cnt = conn.execute("SELECT COUNT(*) FROM stock_sw_industry WHERE industry_name=?", [name]).fetchone()[0]
    print(f"  {name}: {cnt} 只")

# 6. 检查 stock_industry (source=sw_2021) 中的行业分类
print(f"\n--- stock_industry (source=sw_2021) 含'工业'的行业 ---")
rows = conn.execute("SELECT DISTINCT industry_name, COUNT(DISTINCT stock_code) FROM stock_industry WHERE source='sw_2021' AND industry_name LIKE '%工业%' GROUP BY industry_name ORDER BY 2 DESC").fetchall()
for name, cnt in rows:
    print(f"  {name}: {cnt} 只")

conn.close()
