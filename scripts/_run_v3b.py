# Fix the actual f-string syntax issue
content = open('D:/hanako/investment-system/scripts/pipeline_mw_cross_v3.py', 'r', encoding='utf-8').read()
# The issue: f'\n{\"-\" * 85}' — escaped quotes inside f-string
# Fix: use double-quote outer, or use a variable
old = '''print(f'\\n{\"-\" * 85}')'''
new = '''print('\\n' + '-' * 85)'''
content = content.replace(old, new)
# Also fix any other similar patterns
content = content.replace("f'\\n{\\\"-\\\" * 85}'", "'\\n' + '-' * 85")
open('D:/hanako/investment-system/scripts/pipeline_mw_cross_v3.py', 'w', encoding='utf-8').write(content)
print("Fixed v2")

exec(compile(content, 'pipeline_mw_cross_v3.py', 'exec'))
