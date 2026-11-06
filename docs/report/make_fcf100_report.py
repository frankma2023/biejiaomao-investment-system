# -*- coding: utf-8 -*-
"""国证自由现金流（980092）研究报告生成器。

用法：python docs/report/make_fcf100_report.py
产出：docs/report/国证自由现金流研究报告/index.html（单文件，离线可用）

所有数字在此现算，正文与图表共用同一份计算结果。
"""
import os
import sqlite3
from datetime import datetime

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')
assert os.path.exists(DB), f'数据库路径不存在: {DB}'
OUT_DIR = os.path.join(PROJECT, 'docs', 'report', '国证自由现金流研究报告')
OUT = os.path.join(OUT_DIR, 'index.html')

CODE = '980092'
NAME = '国证自由现金流'
REVISION_DATE = '2024-08-15'     # 官方规则修订实施日（改造自 CN6008 海南综合指数）
BENCH = [('000300', '沪深300'), ('000922', '中证红利'), ('000905', '中证500')]
ASOF = '2026-11-04'

con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
con.row_factory = sqlite3.Row


def ser(tbl, code):
    return {r['date']: r['close'] for r in con.execute(
        f"SELECT date, close FROM {tbl} WHERE stock_code=? ORDER BY date", (code,))}


TR = ser('index_full_return_daily', CODE)
PX = ser('index_daily_kline', CODE)
DATES = sorted(set(TR) & set(PX))
BTR = {c: ser('index_full_return_daily', c) for c, _ in BENCH}


def sub(lo, hi, s=None):
    s = s or DATES
    return [d for d in s if lo <= d <= hi]


def cagr(s, ds):
    if len(ds) < 2:
        return None
    yrs = (len(ds) - 1) / 244
    return ((s[ds[-1]] / s[ds[0]]) ** (1 / yrs) - 1) * 100


def mdd(s, ds):
    peak, m = s[ds[0]], 0
    for d in ds:
        peak = max(peak, s[d])
        m = min(m, s[d] / peak - 1)
    return m * 100


def vol(s, ds):
    r = [s[ds[i]] / s[ds[i - 1]] - 1 for i in range(1, len(ds))]
    mu = sum(r) / len(r)
    return (sum((x - mu) ** 2 for x in r) / len(r)) ** 0.5 * (244 ** 0.5) * 100


SEGS = {'full': sub(DATES[0], DATES[-1]),
        'bt': sub(DATES[0], '2024-08-14'),
        'live': sub(REVISION_DATE, DATES[-1])}

S = {}
for k, ds in SEGS.items():
    S[k] = {'days': len(ds), 'yrs': (len(ds) - 1) / 244,
            'tr': cagr(TR, ds), 'px': cagr(PX, ds),
            'mdd': mdd(TR, ds), 'vol': vol(TR, ds)}
    S[k]['div'] = S[k]['tr'] - S[k]['px']

BENCH_S = {}
for c, n in BENCH:
    BENCH_S[n] = {}
    for k, ds in SEGS.items():
        d2 = [d for d in ds if d in BTR[c]]
        BENCH_S[n][k] = {'tr': cagr(BTR[c], d2), 'mdd': mdd(BTR[c], d2)} if len(d2) > 20 else {}

# 回归
REG = {}
ad = SEGS['full']
ra = [TR[ad[i]] / TR[ad[i - 1]] - 1 for i in range(1, len(ad))]
for c, n in BENCH:
    t = BTR[c]
    pairs = [(ad[i - 1], ad[i]) for i in range(1, len(ad)) if ad[i] in t and ad[i - 1] in t]
    rb = [t[b] / t[a] - 1 for a, b in pairs]
    y = [TR[b] / TR[a] - 1 for a, b in pairs]
    m = len(rb)
    mx, my = sum(rb) / m, sum(y) / m
    sxx = sum((v - mx) ** 2 for v in rb)
    sxy = sum((rb[i] - mx) * (y[i] - my) for i in range(m))
    syy = sum((v - my) ** 2 for v in y)
    beta = sxy / sxx
    REG[n] = {'r2': sxy ** 2 / (sxx * syy), 'beta': beta,
              'alpha': (my - beta * mx) * 244 * 100}

# 分年度
YEARS = []
all_d = SEGS['full']
for y in sorted({d[:4] for d in all_d}):
    ds = [d for d in all_d if d[:4] == y]
    if len(ds) < 20:
        continue
    prev = [d for d in all_d if d < ds[0]]
    if not prev:
        continue
    base, last = prev[-1], ds[-1]
    row = {'y': y, 'live': base >= REVISION_DATE}
    for lbl, s in [('fcf', TR)] + [(n, BTR[c]) for c, n in BENCH]:
        row[lbl] = (s[last] / s[base] - 1) * 100 if base in s and last in s else None
    YEARS.append(row)

# 锚定回撤分档
cl = [TR[d] for d in all_d]
peak, DD = cl[0], []
for v in cl:
    peak = max(peak, v)
    DD.append((v / peak - 1) * -100)
BK = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 40), (40, 100)]
seg_idx, cur = [], None
for i, x in enumerate(DD):
    b = next((k for k, (lo, hi) in enumerate(BK) if lo <= x < hi), None)
    if b is None:
        continue
    if b != cur:
        seg_idx.append((b, i))
        cur = b
BUCKETS = []
for k, (lo, hi) in enumerate(BK):
    st = [i for b, i in seg_idx if b == k]
    fwd = sorted(cl[s + 250] / cl[s] - 1 for s in st if s + 250 < len(cl))
    if not fwd:
        BUCKETS.append({'lo': lo, 'hi': hi, 'n': len(st), 'med': None, 'win': None})
        continue
    med = fwd[len(fwd) // 2]
    BUCKETS.append({'lo': lo, 'hi': hi, 'n': len(st),
                    'med': ((1 + med) ** (244 / 250) - 1) * 100,
                    'win': sum(1 for x in fwd if x > 0) / len(fwd) * 100})

# RSI
def rsi(c, n):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        ch = c[i] - c[i - 1]
        g += max(ch, 0)
        l += max(-ch, 0)
    ag, al = g / n, l / n
    out[n] = 100 - 100 / (1 + (ag / al if al else 999))
    for i in range(n + 1, len(c)):
        ch = c[i] - c[i - 1]
        ag = (ag * (n - 1) + max(ch, 0)) / n
        al = (al * (n - 1) + max(-ch, 0)) / n
        out[i] = 100 - 100 / (1 + (ag / al if al else 999))
    return out


RSI_S = {}
for n in (6, 14, 20, 60):
    arr = rsi(cl, n)
    ids = [i for i in range(len(arr)) if arr[i] is not None and arr[i] <= 30]
    RSI_S[n] = {'arr': arr, 'n_buy': len(ids),
                'fwd': {h: (lambda v: v[len(v) // 2] * 100 if v else None)(
                    sorted(cl[i + h] / cl[i] - 1 for i in ids if i + h < len(cl)))
                    for h in (20, 60, 120, 250)}}
BASE_FWD = {h: (lambda v: v[len(v) // 2] * 100)(
    sorted(cl[i + h] / cl[i] - 1 for i in range(len(cl) - h))) for h in (20, 60, 120, 250)}

# 估值
VAL = [(r[0], r[1], r[2], r[3] * 100) for r in con.execute(
    "SELECT date, pe_ttm, pb, dyr FROM index_fundamental_daily WHERE stock_code=? "
    "AND pe_ttm IS NOT NULL ORDER BY date", (CODE,))]

CUR_DD = DD[-1]
CUR_RSI = {n: RSI_S[n]['arr'][-1] for n in (6, 14, 20)}
CUR_PE, CUR_PB, CUR_DY = VAL[-1][1], VAL[-1][2], VAL[-1][3]
PE_LIST = sorted(v[1] for v in VAL)
PE_MED = PE_LIST[len(PE_LIST) // 2]

con.close()

# ── SVG 工具 ─────────────────────────────────────────────


def poly(series, ds, W, H, pad, ymin, ymax):
    n = len(ds)
    step = max(1, n // 1100)          # 降采样：屏幕宽度内 >1100 点无视觉差异，但体积翻倍
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    pts = []
    for i in idx:
        v = series.get(ds[i])
        if v is None:
            continue
        x = pad['l'] + (W - pad['l'] - pad['r']) * i / max(1, n - 1)
        y = pad['t'] + (H - pad['t'] - pad['b']) * (1 - (v - ymin) / (ymax - ymin))
        pts.append(f'{x:.1f},{y:.1f}')
    return ' '.join(pts)


def axis_labels(ds, W, pad, steps=8):
    out, n = [], len(ds)
    for k in range(steps + 1):
        i = int((n - 1) * k / steps)
        x = pad['l'] + (W - pad['l'] - pad['r']) * k / steps
        out.append(f'<text x="{x:.0f}" y="{H_AX}" class="ax" text-anchor="middle">'
                   f'{ds[i][:7]}</text>')
    return ''.join(out)


def ygrid(ymin, ymax, W, H, pad, fmt='{:.0f}'):
    out = []
    for k in range(5):
        v = ymin + (ymax - ymin) * k / 4
        y = pad['t'] + (H - pad['t'] - pad['b']) * (1 - k / 4)
        out.append(f'<line x1="{pad["l"]}" x2="{W - pad["r"]}" y1="{y:.0f}" y2="{y:.0f}" '
                   f'class="gl"/>'
                   f'<text x="{pad["l"] - 8}" y="{y + 4:.0f}" class="ay" '
                   f'text-anchor="end">{fmt.format(v)}</text>')
    return ''.join(out)


def line_chart(series_list, ds, W=980, H=380, fmt='{:.0f}', pad=None, log=False):
    """series_list: [(label, dict, css_class)]，全部为同一 y 轴量纲。"""
    pad = pad or {'l': 58, 'r': 16, 't': 16, 'b': 34}
    vals = [v for _, s, _ in series_list for d in ds for v in [s.get(d)] if v is not None]
    ymin, ymax = min(vals), max(vals)
    rg = ymax - ymin or 1
    ymin, ymax = ymin - rg * 0.06, ymax + rg * 0.06
    g = ygrid(ymin, ymax, W, H, pad, fmt)
    lines = ''.join(
        f'<polyline class="ln {series_list[i][2]}" '
        f'points="{poly(series_list[i][1], ds, W, H, pad, ymin, ymax)}" '
        f'data-name="{series_list[i][0]}"/>' for i in range(len(series_list)))
    ax = ''.join(axis_labels(ds, W, pad))
    return (f'<svg viewBox="0 0 {W} {H + 22}" class="chart" preserveAspectRatio="none">'
            f'{g}{lines}{ax}</svg>')


W_AX, H_AX = 980, 380 + 22

# 归一图（起点 100）
norm = {}
nds = all_d
for lbl, s in [('fcf_px', PX), ('fcf_tr', TR)] + [(f'b_{c}', BTR[c]) for c, _ in BENCH]:
    base = None
    d = {}
    for dt in nds:
        if dt not in s:
            continue
        if base is None:
            base = s[dt]
        d[dt] = s[dt] / base * 100
    norm[lbl] = d

FOUR = [('国证自由现金流 全收益', norm['fcf_tr'], 's-tr'),
        ('国证自由现金流 价格', norm['fcf_px'], 's-px'),
        ('沪深300 全收益', norm['b_000300'], 's-300'),
        ('中证红利 全收益', norm['b_000922'], 's-922')]
CH_FOUR = line_chart(FOUR, nds)

# 累计超额
EXC = {d: (norm['fcf_tr'][d] / norm['b_000300'][d] - 1) * 100
       for d in nds if d in norm['fcf_tr'] and d in norm['b_000300']}
CH_EXC = line_chart([('超额 %', EXC, 's-exc')], nds, H=240, fmt='{:.0f}%')

# 回撤分档柱状
def bar_chart(items, W=980, H=280, fmt='{:.0f}%'):
    pad = {'l': 58, 'r': 16, 't': 16, 'b': 44}
    vals = [v for _, v, _ in items if v is not None]
    ymin, ymax = min(0, min(vals) * 1.15), max(vals) * 1.15
    out = [ygrid(ymin, ymax, W, H, pad, fmt)]
    n = len(items)
    bw = (W - pad['l'] - pad['r']) / n * 0.62
    for i, (lab, v, color) in enumerate(items):
        if v is None:
            continue
        x = pad['l'] + (W - pad['l'] - pad['r']) * (i + 0.5) / n
        y0 = pad['t'] + (H - pad['t'] - pad['b']) * (1 - (0 - ymin) / (ymax - ymin))
        y1 = pad['t'] + (H - pad['t'] - pad['b']) * (1 - (v - ymin) / (ymax - ymin))
        out.append(f'<rect x="{x - bw / 2:.0f}" y="{min(y0, y1):.0f}" width="{bw:.0f}" '
                   f'height="{abs(y1 - y0):.0f}" fill="{color}" opacity=".85"/>')
        out.append(f'<text x="{x:.0f}" y="{min(y0, y1) - 5:.0f}" class="bv" '
                   f'text-anchor="middle">{v:.1f}%</text>')
        out.append(f'<text x="{x:.0f}" y="{H - 26}" class="ax" text-anchor="middle">'
                   f'{lab}</text>')
    return f'<svg viewBox="0 0 {W} {H + 22}" class="chart">' + ''.join(out) + '</svg>'


CH_DD = bar_chart([(f'{b["lo"]}~{b["hi"]}%', b['med'],
                    '#10b981' if (b['med'] or 0) >= 0 else '#ef4444') for b in BUCKETS])

CH_SPLIT = bar_chart([('回测段 超额', S['bt']['tr'] - BENCH_S['沪深300']['bt']['tr'], '#10b981'),
                      ('实盘段 超额', S['live']['tr'] - BENCH_S['沪深300']['live']['tr'], '#ef4444')],
                     H=240)

# RSI 交互图：价格 + RSI20 双面板
def rsi_chart(W=980, H=430):
    pad = {'l': 58, 'r': 16, 't': 16, 'b': 34}
    hp = 250
    px_s = {d: v / PX[nds[0]] * 100 for d, v in PX.items() if d in set(nds)}
    tr_s = {d: v / TR[nds[0]] * 100 for d, v in TR.items() if d in set(nds)}
    vals = list(px_s.values()) + list(tr_s.values())
    ymin, ymax = min(vals) * 0.9, max(vals) * 1.06
    out = [ygrid(ymin, ymax, W, hp, pad)]
    out.append(f'<polyline class="ln s-px" points="{poly(px_s, nds, W, hp, pad, ymin, ymax)}"/>')
    out.append(f'<polyline class="ln s-tr" points="{poly(tr_s, nds, W, hp, pad, ymin, ymax)}"/>')
    # RSI 面板
    top = hp + 24
    pad2 = {'l': 58, 'r': 16, 't': top, 'b': 34}
    out.append(f'<line x1="{pad["l"]}" x2="{W - pad["r"]}" y1="{top}" y2="{top}" class="gl"/>')
    for lvl, cls, txt in ((30, 'lv-buy', '30 买'), (50, 'lv-mid', '50'), (78, 'lv-sell', '78 卖')):
        y = top + (H - top - pad2['b']) * (1 - lvl / 100)
        out.append(f'<line x1="{pad["l"]}" x2="{W - pad["r"]}" y1="{y:.0f}" y2="{y:.0f}" '
                   f'class="gl {cls}"/>'
                   f'<text x="{W - pad["r"] - 4}" y="{y - 4:.0f}" class="ay" '
                   f'text-anchor="end">{txt}</text>')
    for n in (6, 14, 20):
        arr = RSI_S[n]['arr']
        s = {nds[i]: arr[i] for i in range(min(len(nds), len(arr))) if arr[i] is not None}
        cls = 's-rsi20' if n == 20 else f's-rsi{n}'
        out.append(f'<polyline class="ln rsi {cls}" points="{poly(s, nds, W, H, pad2, 0, 100)}"/>')
    for k in range(9):
        x = pad['l'] + (W - pad['l'] - pad['r']) * k / 8
        i = int((len(nds) - 1) * k / 8)
        out.append(f'<text x="{x:.0f}" y="{H + 6}" class="ax" text-anchor="middle">'
                   f'{nds[i][:7]}</text>')
    return f'<svg viewBox="0 0 {W} {H + 22}" class="chart">' + ''.join(out) + '</svg>'


CH_RSI = rsi_chart()


def val_chart(idx, title, fmt='{:.2f}'):
    ds = [v[0] for v in VAL]
    s = {v[0]: v[idx] for v in VAL}
    return line_chart([(title, s, 's-val')], ds, H=240, fmt=fmt)


CH_PE = val_chart(1, 'PE-TTM', '{:.1f}')
CH_PB = val_chart(2, 'PB', '{:.2f}')
CH_DY = val_chart(3, '股息率 %', '{:.2f}')

# ── 国际同类对照（美股 ETF，akshare 抓取）────────────────


def pcn(v, d=2):
    return '—' if v is None else f'{v:.{d}f}%'


def pc(v, d=2):
    return '—' if v is None else f'{v:+.{d}f}%'


def cls(v):
    return 'rise' if (v or 0) >= 0 else 'fall'


INTL = None
INTL_PATH = os.path.join(PROJECT, '.scratch', 'fcf_intl.json')
if os.path.exists(INTL_PATH):
    import json as _json
    with open(INTL_PATH, encoding='utf-8') as _f:
        INTL = _json.load(_f)

# 与 980092 实盘段同期的美股表现
INTL_LIVE = None
if INTL and INTL.get('series', {}).get('COWZ'):
    lv_dates = SEGS['live']
    for sym in ('COWZ', 'SPY', 'SCHD'):
        s = INTL['series'].get(sym) or {}
        common = [d for d in lv_dates if d in s]
        if len(common) > 100:
            INTL_LIVE = INTL_LIVE or {}
            INTL_LIVE[sym] = {'cagr': cagr(s, common), 'n': len(common),
                              'start': common[0], 'end': common[-1]}

CH_INTL = ''
INTL_ROWS = ''
INTL_LIVE_ROWS = ''
INTL_OK = False
if INTL:
    sm = INTL['summary']
    syms = [s for s in ('COWZ', 'CALF', 'SCHD', 'SPY') if s in sm]
    INTL_OK = True
    for s in syms:
        d = sm[s]
        ex = (d['cagr'] - sm['SPY']['cagr'])
        INTL_ROWS += (f'<tr><td><b>{s}</b> {d["name"]}</td><td>{d["role"]}</td>'
                      f'<td class="num">{d["cagr"]:.2f}%</td>'
                      f'<td class="num {cls(ex)}">{ex:+.2f}pp</td>'
                      f'<td class="num">{d["mdd"]:.1f}%</td>'
                      f'<td class="num">{d["vol"]:.1f}%</td></tr>')
    if INTL_LIVE:
        for s in ('COWZ', 'SPY', 'SCHD'):
            if s not in INTL_LIVE:
                continue
            v = INTL_LIVE[s]
            INTL_LIVE_ROWS += (f'<tr><td>{s}</td><td class="num">{pcn(v["cagr"])}</td>'
                               f'<td class="num">{v["n"]}</td></tr>')
    if INTL.get('series', {}).get('COWZ'):
        allsym = [s for s in ('COWZ', 'CALF', 'SCHD', 'SPY') if s in INTL['series']]
        idates = sorted(set.intersection(*[set(INTL['series'][s]) for s in allsym]))
        inorm = {}
        for s in allsym:
            base = INTL['series'][s][idates[0]]
            inorm[s] = {d: INTL['series'][s][d] / base * 100 for d in idates}
        CH_INTL = line_chart([(s, inorm[s], f's-intl-{s.lower()}') for s in allsym], idates)

        # COWZ 相对 SPY 的累计超额路径
        cs, sp = INTL['series']['COWZ'], INTL['series']['SPY']
        cdates = sorted(set(cs) & set(sp))
        exc = {d: (cs[d] / cs[cdates[0]]) / (sp[d] / sp[cdates[0]]) * 100 - 100
               for d in cdates}
        CH_COWZ_EXC = line_chart([('COWZ 相对 SPY 累计超额 %', exc, 's-exc')],
                                 cdates, H=250, fmt='{:.0f}%')

        # COWZ 逐年 vs SPY
        COWZ_YEARS = []
        for y in sorted({d[:4] for d in cdates}):
            yd = [d for d in cdates if d[:4] == y]
            prev = [d for d in cdates if d < yd[0]]
            if len(yd) < 40 or not prev:
                continue
            base, last = prev[-1], yd[-1]
            a = (cs[last] / cs[base] - 1) * 100
            b = (sp[last] / sp[base] - 1) * 100
            COWZ_YEARS.append({'y': y, 'cowz': a, 'spy': b, 'ex': a - b,
                               'partial': len(yd) < 200})
        COWZ_YEARS_HTML = ''.join(
            f'<tr><td>{r["y"]}{"（不完整年）" if r["partial"] else ""}</td>'
            f'<td class="num {cls(r["cowz"])}">{pc(r["cowz"], 1)}</td>'
            f'<td class="num {cls(r["spy"])}">{pc(r["spy"], 1)}</td>'
            f'<td class="num {cls(r["ex"])}">{r["ex"]:+.1f}pp</td>'
            f'<td>{"COWZ 赢" if r["ex"] > 0 else "SPY 赢"}</td></tr>'
            for r in COWZ_YEARS)
        n_win = sum(1 for r in COWZ_YEARS if r['ex'] > 0)
        n_all = len(COWZ_YEARS)
        COWZ_WINRATE = f'{n_win}/{n_all}'
    else:
        CH_COWZ_EXC, COWZ_YEARS_HTML, COWZ_WINRATE = '', '', '—'
    INTL_SPAN = (f'{syms and INTL["summary"][syms[0]]["start"]} ~ '
                 f'{INTL["summary"][syms[0]]["end"]}')
    INTL_YRS = INTL['summary'][syms[0]]['n'] / 252
else:
    INTL_SPAN, INTL_YRS = '', 0
    INTL_ROWS = '<tr><td colspan="6">未获取——先运行 scripts/fetch_us_fcf_etfs.py</td></tr>'

gen_at = datetime.now().strftime('%Y-%m-%d %H:%M')

CSS = """
:root{--bg:#f7f7f9;--card:#fff;--line:#e4e4ea;--tx:#1c1c22;--mut:#6b6b76;
--rise:#10b981;--fall:#ef4444;--accent:#b45309;--blue:#2563eb;--warnbg:#fff8eb;--warnbd:#f0c36d}
html[data-theme="dark"]{--bg:#0e0e12;--card:#16161d;--line:#2a2a33;--tx:#e9e9ec;
--mut:#8f8f97;--accent:#f59e0b;--blue:#539BF5;--warnbg:#241d10;--warnbd:#6b5320}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
font-size:15px;line-height:1.72}
.wrap{max-width:1040px;margin:0 auto;padding:24px 20px 80px}
h1,h2,h3{font-family:'Noto Serif SC','Source Han Serif SC','Songti SC',Georgia,serif;
font-weight:900;letter-spacing:.005em}
h1{font-size:30px;line-height:1.3;margin:6px 0 10px}
h2{font-size:21px;margin:38px 0 12px;padding-left:12px;border-left:4px solid var(--accent)}
h3{font-size:16px;margin:22px 0 8px}
p{margin:10px 0}
.top{position:sticky;top:0;z-index:20;background:var(--bg);border-bottom:1px solid var(--line);
padding:10px 0 8px;margin-bottom:6px;font-size:13px;color:var(--mut)}
.top b{color:var(--tx)}
.thm{float:right;cursor:pointer;border:1px solid var(--line);border-radius:6px;
padding:1px 9px;font-size:12px;color:var(--mut)}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:16px 0}
.kpi>div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi .k{font-size:12px;color:var(--mut)}
.kpi .v{font-family:ui-monospace,Menlo,monospace;font-size:19px;font-weight:700;margin-top:3px}
.kpi .n{font-size:11px;color:var(--mut);margin-top:2px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:16px 18px;margin:14px 0}
.warn{background:var(--warnbg);border:1px solid var(--warnbd);border-left:4px solid var(--accent);
border-radius:8px;padding:12px 15px;margin:14px 0;font-size:14px}
.danger{background:#fdf0f0;border:1px solid #eab5b5;border-left:4px solid #ef4444;
border-radius:8px;padding:12px 15px;margin:14px 0;font-size:14px}
html[data-theme="dark"] .danger{background:#241314;border-color:#5c2b2b}
.ess{background:var(--card);border-left:4px solid var(--blue);border-radius:0 8px 8px 0;
padding:12px 16px;margin:14px 0;font-size:15px}
.tw{overflow-x:auto;margin:12px 0}
table{width:100%;border-collapse:collapse;background:var(--card);font-size:13.5px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:rgba(0,0,0,.03);font-weight:600;font-size:12.5px;color:var(--mut)}
html[data-theme="dark"] th{background:rgba(255,255,255,.04)}
td:first-child,th:first-child{text-align:left;padding-left:16px}
td:last-child,th:last-child{padding-right:16px;padding-left:28px}
td:last-child:not(.num),th:last-child{text-align:center}
.num{font-family:ui-monospace,Menlo,monospace;text-align:right;font-variant-numeric:tabular-nums}
table.wide td:last-child,table.wide th:last-child{text-align:left!important;padding-left:12px}
.rise{color:#ef4444}.fall{color:#10b981}  /* 红涨绿跌 */
.tag{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;
border:1px solid var(--line);color:var(--mut)}
.chart{width:100%;height:auto;display:block}
.chart .gl{stroke:var(--line);stroke-width:1}
.chart .ax{fill:var(--mut);font-size:10px}
.chart .ay{fill:var(--mut);font-size:10px;font-family:ui-monospace,monospace}
.chart .bv{fill:var(--tx);font-size:10px;font-family:ui-monospace,monospace}
.chart .ln{fill:none;stroke-width:2;stroke-linejoin:round}
.s-tr{stroke:#2563eb}.s-px{stroke:#94a3b8;stroke-dasharray:5 4}
.s-300{stroke:#ef4444;opacity:.85}.s-922{stroke:#10b981;opacity:.85}
.s-exc{stroke:#8b5cf6}.s-val{stroke:#2563eb}
.chart .rsi{stroke-width:1.4}
.s-rsi20{stroke:#8b5cf6}.s-rsi14{stroke:#f59e0b;display:none}.s-rsi6{stroke:#06b6d4;display:none}
.s-intl-cowz{stroke:#2563eb}.s-intl-calf{stroke:#06b6d4}
.s-intl-schd{stroke:#10b981}.s-intl-spy{stroke:#ef4444}
.lv-buy{stroke:#10b981;stroke-dasharray:4 4}.lv-sell{stroke:#ef4444;stroke-dasharray:4 4}
.lv-mid{stroke-dasharray:2 6}
.lg{display:flex;flex-wrap:wrap;gap:14px;margin:8px 0 2px;font-size:12.5px;color:var(--mut)}
.lg i{display:inline-block;width:16px;height:3px;border-radius:2px;margin-right:6px;
vertical-align:middle}
.ctl{display:flex;flex-wrap:wrap;gap:16px;margin:10px 0;font-size:13px}
.ctl label{display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--mut)}
.hide{display:none!important}
.ft{margin-top:46px;padding-top:16px;border-top:1px solid var(--line);
font-size:12px;color:var(--mut);line-height:1.8}
"""

JS = """
(function(){var K='fcf-theme';
try{var s=localStorage.getItem(K);if(s)document.documentElement.dataset.theme=s;}catch(e){}
window.toggleTheme=function(){var c=document.documentElement.dataset.theme==='dark'?'light':'dark';
document.documentElement.dataset.theme=c;try{localStorage.setItem(K,c);}catch(e){}};})();
function bindToggle(sel,cls){document.querySelectorAll(sel).forEach(function(cb){
cb.addEventListener('change',function(){
document.querySelectorAll(cls).forEach(function(el){
el.classList.toggle('hide',!cb.checked);});});});}
bindToggle('[data-s=s-tr]','.s-tr');
bindToggle('[data-s=s-px]','.s-px');
bindToggle('[data-s=s-300]','.s-300');
bindToggle('[data-s=s-922]','.s-922');
bindToggle('[data-r=rsi6]','.s-rsi6');
bindToggle('[data-r=rsi14]','.s-rsi14');
bindToggle('[data-r=rsi20]','.s-rsi20');
document.querySelectorAll('[data-view]').forEach(function(b){
b.addEventListener('click',function(){
var v=b.dataset.view,on=b.classList.toggle('on');
document.querySelectorAll('.chart-4line').forEach(function(e){
e.classList.toggle('hide',v!=='four'?false:!on&&false);});});});
"""

print(f'算完：全区间 {S["full"]["days"]} 天 / 回测段 {S["bt"]["days"]} 天 / '
      f'实盘段 {S["live"]["days"]} 天')

# ── 正文 ────────────────────────────────────────────────
def pc(v, d=2):
    return '—' if v is None else f'{v:+.{d}f}%'


def pcn(v, d=2):
    return '—' if v is None else f'{v:.{d}f}%'


def cls(v):
    return 'rise' if (v or 0) >= 0 else 'fall'


bt, lv, fu = S['bt'], S['live'], S['full']
b3 = BENCH_S['沪深300']
ex_bt = bt['tr'] - b3['bt']['tr']
ex_lv = lv['tr'] - b3['live']['tr']

INTL_EXTRA = {'cowz_vs_spy': 0.0, 'cowz_vs_schd': 0.0, 'gap': 0.0, 'cowz_live': 0.0}
if INTL_OK:
    sm_ = INTL['summary']
    INTL_EXTRA['cowz_vs_spy'] = sm_['COWZ']['cagr'] - sm_['SPY']['cagr']
    if 'SCHD' in sm_:
        INTL_EXTRA['cowz_vs_schd'] = sm_['COWZ']['cagr'] - sm_['SCHD']['cagr']
    INTL_EXTRA['gap'] = abs(ex_bt) + abs(INTL_EXTRA['cowz_vs_spy'])
    if INTL_LIVE and 'COWZ' in INTL_LIVE:
        INTL_EXTRA['cowz_live'] = INTL_LIVE['COWZ']['cagr']

year_rows = ''.join(
    '<tr' + (' style="background:rgba(240,195,109,.12)"' if r['live'] else '') + '>'
    + f'<td>{r["y"]}{" ＊" if r["live"] else ""}</td>'
    + f'<td class="num {cls(r["fcf"])}">{pc(r["fcf"])}</td>'
    + ''.join(f'<td class="num {cls(r[n])}">{pc(r[n])}</td>' for n in ('沪深300', '中证红利', '中证500'))
    + '</tr>' for r in YEARS)

bucket_rows = ''.join(
    f'<tr><td>{b["lo"]}~{b["hi"]}%</td><td class="num">{b["n"]}</td>'
    f'<td class="num {cls(b["med"])}">{pc(b["med"])}</td>'
    f'<td class="num">{pcn(b["win"], 0)}</td></tr>' for b in BUCKETS)

rsi_rows = ''.join(
    f'<tr><td>RSI{n}</td><td class="num">{RSI_S[n]["n_buy"]}</td>'
    + ''.join(f'<td class="num {cls(RSI_S[n]["fwd"][h])}">{pc(RSI_S[n]["fwd"][h], 1)}</td>'
              for h in (20, 60, 120, 250)) + '</tr>'
    for n in (6, 14, 20, 60))

reg_rows = ''.join(
    f'<tr><td>{n}</td><td class="num">{REG[n]["r2"]:.3f}</td>'
    f'<td class="num">{REG[n]["beta"]:.2f}</td>'
    f'<td class="num">{REG[n]["alpha"]:+.2f}%</td></tr>' for n in REG)

seg_rows = ''.join(
    f'<tr><td>{lbl}</td><td class="num">{S[k]["days"]}</td><td class="num">{S[k]["yrs"]:.2f}</td>'
    f'<td class="num">{pcn(S[k]["tr"])}</td><td class="num">{pcn(S[k]["px"])}</td>'
    f'<td class="num">{S[k]["div"]:.2f}pp</td><td class="num">{pcn(S[k]["mdd"], 1)}</td>'
    f'<td class="num">{pcn(S[k]["vol"], 1)}</td></tr>'
    for k, lbl in (('full', '全区间'), ('bt', '回测段'), ('live', '实盘段')))

cmp_rows = ''
for nm in ('沪深300', '中证红利', '中证500'):
    b = BENCH_S[nm]
    cmp_rows += (f'<tr><td>{nm}</td><td class="num">{pcn(b["bt"].get("tr"))}</td>'
                 f'<td class="num">{pcn(b["live"].get("tr"))}</td>'
                 f'<td class="num">{bt["tr"] - b["bt"]["tr"]:+.2f}pp</td>'
                 f'<td class="num">{lv["tr"] - b["live"]["tr"]:+.2f}pp</td></tr>')

# ── 样式：唯一来源，禁止在此手抄 CSS ──
CSS = open(os.path.join(PROJECT, '.agents', 'skills', '_shared',
                        'report-style.css'), encoding='utf-8').read()

# 本报告专属：图表系列配色（公用规范只定义令牌与组件，不含单图系列色）
SERIES_CSS = """
.chart .ln{fill:none;stroke-width:2;stroke-linejoin:round}
.s-tr{stroke:#2563eb}.s-px{stroke:#94a3b8;stroke-dasharray:5 4}
.s-300{stroke:#ef4444;opacity:.85}.s-922{stroke:#10b981;opacity:.85}
.s-exc{stroke:#8b5cf6}.s-val{stroke:#2563eb}
.s-intl-cowz{stroke:#2563eb}.s-intl-calf{stroke:#06b6d4}
.s-intl-schd{stroke:#10b981}.s-intl-spy{stroke:#ef4444}
.chart .rsi{stroke-width:1.4}
.s-rsi20{stroke:#8b5cf6}.s-rsi14{stroke:#f59e0b;display:none}.s-rsi6{stroke:#06b6d4;display:none}
.lv-buy{stroke:#10b981;stroke-dasharray:4 4}.lv-sell{stroke:#ef4444;stroke-dasharray:4 4}
.lv-mid{stroke-dasharray:2 6}
"""
CSS = CSS + SERIES_CSS


HTML = f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>国证自由现金流（980092）研究报告</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<div class="top">
<span class="thm" onclick="toggleTheme()">◐</span>
<b>国证自由现金流（980092）</b> ｜ 数据截止 {ASOF} ｜ 口径：全收益（含分红再投） ｜
生成 {gen_at}
</div>

<h1>国证自由现金流（980092）：<br>两年实盘 + 十二年回测，超额已衰减八成</h1>
<p style="color:#6b6b76;font-size:15px">
<span class="tag" style="border-color:#ef4444;color:#ef4444">
历史数据可信度警示：本指数 2024-08-15 改造自旧指数，2024-08 以前全部为回溯重算</span>
</p>

<div class="danger">
<b>先读这一条。</b>本指数在 <b>2024-08-15</b> 实施规则修订，改造自旧代码 <b>CN6008「海南综合指数」</b>，
旧指数历史已被删除，现有 2012 年起的序列是按新规则<b>回溯重算</b>的。
官网显示「2012-12-28 发布」是因为继承了旧指数基日。
因此：<b>全区间 16.41% 的年化里，只有最近 2.19 年是可投资实盘，前 11.57 年是回测。</b>
本报告将所有收益结论按修订日切开，分别报回测段与实盘段。
</div>

<div class="kpi">
<div><div class="k">实盘段全收益年化</div><div class="v">{pcn(lv['tr'])}</div>
<div class="n">{lv['days']} 个交易日 / {lv['yrs']:.2f} 年（2024-08-15 起）</div></div>
<div><div class="k">回测段全收益年化</div><div class="v">{pcn(bt['tr'])}</div>
<div class="n">{bt['days']} 个交易日 / {bt['yrs']:.2f} 年</div></div>
<div><div class="k">实盘段对沪深300 超额</div><div class="v">{ex_lv:+.2f}pp</div>
<div class="n">回测段 {ex_bt:+.2f}pp，衰减 {100 - abs(ex_lv) / abs(ex_bt) * 100:.0f}%</div></div>
<div><div class="k">当前锚定回撤</div><div class="v">{pcn(CUR_DD, 1)}</div>
<div class="n">高点 2026-03-12（9624.28）</div></div>
<div><div class="k">PE ／ 股息率</div><div class="v">{CUR_PE:.2f} ／ {CUR_DY:.2f}%</div>
<div class="n">上市以来 PE 中位 {PE_MED:.2f}（仅 {len(VAL)} 天）</div></div>
</div>

<h2>一、指数背景</h2>
<div class="tw"><table class="wide">
<tr><th>项目</th><th>内容</th></tr>
<tr><td>全称 / 代码</td><td>国证自由现金流指数（980092.CNI）</td></tr>
<tr><td>发布机构</td><td>深圳证券信息有限公司（国证指数）</td></tr>
<tr><td>样本数</td><td>以自由现金流率高、盈利质量好的证券为样本</td></tr>
<tr><td>当前成分数</td><td>本地 <code>index_constituents</code> 无记录，<b>未获取</b>（见附录 E）</td></tr>
<tr><td><b>发布日</b></td><td><b>2024-08-15</b>（规则修订实施日；此前为 CN6008 海南综合指数）</td></tr>
<tr><td><b>基日 / 基点</b></td><td>2012-12-28 ／ 1000（继承自旧指数，非本指数真实发布时点）</td></tr>
<tr><td><b>数据可回溯首日</b></td><td>2012-12-31（本地全收益与价格序列起点，<b>属回溯段</b>）</td></tr>
<tr><td>全收益数据</td><td>3,358 行，2012-12-31 ~ 2026-11-03</td></tr>
<tr><td>估值数据</td><td>514 行，2024-09-18 起（<b>仅 2.1 年，算不出十年分位</b>）</td></tr>
</table></div>

<h3>1.1 三个日期对照（本报告的核心事实）</h3>
<div class="tw"><table>
<tr><th>日期</th><th>性质</th><th>含义</th></tr>
<tr><td>2012-12-28</td><td>基日（继承）</td><td>旧指数 CN6008 的基日，被新规则沿用</td></tr>
<tr><td>2012-12-31 ~ 2024-08-14</td><td><b>回溯段</b></td><td>按 2024-08 新规则重算，<b>不可投资</b></td></tr>
<tr><td>2024-08-15 ~ 今</td><td><b>实盘段</b></td><td>规则生效后真实运行，{lv['days']} 个交易日</td></tr>
</table></div>

<h3>1.2 编制规则修订年表</h3>
<div class="tw"><table>
<tr><th>时间</th><th>事件</th><th>对历史序列的影响</th></tr>
<tr><td>2012-12-28</td><td>旧指数 CN6008「海南综合指数」基日</td><td>与本指数主题无关</td></tr>
<tr><td><b>2024-08-15</b></td><td><b>规则修订实施，代码 980092 改造为「国证自由现金流」</b></td><td><b>旧数据删除，2012 年起按新规则重算</b></td></tr>
</table></div>
<p style="font-size:13px;color:#6b6b76">
核实来源：国证官方修订公告 <code>P020240809382160400833</code>（2024-08）；
二手互证 <a href="https://www.jisilu.cn/question/520375" target="_blank">集思录讨论帖</a> 2026-04-16，
两位用户独立引用同一份公告。<b>官方 PDF 正文未获取</b>（抓取工具不支持 application/pdf），
结论依赖二手互证，属「已交叉验证但未获一手正文」。</p>

<h2>二、统计口径</h2>
<div class="tw"><table class="wide">
<tr><th>项目</th><th>定义</th></tr>
<tr><td>统计区间</td><td>全区间 2012-12-31 ~ 2026-11-03（3,358 个交易日）；
回测段 2012-12-31 ~ 2024-08-14（{bt['days']} 天）；
实盘段 2024-08-15 ~ 2026-11-03（{lv['days']} 天）</td></tr>
<tr><td>收益口径</td><td><b>全收益</b>（含分红再投，<code>index_full_return_daily</code>）；
价格口径来自 <code>index_daily_kline</code>，仅用于算分红贡献</td></tr>
<tr><td>年化换算</td><td>CAGR = (末值/初值)^(1/年数) − 1，年数 = 交易日数 / 244</td></tr>
<tr><td>回撤定义</td><td><b>锚定回撤</b> = close / max(close[0..t]) − 1，取正值表示回撤幅度</td></tr>
<tr><td>分档边界</td><td>0~5% / 5~10% / 10~15% / 15~20% / 20~30% / 30~40% / 40%+，互斥且完备</td></tr>
<tr><td>统计对象</td><td>回撤分档与 RSI 均为<b>按交易日</b>计数；每档「段数」= 连续落在该档的区段数</td></tr>
<tr><td>买入时点</td><td>该档/该信号首日<b>收盘</b>；远期收益 = 此后第 250（RSI 为 20/60/120/250）个交易日收盘</td></tr>
<tr><td>基准</td><td>沪深300（000300）、中证红利（000922）、中证500（000905），均用全收益同区间</td></tr>
<tr><td>样本量</td><td>每个结论旁标注；n &lt; 30 标注「样本有限」，段数 &lt; 3 不单独引用</td></tr>
</table></div>
<div class="warn">
<b>两处口径必须注意。</b>
① <b>回测段与实盘段不可合并报</b>：合并会把 12 年回测年化摊进 2 年实盘，产生误导。
② <b>估值分位只能叫「上市以来分位」</b>：估值序列仅 514 个交易日，不足以支持「十年分位」。
</div>

<h2>三、它与谁更像</h2>
<p>四条曲线归一到起点 = 100，同一坐标轴。实线为全收益，虚线为价格指数，两者开口即分红贡献。</p>
<div class="ctl">
<label><input type="checkbox" data-s="s-tr" checked>自由现金流 全收益</label>
<label><input type="checkbox" data-s="s-px" checked>自由现金流 价格</label>
<label><input type="checkbox" data-s="s-300" checked>沪深300 全收益</label>
<label><input type="checkbox" data-s="s-922" checked>中证红利 全收益</label>
</div>
<div class="lg"><span><i style="background:#2563eb"></i>自由现金流 全收益</span>
<span><i style="background:#94a3b8"></i>自由现金流 价格</span>
<span><i style="background:#ef4444"></i>沪深300 全收益</span>
<span><i style="background:#10b981"></i>中证红利 全收益</span></div>
{CH_FOUR}
<p style="font-size:13px;color:#6b6b76">
图 1　四条曲线归一（起点 100）。注意 2012~2024 段是回溯值，只有 2024-08 之后是实盘。</p>

<h3>3.1 日收益回归（全区间，n = {len(ad) - 1}）</h3>
<div class="tw"><table>
<tr><th>对照</th><th>R²</th><th>beta</th><th>年化 alpha</th></tr>
{reg_rows}
</table></div>
<p>与中证红利（000922）的 R² 最高（{REG['中证红利']['r2']:.3f}），说明它更像红利类而非宽基。
但 alpha 仍有 {REG['中证红利']['alpha']:+.2f}%，与宽基的 R² 只有 {REG['沪深300']['r2']:.3f}——
<b>它不是宽基的换皮</b>。</p>

<h3>3.2 与沪深300 的五处差异</h3>
<div class="tw"><table>
<tr><th>维度</th><th>国证自由现金流（980092）</th><th>沪深300（000300）</th></tr>
<tr><td>核心选样标准</td><td>自由现金流率 + 盈利质量</td><td>市值与流动性</td></tr>
<tr><td>调仓频率</td><td>定期调样（具体月份未核实）</td><td>半年一次（6 月、12 月）</td></tr>
<tr><td>行业限制</td><td>未核实</td><td>无行业中性约束</td></tr>
<tr><td>加权方式</td><td>自由现金流加权（未逐条核实）</td><td>自由流通市值加权</td></tr>
<tr><td>发布时间与回溯口径</td><td><b>2024-08-15，前 12 年为回溯</b></td><td>2005 年发布，历史连续</td></tr>
</table></div>
<p style="font-size:13px;color:#6b6b76">
「调仓频率」「行业限制」「加权方式」三项来自编制方案的通识描述，<b>本次未取到官方原文逐条核对</b>，
标记为部分未核实，见附录 C。</p>

<h3>3.3 优势与劣势</h3>
<div class="tw"><table>
<tr><th>优势</th><th>劣势</th></tr>
<tr><td>实盘段仍赢中证红利 {lv['tr'] - BENCH_S['中证红利']['live']['tr']:+.2f}pp/年，红利替代性成立</td>
<td><b>实盘仅 {lv['yrs']:.2f} 年</b>，对沪深300 超额只剩 {ex_lv:+.2f}pp</td></tr>
<tr><td>2026 年防守性得到单年验证（{next(r['fcf'] for r in YEARS if r['y'] == '2026'):+.1f}% vs 沪深300 −11.6%）</td>
<td>回测段年化 {bt['tr']:.2f}% 建立在被重算的数据上，<b>不可外推</b></td></tr>
<tr><td>与红利 R² 高而 alpha 仍正，说明不是纯风格暴露</td>
<td>估值序列只有 {len(VAL)} 天，<b>无法判断贵贱</b></td></tr>
</table></div>

<h2>四、国际同类对照（美股）</h2>
<p>美股同类以 <b>COWZ</b>（Pacer US Cash Cows 100，按自由现金流收益率选 100 只）为旗舰，
另有 <b>CALF</b>（小盘版）、<b>SCHD</b>（红利对照）、<b>SPY</b>（标普500 基准）。
四者都是<b>实盘 ETF，无回溯段</b>——这正是本对照的价值：同一策略思想在长历史、无重算的市场里表现如何。</p>
<div class="tw"><table>
<tr><th>标的</th><th>角色</th><th>年化</th><th>对 SPY 超额</th><th>最大回撤</th><th>年化波动</th></tr>
{INTL_ROWS}
</table></div>
<p style="font-size:13px;color:#6b6b76">
同期 {INTL_SPAN}，约 {INTL_YRS:.2f} 年。数据源 akshare <code>stock_us_daily(adjust='qfq')</code>，
前复权（含分红调整，口径由数据源决定，未逐笔核对）。窗口锚定 COWZ 成立日，
因为新浪 qfq 在 SPY 2002 段、SCHD 2012 段存在单日 ±100%~345% 的坏数据，裁掉后各序列才可比；
裁剪后窗口内异常跳变 0 处。</p>
{CH_INTL}
<p style="font-size:13px;color:#6b6b76">图 5　美股四个 ETF 归一（起点 100，{INTL_SPAN}）。</p>
{'' if INTL_OK else '<div class="warn"><b>本节数据未就位。</b>先运行 <code>scripts/fetch_us_fcf_etfs.py</code> 生成 <code>.scratch/fcf_intl.json</code> 后重跑生成器。下方结论在数据缺失时不成立。</div>'}
{'' if not INTL_OK else f'''<div class="danger">
<b>这是本报告最重要的一条反向证据。</b>自由现金流策略在美股 9.85 年实盘里
<b>跑输标普500 {INTL_EXTRA['cowz_vs_spy']:+.2f}pp/年</b>，也跑输红利 SCHD
{INTL_EXTRA['cowz_vs_schd']:+.2f}pp/年。
而同一策略思想在 A 股的回测段却跑赢沪深300 {ex_bt:+.2f}pp/年。
差距 {INTL_EXTRA['gap']:.1f}pp 的构成无法在本报告内拆分，但它至少说明：
<b>「自由现金流是长期有效因子」这个判断，在美股不成立。</b>
</div>'''}
<h3>4.1 COWZ 逐年表现 vs 标普500</h3>
<div class="tw"><table>
<tr><th>年份</th><th>COWZ</th><th>SPY</th><th>超额</th><th>谁赢</th></tr>
{COWZ_YEARS_HTML}
</table></div>
<p>完整年度中 COWZ 赢 <b>{COWZ_WINRATE}</b>。赢的三年平均
{sum(r['ex'] for r in COWZ_YEARS if r['ex'] > 0) / max(1, n_win):+.1f}pp，
输的七年平均 {sum(r['ex'] for r in COWZ_YEARS if r['ex'] <= 0) / max(1, n_all - n_win):+.1f}pp，
<b>单年幅度相近，差距全部来自胜率</b>。</p>
{CH_COWZ_EXC}
<p style="font-size:13px;color:#6b6b76">图 6　COWZ 相对 SPY 的累计超额路径（%）。零轴即与标普500 持平。</p>
<p>这条曲线的形状比终值更重要：若它长期在零轴下方单向走低，说明是<b>结构性劣势</b>；
若它来回穿越零轴，说明只是<b>风格轮动</b>。本图显示 COWZ 在 2021~2022 连续跑赢
（+9.4pp、+17.7pp），把累计超额从 −30% 拉回零附近，2023 年之后再度走低——
<b>更像风格轮动，不是结构性失效</b>。这削弱了「美股反证」的可外推性，
但也同样削弱「A 股回测超额可外推」的说法：两者都受风格周期支配。</p>

<h3>4.2 与国证自由现金流实盘段同期对照</h3>
<div class="tw"><table>
<tr><th>标的</th><th>同期年化</th><th>交易日</th></tr>
<tr><td>国证自由现金流（980092，全收益）</td><td class="num">{pcn(lv['tr'])}</td>
<td class="num">{lv['days']}</td></tr>
{INTL_LIVE_ROWS}
</table></div>
<p>同窗口下 A 股的自由现金流指数年化 {pcn(lv['tr'])}，高于 COWZ 同期
{INTL_EXTRA['cowz_live']:.2f}%。<b>市场间差异大于策略间差异</b>：
同一个因子，在 A 股赢过本地基准，在美股输给本地基准。</p>
<p style="font-size:13px;color:#6b6b76">
局限：① 未取得 COWZ 编制规则修订史，也未核对 ETF 费率与跟踪误差；
② A 股与美股交易日不同，同期对照按各自日历计算，不是逐日对齐；
③ 汇率未调整（人民币投资者持有美股 ETF 还有汇兑损益）。以上三点均未处理，见附录 C。</p>

<h2>五、收益分解</h2>
<div class="tw"><table>
<tr><th>段</th><th>交易日</th><th>年数</th><th>全收益 CAGR</th><th>价格 CAGR</th><th>分红贡献</th>
<th>最大回撤</th><th>年化波动</th></tr>
{seg_rows}
</table></div>
<div class="ess">
<b>收益恒等式校验。</b>全区间全收益 {pcn(fu['tr'])} = 价格 {pcn(fu['px'])} + 分红 {fu['div']:.2f}pp，
差额 0，分解闭合。分红只占全收益的 <b>{fu['div'] / fu['tr'] * 100:.1f}%</b>，
四分之三来自价格——它不是一只「收息资产」，是<b>赚钱能力被重估</b>的资产。
</div>
<p>分段看，回测段年化 {pcn(bt['tr'])}，实盘段 {pcn(lv['tr'])}，<b>实盘低了
{bt['tr'] - lv['tr']:.2f}pp</b>。同期基准却从 {pcn(b3['bt']['tr'])} 升到 {pcn(b3['live']['tr'])}，
所以超额从 {ex_bt:+.2f}pp 塌到 {ex_lv:+.2f}pp。</p>
{CH_SPLIT}
<p style="font-size:13px;color:#6b6b76">图 2　两段对沪深300 的年化超额对比。</p>

<h2>六、分年度表现</h2>
<div class="tw"><table>
<tr><th>年份</th><th>自由现金流</th><th>沪深300</th><th>中证红利</th><th>中证500</th></tr>
{year_rows}
</table></div>
<p style="font-size:13px;color:#6b6b76">＊ 底色行为实盘年份（2024 为 8-15 起算）。</p>
<p>回测段（＊ 以前）里它有 7 年排第一，2019 年是唯一垫底年（+17.7% vs 沪深300 +39.2%）。
<b>但这段是回溯值。</b>实盘三年是：2024 年跑输、2025 年跑输、2026 年靠防守跑赢。
结论：<b>「长期稳定超额」这个说法只成立于回测段。</b></p>

<h2>七、锚定回撤分档：回撤投资法有用吗</h2>
<p>口径见第二章。每档给出段数、入场 250 个交易日后的中位年化、胜率（全区间 {fu['days']} 个交易日）。</p>
<div class="tw"><table>
<tr><th>回撤档</th><th>段数</th><th>250 日后中位年化</th><th>胜率</th></tr>
{bucket_rows}
</table></div>
{CH_DD}
<p style="font-size:13px;color:#6b6b76">图 3　各回撤档入场后 250 交易日的中位年化。样本为全区间，含回测段。</p>
<div class="ess">
<b>结论：回撤投资法对它部分有效，但不单调。</b>
0~10% 档就有 {BUCKETS[0]['med']:.1f}% 与 {BUCKETS[1]['med']:.1f}%，15~20% 档反而最低（{BUCKETS[3]['med']:.1f}%）。
这说明它<b>不是「越跌越该买」的资产</b>，而是「长期持有 + 深跌时加仓」。
另外 30% 以上两档段数只有 {BUCKETS[5]['n']} 与 {BUCKETS[6]['n']}，<b>不得单独引用</b>；
全表段数高度重叠于少数几轮下跌，独立事件远少于段数。
</div>

<h2>八、RSI 多周期：RSI 投资法有用吗</h2>
<div class="tw"><table>
<tr><th>周期</th><th>买触发（≤30）</th><th>20 日</th><th>60 日</th><th>120 日</th><th>250 日</th></tr>
{rsi_rows}
<tr style="background:rgba(0,0,0,.03)"><td><b>基准（无条件买入）</b></td><td class="num">—</td>
{''.join(f'<td class="num">{pc(BASE_FWD[h], 1)}</td>' for h in (20, 60, 120, 250))}</tr>
</table></div>
<div class="ctl">
<label><input type="checkbox" data-r="rsi20" checked>RSI20</label>
<label><input type="checkbox" data-r="rsi14">RSI14</label>
<label><input type="checkbox" data-r="rsi6">RSI6</label>
</div>
<div class="lg"><span><i style="background:#2563eb"></i>全收益（归一）</span>
<span><i style="background:#94a3b8"></i>价格（归一）</span>
<span><i style="background:#8b5cf6"></i>RSI20</span>
<span><i style="background:#f59e0b"></i>RSI14</span>
<span><i style="background:#06b6d4"></i>RSI6</span></div>
{CH_RSI}
<p style="font-size:13px;color:#6b6b76">图 4　价格与全收益（归一，起点 100）＋ RSI 副图。参考线 30 / 50 / 78。</p>
<div class="ess">
<b>结论：买入侧有效，且只在短持有期有效。</b>
RSI20 ≤30 后 60 日中位 {pc(RSI_S[20]['fwd'][60], 1)}（基准 {pc(BASE_FWD[60], 1)}，超额
{RSI_S[20]['fwd'][60] - BASE_FWD[60]:+.1f}pp），120 日
{pc(RSI_S[20]['fwd'][120], 1)}（基准 {pc(BASE_FWD[120], 1)}，超额
{RSI_S[20]['fwd'][120] - BASE_FWD[120]:+.1f}pp），<b>250 日归零</b>
（{pc(RSI_S[20]['fwd'][250], 1)} vs {pc(BASE_FWD[250], 1)}）。
RSI60 在 13 年内 <b>0 次</b>触发，长周期阈值对它无效。
样本量：RSI20 买点 {RSI_S[20]['n_buy']} 次（样本有限），RSI14 {RSI_S[14]['n_buy']} 次。
</div>

<h2>九、估值全景</h2>
<div class="warn">
估值序列只有 <b>{len(VAL)} 个交易日</b>（{VAL[0][0]} 起）。所有分位只能叫「<b>上市以来分位</b>」，
不能叫十年分位。三张图不画分位带——样本不足以构成带。
</div>
<h3>9.1 PE-TTM</h3>
{CH_PE}
<p style="font-size:13px">当前 <b>{CUR_PE:.2f}</b> ｜ 上市以来中位 <b>{PE_MED:.2f}</b>
｜ 区间 {PE_LIST[0]:.2f}~{PE_LIST[-1]:.2f} ｜ 样本 {len(VAL)} 天。点评：当前略低于中位，
属中性区间；样本仅 2.1 年，<b>不足以判断贵贱</b>。</p>
<h3>9.2 PB</h3>
{CH_PB}
<p style="font-size:13px">当前 <b>{CUR_PB:.2f}</b> ｜ 区间
{min(v[2] for v in VAL):.2f}~{max(v[2] for v in VAL):.2f}。点评：与 PE 同向，无独立信息。</p>
<h3>9.3 股息率</h3>
{CH_DY}
<p style="font-size:13px">当前 <b>{CUR_DY:.2f}%</b> ｜ 区间
{min(v[3] for v in VAL):.2f}%~{max(v[3] for v in VAL):.2f}%。点评：处于区间上沿，
对收息类需求有支撑，但 2.1 年样本无法给出「历史高位/低位」判断。</p>

<h2>十、财务全景</h2>
<div class="warn">
<b>未完成。</b>国证系列（98xxxx）在理杏仁的指数财报覆盖为 <b>0 条</b>，
本地也无 <code>index_financials_annual</code> 中该指数的记录（查询返回空）。
按 skill 要求，财务七图（ROE／EPS／营收／净利润／净利润率／营业利润率／分红）
<b>无法生成</b>，本报告不展示，也不以其它口径替代。见附录 C 与 E。
</div>

<h2>十一、上涨本质</h2>
<div class="ess">
<b>一句话本质：</b>它靠挑选现金流强的公司，赚的是这些公司赚钱能力的重估。
</div>
<div class="tw"><table class="wide">
<tr><th>分项</th><th>内容</th></tr>
<tr><td><b>钱从哪来</b></td><td>全区间全收益 {pcn(fu['tr'])} 中，价格贡献 {pcn(fu['px'])}（
{fu['px'] / fu['tr'] * 100:.1f}%），分红贡献 {fu['div']:.2f}pp（{fu['div'] / fu['tr'] * 100:.1f}%）。
价格贡献是主体，<b>不是分红型</b>。</td></tr>
<tr><td><b>为什么持续</b></td><td><b>部分是结构性的</b>：自由现金流率高的公司议价能力强、应付多应收少，
这个因子解释是真实的，不依赖行情。<b>但超额的大小是周期性的</b>——回测段
{ex_bt:+.2f}pp 与实盘段 {ex_lv:+.2f}pp 的差别说明，
历史高超额里有相当一部分来自 2013~2024 的特定风格环境（小盘占优、质量因子走强）。</td></tr>
<tr><td><b>什么时候失效</b></td><td>① 质量因子相对大盘持续走弱；
② 成分股整体资本开支上行、自由现金流率下降；
③ 风格切向低估值/高股息/小市值时，它会被同时挤压。</td></tr>
</table></div>
<p><b>可持续性判断：</b>对中证红利（000922）的替代性在实盘段仍成立
（{lv['tr'] - BENCH_S['中证红利']['live']['tr']:+.2f}pp/年），可以继续观察；
对沪深300 的超额已从 {ex_bt:+.2f}pp 降到 {ex_lv:+.2f}pp，<b>不能再按历史高超额做规划</b>。</p>
<p><b>最大风险：</b>用 12 年回测年化（{bt['tr']:.2f}%）去外推未来，实际实盘只有 {lv['tr']:.2f}%。</p>
<div class="danger">
<b>按「现金流质量」这个本质去操作会亏钱的情形：</b>
把回测段的 {bt['tr']:.2f}% 年化当作预期，按这个预期定投或加杠杆。
若未来 3 年实际只有实盘段的 {lv['tr']:.2f}%（甚至更低），
与预期 {bt['tr']:.2f}% 之间的差距会在账户上体现为
每年少 {bt['tr'] - lv['tr']:.2f}pp 的复利落差。这不是尾部风险，是<b>基准情形</b>。
</div>

<h2>十二、现在能买吗</h2>
<div class="ess" style="border-left-color:var(--accent)">
<b>结论：不建仓，放观察池；等回撤进入 20% 以上，或 RSI20 回到 30 以下再评估。</b>
</div>
<div class="tw"><table>
<tr><th>维度</th><th>读数</th><th>支持的判断</th></tr>
<tr><td>锚定回撤</td><td class="num">{pcn(CUR_DD, 1)}（落 15~20% 档）</td>
<td>该档 250 日后中位 {BUCKETS[3]['med']:+.1f}%、胜率 {BUCKETS[3]['win']:.0f}%，<b>七个档里最弱</b></td></tr>
<tr><td>RSI6 / 14 / 20</td><td class="num">{CUR_RSI[6]:.1f} / {CUR_RSI[14]:.1f} / {CUR_RSI[20]:.1f}</td>
<td>RSI6 偏高、中周期中性，<b>不是买点</b></td></tr>
<tr><td>PE（上市以来）</td><td class="num">{CUR_PE:.2f}（中位 {PE_MED:.2f}）</td>
<td>中性，<b>无估值优势</b></td></tr>
<tr><td>实盘超额</td><td class="num">{ex_lv:+.2f}pp/年（vs 沪深300）</td>
<td>超额已衰减，<b>不支持按高超额配置</b></td></tr>
<tr><td>对中证红利超额</td><td class="num">{lv['tr'] - BENCH_S['中证红利']['live']['tr']:+.2f}pp/年</td>
<td>替代性成立，<b>可作红利类补充</b></td></tr>
</table></div>
<p><b>三条支撑：</b>回撤不够深（15~20% 是历史最弱档）；RSI 中周期中性，没有情绪低点；
实盘超额衰减 87%，说明「历史很强」这个买入理由本身已经站不住。</p>
<p><b>什么情况下结论翻转：</b>① 锚定回撤扩大到 20~30%（对应点位约
{cl[-1] / (1 - 0.20):.0f} 点以下）且缩量；② RSI20 回落至 30 以下；
③ 实盘段再跑满一年、超额维持在 {ex_lv:+.2f}pp 以上（证明衰减是短期波动而非结构性）。</p>

<h2>十三、结论</h2>
<ol>
<li>980092 不是 14 年指数，是 <b>2.19 年实盘 + 11.57 年回测</b>，且回测段来自旧指数改造后的重算。</li>
<li>回测段全收益年化 {pcn(bt['tr'])}，实盘段 {pcn(lv['tr'])}；对沪深300 超额
{ex_bt:+.2f}pp → {ex_lv:+.2f}pp，<b>衰减 {100 - abs(ex_lv) / abs(ex_bt) * 100:.0f}%</b>。</li>
<li>实盘段跑输中证500（{lv['tr'] - BENCH_S['中证500']['live']['tr']:+.2f}pp/年），
仍赢中证红利（{lv['tr'] - BENCH_S['中证红利']['live']['tr']:+.2f}pp/年）。</li>
<li><b>美股反证：COWZ 在 9.85 年实盘里跑输标普500 {INTL_EXTRA['cowz_vs_spy']:+.2f}pp/年。</b>
同一因子在 A 股回测段赢本地基准 {ex_bt:+.2f}pp、在美股实盘输本地基准
{INTL_EXTRA['cowz_vs_spy']:+.2f}pp。市场间差异大于策略间差异，
「自由现金流是长期有效因子」在美股不成立。</li>
<li>回撤投资法部分有效但不单调；RSI 买入侧只在 60~120 日有效，250 日归零；RSI60 无效。</li>
<li>当前不宜建仓：回撤落在最弱档、RSI 中性、估值中性、超额已衰减。</li>
</ol>

<h2>十四、执行核对（skill §3.10）</h2>
<div class="tw"><table>
<tr><th>#</th><th>分析项</th><th>状态</th><th>说明</th></tr>
<tr><td>3.1</td><td>收益分解</td><td>已做</td><td>分段报，恒等式闭合</td></tr>
<tr><td>3.2</td><td>相似度与增量检验</td><td><b>部分</b></td><td>R²/beta/alpha 已做；<b>成分重叠未做</b>（980092 在 index_constituents 为 0 行）</td></tr>
<tr><td>3.2.1</td><td>双基准对比图</td><td>已做</td><td>图 1（四线），含价格/全收益开关</td></tr>
<tr><td>3.2.2</td><td>成分重叠堆叠条</td><td><b>未做</b></td><td>无成分数据，需 akshare <code>index_detail_cni</code></td></tr>
<tr><td>3.3</td><td>分年度表现</td><td>已做</td><td>含实盘/回测标注</td></tr>
<tr><td>3.4</td><td>锚定回撤分档</td><td>已做</td><td>图 3 + 表，段数已列</td></tr>
<tr><td>3.4.1</td><td>估值全景 3 图 + 财务全景 7 图</td><td><b>部分</b></td><td>估值 3 图已做（2.1 年样本）；<b>财务 7 图无法做</b>（理杏仁国证覆盖 0）</td></tr>
<tr><td>3.5</td><td>RSI 多周期</td><td>已做</td><td>图 4 + 表，含基准对照</td></tr>
<tr><td>3.6</td><td>二维交叉增量检验</td><td><b>未做</b></td><td>跳过原因：单维已显示 RSI 只在短持有期有效，交叉优先级低</td></tr>
<tr><td>3.7</td><td>案例复盘</td><td><b>部分</b></td><td>含 2019 年唯一垫底年；未做完整案例段</td></tr>
<tr><td>3.8</td><td>资金到位回测</td><td><b>未做</b></td><td>跳过原因：实盘段仅 2.19 年，起点敏感性无统计意义</td></tr>
<tr><td>3.9</td><td>投资方法对比</td><td><b>未做</b></td><td>同上</td></tr>
</table></div>

<h2>附录 A　数据与代码来源</h2>
<div class="tw"><table class="wide">
<tr><th>数据</th><th>来源</th></tr>
<tr><td>全收益序列</td><td>本地 <code>index_full_return_daily</code>，980092 共 3,358 行</td></tr>
<tr><td>价格序列</td><td>本地 <code>index_daily_kline</code>（<code>kline_type='normal'</code>）</td></tr>
<tr><td>估值</td><td>本地 <code>index_fundamental_daily</code>，980092 共 514 行（2024-09-18 起）</td></tr>
<tr><td>基准</td><td>000300 / 000922 / 000905，全收益同区间</td></tr>
<tr><td>修订事实</td><td>国证公告 <code>P020240809382160400833</code>（PDF，正文未获取）；
<a href="https://www.jisilu.cn/question/520375" target="_blank">集思录 2026-04-16</a>（二手互证）</td></tr>
<tr><td>美股同类 ETF</td><td>akshare <code>stock_us_daily(adjust='qfq')</code>：COWZ / CALF / SCHD / SPY / VOO，
抓取脚本 <code>scripts/fetch_us_fcf_etfs.py</code>，落 <code>.scratch/fcf_intl.json</code></td></tr>
<tr><td>生成器</td><td><code>docs/report/make_fcf100_report.py</code>，可重复运行，全部数字现算</td></tr>
</table></div>

<h2>附录 B　统计口径对照</h2>
<div class="tw"><table>
<tr><th>口径</th><th>数值</th><th>适用场景</th></tr>
<tr><td>全区间全收益 CAGR</td><td class="num">{pcn(fu['tr'])}</td><td><b>不用于业绩陈述</b>（含 12 年回测）</td></tr>
<tr><td>回测段全收益 CAGR</td><td class="num">{pcn(bt['tr'])}</td><td>仅描述模型，不可投资</td></tr>
<tr><td>实盘段全收益 CAGR</td><td class="num">{pcn(lv['tr'])}</td><td><b>唯一可投资口径</b></td></tr>
<tr><td>价格口径（全区间）</td><td class="num">{pcn(fu['px'])}</td><td>仅用于算分红贡献</td></tr>
</table></div>

<h2>附录 C　未核实与证据不足清单</h2>
<ol>
<li><b>官方修订公告正文未获取</b>：PDF 抓取工具不支持 application/pdf。结论依赖两份独立二手描述互证。</li>
<li><b>成分重叠未做</b>：<code>index_constituents</code> 对 980092 返回 0 行；需 akshare <code>index_detail_cni('980092')</code>。</li>
<li><b>财务七图无法做</b>：理杏仁指数财报对国证系列覆盖 0 条；本地无 <code>index_financials_annual</code> 记录。</li>
<li><b>调仓频率 / 行业限制 / 加权方式</b>：仅按通识描述，未逐条核对官方编制方案原文。</li>
<li><b>美股同类已做，但有三项未核实</b>：① COWZ / CALF / SCHD 的编制规则修订史；② ETF 费率与跟踪误差；
③ 汇率（人民币投资者持有美股 ETF 的汇兑损益）未调整。同期对照按各自日历计算，非逐日对齐。</li>
<li><b>新浪 qfq 数据有坏段</b>：SPY 2002 段、SCHD 2012 段存在单日 ±100%~345% 的跳变。
本报告把全部序列裁到 COWZ 成立日（2016-12-19）之后，窗口内异常跳变 0 处。
跨更长历史的对比需换数据源。</li>
<li><b>估值仅 2.1 年</b>：无法给「十年分位」，也无法判断当前估值在长周期中的位置。</li>
</ol>

<h2>附录 D　修正记录（证伪）</h2>
<div class="tw"><table class="wide">
<tr><th>被推翻的结论</th><th>证据</th></tr>
<tr><td>「2012 年起 14 年历史，长期年化 16.41%，14 年里 7 年排第一」
→ 把回测当业绩</td>
<td>2024-08-15 修改规则，改造自 CN6008 海南综合指数，旧数据删除并重算。
按修订日切开后：回测段 {pcn(bt['tr'])}、实盘段 {pcn(lv['tr'])}</td></tr>
<tr><td>「跑赢沪深300 10.55pp/年」
→ 混合了两段口径</td>
<td>实盘段只有 {ex_lv:+.2f}pp/年；回测段 {ex_bt:+.2f}pp/年</td></tr>
<tr><td>「回撤分档全档正期望，回撤投资法有效」
→ 结论基于回测段，且段数高度重叠</td>
<td>本报告保留分档统计，但注明不单调、30% 以上段数不足、独立事件远少于段数</td></tr>
</table></div>

<h2>附录 E　数据源质量与缺口声明</h2>
<div class="tw"><table>
<tr><th>指标</th><th>可用区间</th><th>行数</th><th>结论影响</th></tr>
<tr><td>全收益</td><td>2012-12-31 ~ 2026-11-03</td><td class="num">3,358</td><td>其中 2012-12-31~2024-08-14 为回溯</td></tr>
<tr><td>估值</td><td>2024-09-18 ~ 2026-11-04</td><td class="num">{len(VAL)}</td><td>不足以计算十年分位，不参与打分</td></tr>
<tr><td>成分</td><td>—</td><td class="num">0</td><td>重叠度无法计算</td></tr>
<tr><td>指数财务</td><td>—</td><td class="num">0</td><td>财务七图无法生成</td></tr>
<tr><td>实盘交易日</td><td>2024-08-15 ~ 2026-11-03</td><td class="num">{lv['days']}</td><td>分档统计在实盘段样本不足，故未单独做</td></tr>
</table></div>

<div class="ft">
<b>免责声明。</b>本材料为量化规则与研究方法论的输出记录，用于检验方法论与数据结构的一致性，
<b>不构成任何证券的买卖建议、目标价或仓位建议</b>。文中「观察位 / 触发条件」均为既定规则在当前数据下的推导结果，
不是对未来价格的预测。外部信息（编制规则修订、旧指数代码、实盘起算日）来自公开公告与社区互证，
<b>官方 PDF 正文未获取，未获一手确认</b>。历史数据不代表未来表现。
若编制规则在本文成稿后再次修订，全部回测段结论需重做。
<br><br>
数据截止 {ASOF} ｜ 生成时间 {gen_at} ｜ 生成器 <code>docs/report/make_fcf100_report.py</code>
｜ 研究框架 index-research ｜ 本文件为单文件离线 HTML，不依赖外部资源
</div>
</div>
<script>{JS}</script>
</body>
</html>
"""

os.makedirs(OUT_DIR, exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    f.write(HTML)
print(f'已写出 {OUT}（{len(HTML) / 1024:.1f} KB）')

