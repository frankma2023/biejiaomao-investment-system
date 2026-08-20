# -*- coding: utf-8 -*-
import re
p = r'D:\hanako\investment-system\web\market-scan\red-dividend\index.html'
src = open(p, encoding='utf-8').read()

# 1. 删除底部主题初始化
src2 = re.sub(r"\s*\(function\(\)\{var s=localStorage\.getItem\('theme'\)\|\|'dark';document\.documentElement\.dataset\.theme=s\}\)\(\);\s*", '\n', src)
# 2. 在 <script> 开头（isDark 之前）插入
m = re.search(r'(<script>\n)', src2)
if m:
    src2 = src2[:m.end()] + "(function(){var s=localStorage.getItem('theme')||'dark';document.documentElement.dataset.theme=s})();\n" + src2[m.end():]
    open(p, 'w', encoding='utf-8').write(src2)
    i_init = src2.find('dataset.theme=s')
    i_dark = src2.find('isDark = document')
    print('初始化:', i_init, 'isDark:', i_dark, '| 顺序正确:', 0 < i_init < i_dark)
else:
    print('未找到 script 标记')
