lines = open("D:/hanako/investment-system/docs/product/像欧奈尔信徒一样交易-手工整理版.md", "r", encoding="utf-8").readlines()
# Find pocket pivot related lines
pp = [(i+1, l.strip()[:130]) for i,l in enumerate(lines) if '口袋支点' in l or 'Pocket Pivot' in l]
print(f"Pocket pivot mentions: {len(pp)}")
for ln, text in pp[:20]:
    print(f"  L{ln}: {text}")

# Find key rules
rules = [(i+1, l.strip()[:130]) for i,l in enumerate(lines) if l.startswith('## ') and ('规则' in l or '法则' in l or '原则' in l or '止损' in l or '买入' in l)]
print(f"\nRule chapters: {len(rules)}")
for ln, text in rules[:20]:
    print(f"  L{ln}: {text}")
