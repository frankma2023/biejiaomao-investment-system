import re
txt = open('D:/hanako/investment-system/scripts/pipeline_mw_cross_v3.py', 'r', encoding='utf-8').read()
# Fix: replace the problematic f-string line
old = "print(f'\\n{\"─\" * 85}')"
new = "print('\\n' + '-' * 85)"
txt = txt.replace(old, new)
open('D:/hanako/investment-system/scripts/pipeline_mw_cross_v3.py', 'w', encoding='utf-8').write(txt)
print('Fixed')
