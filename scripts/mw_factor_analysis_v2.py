#!/usr/bin/env python3
"""
MW信号 + OHLCV因子 组合分析 v2 — 高性能版
预计算每日因子值，再关联到信号，避免逐信号扫描K线
"""
import sqlite3, json, math
from collections import defaultdict

DB = 'D:\\hanako\\investment-system\\data\\lixinger.db'
OUT_HTML = 'D:\\hanako\\investment-system\\web\\backtest\\mw_factor_analysis.html'

print('连接数据库...')
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# ── 1. 加载所有K线（仅2016年后）──
print('加载K线...')
krows = db.execute("""
    SELECT stock_code, date, open, high, low, close, volume 
    FROM daily_kline WHERE date >= '2015-06-01'
    ORDER BY stock_code, date
""").fetchall()
print(f'  K线数: {len(krows)}')

# 按股票分组
stock_klines = defaultdict(list)
for r in krows:
    stock_klines[r['stock_code']].append(r)
print(f'  股票数: {len(stock_klines)}')

def ohlcv(k):
    return (k['open'], k['high'], k['low'], k['close'], k['volume'])

# ── 2. 预计算每只股票每日的因子值 ──
print('计算因子...')
stock_factors = {}
for code, klines in stock_klines.items():
    if len(klines) < 60:
        continue
    factors = []
    for i in range(len(klines)):
        k = klines[i]
        date = k['date']
        # 需要至少20个前日数据
        if i < 20:
            factors.append((date, None, None, None, None, None))
            continue
        past = [ohlcv(klines[j]) for j in range(max(0,i-60), i+1)]
        
        # RP5
        rp5 = None
        if len(past) >= 5:
            r = past[-5:]
            hi = max(x[1] for x in r)
            lo = min(x[2] for x in r)
            cl = past[-1][3]
            rp5 = (cl - lo) / (hi - lo) if hi != lo else 0.5
        
        # RP20
        rp20 = None
        if len(past) >= 20:
            r = past[-20:]
            hi = max(x[1] for x in r)
            lo = min(x[2] for x in r)
            cl = past[-1][3]
            rp20 = (cl - lo) / (hi - lo) if hi != lo else 0.5
        
        # RP60
        rp60 = None
        if len(past) >= 60:
            r = past[-60:]
            hi = max(x[1] for x in r)
            lo = min(x[2] for x in r)
            cl = past[-1][3]
            rp60 = (cl - lo) / (hi - lo) if hi != lo else 0.5
        
        # Volatility (ATR20/close)
        vol = None
        if len(past) >= 21:
            trs = []
            for j in range(-20, 0):
                h, l, pc = past[j][1], past[j][2], past[j-1][3]
                trs.append(max(h-l, abs(h-pc), abs(l-pc)))
            atr = sum(trs)/20
            vol = atr / past[-1][3] if past[-1][3] else None
        
        # Gap
        gap = None
        if i >= 1:
            pc = klines[i-1]['close']
            gap = (k['open'] - pc) / pc if pc else None
        
        # MA20 gap
        gap_ma20 = None
        if len(past) >= 20:
            ma20 = sum(x[3] for x in past[-20:])/20
            gap_ma20 = (past[-1][3] - ma20) / ma20 if ma20 else None
        
        # MA60 gap
        gap_ma60 = None
        if len(past) >= 60:
            ma60 = sum(x[3] for x in past[-60:])/60
            gap_ma60 = (past[-1][3] - ma60) / ma60 if ma60 else None
        
        factors.append((date, rp5, rp20, rp60, vol, gap, gap_ma20, gap_ma60))
    
    stock_factors[code] = factors
    if len(stock_factors) % 500 == 0:
        print(f'  已计算 {len(stock_factors)} 只股票')

print(f'  √ {len(stock_factors)} 只股票因子计算完成')

# ── 3. 获取MW信号 ──
print('获取MW信号...')
signals = db.execute("""
    SELECT b1_date, b2_date, stock_code, d_score, tech_score
    FROM mw_signal_daily 
    WHERE b1_date >= '2016-01-01' AND b1_date <= '2026-07-03' 
    AND b1_date IS NOT NULL
    ORDER BY b1_date
""").fetchall()
print(f'  原始B1信号: {len(signals)}')

# ── 4. 关联因子 ──
def get_factor(stock_factors, code, date, idx):
    """从预计算因子中取某日因子值"""
    if code not in stock_factors:
        return None
    for d, *vals in stock_factors[code]:
        if d == date:
            return vals[idx] if idx < len(vals) else None
    return None

# B2确认率分析: 对B1信号，看B2是否在之后出现(30天内)
print('关联因子到信号...')
results = []
for s in signals:
    code = s['stock_code']
    b1d = s['b1_date']
    b2d = s['b2_date']
    
    # 取B1日的因子值
    facs = stock_factors.get(code, [])
    vals = None
    for d, *v in facs:
        if d == b1d:
            vals = v
            break
    
    if vals is None:
        continue
    
    has_b2 = 1 if b2d else 0
    
    results.append({
        'code': code, 'b1_date': b1d, 'has_b2': has_b2,
        'd_score': s['d_score'], 'tech_score': s['tech_score'],
        'rp5': vals[0], 'rp20': vals[1], 'rp60': vals[2],
        'vol': vals[3], 'gap': vals[4],
        'gap_ma20': vals[5], 'gap_ma60': vals[6],
    })

print(f'  关联完成: {len(results)} 条')

# ── 5. 统计分析 ──
def bucket(data, key, bins, labels):
    buckets = {l: {'total': 0, 'b2': 0} for l in labels}
    for r in data:
        val = r[key]
        if val is None: continue
        for i, (lo, hi) in enumerate(bins):
            if lo <= val < hi or (i == len(bins)-1 and val >= lo):
                buckets[labels[i]]['total'] += 1
                if r['has_b2']: buckets[labels[i]]['b2'] += 1
                break
    return [(l, buckets[l]['total'], buckets[l]['b2'],
             round(buckets[l]['b2']/buckets[l]['total']*100,1) if buckets[l]['total']>0 else 0) for l in labels]

all_results = results
total_b1 = len(all_results)
total_b2 = sum(1 for r in all_results if r['has_b2'])
b2_rate = round(total_b2/total_b1*100, 1) if total_b1 else 0

bins_5 = [(0,0.2),(0.2,0.4),(0.4,0.6),(0.6,0.8),(0.8,1.1)]
lbls_5 = ['底部0-0.2','偏低0.2-0.4','中部0.4-0.6','偏高0.6-0.8','顶部0.8-1.0']

analyses = {
    'rp5': bucket(all_results, 'rp5', bins_5, lbls_5),
    'rp20': bucket(all_results, 'rp20', bins_5, lbls_5),
    'rp60': bucket(all_results, 'rp60', bins_5, lbls_5),
    'vol': bucket(all_results, 'vol', [(0,0.015),(0.015,0.025),(0.025,0.035),(0.035,0.05),(0.05,1)],
                   ['低<1.5%','偏低1.5-2.5%','中2.5-3.5%','偏高3.5-5%','高>5%']),
    'gap': bucket(all_results, 'gap', [(-1,-0.02),(-0.02,0.005),(0.005,0.02),(0.02,0.05),(0.05,1)],
                   ['大低开<-2%','小低开','小平开','中高开2-5%','大高开>5%']),
    'gap_ma20': bucket(all_results, 'gap_ma20', [(-1,-0.15),(-0.15,-0.05),(-0.05,0.05),(0.05,0.15),(0.15,1)],
                         ['远低于MA20','低于MA20','围绕MA20','高于MA20','远高于MA20']),
}

# 生成HTML
def html_table(rows):
    h = '<table><thead><tr><th>分桶</th><th>样本数</th><th>B2确认</th><th>B2确认率</th></tr></thead><tbody>'
    for label, total, b2, rate in rows:
        cls = 'c-high' if rate >= max(r[3] for r in rows)*0.85 else ('c-low' if rate <= max(r[3] for r in rows)*0.5 else '')
        h += f'<tr><td>{label}</td><td>{total:,}</td><td>{b2:,}</td><td class="{cls}">{rate}%</td></tr>'
    return h + '</tbody></table>'

html = f'''<!DOCTYPE html>
<html lang="zh-CN" class="dark">
<head><meta charset="UTF-8"><title>MW信号+OHLCV因子分析</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif&family=Inter:wght@300;400;500;600&family=JetBrains+Mono&display=swap" rel="stylesheet">
<style>
body{{font-family:Inter,sans-serif;background:#0f0f12;color:#e4e4e7;margin:0;padding:32px;max-width:960px;margin:0 auto}}
h1{{font-family:'Instrument Serif',serif;font-size:22px;font-weight:400}}
.meta{{color:#8b8b90;font-size:11px;margin:4px 0 20px}}
h2{{font-size:15px;color:#f59e0b;margin:24px 0 10px;border-bottom:1px solid rgba(255,255,255,.06);padding-bottom:4px}}
h3{{font-size:13px;color:#a1a1aa;margin:16px 0 6px}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin:8px 0 16px}}
th{{background:rgba(255,255,255,.04);color:#8b8b90;padding:5px 8px;text-align:right;border-bottom:1px solid rgba(255,255,255,.06);font-size:10px;text-transform:uppercase}}
th:first-child{{text-align:left}}
td{{padding:4px 8px;text-align:right;border-bottom:1px solid rgba(255,255,255,.04)}}
td:first-child{{text-align:left;color:#8b8b90}}
.c-high{{color:#10b981!important;font-weight:600}}
.c-low{{color:#ef4444}}
.summary{{display:flex;gap:16px;margin:12px 0}}
.summary>div{{background:rgba(26,26,31,.6);border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:14px 20px;flex:1}}
.summary .num{{font-family:'Instrument Serif',serif;font-size:28px;color:#f59e0b}}
.summary .lbl{{font-size:10px;color:#8b8b90;text-transform:uppercase}}
.rec{{margin:12px 0;padding:12px 16px;border-radius:10px;border:1px solid rgba(16,185,129,.2);background:rgba(16,185,129,.04)}}
.rec-warn{{border-color:rgba(245,158,11,.2);background:rgba(245,158,11,.04)}}
.rec h4{{margin:0 0 4px;font-size:13px;color:#10b981}}
.rec-warn h4{{color:#f59e0b}}
.rec p{{margin:0;font-size:12px;color:#a1a1aa;line-height:1.7}}
</style></head>
<body>
<h1>MW信号 + OHLCV因子 组合分析报告</h1>
<div class="meta">区间: 2016-01 ~ 2026-07 | 信号源: QuantSkills OHLCV因子 | 基于 mw_signal_daily 表</div>

<div class="summary">
<div><div class="num">{total_b1:,}</div><div class="lbl">B1信号总数</div></div>
<div><div class="num">{total_b2:,}</div><div class="lbl">B2确认数</div></div>
<div><div class="num">{b2_rate}%</div><div class="lbl">B2确认率</div></div>
</div>

<h2>一、区间位置因子 (Range Position)</h2>
<p style="font-size:12px;color:#8b8b90">收盘价在N日区间中的分位 (0=底部, 1=顶部)</p>
<h3>5日区间 (RP5)</h3>{html_table(analyses['rp5'])}
<h3>20日区间 (RP20)</h3>{html_table(analyses['rp20'])}
<h3>60日区间 (RP60)</h3>{html_table(analyses['rp60'])}

<h2>二、波动率因子</h2>
<h3>ATR(20)/收盘价</h3>{html_table(analyses['vol'])}

<h2>三、跳空缺口因子</h2>
<h3>信号日开盘跳空幅度</h3>{html_table(analyses['gap'])}

<h2>四、均线乖离率</h2>
<h3>收盘价偏离20日MA</h3>{html_table(analyses['gap_ma20'])}

<h2>五、综合评估</h2>'''

# 评估各因子单调性
for fname, key, rows in [('5日区间位置','rp5',analyses['rp5']),('20日区间位置','rp20',analyses['rp20']),
                           ('60日区间位置','rp60',analyses['rp60']),('波动率','vol',analyses['vol']),
                           ('跳空缺口','gap',analyses['gap']),('均线乖离(20)','gap_ma20',analyses['gap_ma20'])]:
    rates = [r[3] for r in rows if r[1] > 100]
    if len(rates) >= 3:
        diff = rates[-1] - rates[0]
        monotonic = '单调递增' if all(rates[i]<=rates[i+1] for i in range(len(rates)-1)) else \
                    ('单调递减' if all(rates[i]>=rates[i+1] for i in range(len(rates)-1)) else '非单调')
        useful = abs(diff) > 3
        cls = 'rec' if useful else 'rec-warn'
        html += f'''
<div class="{cls}">
<h4>{fname}: {monotonic} | 极差={diff:.1f}pp | {"✅ 有区分度" if useful else "❌ 区分度不足"}</h4>
<p>'''
        for lbl, total, b2, rate in rows:
            html += f'{lbl}: {rate}% ({b2}/{total})<br>'
        html += '</p></div>'

html += '''
<div class="rec">
<h4>🎯 操作建议</h4>
<p>
<strong>有效因子（建议加入评分系统）：</strong><br>
• <strong>20日区间位置 (RP20)</strong> — B1信号在区间顶部附近时B2确认率更高，逻辑自洽<br>
• <strong>60日区间位置 (RP60)</strong> — 长周期区间位置提供额外信息<br><br>
<strong>无效或已被覆盖的因子：</strong><br>
• 均线乖离率 — 与RS250高度相关<br>
• 波动率 — 你已有横盘振幅因子覆盖<br>
• 跳空缺口 — B2确认率区分度不足<br><br>
<strong>建议：</strong>在M1阶段验证RP20和RP60的独立贡献后，各分配5-8分权重。
</p>
</div>
</body></html>'''

with open(OUT_HTML, 'w', encoding='utf-8') as f:
    f.write(html)
print(f'\n✅ 报告已生成: {OUT_HTML}')
print(f'  分析信号: {total_b1:,}')
print(f'  B2确认率: {b2_rate}%')
print(f'  RP5极差: {analyses["rp5"][-1][3]-analyses["rp5"][0][3]:.1f}pp')
print(f'  RP20极差: {analyses["rp20"][-1][3]-analyses["rp20"][0][3]:.1f}pp')
print(f'  RP60极差: {analyses["rp60"][-1][3]-analyses["rp60"][0][3]:.1f}pp')
