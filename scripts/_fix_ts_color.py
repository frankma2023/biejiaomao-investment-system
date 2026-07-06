t = open('D:/hanako/investment-system/web/mw-signals/index.html', 'r', encoding='utf-8').read()
# Replace blue #3b82f6 with orange var(--accent)/#f59e0b
t = t.replace('background:rgba(59,130,246,.12);color:#3b82f6', 'background:rgba(245,158,11,.12);color:var(--accent)')
open('D:/hanako/investment-system/web/mw-signals/index.html', 'w', encoding='utf-8').write(t)
print('Done')
