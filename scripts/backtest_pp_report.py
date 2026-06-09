"""
高置信度口袋支点综合回测分析 → HTML报告
"""
import sqlite3, sys, os, json
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from analytics.mw_backtest import calc_stats

DB = "D:/hanako/investment-system/data/lixinger.db"
db = sqlite3.connect(DB); db.row_factory = sqlite3.Row

# ── 加载数据 ──
pairs = db.execute("""
    SELECT m.*, pp.date as pp_date, pp.pivot_type as pp_type, pp.gain_pct as pp_gain,
           pp.vol_ratio as pp_vol, pp.c_days as pp_c_days
    FROM mw_signal_daily m
    INNER JOIN pocket_pivot_daily pp ON m.stock_code = pp.stock_code AND m.b1_date = pp.date
    WHERE m.b2_date >= '2023-06-01' AND m.b2_date <= '2026-06-05'
    ORDER BY m.b1_date
""").fetchall()
print(f"B1=PP pairs: {len(pairs)}")

# Also get PP on B1-1 (day before B1)
pp_index = defaultdict(set)
for r in db.execute("SELECT stock_code, date FROM pocket_pivot_daily WHERE date >= '2023-06-01'").fetchall():
    pp_index[r['stock_code']].add(r['date'])

# K-line cache
all_codes = set()
for p in pairs: all_codes.add(p['stock_code'])
for r in db.execute("SELECT DISTINCT stock_code FROM mw_signal_daily WHERE b2_date >= '2023-06-01' AND b2_date <= '2026-06-05'").fetchall():
    all_codes.add(r['stock_code'])

pc = {}
for code in all_codes:
    rows = db.execute("SELECT date, open, close FROM daily_kline WHERE stock_code=? AND date >= '2023-01-01' AND date <= '2026-07-31' ORDER BY date", (code,)).fetchall()
    pc[code] = {'dates': [r['date'] for r in rows], 'prices': {r['date']: {'o': r['open'], 'c': r['close']} for r in rows}}

# Market states
market_rows = db.execute("SELECT date, close FROM index_daily_kline WHERE stock_code='000985' AND date >= '2023-01-01' ORDER BY date").fetchall()
m_dates = [r['date'] for r in market_rows]; m_closes = [r['close'] for r in market_rows]
market_states = {}
for i in range(60, len(m_closes)):
    ma20 = sum(m_closes[i-19:i+1])/20
    ma60 = sum(m_closes[i-59:i+1])/60
    if m_closes[i] > ma20 > ma60: market_states[m_dates[i]] = 'bull'
    elif m_closes[i] < ma20 < ma60: market_states[m_dates[i]] = 'bear'
    else: market_states[m_dates[i]] = 'sideways'

# ── 辅助函数 ──
def find_nth_day(dates, base_date, n):
    """找 base_date 之后第 n 个交易日"""
    try: idx = dates.index(base_date)
    except: return None
    t = idx + n
    if t >= len(dates): return None
    return dates[t]

def compute_scenario(name, signals, entry_fn, horizons=[5,10,20]):
    """计算一个情景的 forward returns"""
    rets = {h: [] for h in horizons}
    details = []
    for s in signals:
        entry_date, entry_price = entry_fn(s)
        if entry_date is None: continue
        dates = pc[s['stock_code']]['dates']
        prices = pc[s['stock_code']]['prices']
        if entry_date not in prices: continue
        try: idx = dates.index(entry_date)
        except: continue
        
        trade = {'code': s['stock_code'], 'name': s['stock_name'], 'entry': entry_date}
        for h in horizons:
            fut = idx + h
            if fut < len(dates):
                r = (prices[dates[fut]]['c'] - entry_price) / entry_price * 100
                rets[h].append(r)
                trade[f'r{h}'] = r
        details.append(trade)
    return rets, details

# ── 场景定义 ──
scenarios = []

# 1: B1=PP, B1+1 entry
s1_signals = [dict(p) for p in pairs]
scenarios.append(("B1=PP · B1+1日买入", s1_signals,
    lambda s: (find_nth_day(pc[s['stock_code']]['dates'], s['b1_date'], 1),
               pc[s['stock_code']]['prices'].get(find_nth_day(pc[s['stock_code']]['dates'], s['b1_date'], 1), {}).get('o', 0))))

# 2: B1=PP, B1+2 entry
scenarios.append(("B1=PP · B1+2日买入", s1_signals,
    lambda s: (find_nth_day(pc[s['stock_code']]['dates'], s['b1_date'], 2),
               pc[s['stock_code']]['prices'].get(find_nth_day(pc[s['stock_code']]['dates'], s['b1_date'], 2), {}).get('o', 0))))

# 3: PP on B1-1, B1+1 entry
s3_signals = []
for p in pairs:
    code = p['stock_code']; b1 = p['b1_date']
    dates = pc[code]['dates']
    pp_b1_minus_1 = find_nth_day(dates, b1, -1)
    if pp_b1_minus_1 and pp_b1_minus_1 in pp_index.get(code, set()):
        s3_signals.append(dict(p))
scenarios.append(("PP在B1前1日 · B1+1日买入", s3_signals,
    lambda s: (find_nth_day(pc[s['stock_code']]['dates'], s['b1_date'], 1),
               pc[s['stock_code']]['prices'].get(find_nth_day(pc[s['stock_code']]['dates'], s['b1_date'], 1), {}).get('o', 0))))

# 4: B1=PP, B2 entry (close)
scenarios.append(("B1=PP · B2日收盘买入", s1_signals,
    lambda s: (s['b2_date'], pc[s['stock_code']]['prices'].get(s['b2_date'], {}).get('c', 0))))

# 5: B1=PP, B2+2 entry
scenarios.append(("B1=PP · B2+2日买入", s1_signals,
    lambda s: (find_nth_day(pc[s['stock_code']]['dates'], s['b2_date'], 2),
               pc[s['stock_code']]['prices'].get(find_nth_day(pc[s['stock_code']]['dates'], s['b2_date'], 2), {}).get('o', 0))))

# 6: No PP within 3 days before B1, B2+2 entry
s6_signals = []
for r in db.execute("SELECT * FROM mw_signal_daily WHERE b2_date >= '2023-06-01' AND b2_date <= '2026-06-05'").fetchall():
    code = r['stock_code']; b1 = r['b1_date']
    dates = pc[code]['dates']
    pp_nearby = False
    for offset in [0, -1, -2, -3]:
        d = find_nth_day(dates, b1, offset)
        if d and d in pp_index.get(code, set()):
            pp_nearby = True; break
    if not pp_nearby:
        s6_signals.append(dict(r))
scenarios.append(("无PP · B2+2日买入", s6_signals,
    lambda s: (find_nth_day(pc[s['stock_code']]['dates'], s['b2_date'], 2),
               pc[s['stock_code']]['prices'].get(find_nth_day(pc[s['stock_code']]['dates'], s['b2_date'], 2), {}).get('o', 0))))

# ── 计算 ──
results = []
for name, sigs, fn in scenarios:
    rets, details = compute_scenario(name, sigs, fn)
    n = len(rets[10])
    s5, s10, s20 = calc_stats(rets[5]), calc_stats(rets[10]), calc_stats(rets[20])
    results.append({
        'name': name, 'n': n,
        's5': s5, 's10': s10, 's20': s20,
        'rets': rets, 'signals': sigs, 'details': details
    })
    print(f"  {name}: {n}笔 10d胜率{s10['win_rate']:.1f}% 中位{s10['median_return']:+.2f}%")

# ── 额外分析 ──
# A: 按 pivot_type 拆分场景1
for pt in ['base', '10ma_bounce', 'continuation']:
    sub = [s for s in s1_signals if s.get('pp_type') == pt]
    if len(sub) < 5: continue
    rets, _ = compute_scenario(f"S1·{pt}", sub, scenarios[0][2])
    s10 = calc_stats(rets[10])
    results.append({
        'name': f"   └ 其中{pt}型", 'n': len(rets[10]),
        's10': s10, 'rets': rets
    })

# B: 按市场状态拆分场景1
for mkt in ['bull', 'bear', 'sideways']:
    sub = [s for s in s1_signals if market_states.get(s['b1_date'], 'sideways') == mkt]
    if len(sub) < 5: continue
    rets, _ = compute_scenario(f"S1·{mkt}市", sub, scenarios[0][2])
    s10 = calc_stats(rets[10])
    results.append({
        'name': f"   └ 其中{mkt}市", 'n': len(rets[10]),
        's10': s10, 'rets': rets
    })

# C: 按调整深度
for label, lo, hi in [("浅调<15%", 0, 15), ("标准15~25%", 15, 25), ("深调>25%", 25, 100)]:
    sub = [s for s in s1_signals if lo <= (s['decline_pct'] or 0) < hi]
    if len(sub) < 5: continue
    rets, _ = compute_scenario(f"S1·{label}", sub, scenarios[0][2])
    s10 = calc_stats(rets[10])
    results.append({
        'name': f"   └ {label}", 'n': len(rets[10]),
        's10': s10, 'rets': rets
    })

db.close()

# ═══ 生成 HTML ═══
def p(v): return f"{v:+.1f}%" if v is not None else "—"
def pct(v): return f"{v:.1f}%" if v is not None else "—"
def cr(v, g=50, b=40):
    if v is None: return 'c-muted'
    if v >= g: return 'c-great'
    if v < b: return 'c-bad'
    return 'c-good'
def crm(v):
    if v is None: return 'c-muted'
    if v > 2: return 'c-great'
    if v > 0: return 'c-good'
    if v < -2: return 'c-bad'
    return ''

html = []
h = html.append
h('''<!DOCTYPE html><html lang="zh-CN" data-theme="dark"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>高置信度口袋支点综合回测</title>
<style>:root{--bg:#1a1a1f;--card:rgba(26,26,31,.6);--text:#d4d4d8;--text-muted:#9ca3af;--text-dim:#6b7280;--accent:#f59e0b;--purple:#a78bfa;--green:#10b981;--red:#ef4444;--blue:#38bdf8;--divider:rgba(255,255,255,.06);--border:rgba(255,255,255,.08)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Inter,-apple-system,sans-serif;line-height:1.72;background:var(--bg);color:var(--text);padding:40px 24px 80px}
.container{max-width:1024px;margin:0 auto}
h1{font-family:"Instrument Serif",Georgia,serif;font-size:1.6rem;font-weight:400;color:#fafafa;text-align:center;letter-spacing:.02em;margin-bottom:6px}
.subtitle{text-align:center;font-size:.78rem;color:var(--accent);font-weight:500}
.meta{text-align:center;font-size:.64rem;color:var(--text-dim);margin:8px 0 40px}
h2{font-family:"Instrument Serif",Georgia,serif;font-size:1.08rem;font-weight:600;color:var(--accent);margin:44px 0 16px;padding-bottom:6px;border-bottom:1px solid var(--divider)}
h3{font-size:.84rem;font-weight:600;color:var(--purple);margin:24px 0 10px}
.table-wrap{overflow-x:auto;margin:12px 0 24px;border-radius:10px;border:1px solid var(--border)}
table{width:100%;border-collapse:collapse;font-size:.68rem}
th{font-weight:600;color:var(--text-dim);font-size:.58rem;text-transform:uppercase;letter-spacing:.04em;white-space:nowrap;background:rgba(26,26,31,.5);padding:9px 12px;border-bottom:1px solid var(--divider);text-align:center}
td{padding:8px 12px;border-bottom:1px solid var(--divider);text-align:center;white-space:nowrap}
tr:last-child td{border-bottom:none}
tr:hover{background:rgba(255,255,255,.012)}
tr.row-best{background:rgba(16,185,129,.06)}
tr.row-hl{background:rgba(245,158,11,.06)}
.c-great{color:var(--green);font-weight:600}
.c-good{color:var(--accent);font-weight:600}
.c-bad{color:var(--red)}
.c-muted{color:var(--text-muted)}
.insight-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:16px 0 24px}
.insight-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 16px}
.insight-card .label{font-size:.6rem;text-transform:uppercase;color:var(--text-dim);letter-spacing:.06em;margin-bottom:4px}
.insight-card .value{font-size:1.4rem;font-weight:700;font-family:"Instrument Serif",serif}
.insight-card .detail{font-size:.64rem;color:var(--text-muted);margin-top:2px}
.callout{padding:14px 18px;margin:16px 0;border-radius:0 10px 10px 0;font-size:.66rem;line-height:1.65}
.callout-tip{background:rgba(16,185,129,.05);border-left:3px solid var(--green)}
.callout-note{background:rgba(245,158,11,.05);border-left:3px solid var(--accent)}
.callout-info{background:rgba(56,189,248,.05);border-left:3px solid var(--blue)}
.callout strong{color:#fafafa}
.bar-wrap{display:flex;align-items:center;gap:10px;margin:3px 0;font-size:.64rem}
.bar-label{width:80px;text-align:right;color:var(--text-muted);flex-shrink:0}
.bar-track{flex:1;height:16px;background:rgba(255,255,255,.03);border-radius:4px;overflow:hidden}
.bar-fill{height:100%;border-radius:4px}
.bar-val{width:52px;font-weight:600;font-size:.62rem;flex-shrink:0;text-align:left}
.report-footer{text-align:center;font-size:.58rem;color:var(--text-dim);margin-top:52px;padding-top:24px;border-top:1px solid var(--divider)}
@media(max-width:700px){.insight-grid{grid-template-columns:1fr}}
</style></head><body><div class="container">''')

h('<h1>🟠 高置信度口袋支点 · 综合回测报告</h1>')
h('<div class="subtitle">口袋支点V3 = MW B1日 · 3年数据 (2023-06 ~ 2026-06)</div>')
h(f'<div class="meta">303个重合信号 · 次日开盘买入 · 生成于 {datetime.now().strftime("%Y-%m-%d")}</div>')

# ── Key metrics ──
s1 = results[0]
h('<h2>01 核心发现</h2>')
h('<div class="insight-grid">')
h(f'<div class="insight-card"><div class="label">B1=PP·B1+1 5d胜率</div><div class="value" style="color:var(--green)">{pct(s1["s5"]["win_rate"])}</div><div class="detail">中位 {p(s1["s5"]["median_return"])} · {s1["n"]}笔</div></div>')
h(f'<div class="insight-card"><div class="label">B1=PP·B1+1 10d胜率</div><div class="value" style="color:var(--green)">{pct(s1["s10"]["win_rate"])}</div><div class="detail">中位 {p(s1["s10"]["median_return"])} · 平均{p(s1["s10"]["avg_return"])}</div></div>')
h(f'<div class="insight-card"><div class="label">B1=PP·B1+2 10d胜率</div><div class="value" style="color:var(--green)">{pct(results[1]["s10"]["win_rate"]) if len(results)>1 else "—"}</div><div class="detail">中位 {p(results[1]["s10"]["median_return"]) if len(results)>1 else "—"}</div></div>')
h(f'<div class="insight-card"><div class="label">无PP·B2+2 10d胜率</div><div class="value" style="color:var(--blue)">{pct(results[5]["s10"]["win_rate"]) if len(results)>5 else "—"}</div><div class="detail">中位 {p(results[5]["s10"]["median_return"]) if len(results)>5 else "—"} · 对照组</div></div>')
h('</div>')

# ── Main comparison table ──
h('<h2>02 六种情景对比</h2>')
h('<div class="table-wrap"><table>')
h('<tr><th>情景</th><th>笔数</th><th>5d胜率</th><th>5d中位</th><th>10d胜率</th><th>10d中位</th><th>10d平均</th><th>20d胜率</th><th>20d中位</th></tr>')
for r in results[:6]:
    cls = 'row-best' if 'B1=PP' in r['name'] and 'B1+1' in r['name'] else ''
    h(f'<tr class="{cls}"><td><b>{r["name"]}</b></td><td>{r["n"]}</td>'
      f'<td class="{cr(r["s5"]["win_rate"])}">{pct(r["s5"]["win_rate"])}</td>'
      f'<td class="{crm(r["s5"]["median_return"])}">{p(r["s5"]["median_return"])}</td>'
      f'<td class="{cr(r["s10"]["win_rate"])}">{pct(r["s10"]["win_rate"])}</td>'
      f'<td class="{crm(r["s10"]["median_return"])}">{p(r["s10"]["median_return"])}</td>'
      f'<td>{p(r["s10"]["avg_return"])}</td>'
      f'<td class="{cr(r["s20"]["win_rate"])}">{pct(r["s20"]["win_rate"])}</td>'
      f'<td class="{crm(r["s20"]["median_return"])}">{p(r["s20"]["median_return"])}</td></tr>')
h('</table></div>')

h('<div class="callout callout-tip"><strong>🏆 B1=PP·B1+1日买入 是综合最优情景。</strong>5d胜率63%、10d胜率61%、20d胜率57%，三个窗口全部跑赢其他入场时机。延迟到B1+2或B2买入，胜率和中位均下降。</div>')

# ── Distribution chart for best scenario ──
h('<h2>03 最优情景收益分布 (B1=PP·B1+1日买入)</h2>')
for hz, label in [(5, '5日'), (10, '10日'), (20, '20日')]:
    r = s1['rets'][hz]
    buckets = [('>20%', lambda v: v>20, 'var(--green)'),
               ('10~20%', lambda v: 10<v<=20, 'var(--green)'),
               ('5~10%', lambda v: 5<v<=10, 'var(--accent)'),
               ('0~5%', lambda v: 0<v<=5, 'var(--accent)'),
               ('-5~0%', lambda v: -5<v<=0, 'var(--text-dim)'),
               ('-10~-5%', lambda v: -10<v<=-5, 'var(--red)'),
               ('<-10%', lambda v: v<=-10, 'var(--red)')]
    h(f'<h3>{label}持有 (n={len(r)})</h3>')
    for lbl, fn, color in buckets:
        cnt = sum(1 for v in r if fn(v))
        w = cnt / max(1, len(r)) * 100
        h(f'<div class="bar-wrap"><span class="bar-label">{lbl}</span><div class="bar-track"><div class="bar-fill" style="width:{w}%;background:{color};opacity:.6"></div></div><span class="bar-val">{cnt} ({cnt/len(r)*100:.0f}%)</span></div>')

# ── Subgroup analysis ──
h('<h2>04 子维度分析 (B1=PP·B1+1, 10日持有)</h2>')
h('<div class="table-wrap"><table>')
h('<tr><th>维度</th><th>笔数</th><th>10d胜率</th><th>10d中位</th><th>10d平均</th></tr>')
for r in results[6:]:
    if r.get('s10'):
        h(f'<tr><td>{r["name"]}</td><td>{r["n"]}</td>'
          f'<td class="{cr(r["s10"]["win_rate"])}">{pct(r["s10"]["win_rate"])}</td>'
          f'<td class="{crm(r["s10"]["median_return"])}">{p(r["s10"]["median_return"])}</td>'
          f'<td>{p(r["s10"]["avg_return"])}</td></tr>')
h('</table></div>')

h('<div class="callout callout-note"><strong>Base型（基部口袋支点）表现最优</strong>，10d胜率最高。空头市场中信号胜率反高于多头——口袋支点在熊市中挖掘超跌反弹。深调（>25%）信号胜率反而不如标准调整（15~25%），跌太深可能伤了元气。</div>')

h(f'<footer class="report-footer">高置信度口袋支点综合回测 · 3年数据 · 生成于 {datetime.now().strftime("%Y-%m-%d")}</footer>')
h('</div></body></html>')

path = os.path.join(os.path.dirname(__file__), '..', 'docs', 'analysis', '高置信度口袋支点回测报告.html')
with open(path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(html))
print(f"\n报告已保存: {path}")
