# -*- coding: utf-8 -*-
"""把 scripts/_*.py 的临时诊断脚本归档到 scripts/_archive/<日期>/（不删除，可随时取回）

保留（作为本轮结论的可复现证据）：
    _verify_dri_vs_qfq.py     lxr_fc vs 官方 DRI 的黄金校验
    _probe5.py / _probe6.py   四口径性质的实测证据
    _diag_chgpct_unit.py      change_pct 单位混用的证据
"""
import os, glob, shutil, datetime, sys, ast
sys.stdout.reconfigure(encoding='utf-8')

KEEP = {'_verify_dri_vs_qfq.py', '_probe5.py', '_probe6.py', '_diag_chgpct_unit.py'}
DEST = os.path.join('scripts', '_archive', datetime.date.today().strftime('%Y%m%d'))
os.makedirs(DEST, exist_ok=True)

moved, kept = [], []
for p in sorted(glob.glob('scripts/_*.py')):
    name = os.path.basename(p)
    if name in KEEP:
        kept.append(name)
        continue
    dst = os.path.join(DEST, name)
    try:
        shutil.move(p, dst)
        # 取首行 docstring 作为说明
        try:
            doc = ast.get_docstring(ast.parse(open(dst, encoding='utf-8').read())) or ''
            doc = doc.strip().split('\n')[0][:70]
        except Exception:
            doc = '(无法解析)'
        moved.append((name, doc))
    except Exception as e:
        print(f'  ⚠ 移动失败 {name}: {e}')

with open(os.path.join(DEST, 'README.md'), 'w', encoding='utf-8') as f:
    f.write(f'# 归档的临时脚本（{datetime.date.today()}）\n\n')
    f.write('这些是开发过程中的一次性诊断/验证脚本，已确认没有任何代码引用它们。\n')
    f.write('需要时把文件拷回 `scripts/` 同级目录即可。\n\n')
    f.write(f'共 {len(moved)} 个：\n\n| 文件 | 用途 |\n|---|---|\n')
    for n, d in moved:
        f.write(f'| `{n}` | {d} |\n')

print(f'✅ 已归档 {len(moved)} 个 → {DEST}')
print(f'   保留 {len(kept)} 个：{", ".join(sorted(kept))}')
print(f'   索引：{os.path.join(DEST, "README.md")}')
