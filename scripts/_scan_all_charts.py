# -*- coding: utf-8 -*-
"""全面扫描指数投资栏目所有页面的图表配置（slider 干净度 + 三层布局）"""
import re, os

FILES = [
    r'D:\hanako\investment-system\web\market-scan\red-dividend\index.html',
    r'D:\hanako\investment-system\web\market-scan\red-dividend\lab.html',
    r'D:\hanako\investment-system\web\market-scan\dividend-advice-detail.html',
    r'D:\hanako\investment-system\web\market-scan\fcf-advice-detail.html',
    r'D:\hanako\investment-system\web\market-scan\coal-advice-detail.html',
]

for p in FILES:
    src = open(p, encoding='utf-8').read()
    name = p.split('\\')[-1]
    print(f'=== {name} ===')

    # 1. 所有 slider 配置
    sliders = re.findall(r"\{type:\s*['\"]slider['\"][^}]*\}", src)
    for s in set(sliders):
        clean = 'dataBackground' in s
        h = re.search(r'height:\s*(\d+)', s)
        b = re.search(r'bottom:\s*(\d+)', s)
        print(f'  slider: 迷你图{"已透明" if clean else "❌未透明"} 高度={h.group(1) if h else "?"} bottom={b.group(1) if b else "?"}')

    # 2. legend bottom 分布
    legends = re.findall(r"legend:\{[^}]*?bottom:\s*(\d+)", src)
    if legends:
        dist = {v: legends.count(v) for v in set(legends)}
        print(f'  legend bottom 分布: {dist}')

    # 3. grid bottom 分布
    grids = re.findall(r"grid:\{[^}]*?bottom:\s*(\d+)", src)
    if grids:
        dist = {v: grids.count(v) for v in set(grids)}
        print(f'  grid bottom 分布: {dist}')

    # 4. 检查是否有 slider 但其配置不含透明（漏网）
    dirty = [s[:60] for s in set(sliders) if 'dataBackground' not in s]
    if dirty:
        print(f'  ❌ 未透明 slider: {dirty}')
    print()
