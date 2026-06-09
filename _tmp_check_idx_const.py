import sqlite3
conn = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')

l1 = ['000986','000987','000988','000989','000990','000991','000992','000993','000994','000995','931775']
l2 = ['000018','000036','000037','000807','000808','000819','000841','000857','000858','399965','399975','399986','399995','399998','930697','930910','930965','931479','931897','932087','932088','H30171','H30182','H30184','H30198','H30199','H30217','H30463','000935','000936','000937','000908','000909','000910','000911','000912','000913','000915','000917','000928','000929','000930','000931','000932','000933']

print("=== index_constituents L1/L2 ===")
for code in l1 + l2:
    cnt = conn.execute("SELECT COUNT(*) FROM index_constituents WHERE index_code=?", [code]).fetchone()[0]
    latest = conn.execute("SELECT MAX(date) FROM index_constituents WHERE index_code=?", [code]).fetchone()[0]
    print(f"  {code}: {cnt} 只, 最新日期={latest}")

print()
print("=== stock_index L1/L2 (对比) ===")
for code in l1 + l2:
    cnt = conn.execute("SELECT COUNT(*) FROM stock_index WHERE index_code=?", [code]).fetchone()[0]
    name = conn.execute("SELECT DISTINCT index_name FROM stock_index WHERE index_code=?", [code]).fetchone()
    nm = name[0] if name else 'NOT IN DB'
    print(f"  {code} {nm}: {cnt} 只")

print()
# Check existing scripts
print("=== fetch_index_constituents.py ===")
import os
script_path = "D:/hanako/investment-system/scripts/fetch_index_constituents.py"
if os.path.exists(script_path):
    with open(script_path, "r", encoding="utf-8") as f:
        # just first 50 lines
        for i, line in enumerate(f):
            if i < 50:
                print(line, end="")
            else:
                break
else:
    print("FILE NOT FOUND")

conn.close()
