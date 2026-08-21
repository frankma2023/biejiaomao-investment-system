# -*- coding: utf-8 -*-
p = r'D:\hanako\investment-system\web\market-scan\red-dividend\index.html'
src = open(p, encoding='utf-8').read()
fixes = {'red +=': 'redHtml +=', 'fcf +=': 'fcfHtml +=', 'coal +=': 'coalHtml +=',
         'hk +=': 'hkHtml +=', 'broker +=': 'brokerHtml +='}
for old, new in fixes.items():
    n = src.count(old)
    src = src.replace(old, new)
    print(f'{old} → {new}: {n} 处')
open(p, 'w', encoding='utf-8').write(src)
# 验证无残留
for k in fixes:
    assert src.count(k) == 0, k
print('全部替换完成')
