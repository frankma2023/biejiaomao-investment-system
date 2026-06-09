t = open("D:/hanako/investment-system/src/server.py", "r", encoding="utf-8").read()
for i, line in enumerate(t.split('\n'), 1):
    if 'pattern' in line.lower() or 'pocket' in line.lower():
        print(f"{i}: {line.strip()[:120]}")
