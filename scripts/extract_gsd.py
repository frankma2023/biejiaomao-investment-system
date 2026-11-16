# -*- coding: utf-8 -*-
"""从 global-stock-data 的 SKILL.md 抽取可导入的 Python 模块。

上游仓库 https://github.com/simonlin1212/global-stock-data 只提供 SKILL.md，
全部实现以 python 代码块的形式写在文档里，且块与块之间有演示语句（会调用
后面才定义的函数），直接拼起来 import 会 NameError。本脚本用 AST 过滤：

  * 保留 import / 函数定义 / 类定义
  * 保留赋值语句，但丢掉「调用了本文件内函数」的演示语句
  * 丢弃其它模块级表达式（示例调用、print、if __name__ 等）

用法：
    python scripts/extract_gsd.py                       # 读 .scratch/global-stock-data/SKILL.md
    python scripts/extract_gsd.py --src path/to/SKILL.md
    python scripts/extract_gsd.py --clone               # 先 git clone 再抽取

产物：src/data/gsd_vendor.py
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import re
import subprocess
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.join(PROJECT, '.scratch', 'global-stock-data', 'SKILL.md')
OUT = os.path.join(PROJECT, 'src', 'data', 'gsd_vendor.py')
REPO = 'https://github.com/simonlin1212/global-stock-data.git'
CLONE_DIR = os.path.join(PROJECT, '.scratch', 'global-stock-data')

HEADER = '''# -*- coding: utf-8 -*-
"""global-stock-data 的实现代码（自动抽取，请勿手改）。

来源：{repo}
抽取脚本：scripts/extract_gsd.py
抽取时间：{stamp}
函数数：{nfun}

上游只提供 SKILL.md，代码以 python 代码块散落在文档里。抽取时用 AST 丢掉了
文档中的演示语句，因此本文件可以安全 import；升级上游时重跑抽取脚本即可。
"""
'''


def extract(src_path: str) -> str:
    raw = io.open(src_path, encoding='utf-8', errors='replace').read()
    blocks = re.findall(r'```python\n(.*?)```', raw, re.S)
    if not blocks:
        raise SystemExit('没有找到 python 代码块：%s' % src_path)
    code = '\n\n'.join(blocks)
    tree = ast.parse(code)
    local_fns = {n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def calls_local(node):
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id in local_fns):
                return True
        return False

    keep, seen = [], set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            keep.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in seen:
                continue
            seen.add(node.name)
            keep.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            if calls_local(node):
                continue
            keep.append(node)

    mod = ast.Module(body=keep, type_ignores=[])
    ast.fix_missing_locations(mod)
    body = ast.unparse(mod)
    compile(body, 'gsd_vendor', 'exec')
    nfun = sum(1 for n in keep if isinstance(n, ast.FunctionDef))
    stamp = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')
    return HEADER.format(repo=REPO, stamp=stamp, nfun=nfun) + '\n' + body + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=DEFAULT_SRC)
    ap.add_argument('--clone', action='store_true', help='先 git clone 上游仓库')
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args()

    if args.clone and not os.path.exists(args.src):
        os.makedirs(os.path.dirname(CLONE_DIR), exist_ok=True)
        subprocess.check_call(['git', 'clone', '--depth', '1', REPO, CLONE_DIR])

    if not os.path.exists(args.src):
        raise SystemExit('找不到 SKILL.md：%s\n先 git clone 上游仓库，或用 --src 指定' % args.src)

    text = extract(args.src)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    io.open(args.out, 'w', encoding='utf-8').write(text)
    print('写入 %s（%d 字节）' % (os.path.relpath(args.out, PROJECT), len(text)))

    # 自检：能否 import
    sys.path.insert(0, PROJECT)
    import importlib
    m = importlib.import_module('src.data.gsd_vendor')
    pub = [x for x in dir(m) if not x.startswith('_')]
    print('import 自检通过，公开名 %d 个' % len(pub))


if __name__ == '__main__':
    main()
