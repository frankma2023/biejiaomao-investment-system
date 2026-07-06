# Update TS badge to use tier-based colors
t = open('D:/hanako/investment-system/web/mw-signals/index.html', 'r', encoding='utf-8').read()

# Replace the TS badge with tier-specific colors
old = "(isB1?(s.tech_score?'<span class=\"badge\" style=\"background:rgba(245,158,11,.12);color:var(--accent);border:1px solid rgba(245,158,11,.2)\">技术置信度:'+s.tech_score+'/'+(s.tech_score>=85?'极高':s.tech_score>=75?'很高':s.tech_score>=65?'高':s.tech_score>=50?'中':'低')+'</span>'"

# Build tier logic inline in JS
new = """(isB1?(s.tech_score?'<span class=\"badge\" style=\"'+(s.tech_score>=85?'background:rgba(239,68,68,.12);color:#ef4444;border:1px solid rgba(239,68,68,.2)':s.tech_score>=75?'background:rgba(239,68,68,.08);color:#ef4444;border:1px solid rgba(239,68,68,.15)':s.tech_score>=65?'background:rgba(245,158,11,.15);color:#f59e0b;border:1px solid rgba(245,158,11,.3)':'background:rgba(245,158,11,.08);color:#f59e0b;border:1px solid rgba(245,158,11,.15)')+'\">技术置信度:'+s.tech_score+'/'+(s.tech_score>=85?'极高':s.tech_score>=75?'很高':s.tech_score>=65?'高':s.tech_score>=50?'中':'低')+'</span>'"""

t = t.replace(old, new)
open('D:/hanako/investment-system/web/mw-signals/index.html', 'w', encoding='utf-8').write(t)
print('Done')
