import sqlite3
conn = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')

# 000988 全部日期 vs 最新一期
total_all = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM index_constituents WHERE index_code='000988'").fetchone()[0]
dates = conn.execute("SELECT DISTINCT date FROM index_constituents WHERE index_code='000988' ORDER BY date DESC LIMIT 5").fetchall()
print(f"全指工业(000988) 全部历史去重成分: {total_all} 只")
print(f"最近5期数据:")
for d in dates:
    cnt = conn.execute("SELECT COUNT(*) FROM index_constituents WHERE index_code='000988' AND date=?", [d[0]]).fetchone()[0]
    print(f"  {d[0]}: {cnt} 只")

# 申万行业分组,按工业相关性分类
print()
print("=== 申万一级行业,按'是否与工业相关'分组 ===")
# 中证全指工业大致对应: 机械设备、电力设备、国防军工、基础化工、建筑装饰、汽车、轻工制造、公用事业、环保、交通运输等
industrial_sw_set = {'机械设备','电力设备','国防军工','基础化工','建筑装饰','汽车','轻工制造','公用事业','环保','交通运输','有色金属','钢铁','建筑材料','石油石化','煤炭','电子'}
rows = conn.execute("SELECT industry_name, COUNT(*) as cnt FROM stock_sw_industry GROUP BY industry_name ORDER BY cnt DESC").fetchall()
industrial_total = 0
non_industrial_total = 0
print()
for name, cnt in rows:
    is_ind = name in industrial_sw_set
    tag = "工业相关" if is_ind else "非工业"
    if is_ind:
        industrial_total += cnt
    else:
        non_industrial_total += cnt
    print(f"  [{tag}] {name}: {cnt} 只")

print(f"\n工业相关申万行业合计: {industrial_total} 只")
print(f"非工业申万行业合计: {non_industrial_total} 只")
print(f"全指工业(000988)最新成分: 355 只")
print(f"覆盖率(全指工业/申万工业相关): {355/industrial_total*100:.1f}%")

# 交叉: 全指工业成分股中有多少在申万工业相关行业中
p988 = set(row[0] for row in conn.execute("SELECT DISTINCT stock_code FROM index_constituents WHERE index_code='000988' AND date=(SELECT MAX(date) FROM index_constituents WHERE index_code='000988')"))
sw_ind = set(row[0] for row in conn.execute(f"SELECT DISTINCT stock_code FROM stock_sw_industry WHERE industry_name IN ({','.join(['?' for _ in industrial_sw_set])})", list(industrial_sw_set)))
overlap = p988 & sw_ind
print(f"\n全指工业(000988) ∩ 申万工业 = {len(overlap)} 只")
print(f"全指工业中有申万归属的: {len(p988 & set(row[0] for row in conn.execute('SELECT DISTINCT stock_code FROM stock_sw_industry')))} 只")
print(f"全指工业中无申万归属的: {len(p988 - sw_ind)} 只")

conn.close()
