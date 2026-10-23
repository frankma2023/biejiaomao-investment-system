# -*- coding: utf-8 -*-
"""A500 研究第一步：数据可得性调查（skill §二）。"""
import sqlite3

CON = sqlite3.connect('file:data/lixinger.db?mode=ro', uri=True)

print('=' * 72)
print('  1. 名录：哪些代码叫 A500')
for r in CON.execute("""
    SELECT DISTINCT index_code, index_name FROM stock_index
    WHERE index_name LIKE '%A500%' OR index_name LIKE '%A50%'
    ORDER BY index_code"""):
    print('   ', r)

print()
print('  2. K 线与全收益覆盖')
for code in ('000510', '000903', '000300', '000905'):
    k = CON.execute("""
        SELECT COUNT(*), MIN(date), MAX(date) FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal'""", (code,)).fetchone()
    tr = CON.execute("""
        SELECT COUNT(*), MIN(date), MAX(date) FROM index_full_return_daily
        WHERE stock_code=?""", (code,)).fetchone()
    print(f'   {code}: K线 {k[0]:>5} 行 {k[1]} ~ {k[2]}')
    print(f'            全收益 {tr[0]:>5} 行 {tr[1] or "—"} ~ {tr[2] or "—"}')

print()
print('  3. 估值序列（PE/PB/股息率）')
for code in ('000510', '000300', '000905'):
    try:
        q = CON.execute("""
            SELECT COUNT(*), MIN(date), MAX(date) FROM index_fundamental_daily
            WHERE stock_code=?""", (code,)).fetchone()
        print(f'   {code}: {q[0]} 行  {q[1] or "—"} ~ {q[2] or "—"}')
    except sqlite3.Error as e:
        print(f'   {code}: {e}')

print()
print('  4. 成分与权重')
for code in ('000510',):
    try:
        c = CON.execute("SELECT COUNT(*) FROM index_constituents WHERE index_code=?",
                        (code,)).fetchone()[0]
        w = CON.execute("SELECT COUNT(*) FROM index_constituent_weightings WHERE index_code=?",
                        (code,)).fetchone()[0]
        print(f'   {code}: 成分 {c} 行, 权重 {w} 行')
    except sqlite3.Error as e:
        print(f'   {code}: {e}')

print()
print('  5. index_rs_daily 覆盖（含 MA50/150/200）')
for code in ('000510',):
    try:
        q = CON.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM index_rs_daily WHERE stock_code=?",
                        (code,)).fetchone()
        print(f'   {code}: {q[0]} 行  {q[1] or "—"} ~ {q[2] or "—"}')
    except sqlite3.Error as e:
        print(f'   {code}: {e}')

CON.close()
