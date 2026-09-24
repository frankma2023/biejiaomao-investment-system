# -*- coding: utf-8 -*-
"""
PRD v3 ↔ 配置 ↔ 代码 三方一致性校验。

检查：
  1. YAML 的键集合 == REQUIRED_PARAMS（缺一即报错，多一即死配置）
  2. 代码里 params['xxx'] 引用到的键 ⊆ REQUIRED_PARAMS（引用未声明的键 = 运行期 KeyError）
  3. REQUIRED_PARAMS 里未被任何代码引用（含 _build_record 的输出字段）= 死参数
  4. PRD v3 参数表里列出的键 == YAML 键集合
  5. PRD v3 里列出的默认值 == YAML 里的值
"""
import io
import re
import sys

import yaml

PRD = 'docs/product/杯柄形态突破检测引擎_产品需求书_v3.md'
YML = 'config/market/cup_handle_v2.yaml'
SRC = 'src/scanners/cup_handle_v2.py'

fail = 0


def bad(msg):
    global fail
    fail += 1
    print('  [FAIL] ' + msg)


src = io.open(SRC, encoding='utf-8').read()
prd = io.open(PRD, encoding='utf-8').read()
ycfg = yaml.safe_load(io.open(YML, encoding='utf-8'))['cup_handle_v2']

m = re.search(r"REQUIRED_PARAMS = \((.*?)\n\)", src, re.S)
required = set(re.findall(r"'([a-z_]+)'", m.group(1)))
used = set(re.findall(r"params\['([a-z_]+)'\]", src))
ykeys = set(ycfg)

print('YAML 键 %d 个 / REQUIRED_PARAMS %d 个 / 代码引用 %d 个'
      % (len(ykeys), len(required), len(used)))
print('')

print('① YAML 键集合 vs REQUIRED_PARAMS')
for k in sorted(ykeys - required):
    bad('YAML 有但未声明为必需（死配置）: %s' % k)
for k in sorted(required - ykeys):
    bad('声明为必需但 YAML 缺失: %s' % k)
if ykeys == required:
    print('  [OK] 完全一致')
print('')

print('② 代码引用 vs REQUIRED_PARAMS')
for k in sorted(used - required):
    bad('代码引用了未声明的参数（运行期 KeyError）: %s' % k)
if not (used - required):
    print('  [OK] 无越界引用')
print('')

print('③ REQUIRED_PARAMS 中未被引用的（可能是死参数）')
EXTERNAL = {'speed_rule_days', 'speed_rule_gain'}   # 由 discipline/ 消费，引擎不引用
dead = sorted(required - used - EXTERNAL)
if dead:
    print('  [WARN] %s' % ', '.join(dead))
else:
    print('  [OK] 无')
print('')

print('④ PRD v3 参数表 vs YAML')
prd_rows = {}
for line in prd.splitlines():
    if not line.startswith('|'):
        continue
    cells = [c.strip() for c in line.strip('|').split('|')]
    if len(cells) < 2:
        continue
    names = re.findall(r"`([a-z_]+)`", cells[0])
    if not names:
        continue
    vals = re.findall(r"^([0-9.]+)$", cells[1])          # 单值：0.25
    if len(vals) == 1 and len(names) > 1:
        vals = []
    elif not vals:
        vals = [v.strip() for v in cells[1].split('/')]   # 合并行：0.15 / 0.40
    for i, nm in enumerate(names):
        if i < len(vals) and re.match(r"^[0-9.]+$", vals[i]):
            prd_rows[nm] = vals[i]
        else:
            prd_rows.setdefault(nm, None)
prd_keys = set(prd_rows)
for k in sorted(prd_keys - ykeys):
    bad('PRD 列了但 YAML 没有: %s' % k)
for k in sorted(ykeys - prd_keys):
    bad('YAML 有但 PRD 没列: %s' % k)
if prd_keys == ykeys:
    print('  [OK] 完全一致')
print('')

print('⑤ PRD 里写的默认值 vs YAML 实际值')
n_checked = 0
for k, v in sorted(ycfg.items()):
    if k not in prd_rows or prd_rows[k] is None:
        continue
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        continue
    n_checked += 1
    if float(prd_rows[k]) != float(v):
        bad('%s: PRD 写 %s，YAML 是 %s' % (k, prd_rows[k], v))
print('  （已核对 %d 个数值型默认值）' % n_checked)
print('')

print('结论: %s' % ('全部通过' if fail == 0 else '%d 处不一致' % fail))
sys.exit(1 if fail else 0)
