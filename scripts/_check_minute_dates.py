import struct, os, sys
sys.path.insert(0, 'D:/hanako/investment-system')
from scripts.fetch_tdx_minute import parse_lc1

for code in ['600519', '000001', '300750']:
    market = 'sh' if code.startswith(('5','6','9')) else 'sz'
    path = f'D:/new_tdx/vipdoc/{market}/minline/{market}{code}.lc1'
    rows = parse_lc1(path)
    if rows:
        print(f'{code}: {len(rows)} bars, {rows[0][0]} ~ {rows[-1][0]}')
    else:
        print(f'{code}: no data or file not found')
