t = open('D:/hanako/investment-system/web/mw-signals/index.html', 'r', encoding='utf-8').read()
# Change: hide PLUS on B1 tab
old = "(s.is_plus ? '<span class=\"badge\" style=\"background:rgba(16,185,129,.12);color:#10b981;border:1px solid rgba(16,185,129,.2);margin-left:4px\">✦ PLUS</span>' : '')"
new = "(!isB1&&s.is_plus ? '<span class=\"badge\" style=\"background:rgba(16,185,129,.12);color:#10b981;border:1px solid rgba(16,185,129,.2);margin-left:4px\">✦ PLUS</span>' : '')"
t = t.replace(old, new)
open('D:/hanako/investment-system/web/mw-signals/index.html', 'w', encoding='utf-8').write(t)
print('Done')
