# Fix B1 tab badge logic - replace MW score with TS score
lines = open('D:/hanako/investment-system/web/mw-signals/index.html','r',encoding='utf-8').readlines()

# Replace line 239 (confidence badge) and line 240 (TS badge) with conditional logic
# Old: always show MW confidence, plus optional TS
# New: B1 tab → show TS only; B2 tab → show MW confidence

new_lines = []
for i, line in enumerate(lines):
    if i == 238:  # Line 239 (0-indexed 238): confidence badge
        # Replace with conditional: B1→TS, B2→MW confidence
        new_lines.append(
            "        (isB1\\n" +
            "          ? (s.tech_score\\n" +
            "            ? '<span class=\"badge\" style=\"background:rgba(59,130,246,.12);color:#3b82f6;border:1px solid rgba(59,130,246,.2)\">TS:'+s.tech_score+'/'+(s.tech_score>=85?'极高':s.tech_score>=75?'很高':s.tech_score>=65?'高':s.tech_score>=50?'中':'低')+'</span>'\\n" +
            "            : '<span class=\"badge\" style=\"background:rgba(139,139,144,.1);color:#8b8b90;border:1px solid rgba(139,139,144,.2)\">TS:--</span>')\\n" +
            "          : '<span class=\"badge '+badgeCls+'\">'+stars+' '+s.confidence+' ('+s.score+'分)</span>')+\\n"
        )
    elif i == 239:  # Line 240: old TS badge — remove (merged into above)
        continue
    else:
        new_lines.append(line)

open('D:/hanako/investment-system/web/mw-signals/index.html','w',encoding='utf-8').writelines(new_lines)
print('Done')
