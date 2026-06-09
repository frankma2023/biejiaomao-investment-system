f=open("D:/hanako/investment-system/web/pattern-scan/index.html","r",encoding="utf-8");lines=f.readlines();f.close()
for i,l in enumerate(lines,1):
    lo=l.lower()
    if 'pocket' in lo or '口袋' in l or 'signal_type' in lo[:40]:
        print(f"{i}: {l.rstrip()[:130]}")
