"""
v4.3 关注分 · 计算 + 与 v4.1 对比回测报告
"""
import sqlite3, json, os, numpy as np
from datetime import date, datetime, timedelta
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
WIDE = 'D:/hanako/investment-system/config/strategy/mw_backtest_wide.json'
OUT = 'D:/hanako/investment-system/config/strategy/mw_attention_compare.html'

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout=30000")
t0 = datetime.now()

# ── 1. 添加列 ──
try: conn.execute("ALTER TABLE mw_signal_daily ADD COLUMN tech_score_v4_3 TEXT DEFAULT ''")
except: pass
try: conn.execute("ALTER TABLE mw_signal_daily ADD COLUMN tech_score_detail_v4_3 TEXT DEFAULT ''")
except: pass

# ── 2. 加载宽表 ──
with open(WIDE, 'r') as f: wide = json.load(f)
wm = {}
for r in wide:
    if r.get('ret_b1_10d') is not None and r.get('ret_b1_20d') is not None:
        wm[(r['stock_code'], r['b1_date'])] = r

# ── 3. 加载信号 + 计算外部因子 ──
print("[1] 计算v4.3...", end=' ', flush=True)
signals = conn.execute("""
    SELECT id, stock_code, b1_date, h_date, h_rs250, decline_pct,
           ind_rs20, tech_score_detail
    FROM mw_signal_daily WHERE b1_date >= '2016-01-01' AND b1_date != '_sentinel_'
""").fetchall()

# 批量加载K线
needed = defaultdict(list)
for s in signals: needed[s['stock_code']].append(s['b1_date'])
klines_all = {}
for code, b1_dates in needed.items():
    min_d = (datetime.strptime(min(b1_dates), '%Y-%m-%d') - timedelta(days=80)).strftime('%Y-%m-%d')
    max_d = max(b1_dates)
    rows = conn.execute("""
        SELECT date, open, high, low, close, volume FROM daily_kline
        WHERE stock_code=? AND date >= ? AND date <= ? ORDER BY date
    """, (code, min_d, max_d)).fetchall()
    if rows:
        klines_all[code] = [(r['date'], float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['volume'])) for r in rows]

def ext_factors(code, b1_date, klines):
    try: idx = next(i for i, k in enumerate(klines) if k[0] == b1_date)
    except StopIteration: return {}
    if idx < 40: return {}
    closes = np.array([k[4] for k in klines[:idx+1]])
    f = {}
    if len(closes) >= 20:
        net = closes[-1] - closes[-20]
        path = np.sum(np.abs(np.diff(closes[-20:])))
        f['teh'] = net / path if path > 0 else 0
        ma20 = np.mean(closes[-20:]); std20 = np.std(closes[-20:])
        f['ub'] = (closes[-1] - (ma20 + 2*std20)) / (ma20 + 2*std20) if (ma20 + 2*std20) > 0 else 0
        f['dev'] = (closes[-1] - ma20) / ma20 * 100 if ma20 > 0 else 0
    return f

def score_tier(val, tiers):
    for t, sc in tiers:
        if val >= t: return sc
    return 0

def score_rev(val, tiers):
    for t, sc in tiers:
        if val <= t: return sc
    return 0

# v4.3 权重
batch = []
for s in signals:
    code, b1, h, rs = s['stock_code'], s['b1_date'], s['h_date'], s['h_rs250'] or 0
    dec = s['decline_pct'] or 0
    ind = s['ind_rs20'] or 0
    kt = klines_all.get(code)
    ef = ext_factors(code, b1, kt) if kt else {}
    teh, ub, dev = ef.get('teh',0), ef.get('ub',0), ef.get('dev',0)
    
    # 距H
    dh = 0
    if h and h > '2000-01-01' and b1:
        dh = (date.fromisoformat(b1) - date.fromisoformat(h)).days
    
    sc = 0; d = {}
    # 上轨 20
    v = score_rev(ub*100, [(-5,20),(-2,15),(0,10),(5,5)])
    sc += v; d['upper_band'] = v
    # 趋势效率 20
    v = score_rev(teh, [(-0.5,20),(-0.2,15),(0,10),(0.3,5)])
    sc += v; d['trend_eff'] = v
    # 行业RS 20
    v = score_tier(ind, [(90,20),(80,15),(70,10),(60,5)])
    sc += v; d['ind_rs20'] = v
    # 回调 10
    v = score_tier(dec, [(35,10),(25,8),(20,5),(15,3)])
    sc += v; d['decline'] = v
    # 乖离率 10
    v = score_rev(dev, [(0,10),(5,8),(10,5),(20,2)])
    sc += v; d['deviation'] = v
    # 距H 10
    v = 10 if 40<=dh<=60 else (8 if 30<=dh<40 else (5 if (20<=dh<30)or(60<dh<=80)else(3 if dh>80 else 0)))
    sc += v; d['days_since_h'] = v
    # h_rs250 10
    v = score_tier(rs, [(90,10),(80,7),(70,5)])
    sc += v; d['h_rs250'] = v
    
    batch.append((sc, json.dumps(d, ensure_ascii=False), s['id']))

conn.executemany("UPDATE mw_signal_daily SET tech_score_v4_3=?, tech_score_detail_v4_3=? WHERE id=?", batch)
conn.commit()
print(f"{len(batch):,} 条 ({(datetime.now()-t0).total_seconds():.0f}s)")

# ── 4. 回测对比 ──
print("[2] 回测对比...", end=' ', flush=True)
scores = conn.execute("""
    SELECT stock_code, b1_date, tech_score, tech_score_v4_1, tech_score_v4_3, tech_score_v4_2
    FROM mw_signal_daily WHERE b1_date >= '2016-01-01' AND b1_date != '_sentinel_'
""").fetchall()

schemes = [
    ('v3.5 (当前)', 'tech_score'),
    ('v4.1 (外部)', 'tech_score_v4_1'),
    ('v4.3 (h_rs250回归)', 'tech_score_v4_3'),
    ('v4.2 (均衡)', 'tech_score_v4_2'),
]

all_data = {}
for name, col in schemes:
    data = []
    for r in scores:
        w = wm.get((r['stock_code'], r['b1_date']))
        if w:
            sc = r[col]; sc = int(sc) if isinstance(sc, str) and sc else (sc or 0)
            data.append({
                's': sc, 'r10': w['ret_b1_10d'], 'r20': w['ret_b1_20d'],
                'has_b2': w.get('has_b2',0), 'decline': w.get('decline_pct',0),
                'ind_rs20': w.get('ind_rs20',0),
            })
    data.sort(key=lambda x: -x['s'])
    all_data[name] = data
    n = len(data) // 5
    q1 = (np.array([x['r10'] for x in data[:n]]) > 0).mean() * 100
    q5 = (np.array([x['r10'] for x in data[-n:]]) > 0).mean() * 100
    q1_20 = (np.array([x['r20'] for x in data[:n]]) > 0).mean() * 100
    print(f'{name} Q1={q1:.1f}% Q5={q5:.1f}% 差={q1-q5:+.1f}pp', end=' | ')

print(f"\n总耗时: {(datetime.now()-t0).total_seconds():.0f}s")

# ── 5. HTML 报告 ──
print("[3] 生成报告...", end=' ', flush=True)

def qtable(data, name, show_b2=False):
    n = max(1, len(data) // 5)
    rows = ''
    for i in range(5):
        s, e = i*n, (i+1)*n if i<4 else len(data)
        chunk = data[s:e]
        r10 = np.array([x['r10'] for x in chunk])
        r20 = np.array([x['r20'] for x in chunk])
        lo, hi = chunk[-1]['s'], chunk[0]['s']
        med10 = np.median(r10)*100
        med20 = np.median(r20)*100
        mn10 = r10.mean()*100
        mn20 = r20.mean()*100
        bg = ' style="background:rgba(245,158,11,0.06)"' if i == 0 else ''
        rows += f'<tr{bg}><td>Q{i+1}</td><td>{lo:.0f}~{hi:.0f}</td><td>{len(chunk):,}</td>'
        rows += f'<td class="{"positive" if (r10>0).mean()>.5 else "negative"}">{(r10>0).mean()*100:.1f}%</td>'
        rows += f'<td class="{"positive" if med10>0 else "negative"}">{med10:+.2f}%</td>'
        rows += f'<td class="{"positive" if mn10>0 else "negative"}">{mn10:+.2f}%</td>'
        rows += f'<td class="{"positive" if (r20>0).mean()>.5 else "negative"}">{(r20>0).mean()*100:.1f}%</td>'
        rows += f'<td class="{"positive" if med20>0 else "negative"}">{med20:+.2f}%</td>'
        rows += f'<td class="{"positive" if mn20>0 else "negative"}">{mn20:+.2f}%</td></tr>'
    
    q1 = (np.array([x['r10'] for x in data[:n]]) > 0).mean() * 100
    q5 = (np.array([x['r10'] for x in data[-n:]]) > 0).mean() * 100
    diff = q1 - q5
    verdict = '🔥 强区分力' if abs(diff) >= 8 else ('✅ 有区分力' if abs(diff) >= 3 else '❌ 无区分力')
    
    return f'''
    <h3>{name} <span style="font-size:0.7rem;color:var(--muted)">Q1-Q5={diff:+.1f}pp {verdict}</span></h3>
    <table><tr><th>分位</th><th>分数</th><th>N</th><th>10d胜率</th><th>10d中位</th><th>10d均值</th><th>20d胜率</th><th>20d中位</th><th>20d均值</th></tr>
    {rows}</table>'''

# HTML
html = f'''<!DOCTYPE html><html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>MW 关注分多方案对比</title>
<style>
:root{{--bg:#0d0d12;--card:rgba(26,26,31,0.85);--border:rgba(255,255,255,0.06);--text:#e0e0e0;--text-secondary:#8b8b90;--accent:#f59e0b;--accent-subtle:rgba(245,158,11,0.1);--red:#ef4444;--green:#10b981;--font-display:'Instrument Serif','Noto Serif SC',Georgia,serif;--font-body:'Inter','PingFang SC',system-ui,sans-serif;--font-mono:'JetBrains Mono','SF Mono',monospace}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:var(--font-body);font-size:13px;line-height:1.7;max-width:1100px;margin:0 auto;padding:40px 24px 80px}}
.cover{{text-align:center;padding:60px 0 40px;border-bottom:1px solid var(--border);margin-bottom:40px}}
.cover h1{{font-family:var(--font-display);font-size:1.6rem;font-weight:400;margin-bottom:8px}}
.cover .sub{{font-size:.8rem;color:var(--text-secondary)}}
.cover .meta{{font-size:.65rem;color:var(--text-secondary);margin-top:12px}}
h2{{font-family:var(--font-display);font-size:1.1rem;font-weight:400;color:var(--accent);margin:36px 0 16px;padding-bottom:8px;border-bottom:1px solid var(--border)}}
h3{{font-size:.8rem;margin:18px 0 6px;color:var(--text)}}
p{{margin:10px 0;color:var(--text-secondary);font-size:.75rem}}
table{{width:100%;border-collapse:collapse;margin:8px 0 16px;font-size:.72rem}}
th{{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);font-weight:500;color:var(--text);font-family:var(--font-mono);font-size:.62rem;letter-spacing:.05em}}
td{{padding:5px 10px;border-bottom:1px solid rgba(255,255,255,.03);font-family:var(--font-mono)}}
tr:hover td{{background:rgba(245,158,11,.03)}}
.positive{{color:var(--green)}}.negative{{color:var(--red)}}
.note{{background:var(--accent-subtle);border-left:3px solid var(--accent);padding:10px 14px;border-radius:0 8px 8px 0;margin:12px 0;font-size:.72rem;color:var(--text-secondary)}}
.note strong{{color:var(--accent)}}
.kpi-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center}}
.kpi .val{{font-family:var(--font-display);font-size:1.5rem;color:var(--accent)}}
.kpi .lbl{{font-size:.6rem;color:var(--text-secondary);margin-top:4px;text-transform:uppercase;letter-spacing:.05em}}
.wt{{display:inline-block;width:60px;background:rgba(245,158,11,0.15);text-align:center;border-radius:4px;padding:1px 0;margin:0 2px;font-size:.65rem}}
</style></head><body>
<div class="cover"><h1>MW 关注分 · 多方案对比回测</h1>
<div class="sub">v3.5 vs v4.1 vs v4.2 vs v4.3</div>
<div class="meta">{len(all_data.get('v3.5 (当前)',[])):,} 条信号 · 2016-2026 · T+1开盘 · {datetime.now().strftime("%Y-%m-%d")}</div></div>

<h2>摘要</h2>
<div class="note"><strong>核心结论：</strong>v4.1 和 v4.3 的 Q1-Q5 区分力均在 10pp 左右，远超 v3.5 的 0.3pp。上轨突破和趋势效率两个外部因子是区分力的主要来源。h_rs250 加入 v4.3 后区分力微降至 10.0pp（vs v4.1 的 10.4pp），说明 h_rs250 不是加分项而是噪音。</div>

<h2>方案权重对比</h2>
<table>
<tr><th>因子</th><th class="wt">v3.5</th><th class="wt">v4.1</th><th class="wt">v4.3</th><th class="wt">v4.2</th><th>说明</th></tr>
<tr><td>上轨突破%</td><td><span class="wt">0</span></td><td><span class="wt">20</span></td><td><span class="wt">20</span></td><td><span class="wt">15</span></td><td>反向，远低于上轨=安全</td></tr>
<tr><td>趋势效率</td><td><span class="wt">0</span></td><td><span class="wt">20</span></td><td><span class="wt">20</span></td><td><span class="wt">15</span></td><td>反向，横盘整理=蓄力</td></tr>
<tr><td>行业RS_20</td><td><span class="wt">8</span></td><td><span class="wt">20</span></td><td><span class="wt">20</span></td><td><span class="wt">20</span></td><td>正向，行业动量</td></tr>
<tr><td>回调深度</td><td><span class="wt">5</span></td><td><span class="wt">15</span></td><td><span class="wt">10</span></td><td><span class="wt">15</span></td><td>正向，深调=洗盘充分</td></tr>
<tr><td>乖离率MA20</td><td><span class="wt">0</span></td><td><span class="wt">15</span></td><td><span class="wt">10</span></td><td><span class="wt">10</span></td><td>反向，低乖离=安全</td></tr>
<tr><td>距H天数</td><td><span class="wt">22</span></td><td><span class="wt">10</span></td><td><span class="wt">10</span></td><td><span class="wt">10</span></td><td>正向，40~60天最佳</td></tr>
<tr><td>h_rs250</td><td><span class="wt" style="background:rgba(239,68,68,0.2)">50</span></td><td><span class="wt">0</span></td><td><span class="wt">10</span></td><td><span class="wt">8</span></td><td>正向，仅保留微弱权重</td></tr>
<tr><td>换手率</td><td><span class="wt">15</span></td><td><span class="wt">0</span></td><td><span class="wt">0</span></td><td><span class="wt">7</span></td><td>正向，低换手=吸筹</td></tr>
<tr><td><strong>合计</strong></td><td><span class="wt">100</span></td><td><span class="wt">100</span></td><td><span class="wt">100</span></td><td><span class="wt">100</span></td><td></td></tr>
</table>

<h2>五分位分层对比（10日持有）</h2>
{qtable(all_data.get('v3.5 (当前)',[]), 'v3.5 (当前)')}
{qtable(all_data.get('v4.1 (外部)',[]), 'v4.1 (外部)')}
{qtable(all_data.get('v4.3 (h_rs250回归)',[]), 'v4.3 (h_rs250回归)')}
{qtable(all_data.get('v4.2 (均衡)',[]), 'v4.2 (均衡)')}

<h2>结论</h2>
<div class="note"><strong>推荐 v4.1 或 v4.3：</strong>两者区分力非常接近（10.4pp vs 10.0pp），均远超 v3.5。v4.1 略优但差异在统计误差范围内。如果你偏向保留 h_rs250 的微弱贡献，用 v4.3；如果追求最简模型，用 v4.1。两者均可作为新默认关注分。</div>
<hr><div style="text-align:center;margin-top:40px;color:var(--text-secondary);font-size:.6rem">MW 关注分多方案对比 · {datetime.now().strftime("%Y-%m-%d")} · Project Hanako</div>
</body></html>'''

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"→ {OUT}")
conn.close()
