# -*- coding: utf-8 -*-
"""600309：8-10 vs 8-28 箱体上沿选择对比"""
import sys, os, sqlite3
sys.path.insert(0, r'D:\hanako\investment-system')
sys.path.insert(0, r'D:\hanako\investment-system\src')
os.chdir(r'D:\hanako\investment-system')
from src.scanners import box_breakout as bb

conn = sqlite3.connect(bb.DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute("""SELECT date, open, high, low, close, volume, change_pct FROM daily_kline
    WHERE stock_code='600309' AND date<=? ORDER BY date""", ('2026-08-28',)).fetchall()
conn.close()
daily = [dict(r) for r in rows]
daily = bb._adj_prices(daily)
highs = [k['high'] for k in daily]
closes = [k['close'] for k in daily]
dates = [k['date'] for k in daily]
params = bb.load_params()
touch_threshold = params['min_touches'] * 3  # 9

for target in ['2026-08-10', '2026-08-28']:
    t = dates.index(target)
    print(f'\n═══ {target} 视角（close={closes[t]:.2f}，窗口=前{params["lookback_days"]}日）═══')
    upper_bands = bb._find_bands(highs, t, True, params)
    print('上沿带（touches≥3）:')
    for b in sorted(upper_bands, key=lambda x: -x['level']):
        mark = ''
        if b['touches'] >= touch_threshold:
            mark = ' [合格≥9]'
            if b['level'] <= closes[t]:
                mark += ' [已站上]'
            else:
                mark += f' [未站上({b["level"]:.2f}>{closes[t]:.2f})]'
        print(f"  {b['level']:.2f}  touches={b['touches']}{mark}")
    reached = [b for b in upper_bands if b['level'] <= closes[t] and b['touches'] >= touch_threshold]
    cands = [b for b in upper_bands if b['touches'] >= touch_threshold]
    if reached:
        ub = max(reached, key=lambda x: x['level'])
        print(f'→ ub = {ub["level"]:.2f}（reached 最高：已站上且 touches≥9）')
        # 检查 break_pre（前 40 天有无收盘 > ub）
        pre = closes[max(0, t-params['box_min_days']):t]
        break_pre = [c for c in pre if c > ub['level']]
        print(f'  前40天收盘 > {ub["level"]:.2f} 的天数: {len(break_pre)} → {"拦截(不认突破)" if break_pre else "通过"}')
        print(f'  突破判定: close {closes[t]:.2f} >= {ub["level"]:.2f} → {"突破事件!" if closes[t] >= ub["level"] else "未突破"}')
    elif cands:
        ub = max(cands, key=lambda x: x['level'])
        print(f'→ reached 空（已站上的合格带不存在），cands 最高 = {ub["level"]:.2f}（未站上）→ 不算突破')
    else:
        ub = max(upper_bands, key=lambda x: x['level'])
        print(f'→ 兜底最高带 {ub["level"]:.2f}')
