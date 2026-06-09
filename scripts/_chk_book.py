import os
f = "D:/hanako/investment-system/docs/product/像欧奈尔信徒一样交易-手工整理版.md"
s = os.path.getsize(f)
print(f"Size: {s:,} bytes (~{s//3:,} chars)")
with open(f, 'r', encoding='utf-8') as fh:
    lines = fh.readlines()
print(f"Lines: {len(lines)}")
h2s = [l.strip() for l in lines if l.startswith('## ')]
print(f"H2 chapters: {len(h2s)}")
for h in h2s[:40]:
    print(f"  {h}")
