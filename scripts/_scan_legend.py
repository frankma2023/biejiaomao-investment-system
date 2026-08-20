# -*- coding: utf-8 -*-
"""全局检查：有 legend 的图，legend bottom 与 grid bottom 是否冲突（年份被压）"""
import re

FILES = [
    r'D:\hanako\investment-system\web\market-scan\red-dividend\index.html',
    r'D:\hanako\investment-system\web\market-scan\dividend-advice-detail.html',
    r'D:\hanako\investment-system\web\market-scan\fcf-advice-detail.html',
    r'D:\hanako\investment-system\web\market-scan\coal-advice-detail.html',
]

for p in FILES:
    src = open(p, encoding='utf-8').read()
    name = p.split('\\')[-1]
    print(f'=== {name} ===')
    # 找每对 legend + 其后的 grid（同图内）
    for m in re.finditer(r"legend:\{[^}]*?bottom:\s*(\d+)[^}]*\}", src):
        lb = int(m.group(1))
        after = src[m.end():m.end() + 800]
        gm = re.search(r"grid:\{[^}]*?bottom:\s*(\d+)", after)
        gb = int(gm.group(1)) if gm else None
        # 图名（往前找 title）
        before = src[max(0, m.start() - 500):m.start()]
        t = re.findall(r"title:\s*'([^']+)'", before)
        title = t[-1] if t else '?'
        has_slider = 'slider' in after[:800]
        conflict = (gb is not None and gb - lb < 40 and not has_slider) or (has_slider and gb is not None and gb - lb < 80)
        flag = '⚠️ 冲突' if conflict else 'ok'
        print(f'  {flag} | {title[:24]:<26} legend={lb} grid={gb} slider={"有" if has_slider else "无"}')
    print()
