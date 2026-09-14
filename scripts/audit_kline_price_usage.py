#!/usr/bin/env python3
"""
scripts/audit_kline_price_usage.py — 盘点全仓库对 daily_kline 价格列的读取方式

用途
----
close 口径统一（不再是三段混用）之后，需要知道还有哪些代码在按老习惯取价。
本脚本静态扫描仓库源码，把每个读取 daily_kline 价格列的位置按取数方式分类：

  E  lxr_fc_*      已用理杏仁前复权（正确）
  B  adj_*         用 adj_open/adj_close 等（该列已废弃，取值≈不复权）
  A  COALESCE(adj_close, close)   旧兜底写法（实际恒等于不复权价）
  C  close/OHLC   直接读原始价格列
  D  change_pct    读涨跌幅（注意单位混用缺陷）
  X  ex_*          读不复权显式列

输出按文件分组，附行号，便于逐个改造。

用法
----
    python scripts/audit_kline_price_usage.py
    python scripts/audit_kline_price_usage.py --include-temp   # 连 _diag_/_probe_ 临时脚本一起列
    python scripts/audit_kline_price_usage.py --only B,A      # 只看某几类
"""

import argparse
import os
import re
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCAN_DIRS = ['src', 'scripts', 'analysis', 'web', '.agents/skills', 'tests']
EXTS = ('.py', '.js', '.html')
SKIP_DIRS = {'__pycache__', 'node_modules', '.git', 'backup', 'dist', 'venv', '.venv'}
TEMP_PREFIX = ('_diag', '_probe', '_check', '_snap', '_t_', '_tmp', '_run')

# 价格列取值方式的识别规则（按优先级从上到下匹配，命中即归类）
RULES = [
    ('E', re.compile(r'lxr_fc_(open|high|low|close)')),
    ('X', re.compile(r'\bex_(open|high|low|close)\b')),
    ('A', re.compile(r'COALESCE\s*\(\s*adj_(open|high|low|close)\s*,\s*(open|high|low|close)\s*\)')),
    ('B', re.compile(r'\badj_(open|high|low|close)\b')),
    ('C', re.compile(r'(?<![\w.])(open|high|low|close)(?![\w(])')),
    ('D', re.compile(r'\bchange_pct\b')),
]
LABEL = {
    'E': 'E lxr_fc_*   已用正确前复权',
    'X': 'X ex_*       不复权显式列',
    'A': 'A COALESCE(adj_*, x)  旧兜底写法',
    'B': 'B adj_*      已废弃列',
    'C': 'C close/OHLC 原始价格列',
    'D': 'D change_pct 涨跌幅',
}
ORDER = ['E', 'X', 'A', 'B', 'C', 'D']

DL = re.compile(r'\bdaily_kline\b')
WINDOW = 420          # 以 daily_kline 出现位置为中心的上下文窗口（字符）


def walk():
    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(EXTS):
                    yield os.path.join(dirpath, fn)


def classify(seg: str):
    for tag, rx in RULES:
        if rx.search(seg):
            return tag
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--include-temp', action='store_true')
    ap.add_argument('--only', default='')
    args = ap.parse_args()
    only = {x.strip().upper() for x in args.only.split(',') if x.strip()}

    hits = defaultdict(lambda: defaultdict(list))   # file -> tag -> [(line, snippet)]
    n_files = 0
    for path in walk():
        rel = os.path.relpath(path, ROOT).replace('\\', '/')
        name = os.path.basename(path)
        if not args.include_temp and name.startswith(TEMP_PREFIX):
            continue
        try:
            text = open(path, encoding='utf-8', errors='ignore').read()
        except OSError:
            continue
        if 'daily_kline' not in text:
            continue
        n_files += 1
        for m in DL.finditer(text):
            s, e = max(0, m.start() - WINDOW), min(len(text), m.end() + WINDOW)
            seg = text[s:e]
            tag = classify(seg)
            if not tag:
                continue
            line = text.count('\n', 0, m.start()) + 1
            idx = seg.find('daily_kline')
            frag = ' '.join(seg[max(0, idx - 90):idx + 90].split())
            hits[rel][tag].append((line, frag[:150]))

    # ── 汇总统计 ──
    tag_files = defaultdict(set)
    for f, bytag in hits.items():
        for t in bytag:
            tag_files[t].add(f)
    print('=' * 78)
    print('daily_kline 价格列取数方式盘点')
    print(f'扫描目录 {SCAN_DIRS}｜命中 {n_files} 个文件')
    print('=' * 78)
    for t in ORDER:
        if only and t not in only:
            continue
        fs = tag_files.get(t, set())
        print(f'  {LABEL[t]:32s} {len(fs):>3} 个文件')

    print()
    for t in ORDER:
        if only and t not in only:
            continue
        files = sorted(f for f, bytag in hits.items() if t in bytag)
        if not files:
            continue
        print('─' * 78)
        print(f'### {LABEL[t]}')
        print('─' * 78)
        for f in files:
            sites = hits[f][t]
            lines = ', '.join(str(x[0]) for x in sites[:12])
            more = f' …共 {len(sites)} 处' if len(sites) > 12 else ''
            print(f'  {f}\n      行 {lines}{more}')
    print()


if __name__ == '__main__':
    main()
