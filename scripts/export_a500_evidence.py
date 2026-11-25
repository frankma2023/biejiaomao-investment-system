#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出中证A500（000510）研究的数字层快照。

按 hanako-report §5.6：同时导出
  evidence/a500-000510-<asOf>.json  供程序读取
  evidence/a500-000510-<asOf>.txt   陈述文本快照，登记为证据用

正文引用的每个数字都必须在本 txt 中逐字出现。
用法：python scripts/export_a500_evidence.py
"""
import json
import math
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = str(ROOT / 'data' / 'lixinger.db')
EVD = ROOT / 'evidence'
CODE = '000510'
ASOF = '2026-11-04'

CON = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)


def series(code, start=None):
    q = ("SELECT date, close FROM index_daily_kline WHERE stock_code=? "
         "AND kline_type='normal' AND close>0")
    a = [code]
    if start:
        q += " AND date>=?"
        a.append(start)
    return CON.execute(q + " ORDER BY date", a).fetchall()


def cagr(s):
    if len(s) < 30:
        return None
    y = (int(s[-1][0][:4]) + int(s[-1][0][5:7]) / 12) - \
        (int(s[0][0][:4]) + int(s[0][0][5:7]) / 12)
    return ((s[-1][1] / s[0][1]) ** (1 / y) - 1) * 100 if y > 0 else None


def vol(s):
    r = [math.log(s[i][1] / s[i - 1][1]) for i in range(1, len(s)) if s[i - 1][1] > 0]
    m = sum(r) / len(r)
    return (sum((x - m) ** 2 for x in r) / (len(r) - 1)) ** 0.5 * math.sqrt(244) * 100


def mdd(s):
    peak, worst = -1e18, 0.0
    for _, c in s:
        peak = max(peak, c)
        worst = min(worst, c / peak - 1)
    return worst * 100


def med(xs):
    s = sorted(xs)
    k = len(s)
    return s[k // 2] if k % 2 else (s[k // 2 - 1] + s[k // 2]) / 2


def rsi(closes, n):
    if len(closes) <= n:
        return [None] * len(closes)
    g = [0.0]
    l = [0.0]
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g.append(max(ch, 0.0))
        l.append(max(-ch, 0.0))
    ag = sum(g[1:n + 1]) / n
    al = sum(l[1:n + 1]) / n
    out = [None] * len(closes)
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + l[i]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


full = series(CODE)
tr_rows = CON.execute(
    "SELECT date, close FROM index_full_return_daily WHERE stock_code=? ORDER BY date",
    (CODE,)).fetchall()
tr_map = dict(tr_rows)
hs = dict(CON.execute(
    "SELECT date, close FROM index_full_return_daily WHERE stock_code='000300'").fetchall())
dv = dict(CON.execute(
    "SELECT date, close FROM index_full_return_daily WHERE stock_code='000922'").fetchall())

f = CON.execute("SELECT pe_ttm,pe_ttm_pct,pb,dyr,dyr_pct FROM index_fundamental_daily "
                "WHERE stock_code=? ORDER BY date DESC LIMIT 1", (CODE,)).fetchone()
pe, pe_pct, pb, dyr, dyr_pct = f

tr_win = [(d, tr_map[d]) for d, _ in full if d in tr_map and tr_map[d]]
px_win = [(d, c) for d, c in full if d in tr_map]
tr_cagr = cagr(tr_win)
px_cagr = cagr(px_win)
div_pp = tr_cagr - px_cagr
div_share = div_pp / tr_cagr * 100
tr_mdd = mdd(tr_win)
tr_vol = vol(tr_win)

# 相似度：与沪深300 日收益回归
LAUNCH = '2024-09-23'


def regress(bench_code):
    """与生成器 make_a500_report.py 同口径：实盘期价格对数日收益。"""
    a = dict(series(CODE, LAUNCH))
    b = dict(series(bench_code, LAUNCH))
    common = sorted(set(a) & set(b))
    if len(common) < 60:
        return 0, None, None
    da = [math.log(a[common[i]] / a[common[i - 1]]) for i in range(1, len(common))]
    db = [math.log(b[common[i]] / b[common[i - 1]]) for i in range(1, len(common))]
    n = len(da)
    ma, mb = sum(da) / n, sum(db) / n
    cov = sum((da[i] - ma) * (db[i] - mb) for i in range(n)) / (n - 1)
    va = sum((x - ma) ** 2 for x in da) / (n - 1)
    vb = sum((x - mb) ** 2 for x in db) / (n - 1)
    return n, cov ** 2 / (va * vb), cov / vb


n300, r2_300, beta_300 = regress('000300')
n922, r2_922, beta_922 = regress('000922')

closes = [c for _, c in full]
peak_i = max(range(len(closes)), key=lambda i: closes[i])
cur_dd = (closes[-1] / closes[peak_i] - 1) * 100
r20 = rsi(closes, 20)
rsi20_now = r20[-1]

# 估值可用区间
fund = CON.execute("SELECT date, pe_ttm, pb, dyr FROM index_fundamental_daily "
                   "WHERE stock_code=? ORDER BY date", (CODE,)).fetchall()
pe_vals = [r[1] for r in fund if r[1]]
pe_sorted = sorted(pe_vals)
pe_med = med(pe_vals)
pe_rank = sum(1 for v in pe_vals if v < pe_vals[-1]) / len(pe_vals) * 100

# RSI 卖出侧（skill §6 已记录结论的复核）
h120_base = med([(closes[i + 120] / closes[i] - 1) * 100
                 for i in range(len(closes) - 120) if closes[i] > 0])
idx120 = [i for i in range(len(r20)) if r20[i] is not None and r20[i] >= 78 and i + 120 < len(closes)]
xs120 = [(closes[i + 120] / closes[i] - 1) * 100 for i in idx120]
rsi_sell_med = med(xs120)
rsi_sell_edge = rsi_sell_med - h120_base
rsi_sell_win = sum(1 for x in xs120 if x > 0) / len(xs120) * 100

# 国际对照（价格口径，akshare 东方财富全球指数）
intl = json.loads((ROOT / '.scratch' / 'a500_intl.json').read_text(encoding='utf-8'))

CON.close()

data = {
    'asOf': ASOF,
    'code': CODE,
    'window': [tr_win[0][0], tr_win[-1][0]],
    'trCagr': round(tr_cagr, 2),
    'pxCagr': round(px_cagr, 2),
    'divPp': round(div_pp, 2),
    'divShare': round(div_share, 1),
    'trMdd': round(tr_mdd, 1),
    'trVol': round(tr_vol, 2),
    'r2Hs300': round(r2_300, 3),
    'betaHs300': round(beta_300, 2),
    'r2Div': round(r2_922, 3),
    'dyr': round(dyr * 100, 2),
    'curDrawdown': round(cur_dd, 2),
    'peakDate': full[peak_i][0],
    'rsi20': round(rsi20_now, 1),
    'peNow': round(pe, 2),
    'peMedian': round(pe_med, 2),
    'peRank': round(pe_rank, 1),
    'peRows': len(pe_vals),
    'peWindow': [fund[0][0], fund[-1][0]],
    'rsiSell120': round(rsi_sell_med, 2),
    'rsiSellEdge': round(rsi_sell_edge, 2),
    'rsiSellWin': round(rsi_sell_win, 0),
    'rsiSellN': len(xs120),
    'intl': intl,
}
EVD.mkdir(exist_ok=True)
(EVD / f'a500-000510-{ASOF}.json').write_text(
    json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')

lines = [
    f'中证A500（000510）数字层快照，数据截止 {ASOF}。',
    '',
    f'全收益年化收益率为 {tr_cagr:.2f}%，统计区间 {tr_win[0][0]} 至 {tr_win[-1][0]}。',
    f'价格年化收益率为 {px_cagr:.2f}%，分红贡献为 {div_pp:.2f}pp，占全收益的 {div_share:.1f}%。',
    f'全收益最大回撤为 {tr_mdd:.1f}%，全收益年化波动率为 {tr_vol:.2f}%。',
    f'实盘期（{LAUNCH} 起）价格对数日收益与沪深300的 R² 为 {r2_300:.3f}，beta 为 {beta_300:.2f}。',
    f'同一口径下与中证红利的 R² 为 {r2_922:.3f}。',
    f'最新股息率为 {dyr*100:.2f}%。',
    f'价格指数历史最高点出现在 {full[peak_i][0]}，当前回撤为 {cur_dd:.2f}%。',
    f'RSI20 最新值为 {rsi20_now:.1f}。',
    f'RSI20 大于等于 78 之后 120 个交易日的中位收益为 {rsi_sell_med:.2f}%，'
    f'基准中位收益为 {h120_base:.2f}%，超额 {rsi_sell_edge:.2f}pp，胜率 {rsi_sell_win:.0f}%，样本 {len(xs120)} 次。',
    f'估值序列可用区间为 {fund[0][0]} 至 {fund[-1][0]}，共 {len(pe_vals)} 个交易日，不足以计算十年分位。',
    f'当前 PE-TTM 为 {pe:.2f}，可用区间中位数为 {pe_med:.2f}，当前处于可用区间分位 {pe_rank:.1f}%。',
    '',
    '国际同类指数对照（价格口径，同一算法自算）：',
]
for name, r in intl.items():
    lines.append(f'{name} 十年年化 {r["r10"]:.2f}%，二十年年化 {r["r20"]:.2f}%。')
lines.append('')
lines.append('数据来源：理杏仁（行情/全收益/估值）、东方财富全球指数（国际对照）。')
lines.append('本快照由 scripts/export_a500_evidence.py 确定性生成，研究参考，不构成投资建议。')

# newline='' 关闭 Windows 的 \n -> \r\n 文本转换，保证磁盘字节与登记的证据快照逐字节一致
with open(EVD / f'a500-000510-{ASOF}.txt', 'w', encoding='utf-8', newline='') as fh:
    fh.write('\n'.join(lines) + '\n')

print(f'✓ evidence/a500-000510-{ASOF}.json')
print(f'✓ evidence/a500-000510-{ASOF}.txt')
print(f'  全收益年化 {tr_cagr:.2f}%  价格 {px_cagr:.2f}%  分红 {div_pp:.2f}pp  R² {r2_300:.3f}')
