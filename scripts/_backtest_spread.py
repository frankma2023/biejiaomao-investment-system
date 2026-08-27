# -*- coding: utf-8 -*-
"""红利指数 × 股债息差 回测：息差分 5 层 → 前瞻 20/60 日胜率（确定有效阈值）"""
import sqlite3
from collections import defaultdict

db = sqlite3.connect(r'D:\hanako\investment-system\data\lixinger.db')
db.row_factory = sqlite3.Row

# 国债 10Y（2018 起）
bond = {r['date']: r['y10'] for r in db.execute("SELECT date, y10 FROM bond_yield_daily ORDER BY date").fetchall()}
print(f'国债数据: {len(bond)} 条 {min(bond)} ~ {max(bond)}')

INDICES = [('000922', '中证红利'), ('H30269', '红利低波'), ('931468', '红利质量'), ('000015', '红利指数'), ('931848', '800红利低波')]

for code, name in INDICES:
    # 估值（dyr 股息率）+ K线 close
    val = db.execute("""SELECT date, dyr FROM index_fundamental_daily WHERE stock_code=?
        AND dyr IS NOT NULL ORDER BY date""", (code,)).fetchall()
    krows = db.execute("""SELECT date, close FROM index_daily_kline WHERE stock_code=?
        AND kline_type='normal' ORDER BY date""", (code,)).fetchall()
    closes = {r['date']: r['close'] for r in krows}
    dates = sorted(closes.keys())

    # 逐日：息差 = dyr - y10（国债用最近可用）
    spreads = []  # (date, spread)
    bond_dates = sorted(bond.keys())
    for r in val:
        d = r['date']
        if d not in closes:
            continue
        # 找最近国债日期（<= d）
        import bisect
        idx = bisect.bisect_right(bond_dates, d) - 1
        if idx < 0:
            continue
        y10 = bond[bond_dates[idx]]
        spread = r['dyr'] * 100 - y10
        spreads.append((d, spread))

    # 分层统计：息差 <=1 / 1-1.5 / 1.5-2 / 2-2.5 / 2.5-3 / >3 pp
    layers = [(0, 1), (1, 1.5), (1.5, 2), (2, 2.5), (2.5, 3), (3, 99)]
    print(f'\n=== {name}（{code}）息差 {len(spreads)} 条 {spreads[0][0]}~{spreads[-1][0]} ===')
    print(f"{'息差区间(pp)':<14}{'触发日':<8}{'20日胜率':<10}{'20日中位':<10}{'60日胜率':<10}{'60日中位':<10}")
    for lo, hi in layers:
        idxs = [i for i, (d, s) in enumerate(spreads) if lo <= s < hi]
        if not idxs:
            continue
        fwd20, fwd60 = [], []
        for i in idxs:
            d = spreads[i][0]
            di = dates.index(d) if d in closes else None
            if di is None:
                continue
            c0 = closes[d]
            if di + 20 < len(dates) and c0:
                fwd20.append((closes[dates[di+20]] / c0 - 1) * 100)
            if di + 60 < len(dates) and c0:
                fwd60.append((closes[dates[di+60]] / c0 - 1) * 100)
        def _win(xs):
            return f"{sum(1 for x in xs if x > 0)/len(xs)*100:.0f}%" if xs else "—"
        def _med(xs):
            s = sorted(xs); return f"{s[len(s)//2]:+.1f}%" if xs else "—"
        print(f"{lo}-{hi if hi<99 else '>'}pp{'':<6}{len(idxs):<8}{_win(fwd20):<10}{_med(fwd20):<10}{_win(fwd60):<10}{_med(fwd60):<10}")

    # 当前息差
    if spreads:
        print(f"  当前息差: {spreads[-1][1]:+.2f}pp @ {spreads[-1][0]}")

db.close()
