# Fix validate_signals.py - add PPV1
t = open('D:/hanako/investment-system/scripts/validate_signals.py', 'r', encoding='utf-8').read()

# Add PPV1 to daily detail SQL
old_sql = "(SELECT COUNT(*) FROM pocket_pivot_daily WHERE date=d.date AND engine_version='V2') as ppv2,"
new_sql = "(SELECT COUNT(*) FROM pocket_pivot_daily WHERE date=d.date AND engine_version='V1') as ppv1,\n            (SELECT COUNT(*) FROM pocket_pivot_daily WHERE date=d.date AND engine_version='V2') as ppv2,"
t = t.replace(old_sql, new_sql)

# Update check line: add r[5] for PP_V1
t = t.replace("if r[1] == 0 and r[3] == 0 and r[4] == 0:", "if r[1] == 0 and r[3] == 0 and r[4] == 0 and r[5] == 0:")

# Update print line to include PPV1
old_print = "print(f'  {r[0]}  B1={r[1]:>4}  B2={r[2]:>4}  PPV2={r[3]:>3}  Sell={r[4]:>4}{flags}')"
new_print = "print(f'  {r[0]}  B1={r[1]:>4}  B2={r[2]:>4}  PPV1={r[3]:>4}  PPV2={r[4]:>3}  Sell={r[5]:>4}{flags}')"
t = t.replace(old_print, new_print)

open('D:/hanako/investment-system/scripts/validate_signals.py', 'w', encoding='utf-8').write(t)
print('Done')
