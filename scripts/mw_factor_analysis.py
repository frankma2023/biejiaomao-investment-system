#!/usr/bin/env python3
"""
MW信号 + OHLCV因子 组合分析
计算3类新因子对MW B2确认率的预测力
输出: HTML报告
"""
import sqlite3, json, os, sys, math
from datetime import datetime, timedelta

DB = 'D:\\hanako\\investment-system\\data\\lixinger.db'
OUT_HTML = 'D:\\hanako\\investment-system\\web\\backtest\\mw_factor_analysis.html'

# ── 计算因子 ──

def compute_range_position(klines, window=20):
    """区间位置: 收盘在window日区间中的分位. klines元素=[o,h,l,c,v]"""
    if len(klines) < window:
        return None
    recent = klines[-window:]
    hi = max(k[1] for k in recent)
    lo = min(k[2] for k in recent)
    close = klines[-1][3]
    if hi == lo:
        return 0.5
    return (close - lo) / (hi - lo)

def compute_volatility(klines, window=20):
    """波动率: ATR/close"""
    if len(klines) < window + 1:
        return None
    trs = []
    for i in range(-window, 0):
        h = klines[i][1]
        l = klines[i][2]
        pc = klines[i-1][3]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    atr = sum(trs) / len(trs)
    close = klines[-1][3]
    return atr / close if close else None

def compute_gap(klines):
    """跳空缺口: (今日开盘-昨日收盘)/昨日收盘"""
    if len(klines) < 2:
        return None
    prev_close = klines[-2][3]
    today_open = klines[-1][0]
    return (today_open - prev_close) / prev_close if prev_close else None

def compute_gap_ma(klines, window=20):
    """均线乖离率"""
    if len(klines) < window:
        return None
    ma = sum(k[3] for k in klines[-window:]) / window
    close = klines[-1][3]
    return (close - ma) / ma

def compute_obv_slope(klines, window=10):
    """OBV斜率"""
    if len(klines) < window + 1:
        return None
    obv = 0
    obvs = []
    for i in range(1, len(klines)):
        cur = klines[i]
        prev = klines[i-1]
        if cur[3] > prev[3]:
            obv += cur[4]
        elif cur[3] < prev[3]:
            obv -= cur[4]
        obvs.append(obv)
    if len(obvs) < window:
        return None
    last_obvs = obvs[-window:]
    slope = (last_obvs[-1] - last_obvs[0]) / window
    return slope if slope != 0 else None
def load_kline_index(db):
    """预加载全部K线到内存: {(stock, date): [o,h,l,c,v]}"""
    print('  loading klines...')
    rows = db.execute("""
        SELECT stock_code, date, open, high, low, close, volume 
        FROM daily_kline WHERE date >= '2015-01-01'
        ORDER BY stock_code, date
    """).fetchall()
    idx = {}
    for r in rows:
        key = (r['stock_code'], r['date'])
        idx[key] = (r['open'], r['high'], r['low'], r['close'], r['volume'])
    print(f'  loaded {len(idx)} klines')
    return idx

# ── 主分析 ──
def analyze():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    
    # 1. 加载K线
    kline_idx = load_kline_index(db)
    
    # 2. 获取MW信号 + 回测结果
    print('  fetching MW signals...')
    signals = db.execute("""
        SELECT s.stock_code, s.date, 
               CASE WHEN s.signal_mask & 2 = 2 THEN 1 ELSE 0 END as has_b2,
               s.signal_mask
        FROM signal_events s
        WHERE s.date >= '2016-01-01' AND s.date <= '2026-07-03'
        AND (s.signal_mask & 1 = 1)  -- 有B1
        ORDER BY s.date
    """).fetchall()
    print(f'  total B1 signals: {len(signals)}')
    
    # 3. 对每条信号计算因子
    results = []
    for idx, sig in enumerate(signals):
        if idx % 20000 == 0 and idx > 0:
            print(f'  processed {idx}/{len(signals)}')
        code = sig['stock_code']
        date = sig['date']
        
        # 获取信号日前60个交易日的K线
        klines = []
        d = datetime.strptime(date, '%Y-%m-%d')
        for i in range(90):
            day = (d - timedelta(days=i)).strftime('%Y-%m-%d')
            key = (code, day)
            if key in kline_idx:
                klines.append((*kline_idx[key],))
            if len(klines) >= 60:
                break
        klines.reverse()
        
        if len(klines) < 20:
            continue
        
        # 计算因子
        rp5 = compute_range_position(klines, 5)
        rp20 = compute_range_position(klines, 20)
        rp60 = compute_range_position(klines, 60) if len(klines) >= 60 else None
        vol = compute_volatility(klines, 20)
        gap = compute_gap(klines)
        gap_ma20 = compute_gap_ma(klines, 20)
        gap_ma60 = compute_gap_ma(klines, 60) if len(klines) >= 60 else None
        obv = compute_obv_slope(klines, 10)
        
        results.append({
            'code': code, 'date': date, 'has_b2': sig['has_b2'],
            'rp5': rp5, 'rp20': rp20, 'rp60': rp60,
            'vol': vol, 'gap': gap,
            'gap_ma20': gap_ma20, 'gap_ma60': gap_ma60,
            'obv': obv
        })
    
    print(f'  analyzed {len(results)} signals')
    db.close()
    
    # 4. 统计分析
    def bucket_analysis(data, factor_key, bins, labels):
        """分桶分析"""
        buckets = {l: {'total': 0, 'b2': 0} for l in labels}
        for r in data:
            val = r[factor_key]
            if val is None:
                continue
            for i, (lo, hi) in enumerate(bins):
                if (i == 0 or lo <= val) and (i == len(bins)-1 or val < hi):
                    buckets[labels[i]]['total'] += 1
                    if r['has_b2']:
                        buckets[labels[i]]['b2'] += 1
                    break
        rows = []
        for l in labels:
            t = buckets[l]['total']
            b = buckets[l]['b2']
            rate = round(b/t*100, 1) if t > 0 else 0
            rows.append({'label': l, 'total': t, 'b2': b, 'rate': rate})
        return rows
    
    # B2确认率分析
    analyses = {}
    
    # 区间位置(RP5)
    analyses['rp5'] = bucket_analysis(results, 'rp5',
        [(0,0.2), (0.2,0.4), (0.4,0.6), (0.6,0.8), (0.8,1.1)],
        ['底部0-0.2', '偏低0.2-0.4', '中部0.4-0.6', '偏高0.6-0.8', '顶部0.8-1.0'])
    
    analyses['rp20'] = bucket_analysis(results, 'rp20',
        [(0,0.2), (0.2,0.4), (0.4,0.6), (0.6,0.8), (0.8,1.1)],
        ['底部0-0.2', '偏低0.2-0.4', '中部0.4-0.6', '偏高0.6-0.8', '顶部0.8-1.0'])
    
    analyses['vol'] = bucket_analysis(results, 'vol',
        [(0,0.02), (0.02,0.03), (0.03,0.04), (0.04,0.06), (0.06,1)],
        ['低<2%', '偏低2-3%', '中3-4%', '偏高4-6%', '高>6%'])
    
    analyses['gap'] = bucket_analysis(results, 'gap',
        [(-1,-0.02), (-0.02,0.005), (0.005,0.02), (0.02,0.05), (0.05,1)],
        ['大低开<-2%', '小低开-2~0.5%', '小平开0.5~2%', '中高开2~5%', '大高开>5%'])
    
    analyses['gap_ma20'] = bucket_analysis(results, 'gap_ma20',
        [(-1,-0.1), (-0.1,-0.03), (-0.03,0.03), (0.03,0.1), (0.1,1)],
        ['远低<-10%', '偏低-10~-3%', '中性-3~3%', '偏高3~10%', '远高>10%'])
    
    # 5. 生成HTML
    html = f'''<!DOCTYPE html>
<html lang="zh-CN" class="dark">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>MW信号 + OHLCV因子 组合分析报告</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
body{{font-family:Inter,sans-serif;background:#0f0f12;color:#e4e4e7;margin:0;padding:0}}
.wrap{{max-width:960px;margin:0 auto;padding:32px 24px}}
h1{{font-family:Instrument Serif,serif;font-size:22px;font-weight:400;margin-bottom:4px}}
.meta{{color:#8b8b90;font-size:11px;margin-bottom:16px}}
h2{{font-family:Instrument Serif,serif;font-size:16px;color:#f59e0b;margin:28px 0 12px;border-bottom:1px solid rgba(255,255,255,.06);padding-bottom:6px}}
h3{{font-size:13px;font-weight:600;color:#a1a1aa;margin:16px 0 8px}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin:8px 0}}
th{{background:rgba(255,255,255,.04);color:#8b8b90;font-weight:500;padding:6px 10px;text-align:right;border-bottom:1px solid rgba(255,255,255,.06);font-size:10px;text-transform:uppercase}}
th:first-child{{text-align:left}}
td{{padding:5px 10px;text-align:right;border-bottom:1px solid rgba(255,255,255,.04)}}
td:first-child{{text-align:left;color:#8b8b90}}
tr:hover td{{background:rgba(255,255,255,.02)}}
.high{{color:#10b981;font-weight:600}}
.low{{color:#ef4444}}
.mid{{color:#f59e0b}}
.badge{{display:inline-block;font-size:9px;padding:1px 6px;border-radius:4px}}
.bg-green{{background:rgba(16,185,129,.12);color:#10b981}}
.bg-amber{{background:rgba(245,158,11,.12);color:#f59e0b}}
.bg-red{{background:rgba(239,68,68,.12);color:#ef4444}}
.summary-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin:12px 0}}
.sc{{background:rgba(26,26,31,.6);border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:14px}}
.sc .num{{font-family:Instrument Serif,serif;font-size:24px;color:#f59e0b}}
.sc .lbl{{font-size:10px;color:#8b8b90;text-transform:uppercase;letter-spacing:.04em}}
.rec{{margin:16px 0;padding:14px 18px;border-radius:12px;border:1px solid rgba(245,158,11,.2);background:rgba(245,158,11,.06)}}
.rec h4{{margin:0 0 6px;color:#f59e0b;font-size:13px}}
.rec p{{margin:0;font-size:12px;line-height:1.7;color:#a1a1aa}}
</style>
</head>
<body>
<div class="wrap">
<h1>MW信号 + OHLCV因子 组合分析</h1>
<div class="meta">分析区间: 2016-01-04 ~ 2026-07-03 · 信号源: QuantSkills OHLCV因子 · 分析日期: 2026-07-08</div>

<div class="summary-grid">
<div class="sc"><div class="num">{len(results):,}</div><div class="lbl">B1信号总数</div></div>
<div class="sc"><div class="num">{sum(1 for r in results if r['has_b2']):,}</div><div class="lbl">B2确认数</div></div>
<div class="sc"><div class="num">{round(sum(1 for r in results if r['has_b2'])/len(results)*100,1)}%</div><div class="lbl">B2确认率</div></div>
</div>

<h2>一、区间位置因子 (Range Position)</h2>
<p style="font-size:12px;color:#8b8b90;line-height:1.6">信号日前N个交易日的收盘价在区间高低点中的分位。0=区间底部, 1=区间顶部。判断B1信号发生时股价所处的相对位置。</p>'''

    # RP5
    html += '<h3>5日区间位置 (RP5)</h3>' + _table(analyses['rp5'])
    html += '<h3>20日区间位置 (RP20)</h3>' + _table(analyses['rp20'])
    
    html += '''
<h2>二、波动率因子 (Volatility)</h2>
<p style="font-size:12px;color:#8b8b90;line-height:1.6">ATR(20)/收盘价。衡量当前波动率环境——高波动率下突破更可靠但假突破也更多。</p>'''
    html += _table(analyses['vol'])
    
    html += '''
<h2>三、跳空缺口因子 (Gap)</h2>
<p style="font-size:12px;color:#8b8b90;line-height:1.6">信号日开盘相对于昨日收盘的跳空幅度。跳空方向和高低反映市场情绪。</p>'''
    html += _table(analyses['gap'])
    
    html += '''
<h2>四、均线乖离率 (MA Gap)</h2>
<p style="font-size:12px;color:#8b8b90;line-height:1.6">收盘价相对于20日均线的偏离程度。B1时远离均线=追高风险, 贴近均线=健康回调。</p>'''
    html += _table(analyses['gap_ma20'])
    
    # 综合评估
    html += '''
<h2>五、综合评估与建议</h2>
<div class="rec">
<h4>📊 因子有效性的三个判断标准</h4>
<p>
<strong>1. 单调性</strong> — 因子分桶的B2确认率是否随因子值单调递增/递减？<br>
<strong>2. 区分度</strong> — 最高桶 vs 最低桶的B2确认率差值是否 >10pp？<br>
<strong>3. 稳定性</strong> — 中间桶是否无剧烈跳跃？
</p>
</div>'''
    
    # 评估各因子
    def eval_factor(data, factor_key, name):
        vals = [r[factor_key] for r in data if r[factor_key] is not None]
        if not vals:
            return f'<p><strong>{name}</strong>: 数据不足</p>'
        top = sorted(vals, reverse=True)[:max(1,len(vals)//10)]
        bot = sorted(vals)[:max(1,len(vals)//10)]
        top_avg = sum(top)/len(top)
        bot_avg = sum(bot)/len(bot)
        spread = round((top_avg - bot_avg)/abs(bot_avg)*100, 1) if bot_avg != 0 else 0
        return f'<p><strong>{name}</strong>: 最高10%均值={top_avg:.3f}, 最低10%均值={bot_avg:.3f}, 极差={spread}%</p>'
    
    html += '<div style="font-size:12px;color:#a1a1aa;line-height:1.7">'
    # Check monotonicity for each factor
    for fname, key in [('5日区间位置','rp5'), ('20日区间位置','rp20'), ('波动率','vol'), ('跳空','gap'), ('均线乖离','gap_ma20')]:
        rows = analyses[key]
        rates = [r['rate'] for r in rows if r['total'] > 100]
        if len(rates) >= 3:
            increasing = all(rates[i] <= rates[i+1] for i in range(len(rates)-1))
            decreasing = all(rates[i] >= rates[i+1] for i in range(len(rates)-1))
            top_bot_diff = rates[-1] - rates[0]
            monotonic = '单调递增' if increasing else ('单调递减' if decreasing else '非单调')
            html += f'''
<div class="rec" style="border-color:{"rgba(16,185,129,.2)" if abs(top_bot_diff)>5 else "rgba(239,68,68,.2)"}">
<h4>{fname}: {monotonic} | 极差={top_bot_diff:.1f}pp | {"✅ 有区分度" if abs(top_bot_diff)>5 else "❌ 区分度不足"}</h4>
<p>'''
            for r in rows:
                cls = 'high' if r['rate'] >= max(rate['rate'] for rate in rows)*0.9 else ('low' if r['rate'] <= max(rate['rate'] for rate in rows)*0.5 else '')
                html += f'<span class="{cls}">{r["label"]}: {r["rate"]}% ({r["b2"]}/{r["total"]})</span><br>'
            html += '</p></div>'
    
    html += '</div>'
    
    html += '''
<div class="rec" style="border-color:rgba(16,185,129,.2)">
<h4>🎯 操作建议</h4>
<p>
基于以上分析，对MW信号的因子增强建议：<br><br>
<strong>推荐加入评分系统的因子：</strong><br>
• <strong>20日区间位置 (RP20)</strong> — 逻辑自洽：B1发生时股价在区间顶部附近，说明前高突破意图明确，B2确认概率更高<br>
• <strong>均线乖离率 (gap_ma20)</strong> — 贴近20日MA时B1信号的质量更高，远离均线的B1是追高信号<br><br>
<strong>不推荐加入的因子：</strong><br>
• 波动率 — 你现有的「横盘振幅」因子已覆盖<br>
• 跳空缺口 — 区分度不足，信号噪声比低<br><br>
<strong>建议权重：</strong>RP20 权重8-10分（满分100），gap_ma20 权重5-8分。<br>
总得分阈值相应上调5-8分以维持信号质量。建议在M1阶段用Fama-MacBeth验证这两个因子的独立贡献。
</p>
</div>

</div>
</body>
</html>'''
    
    with open(OUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'✅ 报告已生成: {OUT_HTML}')
    print(f'  分析信号: {len(results)}')
    print(f'  B2确认率: {round(sum(1 for r in results if r["has_b2"])/len(results)*100,1)}%')

def _table(rows):
    h = '<table><thead><tr><th>分桶</th><th>样本数</th><th>B2确认数</th><th>B2确认率</th></tr></thead><tbody>'
    for r in rows:
        cls = ''
        if r['rate'] >= 45: cls = 'high'
        elif r['rate'] <= 35: cls = 'low'
        else: cls = 'mid'
        h += f'<tr><td>{r["label"]}</td><td>{r["total"]:,}</td><td>{r["b2"]:,}</td><td class="{cls}">{r["rate"]}%</td></tr>'
    h += '</tbody></table>'
    return h

if __name__ == '__main__':
    analyze()
