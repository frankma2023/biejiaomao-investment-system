# -*- coding: utf-8 -*-
p = r'D:\hanako\investment-system\web\market-scan\red-dividend\index.html'
src = open(p, encoding='utf-8').read()
# ov += → ovHtml +=（load 内，仅在段 A/B 出现）
import re
n = src.count('ov +=')
src = src.replace('ov +=', 'ovHtml +=')
# 检查是否有误伤（'ov' 其他位置：switchIvTab 的 tab 数组、pane id 等不含 'ov += '）
open(p, 'w', encoding='utf-8').write(src)
print('修复 ov += → ovHtml +=:', n, '处')
print('残留 ov +=:', src.count('ov +='))
