with open("D:/hanako/investment-system/web/shared/js/nav.js","r",encoding="utf-8") as f:
    for i,line in enumerate(f,1):
        if '知行' in line or 'discipline' in line.lower():
            print(f"{i}: {line.rstrip()[:120]}")
