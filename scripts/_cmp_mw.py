import sqlite3, json

db = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db', timeout=10)
db.row_factory = sqlite3.Row
rows = db.execute(
    "SELECT * FROM mw_signal_daily WHERE b1_date BETWEEN '2026-06-01' AND '2026-06-21' ORDER BY b1_date, stock_code"
).fetchall()
new_data = [dict(r) for r in rows]

with open(r'D:\hanako\investment-system\data\backup\mw_signal_2026-06_before_refill.json', encoding='utf-8') as f:
    old_data = json.load(f)

print(f'旧: {len(old_data)}条, 新: {len(new_data)}条')

old_codes = {(s['stock_code'], s['b1_date']) for s in old_data}
new_codes = {(s['stock_code'], s['b1_date']) for s in new_data}

only_old = old_codes - new_codes
only_new = new_codes - old_codes
both = old_codes & new_codes

print(f'共同: {len(both)}  仅旧: {len(only_old)}  仅新: {len(only_new)}')
if only_old:
    print('仅旧有(新无):', list(only_old)[:5])
if only_new:
    print('仅新有(旧无):', list(only_new)[:5])

# 对比评分
if both:
    old_map = {(s['stock_code'], s['b1_date']): s for s in old_data}
    new_map = {(s['stock_code'], s['b1_date']): s for s in new_data}
    diffs = []
    for key in list(both)[:50]:
        o = old_map[key]; n = new_map[key]
        if o.get('score') != n.get('score'):
            diffs.append((key, o.get('score'), n.get('score')))
    print(f'评分差异 (前50共同信号): {len(diffs)}个')
    if diffs:
        for k, os, ns in diffs[:5]:
            print(f'  {k}: {os} -> {ns}')
