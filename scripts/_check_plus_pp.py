import sqlite3
db=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db");db.row_factory=sqlite3.Row

# 22 PLUS signals
plus=db.execute("""
    SELECT stock_code,stock_name,b1_date,b2_date,score,decline_pct
    FROM mw_signal_daily
    WHERE b2_date>='2023-06-01' AND b2_date<='2026-06-05'
    AND score>=80 AND score_d=15 AND score_i1=15 AND score_i2=15
    ORDER BY b2_date
""").fetchall()

# Pocket pivot index
pp={}
for r in db.execute("SELECT stock_code,date,pivot_type,b1_overlap FROM pocket_pivot_daily WHERE date>='2023-06-01'").fetchall():
    pp.setdefault(r['stock_code'],{})[r['date']]=dict(r)

print(f"{'代码':<8}{'名称':<10}{'B1':>12}{'B2':>12}{'B1~B2间PP':>20}{'类型':>16}")
print("-"*80)
count=0
for p in plus:
    code=p['stock_code'];b1=p['b1_date'];b2=p['b2_date']
    pp_stock=pp.get(code,{})
    found=[]
    for date in sorted(pp_stock):
        if b1<=date<=b2:
            found.append(f"{date}({pp_stock[date]['pivot_type']}{'★B1' if pp_stock[date]['b1_overlap'] else ''})")
    if found:
        count+=1
        print(f"{code:<8}{p['stock_name']:<10}{b1:>12}{b2:>12}{found[0]:>20}{pp_stock.get(b1,{}).get('pivot_type',''):>16}")
        for f in found[1:]:
            print(f"{'':>62}{f:>20}")

print(f"\n有PP覆盖: {count}/{len(plus)}")
db.close()
