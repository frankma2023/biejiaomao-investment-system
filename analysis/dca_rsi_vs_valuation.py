# -*- coding: utf-8 -*-
"""
红利低波(H30269)三种定投策略对比回测
口径：初始 100 万可用资金，月度定投（每月首个交易日），T-1 日收盘后按信号决策、T 日收盘成交。
  - 标准定投：每期配额 q 全投
  - RSI 定投：RSI14 < 40 才投（否则跳过，现金滚存——体现"跌了才买"）
  - 估值定投：股息率滚动分位（2年窗口）>=75% 投 2q / 50-75% 投 q / 25-50% 投 0.5q / <25% 跳过
基准：一次性买入（首日全投）
指数收益用全收益（红利再投），RSI 用价格指数（2013 起预热）
区间：2017-01 ~ 2026-08（116 期），配额 q = 100万/116
"""
import sys, io, math
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\hanako\investment-system\src')
from analysis.financial import get_db

IDX = 'H30269'
START, END = '2017-01-01', '2026-08-31'
CAPITAL = 1000000.0

db = get_db()

# ── 全收益日线（持仓收益）──
fr = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code=? AND date>=? AND date<=? ORDER BY date", (IDX, START, END)).fetchall()
fr_map = {r['date']: r['close'] for r in fr}

# ── 价格日线（RSI 计算，2013 起预热）──
px = db.execute("SELECT date, close FROM index_daily_kline WHERE stock_code=? ORDER BY date", (IDX,)).fetchall()
px_map = {r['date']: r['close'] for r in px}
px_dates = [r['date'] for r in px]

# ── 股息率日线 ──
dy = db.execute("SELECT date, dyr FROM index_fundamental_daily WHERE stock_code=? AND dyr IS NOT NULL ORDER BY date", (IDX,)).fetchall()
dy_map = {r['date']: r['dyr'] for r in dy}
dy_dates = [r['date'] for r in dy]
db.close()

# ── 月度定投日（每月首个有全收益的交易日）──
month_days = []
for r in fr:
    m = r['date'][:7]
    if not month_days or month_days[-1][0] != m:
        month_days.append((m, r['date']))
month_days = [d for _, d in month_days]
print('定投期数:', len(month_days), month_days[0], '~', month_days[-1])
Q = CAPITAL / len(month_days)
print('每期配额: %.0f 元' % Q)

def prev_val(dmap, d, look='<='):
    """dmap 中 <= d 的最近日期值；返回 (date, value)"""
    # dmap keys 有序 dict（查询按日期升序插入，dict 保序）
    best = None
    for k in dmap:
        if k <= d:
            best = (k, dmap[k])
        else:
            break
    return best

def rsi14(code_dates, code_map, d, n=14):
    """RSI14（Wilder），用 d 前 60 交易日（含 d 的最近 15+）"""
    ds = [x for x in code_dates if x <= d][-60:]
    if len(ds) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(ds)):
        chg = code_map[ds[i]] - code_map[ds[i-1]]
        gains.append(max(chg, 0))
        losses.append(max(-chg, 0))
    # 用最近 n 个
    g, l = gains[-n:], losses[-n:]
    ag = sum(g) / n
    al = sum(l) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)

def dyr_pctile(d, window=500):
    """股息率在 d 前 window 交易日内的分位（当前值高于历史 %），不足窗口用全部"""
    pv = prev_val(dy_map, d)
    if not pv or not pv[1]:
        return None
    cur = pv[1]
    hist = [dy_map[k] for k in dy_dates if k <= d]
    if len(hist) < 60:
        return None
    hist = hist[-window:]
    below = sum(1 for v in hist if v <= cur)
    return below / len(hist)

# ── 各策略模拟（增加现金计息：闲置现金按月 2%/12 复利）──
def sim(rule, cash_yield=0.0):
    """rule(date) -> 倍数(0/0.5/1/2 等)。返回 dict"""
    cash = CAPITAL
    shares = 0.0  # 全收益净值份额
    invested = 0.0
    flows = []  # (月份, 投入) 供 IRR
    skip_months = []
    for m, d in enumerate(month_days):
        mult = rule(d)
        amt = Q * mult
        amt = min(amt, cash)  # 现金不足投所及
        px0 = fr_map.get(d)
        if amt > 0 and px0:
            shares += amt / px0
            cash -= amt
            invested += amt
            flows.append((m, amt))
        elif mult == 0:
            skip_months.append(d[:7])
        # 月度利息
        if cash_yield > 0:
            cash *= (1 + cash_yield / 12)
    last_px = fr_map[month_days[-1]]
    mkt_val = shares * last_px
    total = mkt_val + cash
    return {'cash': cash, 'invested': invested, 'mkt_val': mkt_val, 'total': total,
            'shares': shares, 'skip': len(skip_months), 'flows': flows}

# 策略 0：一次性（首日全投）
d0 = month_days[0]
px0 = fr_map[d0]
shares0 = CAPITAL / px0
px_end = fr_map[month_days[-1]]
res0 = {'invested': CAPITAL, 'cash': 0, 'mkt_val': shares0 * px_end,
        'total': shares0 * px_end, 'skip': 0, 'flows': [(0, CAPITAL)]}

# 策略 1：标准
def rule_std(d):
    return 1.0
res_std = sim(rule_std)

# 策略 2：RSI<40
def rule_rsi(d):
    pv = prev_val(px_map, d)
    if not pv:
        return 1.0
    r = rsi14(px_dates, px_map, d)
    if r is None:
        return 1.0
    return 1.0 if r < 40 else 0.0
res_rsi = sim(rule_rsi)

# 策略 3：估值（股息率分位 4 档）
def rule_val(d):
    p = dyr_pctile(d)
    if p is None:
        return 1.0
    if p >= 0.75:
        return 2.0
    if p >= 0.50:
        return 1.0
    if p >= 0.25:
        return 0.5
    return 0.0
res_val = sim(rule_val)

# 策略 4/5：RSI 阈值敏感性（<50 / <60 才买），现金 0 息
for thr in (50, 60):
    pass

def mk_rsi(thr):
    def rule(d):
        pv = prev_val(px_map, d)
        if not pv:
            return 1.0
        r = rsi14(px_dates, px_map, d)
        if r is None:
            return 1.0
        return 1.0 if r < thr else 0.0
    return rule
res_rsi50 = sim(mk_rsi(50))
res_rsi60 = sim(mk_rsi(60))
# RSI<40 + 现金 2% 计息
res_rsi_i = sim(rule_rsi, cash_yield=0.02)

def xirr(flows_months, end_val, months):
    """月频 IRR：现金流入(负)在每月,期末回收 end_val；二分求月利率"""
    # flows: (month_idx, 投入额)
    f = []
    for mi, amt in flows_months:
        f.append((mi, -amt))
    f.append((months - 0.0, end_val))  # 期末
    def npv(r):
        v = 0.0
        for t, c in f:
            try:
                v += c / (1 + r) ** t
            except Exception:
                return float('inf') if c > 0 else float('-inf')
        return v
    lo, hi = -0.5, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

def fmt_res(name, res, months):
    yrs = months / 12
    multi = res['total'] / CAPITAL
    cagr = multi ** (1 / yrs) - 1 if multi > 0 else None
    if res['flows']:
        irr_m = xirr(res['flows'], res['total'], months)
        irr_a = (1 + irr_m) ** 12 - 1
    else:
        irr_a = None
    # 分笔：投入权重平均成本 vs 期末价
    return {'name': name, 'invested': res['invested'], 'cash': res['cash'],
            'mkt': res['mkt_val'], 'total': res['total'], 'skip': res['skip'],
            'multi': multi, 'cagr': cagr, 'irr_a': irr_a,
            'util': res['invested'] / CAPITAL}

months = len(month_days)
results = [fmt_res('一次性买入', res0, months), fmt_res('标准定投', res_std, months),
           fmt_res('RSI定投(<40)', res_rsi, months), fmt_res('RSI定投(<50)', res_rsi50, months),
           fmt_res('RSI定投(<60)', res_rsi60, months), fmt_res('RSI定投(<40)+现金2%', res_rsi_i, months),
           fmt_res('估值定投(股息率分位)', res_val, months)]

print()
print(f"{'策略':<22}{'实际投入(万)':>12}{'持仓(万)':>12}{'现金(万)':>10}{'期末总资产(万)':>16}{'倍数':>8}{'年化(CAGR)':>12}{'实际IRR':>10}{'跳过月':>7}")
for r in results:
    print(f"{r['name']:<22}{r['invested']/1e4:>12.1f}{r['mkt']/1e4:>12.1f}{r['cash']/1e4:>10.1f}{r['total']/1e4:>16.1f}{r['multi']:>8.2f}"
          f"{('%.2f%%' % (r['cagr']*100)) if r['cagr'] else '-':>12}{('%.2f%%' % (r['irr_a']*100)) if r['irr_a'] else '-':>10}{r['skip']:>7}")

# 估值定投触发分布
print('\n-- 估值定投分位触发分布 --')
pct_l = []
for d in month_days:
    p = dyr_pctile(d)
    if p is not None:
        pct_l.append(p)
import collections
buckets = {'<25%(跳)': sum(1 for p in pct_l if p < .25), '25-50(半)': sum(1 for p in pct_l if .25 <= p < .5),
           '50-75(全)': sum(1 for p in pct_l if .5 <= p < .75), '>=75(双)': sum(1 for p in pct_l if p >= .75)}
print('  有效信号月:', len(pct_l), buckets)

# RSI 触发统计
rsi_l = []
for d in month_days:
    r = rsi14(px_dates, px_map, d)
    if r is not None:
        rsi_l.append((d[:7], r))
for thr in (40, 50, 60):
    print(f'-- RSI<{thr} 月数:', sum(1 for _, r in rsi_l if r < thr), '/', len(rsi_l))
