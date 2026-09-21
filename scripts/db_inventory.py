#!/usr/bin/env python3
"""盘点 data/lixinger.db 的表与真实行数，供 docs/DATABASE_SCHEMA.md 的
「全表汇总」「数据库统计」两节使用。

只做只读查询。大表 COUNT(*) 需要全表扫描，实测约 3 分钟，建议后台运行。
结果写 JSON，由 scripts/regen_schema_summary.py 消费。

用法：
    python scripts/db_inventory.py                    # 输出 data/db_inventory.json
    python scripts/db_inventory.py --out /tmp/x.json  # 指定输出路径
"""
import argparse
import json
import os
import sqlite3
import time

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
DEFAULT_OUT = os.path.join(PROJECT_DIR, 'data', 'db_inventory.json')


def main():
    ap = argparse.ArgumentParser(description='盘点 lixinger.db 表与行数')
    ap.add_argument('--out', default=DEFAULT_OUT, help='JSON 输出路径')
    args = ap.parse_args()

    db = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    views = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
    )]
    print(f'表: {len(tables)}  视图: {len(views)}  ->  {views}', flush=True)

    result = {}
    total_rows = 0
    t_all = time.time()
    for i, t in enumerate(tables, 1):
        t0 = time.time()
        try:
            n = db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except Exception as e:
            result[t] = {'rows': None, 'error': repr(e)}
            print(f'[{i:3}/{len(tables)}] {t:44} ERROR {e}', flush=True)
            continue
        dt = time.time() - t0
        total_rows += n
        result[t] = {'rows': n, 'seconds': round(dt, 2)}
        print(f'[{i:3}/{len(tables)}] {t:44} {n:>13,}  {dt:6.1f}s', flush=True)

    db.close()
    payload = {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'db_file': os.path.relpath(DB, PROJECT_DIR),
        'table_count': len(tables),
        'view_count': len(views),
        'views': views,
        'total_rows': total_rows,
        'tables': result,
    }
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f'\n合计行数: {total_rows:,}')
    print(f'总耗时: {time.time() - t_all:.1f}s')
    print(f'已写出: {args.out}')


if __name__ == '__main__':
    main()
