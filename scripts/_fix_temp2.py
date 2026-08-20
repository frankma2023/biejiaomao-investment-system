# -*- coding: utf-8 -*-
import re

p = r'D:\hanako\investment-system\web\market-scan\dividend-advice-detail.html'
src = open(p, encoding='utf-8').read()

# 1. 温度计图：legend 0 / slider 52 / grid 92（三层各间距≥30）
old1 = "legend: {data: ['股息率', '10年国债', '息差'], bottom: 14, textStyle: {color: tx, fontSize: 9}},"
new1 = "legend: {data: ['股息率', '10年国债', '息差'], bottom: 0, textStyle: {color: tx, fontSize: 9}},"
assert old1 in src
src = src.replace(old1, new1)
# 温度计 slider bottom 38 → 52（该图在 temp-chart 区块）
i_temp = src.find('temp-chart')
seg_head, seg_tail = src[:i_temp], src[i_temp:]
old2 = "dataZoom: [{type: 'inside'}, {type: 'slider', height: 10, bottom: 38,"
new2 = "dataZoom: [{type: 'inside'}, {type: 'slider', height: 10, bottom: 52,"
assert old2 in seg_tail
seg_tail = seg_tail.replace(old2, new2, 1)
# 温度计 grid bottom 62 → 92
old3 = "grid: {left: '8%', right: '4%', top: 12, bottom: 62},"
assert old3 in seg_tail
seg_tail = seg_tail.replace(old3, "grid: {left: '8%', right: '4%', top: 12, bottom: 92},", 1)
src = seg_head + seg_tail
print('温度计图: legend 0 / slider 52 / grid 92')

# 2. 估值全景 slider 统一（旧配置 height16 bottom2 → 干净 height10 bottom24）
old_s = "{type: 'slider', start: 50, end: 100, height: 16, bottom: 2}"
new_s = ("{type: 'slider', start: 50, end: 100, height: 10, bottom: 24,"
         "dataBackground: {lineStyle: {opacity: 0}, areaStyle: {opacity: 0}},"
         "selectedDataBackground: {lineStyle: {opacity: 0}, areaStyle: {opacity: 0}},"
         "borderColor: 'rgba(139,139,144,.25)', fillerColor: 'rgba(139,139,144,.12)',"
         "handleSize: '80%', showDetail: false}")
n = src.count(old_s)
src = src.replace(old_s, new_s)
print('估值全景 slider 替换:', n, '处')

# 3. 估值全景 grid bottom 加大（val-chart 系列图：bottom 34 → 76）
# val 图的 grid 配置可能是 'bottom:34' 或 'bottom: 34'——扫描实际写法
grids = re.findall(r"grid:\{[^}]*?bottom:\s*(\d+)", src)
print('grid bottom 分布:', {g: grids.count(g) for g in set(grids)})
open(p, 'w', encoding='utf-8').write(src)
print('已写入')
