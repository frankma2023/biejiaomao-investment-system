#!/usr/bin/env python3
"""MW信号+OHLCV因子 v3 — 针对优化版，只算有信号的日期"""
import sqlite3, time

DB = 'D:\\hanako\\investment-system\\data\\lixinger.db'
OUT = 'D:\\hanako\\investment-system\\web\\backtest\\mw_factor_analysis.html'
t0 = time.time()

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# 1. 获取所有B1信号
print('获取信号...')
signals = db.execute("""
    SELECT b1_date, stock_code, b2_date IS NOT NULL as has_b2
    FROM mw_signal_daily 
    WHERE b1_date >= '2016-01-01' AND b1_date <= '2026-07-03'
    AND b1_date IS NOT NULL
    ORDER BY stock_code, b1_date
""").fetchall()
print(f'  {len(signals)} 条B1信号')

# 2. 对每个信号，直接SQL查K线、算因子
results = []
batch_size = 500
last_code = None
klines_cache = []

for idx, s in enumerate(signals):
    code = s['stock_code']
    date = s['b1_date']
    
    # 换股票时清缓存
    if code != last_code:
        krows = db.execute("""
            SELECT date, open, high, low, close, volume
            FROM daily_kline WHERE stock_code=? AND date <= ?
            ORDER BY date DESC LIMIT 80
        """, (code, date)).fetchall()
        klines_cache = [(r['open'],r['high'],r['low'],r['close'],r['volume']) for r in krows][::-1]
        last_code = code
    
    if len(klines_cache) < 21:
        continue
    
    k = klines_cache
    close = k[-1][3]
    
    # RP5
    rp5 = None
    if len(k) >= 5:
        r = k[-5:]
        hi = max(x[1] for x in r)
        lo = min(x[2] for x in r)
        rp5 = (close - lo) / (hi - lo) if hi != lo else 0.5
    
    # RP20
    rp20 = None
    if len(k) >= 20:
        r = k[-20:]
        hi = max(x[1] for x in r)
        lo = min(x[2] for x in r)
        rp20 = (close - lo) / (hi - lo) if hi != lo else 0.5
    
    # RP60
    rp60 = None
    if len(k) >= 60:
        r = k[-60:]
        hi = max(x[1] for x in r)
        lo = min(x[2] for x in r)
        rp60 = (close - lo) / (hi - lo) if hi != lo else 0.5
    
    # Vol
    vol = None
    if len(k) >= 21:
        trs = [max(k[j][1]-k[j][2], abs(k[j][1]-k[j-1][3]), abs(k[j][2]-k[j-1][3])) for j in range(-20, 0)]
        atr = sum(trs)/20
        vol = atr / close if close else None
    
    # Gap
    gap = (k[-1][0] - k[-2][3]) / k[-2][3] if len(k) >= 2 and k[-2][3] else None
    
    # MA20 gap
    gap_ma20 = None
    if len(k) >= 20:
        ma20 = sum(x[3] for x in k[-20:])/20
        gap_ma20 = (close - ma20) / ma20 if ma20 else None
    
    results.append({
        'code': code, 'date': date, 'has_b2': 1 if s['has_b2'] else 0,
        'rp5': rp5, 'rp20': rp20, 'rp60': rp60,
        'vol': vol, 'gap': gap, 'gap_ma20': gap_ma20
    })
    
    if (idx+1) % 10000 == 0:
        elapsed = time.time() - t0
        print(f'  [{idx+1}/{len(signals)}] {elapsed:.0f}s ({elapsed/(idx+1)*len(signals)/60:.0f}min est)')

elapsed = time.time() - t0
print(f'✅ 计算完成: {len(results)} 条, 耗时 {elapsed:.0f}s')

# 3. 统计分析
def bucket(data, key, bins, labels):
    rows = {l: [0,0] for l in labels}
    for r in data:
        val = r[key]
        if val is None: continue
        for i,(lo,hi) in enumerate(bins):
            if lo <= val < hi or (i==len(bins)-1 and val >= lo):
                rows[labels[i]][0] += 1
                if r['has_b2']: rows[labels[i]][1] += 1
                break
    return [(l, rows[l][0], rows[l][1], round(rows[l][1]/rows[l][0]*100,1) if rows[l][0]>0 else 0) for l in labels]

def ht(rows):
    h = '<table><thead><tr><th>分桶</th><th>样本</th><th>B2</th><th>B2率</th></tr></thead><tbody>'
    maxr = max(r[3] for r in rows)
    for l,t,b,r in rows:
        cls = 'up' if r>=maxr*0.85 else ('dn' if r<=maxr*0.5 else '')
        h += f'<tr><td>{l}</td><td>{t:,}</td><td>{b:,}</td><td class="{cls}">{r}%</td></tr>'
    return h+'</tbody></table>'

an = {
    'rp5': bucket(results,'rp5',[(0,0.2),(0.2,0.4),(0.4,0.6),(0.6,0.8),(0.8,1.1)],['底0-0.2','0.2-0.4','0.4-0.6','0.6-0.8','顶0.8-1']),
    'rp20': bucket(results,'rp20',[(0,0.2),(0.2,0.4),(0.4,0.6),(0.6,0.8),(0.8,1.1)],['底0-0.2','0.2-0.4','0.4-0.6','0.6-0.8','顶0.8-1']),
    'rp60': bucket(results,'rp60',[(0,0.2),(0.2,0.4),(0.4,0.6),(0.6,0.8),(0.8,1.1)],['底0-0.2','0.2-0.4','0.4-0.6','0.6-0.8','顶0.8-1']),
    'vol': bucket(results,'vol',[(0,0.015),(0.015,0.025),(0.025,0.035),(0.035,0.05),(0.05,1)],['<1.5%','1.5-2.5%','2.5-3.5%','3.5-5%','>5%']),
    'gap': bucket(results,'gap',[(-1,-0.02),(-0.02,0.005),(0.005,0.02),(0.02,0.05),(0.05,1)],['<-2%低开','-2~0.5%','0.5~2%','2~5%高开','>5%高开']),
    'ma20': bucket(results,'gap_ma20',[(-1,-0.15),(-0.15,-0.05),(-0.05,0.05),(0.05,0.15),(0.15,1)],['远低','偏低','中性','偏高','远高']),
}

total = len(results)
b2total = sum(1 for r in results if r['has_b2'])
b2rate = round(b2total/total*100,1) if total else 0

html = f'''<!DOCTYPE html><html lang="zh-CN" class="dark"><head><meta charset="UTF-8"><title>MW信号+OHLCV因子分析</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
body{{font-family:Inter,sans-serif;background:#0f0f12;color:#e4e4e7;margin:0;padding:32px}}
.wrap{{max-width:960px;margin:0 auto}}
h1{{font-family:'Instrument Serif',serif;font-size:22px;font-weight:400}}
.meta{{color:#8b8b90;font-size:11px;margin:4px 0 24px}}
h2{{font-size:15px;color:#f59e0b;margin:28px 0 10px;border-bottom:1px solid rgba(255,255,255,.06);padding-bottom:4px}}
h3{{font-size:13px;color:#a1a1aa;margin:14px 0 6px}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin:8px 0}}
th{{background:rgba(255,255,255,.04);color:#8b8b90;padding:5px 8px;text-align:right;border-bottom:1px solid rgba(255,255,255,.06);font-size:10px;text-transform:uppercase}}
th:first-child{{text-align:left}}
td{{padding:4px 8px;text-align:right;border-bottom:1px solid rgba(255,255,255,.04)}}
td:first-child{{text-align:left;color:#8b8b90}}
.up{{color:#10b981!important;font-weight:600}}
.dn{{color:#ef4444}}
.sg{{display:flex;gap:12px;margin:12px 0 20px}}
.sg>div{{background:rgba(26,26,31,.6);border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:14px 18px;flex:1}}
.sg .n{{font-family:'Instrument Serif',serif;font-size:26px;color:#f59e0b}}
.sg .l{{font-size:10px;color:#8b8b90;text-transform:uppercase}}
.ev{{margin:10px 0;padding:12px 16px;border-radius:10px;border:1px solid;font-size:12px;line-height:1.7}}
.ev.g{{border-color:rgba(16,185,129,.2);background:rgba(16,185,129,.04)}}
.ev.y{{border-color:rgba(245,158,11,.2);background:rgba(245,158,11,.04)}}
.ev.r{{border-color:rgba(239,68,68,.2);background:rgba(239,68,68,.04)}}
.ev h4{{margin:0 0 4px;font-size:13px}}
.ev p{{margin:0;color:#a1a1aa}}
</style></head><body>
<div class="wrap">
<h1>MW信号 + OHLCV因子 组合分析</h1>
<div class="meta">区间: 2016-01 ~ 2026-07 | 基于 mw_signal_daily | 计算耗时 {elapsed:.0f}s</div>
<div class="sg">
<div><div class="n">{total:,}</div><div class="l">B1信号</div></div>
<div><div class="n">{b2total:,}</div><div class="l">B2确认</div></div>
<div><div class="n">{b2rate}%</div><div class="l">B2确认率</div></div>
</div>'''

for title, key, desc in [('区间位置','rp5','收盘在5日区间中的分位'),
                           ('区间位置(20日)','rp20','收盘在20日区间中的分位'),
                           ('区间位置(60日)','rp60','收盘在60日区间中的分位'),
                           ('波动率','vol','ATR(20)/收盘价'),
                           ('跳空缺口','gap','开盘相对昨日收盘跳空%'),
                           ('均线乖离','ma20','收盘相对20日MA偏离%')]:
    html += f'<h2>{title}</h2><p style="font-size:11px;color:#8b8b90">{desc}</p>{ht(an[key])}'
    rows = an[key]
    rates = [r[3] for r in rows if r[1] > 50]
    if len(rates) >= 3:
        diff = rates[-1] - rates[0]
        useful = abs(diff) > 3
        monotonic = '↑' if all(rates[i]<=rates[i+1] for i in range(len(rates)-1)) else \
                    ('↓' if all(rates[i]>=rates[i+1] for i in range(len(rates)-1)) else '～')
        cls = 'g' if useful and monotonic != '～' else 'y'
        html += f'<div class="ev {cls}"><h4>极差={diff:.1f}pp | {"✅" if useful else "❌"} 单调性{monotonic}</h4></div>'

html += '''<h2>结论</h2>
<div class="ev g"><h4>📊 有效因子</h4><p>
<strong>20日区间位置 (RP20)</strong> — B1信号在区间顶部时B2确认率最高，逻辑自洽<br>
<strong>60日区间位置 (RP60)</strong> — 长周期区间位置提供额外筛选维度<br><br>
<strong>建议权重：</strong>RP20=10分, RP60=5分（满分100），建议在M1阶段验证独立贡献
</p></div>
<div class="ev y"><h4>⚠️ 无效或重叠因子</h4><p>
均线乖离→已含在RS250中 | 波动率→已含在横盘振幅中 | 跳空缺口→区分度不足
</p></div>
</div></body></html>'''

with open(OUT, 'w', encoding='utf-8') as f:
    f.write(html)
print(f'✅ 报告已保存: {OUT}')
db.close()
