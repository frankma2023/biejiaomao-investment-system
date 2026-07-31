"""
v4.4 IC校准权重 · 计算 + v4.1/v4.3/v4.4 三方对比
"""
import sqlite3, json, numpy as np, os
from datetime import date, datetime, timedelta
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
WIDE = 'D:/hanako/investment-system/config/strategy/mw_backtest_wide.json'
OUT = 'D:/hanako/investment-system/config/strategy/mw_attention_v44.html'

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout=30000")
t0 = datetime.now()

# 加列
for col in ['tech_score_v4_4', 'tech_score_detail_v4_4']:
    try: conn.execute(f"ALTER TABLE mw_signal_daily ADD COLUMN {col} TEXT DEFAULT ''")
    except: pass

# 加载宽表
with open(WIDE, 'r') as f: wide = json.load(f)
wm = {}
for r in wide:
    if r.get('ret_b1_10d') is not None and r.get('ret_b1_20d') is not None:
        wm[(r['stock_code'], r['b1_date'])] = r

# 加载K线
print("[1] 加载...", end=' ', flush=True)
signals = conn.execute("""
    SELECT id, stock_code, b1_date, h_date, h_rs250, decline_pct, ind_rs20
    FROM mw_signal_daily WHERE b1_date >= '2016-01-01' AND b1_date != '_sentinel_'
""").fetchall()

needed = defaultdict(list)
for s in signals: needed[s['stock_code']].append(s['b1_date'])
klines_all = {}
for code, b1_dates in needed.items():
    min_d = (datetime.strptime(min(b1_dates), '%Y-%m-%d') - timedelta(days=80)).strftime('%Y-%m-%d')
    rows = conn.execute("SELECT date, open, high, low, close, volume FROM daily_kline WHERE stock_code=? AND date>=? AND date<=? ORDER BY date", (code, min_d, max(b1_dates))).fetchall()
    if rows:
        klines_all[code] = [(r['date'], float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['volume'])) for r in rows]

def ef(code, b1, kt):
    try: idx = next(i for i, k in enumerate(kt) if k[0] == b1)
    except StopIteration: return {}
    if idx < 40: return {}
    closes = np.array([k[4] for k in kt[:idx+1]])
    f = {}
    if len(closes) >= 20:
        net = closes[-1] - closes[-20]
        path = np.sum(np.abs(np.diff(closes[-20:])))
        f['teh'] = net / path if path > 0 else 0
        ma20 = np.mean(closes[-20:]); std20 = np.std(closes[-20:])
        f['ub'] = (closes[-1] - (ma20 + 2*std20)) / (ma20 + 2*std20) if (ma20 + 2*std20) > 0 else 0
        f['dev'] = (closes[-1] - ma20) / ma20 * 100 if ma20 > 0 else 0
    return f

def stier(val, tiers):
    for t, sc in tiers:
        if val >= t: return sc
    return 0

def srev(val, tiers):
    for t, sc in tiers:
        if val <= t: return sc
    return 0

# ── v4.4 权重（IC校准）──
# IC: ub=-0.097 dev=-0.082 teh=-0.075 dh=+0.060 dec=+0.043 ind=+0.008 rs=-0.015
# 权重按 |IC| 比例: 24.5/20.8/19.0/15.2/10.9/2.0/3.8 → 25/20/20/15/10/5/5
print("计算v4.4...", end=' ', flush=True)
batch = []
for s in signals:
    code, b1, h, rs = s['stock_code'], s['b1_date'], s['h_date'], s['h_rs250'] or 0
    dec, ind = s['decline_pct'] or 0, s['ind_rs20'] or 0
    kt = klines_all.get(code)
    e = ef(code, b1, kt) if kt else {}
    teh, ub, dev = e.get('teh',0), e.get('ub',0), e.get('dev',0)
    dh = 0
    if h and h > '2000-01-01' and b1:
        dh = (date.fromisoformat(b1) - date.fromisoformat(h)).days
    
    sc = 0; d = {}
    # 上轨突破 25 (反向)
    v = srev(ub*100, [(-5,25),(-2,18),(0,12),(5,6)])
    sc += v; d['upper_band'] = v
    # 乖离率 20 (反向)
    v = srev(dev, [(0,20),(5,15),(10,10),(20,5)])
    sc += v; d['deviation'] = v
    # 趋势效率 20 (反向)
    v = srev(teh, [(-0.5,20),(-0.2,15),(0,10),(0.3,5)])
    sc += v; d['trend_eff'] = v
    # 距H天数 15 (正向)
    v = 15 if 40<=dh<=60 else (12 if 30<=dh<40 else (8 if (20<=dh<30)or(60<dh<=80)else(5 if dh>80 else 0)))
    sc += v; d['days_since_h'] = v
    # 回调深度 10 (正向)
    v = stier(dec, [(35,10),(25,8),(20,5),(15,3)])
    sc += v; d['decline'] = v
    # 行业RS 5 (正向)
    v = stier(ind, [(90,5),(80,4),(70,3),(60,2)])
    sc += v; d['ind_rs20'] = v
    # h_rs250 5 (正向)
    v = stier(rs, [(90,5),(80,3),(70,2)])
    sc += v; d['h_rs250'] = v
    
    batch.append((sc, json.dumps(d, ensure_ascii=False), s['id']))

conn.executemany("UPDATE mw_signal_daily SET tech_score_v4_4=?, tech_score_detail_v4_4=? WHERE id=?", batch)
conn.commit()
print(f"{len(batch):,} 条")

# ── 回测对比 ──
print("[2] 对比...", end=' ', flush=True)
scores = conn.execute("""
    SELECT stock_code, b1_date, tech_score_v4_1, tech_score_v4_3, tech_score_v4_4
    FROM mw_signal_daily WHERE b1_date >= '2016-01-01' AND b1_date != '_sentinel_'
""").fetchall()

schemes = [
    ('v4.1 (外部)', 'tech_score_v4_1'),
    ('v4.3 (h_rs250)', 'tech_score_v4_3'),
    ('v4.4 (IC校准)', 'tech_score_v4_4'),
]

all_data = {}
for name, col in schemes:
    data = []
    for r in scores:
        w = wm.get((r['stock_code'], r['b1_date']))
        if w:
            sc = r[col]; sc = int(sc) if isinstance(sc, str) and sc else (sc or 0)
            data.append({'s': sc, 'r10': w['ret_b1_10d'], 'r20': w['ret_b1_20d']})
    data.sort(key=lambda x: -x['s'])
    all_data[name] = data
    n = max(1, len(data)//5)
    q1 = (np.array([x['r10'] for x in data[:n]])>0).mean()*100
    q5 = (np.array([x['r10'] for x in data[-n:]])>0).mean()*100
    q1_20 = (np.array([x['r20'] for x in data[:n]])>0).mean()*100
    q5_20 = (np.array([x['r20'] for x in data[-n:]])>0).mean()*100
    print(f'{name}: Q1={q1:.1f}% Q5={q5:.1f}% 差10d={q1-q5:+.1f}pp 差20d={q1_20-q5_20:+.1f}pp | ', end='')

# ── HTML ──
print("\n[3] 报告...", end=' ', flush=True)

# 10分位分层
fine_data = [(x['s'], x['r10'], x['r20']) for x in all_data.get('v4.4 (IC校准)', [])]
fine_rows = ''
for lo in range(0, 100, 10):
    hi = lo + 9 if lo < 90 else 100
    chunk = [(s, r10, r20) for s, r10, r20 in fine_data if lo <= s <= hi]
    if not chunk: continue
    r10 = np.array([x[1] for x in chunk])
    r20 = np.array([x[2] for x in chunk])
    pct = len(chunk) / len(fine_data) * 100
    cls = ' style="background:rgba(16,185,129,0.06)"' if (r10>0).mean() > 0.5 else ''
    fine_rows += f'<tr{cls}><td>{lo}~{hi}</td><td>{len(chunk):,}</td><td>{pct:.1f}%</td>'
    fine_rows += f'<td class="{"positive" if (r10>0).mean()>.5 else "negative"}">{(r10>0).mean()*100:.1f}%</td>'
    fine_rows += f'<td class="{"positive" if np.median(r10)>0 else "negative"}">{np.median(r10)*100:+.2f}%</td>'
    fine_rows += f'<td class="{"positive" if (r20>0).mean()>.5 else "negative"}">{(r20>0).mean()*100:.1f}%</td>'
    fine_rows += f'<td class="{"positive" if np.median(r20)>0 else "negative"}">{np.median(r20)*100:+.2f}%</td></tr>'

def qtable(data, name):
    n = max(1, len(data)//5)
    rows = ''
    for i in range(5):
        s, e = i*n, (i+1)*n if i<4 else len(data)
        ck = data[s:e]
        r10 = np.array([x['r10'] for x in ck])
        r20 = np.array([x['r20'] for x in ck])
        lo, hi = ck[-1]['s'], ck[0]['s']
        bg = ' style="background:rgba(245,158,11,0.06)"' if i==0 else ''
        rows += f'<tr{bg}><td>Q{i+1}</td><td>{lo:.0f}~{hi:.0f}</td><td>{len(ck):,}</td>'
        rows += f'<td class="{"positive" if (r10>0).mean()>.5 else "negative"}">{(r10>0).mean()*100:.1f}%</td>'
        rows += f'<td class="{"positive" if np.median(r10)>0 else "negative"}">{np.median(r10)*100:+.2f}%</td>'
        rows += f'<td class="{"positive" if r10.mean()>0 else "negative"}">{r10.mean()*100:+.2f}%</td>'
        rows += f'<td class="{"positive" if (r20>0).mean()>.5 else "negative"}">{(r20>0).mean()*100:.1f}%</td>'
        rows += f'<td class="{"positive" if np.median(r20)>0 else "negative"}">{np.median(r20)*100:+.2f}%</td></tr>'
    q1 = (np.array([x['r10'] for x in data[:n]])>0).mean()*100
    q5 = (np.array([x['r10'] for x in data[-n:]])>0).mean()*100
    diff = q1 - q5
    v = '🔥 强' if abs(diff)>=8 else ('✅' if abs(diff)>=3 else '❌')
    return f'<h3>{name} <span style="font-size:0.7rem;color:var(--muted)">Q1-Q5={diff:+.1f}pp {v}</span></h3><table><tr><th>分位</th><th>分数</th><th>N</th><th>10d胜率</th><th>10d中位</th><th>10d均值</th><th>20d胜率</th><th>20d中位</th></tr>{rows}</table>'

html = f'''<!DOCTYPE html><html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>MW 关注分 v4.4 IC校准 · 三方对比</title>
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
table{{width:100%;border-collapse:collapse;margin:8px 0 16px;font-size:.72rem}}
th{{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);font-weight:500;color:var(--text);font-family:var(--font-mono);font-size:.62rem;letter-spacing:.05em}}
td{{padding:5px 10px;border-bottom:1px solid rgba(255,255,255,.03);font-family:var(--font-mono)}}
tr:hover td{{background:rgba(245,158,11,.03)}}
.positive{{color:var(--green)}}.negative{{color:var(--red)}}
.note{{background:var(--accent-subtle);border-left:3px solid var(--accent);padding:10px 14px;border-radius:0 8px 8px 0;margin:12px 0;font-size:.72rem;color:var(--text-secondary)}}
.note strong{{color:var(--accent)}}
.wt{{display:inline-block;width:50px;background:rgba(245,158,11,0.12);text-align:center;border-radius:4px;padding:1px 0;margin:0 2px;font-size:.62rem}}
.hl{{background:rgba(245,158,11,0.06)}}
</style></head><body>
<div class="cover"><h1>MW 关注分 · v4.4 IC校准 vs v4.1/v4.3</h1>
<div class="sub">基于真实 Spearman IC 重新标定权重</div>
<div class="meta">{len(all_data.get('v4.1 (外部)',[])):,} 条信号 · 2016-2026 · {datetime.now().strftime("%Y-%m-%d")}</div></div>

<h2>权重对比</h2>
<table>
<tr><th>因子</th><th>IC(10d)</th><th class="wt">v4.1</th><th class="wt">v4.3</th><th class="wt">v4.4</th><th>v4.4 评分规则</th></tr>
<tr><td>上轨突破%</td><td style="color:var(--green)">-0.097</td><td class="wt">20</td><td class="wt">20</td><td class="wt" style="background:rgba(245,158,11,0.2)">25</td><td>反向: <-5%=25, -5~-2%=18, -2~0%=12, 0~5%=6</td></tr>
<tr><td>乖离率MA20</td><td style="color:var(--green)">-0.082</td><td class="wt">15</td><td class="wt">10</td><td class="wt" style="background:rgba(245,158,11,0.2)">20</td><td>反向: <0%=20, 0~5%=15, 5~10%=10, >10%=5</td></tr>
<tr><td>趋势效率</td><td style="color:var(--green)">-0.075</td><td class="wt">20</td><td class="wt">20</td><td class="wt">20</td><td>反向: <-0.5=20, -0.5~-0.2=15, -0.2~0=10, 0~0.3=5</td></tr>
<tr><td>距H天数</td><td style="color:var(--green)">+0.060</td><td class="wt">10</td><td class="wt">10</td><td class="wt" style="background:rgba(245,158,11,0.2)">15</td><td>正向: 40~60=15, 30~40=12, 20~30或60~80=8, >80=5</td></tr>
<tr><td>回调深度</td><td>+0.043</td><td class="wt">15</td><td class="wt">10</td><td class="wt">10</td><td>正向: >35%=10, 25~35%=8, 20~25%=5, 15~20%=3</td></tr>
<tr><td>行业RS_20</td><td style="color:var(--red)">+0.008</td><td class="wt">20</td><td class="wt">20</td><td class="wt" style="background:rgba(239,68,68,0.15)">5</td><td>正向: ≥90=5, ≥80=4, ≥70=3, ≥60=2</td></tr>
<tr><td>h_rs250</td><td style="color:var(--red)">-0.015</td><td class="wt">0</td><td class="wt">10</td><td class="wt" style="background:rgba(239,68,68,0.15)">5</td><td>正向: ≥90=5, ≥80=3, ≥70=2</td></tr>
<tr><td><strong>合计</strong></td><td></td><td class="wt">100</td><td class="wt">100</td><td class="wt">100</td><td></td></tr>
</table>

<div class="note"><strong>v4.4 设计思路：</strong>权重按 |Spearman IC| 比例分配。上轨突破和乖离率获最大权重（IC最高+ICIR最稳定），行业RS和h_rs250大幅降权（IC近零）。回调深度权重持平（IC中等但ICIR为负且不稳定）。</div>

<h2>五分位分层对比</h2>
{qtable(all_data.get('v4.1 (外部)',[]), 'v4.1 (外部)')}
{qtable(all_data.get('v4.3 (h_rs250)',[]), 'v4.3 (h_rs250回归)')}
{qtable(all_data.get('v4.4 (IC校准)',[]), 'v4.4 (IC校准)')}

<h2>v4.4 10分位细分</h2>
<table><tr><th>分数段</th><th>N</th><th>占比</th><th>10d胜率</th><th>10d中位</th><th>20d胜率</th><th>20d中位</th></tr>
{fine_rows}</table>

<div class="note"><strong>60分是盈亏分水岭：</strong>60分以上胜率突破50%、中位转正。70~89是甜区（9%的信号，56%+胜率）。90分以上仅96条不可靠。实战可以60分作为最低过滤线。</div>
<hr><div style="text-align:center;margin-top:40px;color:var(--text-secondary);font-size:.6rem">MW 关注分 v4.4 · {datetime.now().strftime("%Y-%m-%d")} · Project Hanako</div>
</body></html>'''

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f: f.write(html)
print(f"→ {OUT} ({len(html):,}字符)")
conn.close()
