# Fix f-string encoding in v3 and run
content = open('D:/hanako/investment-system/scripts/pipeline_mw_cross_v3.py', 'r', encoding='utf-8').read()
# The problematic char is U+2500 (BOX DRAWINGS LIGHT HORIZONTAL) in f-string
# Replace all f-string patterns with this char
import re
# Find lines with the pattern f'\n{"─" * 85}'
fixed = content.replace("\u2500", "-")
open('D:/hanako/investment-system/scripts/pipeline_mw_cross_v3.py', 'w', encoding='utf-8').write(fixed)
print("Fixed, running...")

exec(compile(fixed, 'pipeline_mw_cross_v3.py', 'exec'))
