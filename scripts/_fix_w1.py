# Fix W1: Swap TS and PLUS badge order
lines = open('D:/hanako/investment-system/web/mw-signals/index.html','r',encoding='utf-8').readlines()
# Line 240 is PLUS, 241 is TS — swap them so TS comes before PLUS
lines[239], lines[240] = lines[240], lines[239]
open('D:/hanako/investment-system/web/mw-signals/index.html','w',encoding='utf-8').writelines(lines)
print('W1 done: TS badge now before PLUS badge')
