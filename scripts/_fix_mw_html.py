lines = open('D:/hanako/investment-system/web/mw-signals/index.html','r',encoding='utf-8').readlines()
# Insert TS badge after confidence badge (line 240, 0-indexed 239)
tech_line = "        (isB1&&s.tech_score?'<span class=\"badge\" style=\"background:rgba(59,130,246,.12);color:#3b82f6;border:1px solid rgba(59,130,246,.2);margin-left:4px\">TS:'+s.tech_score+'</span>':'')+\n"
lines.insert(240, tech_line)
open('D:/hanako/investment-system/web/mw-signals/index.html','w',encoding='utf-8').writelines(lines)
print('Done')
