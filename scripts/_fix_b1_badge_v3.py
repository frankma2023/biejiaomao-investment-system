# Fix: B1 tab -> TS badge, with MW fallback when TS=0
t = open('D:/hanako/investment-system/web/mw-signals/index.html', 'r', encoding='utf-8').read()

old = "(isB1?(s.tech_score?'<span class=\"badge\" style=\"background:rgba(59,130,246,.12);color:#3b82f6;border:1px solid rgba(59,130,246,.2)\">TS:'+s.tech_score+'/'+(s.tech_score>=85?'极高':s.tech_score>=75?'很高':s.tech_score>=65?'高':s.tech_score>=50?'中':'低')+'</span>':'<span class=\"badge\" style=\"background:rgba(139,139,144,.1);color:#8b8b90;border:1px solid rgba(139,139,144,.2)\">TS:--</span>'):'<span class=\"badge '+badgeCls+'\">'+stars+' '+s.confidence+' ('+s.score+'分)</span>')+"

new = "(isB1?(s.tech_score?'<span class=\"badge\" style=\"background:rgba(59,130,246,.12);color:#3b82f6;border:1px solid rgba(59,130,246,.2)\">TS:'+s.tech_score+'/'+(s.tech_score>=85?'极高':s.tech_score>=75?'很高':s.tech_score>=65?'高':s.tech_score>=50?'中':'低')+'</span>':'<span class=\"badge '+badgeCls+'\">'+stars+' '+s.confidence+'</span>'):'<span class=\"badge '+badgeCls+'\">'+stars+' '+s.confidence+' ('+s.score+'分)</span>')+"

t = t.replace(old, new)
open('D:/hanako/investment-system/web/mw-signals/index.html', 'w', encoding='utf-8').write(t)
print('Done')
