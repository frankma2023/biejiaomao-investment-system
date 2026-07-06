t = open('D:/hanako/investment-system/web/mw-signals/index.html', 'r', encoding='utf-8').read()

# Find the exact TS badge line and replace it entirely
old_badge = "(isB1?(s.tech_score?'<span class=\"badge\" style=\"background:rgba(245,158,11,.12);color:var(--accent);border:1px solid rgba(59,130,246,.2)\">技术置信度:'+s.tech_score+'/'+(s.tech_score>=85?'极高':s.tech_score>=75?'很高':s.tech_score>=65?'高':s.tech_score>=50?'中':'低')+'</span>'"

# New badge with tier-based colors: red for 很高/极高, orange for others
new_badge = """(isB1?(s.tech_score?'<span class=\"badge\" style=\"'+(s.tech_score>=75?'background:rgba(239,68,68,.12);color:#ef4444;border:1px solid rgba(239,68,68,.25)':'background:rgba(245,158,11,.12);color:#f59e0b;border:1px solid rgba(245,158,11,.25)')+'\">技术置信度:'+s.tech_score+'/'+(s.tech_score>=85?'极高':s.tech_score>=75?'很高':s.tech_score>=65?'高':s.tech_score>=50?'中':'低')+'</span>'"""

t = t.replace(old_badge, new_badge)
open('D:/hanako/investment-system/web/mw-signals/index.html', 'w', encoding='utf-8').write(t)
print('Done - 75+ red, <75 orange')
