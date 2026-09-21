#!/usr/bin/env python3
"""用 scripts/db_inventory.py 的实测结果重建 docs/DATABASE_SCHEMA.md 的
「全表汇总」与「数据库统计」两节。

只有这两节被替换，正文各章节不动。已文档化的表按章节号排序并沿用章节说明；
数据库里有、但文档尚无章节的表统一列在末尾并标注「尚无独立章节」。

用法：
    python scripts/db_inventory.py            # 先实测，产出 data/db_inventory.json
    python scripts/regen_schema_summary.py    # 再重建两节
"""
import argparse
import json
import os
import re

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(PROJECT_DIR, 'docs', 'DATABASE_SCHEMA.md')
INV = os.path.join(PROJECT_DIR, 'data', 'db_inventory.json')
DB_FILE = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')


def human(n):
    """把行数写成便于阅读的中文量级。"""
    if n is None:
        return '—'
    if n >= 1e8:
        return f'{n / 1e8:.2f}亿'
    if n >= 1e4:
        return f'{n / 1e4:.1f}万'
    return f'{n:,}'


def strip_parens(s):
    return re.sub(r'[（(][^）)]*[）)]', '', s).strip(' ：:')


def build_summary(doc, rows):
    """章节标题 -> 表名映射：只认真正存在于数据库的 snake_case 标识符。"""
    sections = {}
    for m in re.finditer(r'(?m)^## (\d+)\.\s*(.+?)\s*$', doc):
        num, title = m.groups()
        names_part, _, desc = title.partition(' — ')
        desc = strip_parens(desc)
        for ident in re.findall(r'[a-z][a-z0-9_]{2,}', names_part):
            if ident in rows:
                sections[ident] = (num, desc)

    # 旧汇总里的说明文案作为兜底
    old_desc = {}
    for line in doc.split('\n'):
        m = re.match(r'^\|\s*(?:\d+[a-z]?|—)\s*\|\s*([a-z_0-9]+)\s*\|[^|]*\|\s*(.+?)\s*\|\s*$', line)
        if m:
            old_desc.setdefault(m.group(1), strip_parens(m.group(2)))

    documented = sorted(
        ((t, sections[t]) for t in sections if t in rows),
        key=lambda x: int(x[1][0]),
    )
    undocumented = sorted(t for t in rows if t not in sections)

    out = ['| # | 表名 | 行数 | 说明 |', '|---|------|------|------|']
    for table, (num, desc) in documented:
        out.append(f'| {num} | {table} | {human(rows[table])} | {old_desc.get(table) or desc} |')
    for table in undocumented:
        note = old_desc.get(table, '')
        out.append(f'| — | {table} | {human(rows[table])} | {note + " " if note else ""}**尚无独立章节** |')
    return out, documented, undocumented


def build_stats(inv, rows):
    size_gb = os.path.getsize(DB_FILE) / 1024 ** 3
    top = sorted(((t, r) for t, r in rows.items() if r), key=lambda x: -x[1])[:5]
    out = [
        '| 指标 | 数值 |',
        '|------|------|',
        f'| 统计时间 | {inv["generated_at"]} |',
        f'| 总表数 | {inv["table_count"]} |',
        f'| 视图 | {inv["view_count"]}（{", ".join(inv["views"])}） |',
        f'| 总行数 | {inv["total_rows"]:,}（约 {inv["total_rows"] / 1e8:.2f} 亿） |',
    ]
    for i, (t, r) in enumerate(top, 1):
        out.append(f'| 第{i}大表 | {t}（{human(r)} 行） |')
    out += [
        f'| DB 文件大小 | {size_gb:.1f} GB |',
        '| 数据来源 | `scripts/db_inventory.py` 对 `data/lixinger.db` 实测（只读 COUNT） |',
        '| 重建方式 | `python scripts/db_inventory.py` → `python scripts/regen_schema_summary.py` |',
    ]
    return out


def replace_section(lines, prefix, canonical, body):
    """替换一个 ## 小节的内容，保留其后的 --- 分隔线。

    按 prefix 前缀定位（标题可能带日期后缀），并把标题规范化为 canonical。
    正文范围是「标题之后」到「下一个 ## 之前那条 ---」，分隔线本身保留。
    """
    start = next(i for i, l in enumerate(lines) if l.strip().startswith(prefix))
    lines[start] = canonical

    nxt = start + 1
    while nxt < len(lines) and not lines[nxt].startswith('## '):
        nxt += 1

    sep = nxt
    for i in range(nxt - 1, start, -1):
        if lines[i].strip() == '---':
            sep = i
            break

    lines[start + 1:sep] = [''] + body + ['']
    return lines


def main():
    ap = argparse.ArgumentParser(description='重建 DATABASE_SCHEMA.md 的汇总与统计两节')
    ap.add_argument('--inventory', default=INV, help='db_inventory.py 产出的 JSON')
    args = ap.parse_args()

    with open(args.inventory, encoding='utf-8') as f:
        inv = json.load(f)
    rows = {t: v['rows'] for t, v in inv['tables'].items()}

    with open(DOC, encoding='utf-8') as f:
        lines = f.read().split('\n')
    doc = '\n'.join(lines)

    summary, documented, undocumented = build_summary(doc, rows)
    lines = replace_section(lines, '## 全表汇总', '## 全表汇总', summary)
    lines = replace_section(lines, '## 数据库统计', '## 数据库统计', build_stats(inv, rows))

    with open(DOC, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f'已文档化表: {len(documented)}   未文档化表: {len(undocumented)}')
    for t in undocumented:
        print(f'  (无章节) {t:46} {human(rows[t])}')
    print(f'\n总行数: {inv["total_rows"]:,}   DB 体积: {os.path.getsize(DB_FILE) / 1024 ** 3:.1f} GB')


if __name__ == '__main__':
    main()
