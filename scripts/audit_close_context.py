# -*- coding: utf-8 -*-
"""抽 daily_kline 价格取数点的代码上下文，用于定性「必改 / 不必改 / 待定」

只扫描生产代码（src/ 与 server.py）；scripts/ 与 analysis/ 多为回测与一次性分析，另列。
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = ['src']
WINDOW_BEFORE, WINDOW_AFTER = 260, 620
DL = re.compile(r'\bdaily_kline\b')
TEMP = ('_diag', '_probe', '_check', '_snap', '_t_', '_tmp', '_run')


def files():
    for base in TARGETS:
        p = os.path.join(ROOT, base)
        for dp, dn, fn in os.walk(p):
            dn[:] = [d for d in dn if d not in ('__pycache__', 'backup')]
            for f in fn:
                if f.endswith('.py'):
                    yield os.path.join(dp, f)
    yield os.path.join(ROOT, 'server.py')


skip_pat = re.compile(r'^\s*(SELECT|FROM|WHERE|AND|OR|--|#|\*)', re.I)
for path in files():
    rel = os.path.relpath(path, ROOT).replace('\\', '/')
    try:
        text = open(path, encoding='utf-8', errors='ignore').read()
    except OSError:
        continue
    if 'daily_kline' not in text:
        continue
    lines = text.split('\n')
    sites = []
    for m in DL.finditer(text):
        ln = text.count('\n', 0, m.start()) + 1
        if any(ln in x for x in sites):
            continue
        s, e = max(0, m.start() - WINDOW_BEFORE), min(len(text), m.end() + WINDOW_AFTER)
        frag = text[s:e]
        sites.append((ln, frag))
    if not sites:
        continue
    print('=' * 96)
    print(f'### {rel}   ({len(sites)} 处)')
    print('=' * 96)
    for ln, frag in sites[:2]:
        body = '\n'.join(l for l in frag.split('\n')
                         if l.strip() and not skip_pat.match(l))
        body = '\n'.join(l.rstrip() for l in body.split('\n') if l.strip())
        print(f'--- line {ln} ---')
        print(body[:1400])
        print()
