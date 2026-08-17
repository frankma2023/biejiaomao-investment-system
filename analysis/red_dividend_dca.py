# -*- coding: utf-8 -*-
"""
红利指数 定投 vs 网格 vs 持有 对比回测（2018起）
=================================================
策略：
  buy-hold  期初一次性买入持有
  dca       每月固定金额定投（1万）
  dca-dd    回撤加码：250日回撤≥10% → 当月2倍；≥15% → 3倍
  dca-pe    估值加权：PE分位<0.33 → 2倍；>0.8 → 0.5倍
  grid      8% 间距对称网格（对照）

口径：价格指数（分红未计入，诚实标注）；000922 附加 H00922 全收益对照
"""
import sys, os, sqlite3, math
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row

INDICES = [
    ('000922', '中证红利', '2018-01-01'),
    ('000015', '红利指数', '2018-01-01'),
    ('H30269', '红利低波', '2018-01-01'),
    ('931848', '800红利低波', '2018-01-01'),
    ('931468', '红利质量', '2018-01-01'),
    ('930955', '红利低波100', '2018-01-01'),
]

def load(code, start):
    rows = db.execute("SELECT date, close FROM index_daily_kline WHERE stock_code=? AND kline_type='normal' AND date>=? ORDER BY date", (code, start)).fetchall()
    return [dict(r) for r in rows]

def load_fund(code):
    rows = db.execute("SELECT date, pe_ttm_pct FROM index_fundamental_daily WHERE stock_code=? AND pe_ttm_pct IS NOT NULL ORDER BY date", (code,)).fetchall()
    return {r['date']: r['pe_ttm_pct'] for r in rows}

def dca_backtest(data, mode, monthly=10000, pe_map=None):
    """每月第一个交易日投入 monthly（或加码倍数）"""
    dates = [r['date'] for r in data]
    closes = [r['close'] for r in data]
    shares, invested = 0.0, 0.0
    prev_month = None
    dd_now = 0.0
    for i, d in enumerate(dates):
        m = d[:7]
        if m == prev_month:
            continue
        prev_month = m
        amt = monthly
        if mode == 'dca-dd' and i >= 250:
            seg = closes[i-249:i+1]
            hi = max(seg)
            dd_now = (hi - closes[i]) / hi * 100
            if dd_now >= 15:
                amt = monthly * 3
            elif dd_now >= 10:
                amt = monthly * 2
        elif mode == 'dca-pe':
            pe = pe_map.get(d)
            if pe is not None:
                if pe < 0.33:
                    amt = monthly * 2
                elif pe > 0.8:
                    amt = monthly * 0.5
        shares += amt / closes[i]
        invested += amt
    final = shares * closes[-1]
    return invested, final

def grid_ret(data):
    """8% 间距对称网格"""
    closes = [r['close'] for r in data]
    c, s, low = 100000.0, 0.0, min(closes) * 0.95
    s = (100000 / 3) / closes[0]; c -= 100000 / 3
    cg = int((closes[0] - low) / (low * 0.08))
    per = 10000.0
    for i in range(1, len(closes)):
        p = closes[i]
        g = int((p - low) / (low * 0.08))
        if g < cg:
            for _ in range(cg - g):
                if c > per: s += per / p; c -= per
            cg = g
        elif g > cg:
            for _ in range(g - cg):
                if s > 0:
                    amt = min(s * p, per); s -= amt / p; c += amt
            cg = g
    return c + s * closes[-1]

print('=== 红利指数 定投 vs 网格 vs 持有（2018起 · 价格口径，分红未计入）===')
print(f'{"指数":<12}{"持有":>8}{"普通定投":>10}{"回撤加码":>10}{"估值加权":>10}{"网格":>8}  |  定投年化(普通/回撤)')
for code, name, start in INDICES:
    data = load(code, start)
    if len(data) < 400:
        print(f'{name:<12} 数据不足'); continue
    pe_map = load_fund(code)
    closes = [r['close'] for r in data]
    # 持有（期初 10 万）
    hold = 100000 / closes[0] * closes[-1]
    # 定投（每月 1 万 → 8.6 年约 103 个月 ≈ 103 万投入）
    inv_d, fin_d = dca_backtest(data, 'dca')
    inv_dd, fin_dd = dca_backtest(data, 'dca-dd')
    inv_pe, fin_pe = dca_backtest(data, 'dca-pe', pe_map=pe_map)
    g = grid_ret(data)
    years = len(closes) / 244
    ann_d = (fin_d / inv_d) ** (1 / years) - 1
    ann_dd = (fin_dd / inv_dd) ** (1 / years) - 1
    print(f'{name:<12}{hold/100000*100-100:>+7.1f}%{fin_d/inv_d*100-100:>+9.1f}%{fin_dd/inv_dd*100-100:>+9.1f}%{fin_pe/inv_pe*100-100:>+9.1f}%{g/100000*100-100:>+7.1f}%  |  {ann_d*100:>5.1f}% / {ann_dd*100:>5.1f}%')

# 000922 全收益对照（分红影响）
tri = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code='H00922' AND date>='2018-01-01' ORDER BY date").fetchall()
tri = [dict(r) for r in tri]
if tri:
    inv, fin = dca_backtest(tri, 'dca')
    inv2, fin2 = dca_backtest(tri, 'dca-dd')
    years = len(tri) / 244
    print(f'\nH00922 全收益对照（含分红再投资）: 普通定投 +{fin/inv*100-100:.1f}% (年化 {(fin/inv)**(1/years)-1:.1%}) | 回撤加码 +{fin2/inv2*100-100:.1f}% (年化 {(fin2/inv2)**(1/years)-1:.1%})')
    # 与价格口径对比
    print(f'  → 分红对定投的贡献: 全收益/价格 = {(fin/inv)/(fin_d/inv_d):.2f}x')
db.close()
