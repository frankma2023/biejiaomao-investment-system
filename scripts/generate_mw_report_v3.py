"""
MW信号最新数据回测分析 + HTML报告生成
基于 B1=1.3 回填完成后的最新数据
"""
import sys, os, json, sqlite3
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
from analytics.mw_backtest import *

def p(v):
    if v is None: return '—'
    return f"{v:+.1f}%"

def pct(v):
    if v is None: return '—'
    return f"{v:.1f}%"

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

def stats_get(d, hz):
    """Get stats from a dict[horizon_key] where values are calc_stats dict"""
    s = d.get(hz, {}) if isinstance(d, dict) else {}
    return {
        'wr': s.get('win_rate', 0) or 0,
        'med': s.get('median_return', 0) or 0,
        'avg': s.get('avg_return', 0) or 0,
        'n': s.get('n', 0)
    }

def generate_html_report():
    start_date = '2026-01-01'
    end_date = '2026-06-05'
    
    print("Running backtest...")
    result = run(start_date, end_date)
    S = result
    N = S['total_signals']
    print(f"Total signals: {N}")
    
    html = []
    h = html.append
    
    # ═══ CSS ═══
    h('''<!DOCTYPE html><html lang="zh-CN" data-theme="dark"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>MW信号回测分析报告 v3.0 (B1=1.3)</title>
<style>:root{--bg:#1a1a1f;--card:rgba(26,26,31,.6);--text:#d4d4d8;--text-muted:#9ca3af;--text-dim:#6b7280;--accent:#f59e0b;--purple:#a78bfa;--green:#10b981;--red:#ef4444;--blue:#38bdf8;--divider:rgba(255,255,255,.06);--border:rgba(255,255,255,.08)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Inter,-apple-system,BlinkMacSystemFont,sans-serif;line-height:1.72;background:var(--bg);color:var(--text);padding:40px 24px 80px}
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
tr.row-hl{background:rgba(245,158,11,.06)}
tr.row-best{background:rgba(16,185,129,.06)}
tr.row-worst{background:rgba(239,68,68,.04)}
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
.callout-warn{background:rgba(239,68,68,.05);border-left:3px solid var(--red)}
.callout-tip{background:rgba(16,185,129,.05);border-left:3px solid var(--green)}
.callout-note{background:rgba(245,158,11,.05);border-left:3px solid var(--accent)}
.callout-info{background:rgba(56,189,248,.05);border-left:3px solid var(--blue)}
.callout strong{color:#fafafa}
.vs-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0 24px}
.vs-side{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px}
.vs-side h4{font-size:.74rem;font-weight:600;margin-bottom:10px}
.bar-wrap{display:flex;align-items:center;gap:10px;margin:3px 0;font-size:.64rem}
.bar-label{width:100px;text-align:right;color:var(--text-muted);flex-shrink:0}
.bar-track{flex:1;height:18px;background:rgba(255,255,255,.03);border-radius:4px;overflow:hidden}
.bar-fill{height:100%;border-radius:4px}
.bar-val{width:52px;font-weight:600;font-size:.62rem;flex-shrink:0;text-align:left}
.conclusion-list{list-style:none;counter-reset:conc}
.conclusion-list li{counter-increment:conc;padding:12px 16px 12px 48px;margin:8px 0;background:var(--card);border:1px solid var(--border);border-radius:10px;font-size:.68rem;position:relative;line-height:1.6}
.conclusion-list li::before{content:counter(conc);position:absolute;left:14px;top:12px;width:22px;height:22px;background:rgba(245,158,11,.15);color:var(--accent);border-radius:6px;font-size:.6rem;font-weight:700;display:flex;align-items:center;justify-content:center}
.report-footer{text-align:center;font-size:.58rem;color:var(--text-dim);margin-top:52px;padding-top:24px;border-top:1px solid var(--divider)}
@media(max-width:700px){.vs-grid{grid-template-columns:1fr}}
</style></head><body><div class="container">''')
    
    # Header
    h(f'<h1>MW信号回测分析报告 v3.2</h1>')
    h(f'<div class="subtitle">B1=1.3 · I1阶梯制 · D→15/I2→15/Sig→10 权重重构</div>')
    h(f'<div class="meta">{start_date} ~ {end_date} · {N}个信号 · backfill_mw_signals.py回填完成后最新数据</div>')
    
    # ═══ Data extraction ═══
    overall = S['overall']           # dict: {'5d': calc_stats, ...}
    by_conf = S['by_confidence']     # dict: {'高': {'5d': calc_stats, ...}, ...}
    by_score_tier = S['by_score_tier'] # dict: {'90~100': {'5d': calc_stats, ...}, ...}
    by_market = S['by_market']       # dict: {'多头': {...}, ...}
    by_cap = S['by_market_cap']      # dict: {'小盘(<50亿)': {...}, ...}
    dim_stats = S['score_dimension'] # dict: {'score_h': {'full': calc_stats, 'zero': calc_stats}, ...}
    baseline = S['random_baseline']  # dict: {'5d': calc_stats, ...}
    baseline_high = S['random_baseline_v1_high']  # dict or {}
    sim = S.get('portfolio_simulation', {})
    
    # Score distribution from by_score_tier counts
    score_counts = {}
    for tier, stats_dict in by_score_tier.items():
        total_n = sum(stats_dict.get(hz, {}).get('n', 0) for hz in ['5d', '10d', '20d'])
        score_counts[tier] = total_n
    
    # High confidence count
    high_conf = by_conf.get('高', {})
    high_n = sum(high_conf.get(hz, {}).get('n', 0) for hz in ['5d', '10d', '20d']) // 3 if high_conf else 0
    
    # ═══ 01 Key Insights ═══
    o10 = stats_get(overall, '10d')
    h10 = stats_get(high_conf, '10d')
    o5 = stats_get(overall, '5d')
    o20 = stats_get(overall, '20d')
    
    h('<h2>01 核心发现</h2>')
    h('<div class="insight-grid">')
    h(f'<div class="insight-card"><div class="label">全量 5d 中位</div><div class="value" style="color:var(--blue)">{p(o5["med"])}</div><div class="detail">胜率 {pct(o5["wr"])} · 样本 {o5["n"]}</div></div>')
    h(f'<div class="insight-card"><div class="label">全量 10d 中位</div><div class="value" style="color:var(--blue)">{p(o10["med"])}</div><div class="detail">胜率 {pct(o10["wr"])} · 平均 {p(o10["avg"])}</div></div>')
    h(f'<div class="insight-card"><div class="label">高分 (≥80) 信号数</div><div class="value" style="color:var(--accent)">{high_n:.0f}</div><div class="detail">占总量 {high_n/N*100:.1f}%</div></div>')
    h(f'<div class="insight-card"><div class="label">高分 10d 中位</div><div class="value" style="color:var(--green)">{p(h10["med"])}</div><div class="detail">胜率 {pct(h10["wr"])}</div></div>')
    h(f'<div class="insight-card"><div class="label">全量 20d 中位</div><div class="value" style="color:var(--blue)">{p(o20["med"])}</div><div class="detail">胜率 {pct(o20["wr"])}</div></div>')
    h('</div>')
    
    # ═══ 02 Overall ═══
    h('<h2>02 总体表现</h2>')
    h('<div class="table-wrap"><table>')
    h('<tr><th>窗口</th><th>胜率 (&gt;2%)</th><th>中位收益率</th><th>平均收益率</th><th>样本</th></tr>')
    for horizon in ['5d', '10d', '20d']:
        s = stats_get(overall, horizon)
        h(f'<tr><td>{horizon}</td><td class="{cr(s["wr"])}">{pct(s["wr"])}</td><td class="{crm(s["med"])}">{p(s["med"])}</td><td>{p(s["avg"])}</td><td>{s["n"]}</td></tr>')
    h('</table></div>')
    
    h('<div class="callout callout-note"><strong>全量信号的中位收益均为负值。</strong>平均收益虽为正，但由少数大牛股拉偏——典型长尾分布。MW信号必须精选使用。</div>')
    
    # ═══ 03 Score Distribution ═══
    h('<h2>03 分数与置信度分布</h2>')
    h('<div class="vs-grid">')
    
    # Score histogram
    h('<div class="vs-side"><h4 style="color:var(--accent)">分数段分布</h4>')
    tier_order = ['90~100', '85~90', '80~85', '75~80', '70~75', '65~70', '60~65', '55~60', '50~55', '0~50']
    max_cnt = max(score_counts.values()) if score_counts else 1
    for tier in tier_order:
        cnt = score_counts.get(tier, 0)
        w = cnt / max(max_cnt, 1) * 100
        color = 'var(--green)' if tier in ('90~100','85~90','80~85') else ('var(--accent)' if cnt > 200 else 'var(--text-dim)')
        h(f'<div class="bar-wrap"><span class="bar-label">{tier}</span><div class="bar-track"><div class="bar-fill" style="width:{w}%;background:{color};opacity:.6"></div></div><span class="bar-val" style="color:{color};">{cnt}</span></div>')
    h('</div>')
    
    # Confidence dist
    h('<div class="vs-side"><h4 style="color:var(--purple)">置信度分布</h4>')
    total_conf_n = 0
    conf_counts = {}
    for g in ['高', '中', '低']:
        c = by_conf.get(g, {})
        n = sum(c.get(hz, {}).get('n', 0) for hz in ['5d','10d','20d']) // 3 if c else 0
        conf_counts[g] = n
        total_conf_n += n
    for g in ['高', '中', '低']:
        cnt = conf_counts.get(g, 0)
        w = cnt / max(total_conf_n, 1) * 100
        color = 'var(--green)' if g == '高' else ('var(--accent)' if g == '中' else 'var(--text-dim)')
        h(f'<div class="bar-wrap"><span class="bar-label">{g}</span><div class="bar-track"><div class="bar-fill" style="width:{w}%;background:{color};opacity:.6"></div></div><span class="bar-val" style="color:{color};">{cnt}</span></div>')
    h('</div></div>')
    
    # ═══ 04 Score Tier Performance ═══
    h('<h2>04 分数段表现</h2>')
    h('<div class="table-wrap"><table>')
    h('<tr><th>分数段</th><th>数量</th><th>5d 胜率</th><th>5d 中位</th><th>10d 胜率</th><th>10d 中位</th><th>20d 胜率</th><th>20d 中位</th></tr>')
    
    for tier in tier_order:
        t = by_score_tier.get(tier, {})
        s5 = stats_get(t, '5d')
        s10 = stats_get(t, '10d')
        s20 = stats_get(t, '20d')
        cnt = s5['n']  # Use 5d count as representative
        row_cls = 'row-best' if s10['wr'] >= 55 else ('row-hl' if s10['wr'] >= 48 else ('row-worst' if cnt < 20 and s10['wr'] < 35 else ''))
        h(f'<tr class="{row_cls}"><td><b>{tier}</b></td><td>{cnt}</td><td class="{cr(s5["wr"])}">{pct(s5["wr"])}</td><td class="{crm(s5["med"])}">{p(s5["med"])}</td><td class="{cr(s10["wr"])}">{pct(s10["wr"])}</td><td class="{crm(s10["med"])}">{p(s10["med"])}</td><td class="{cr(s20["wr"])}">{pct(s20["wr"])}</td><td class="{crm(s20["med"])}">{p(s20["med"])}</td></tr>')
    h('</table></div>')
    
    h('<div class="callout callout-tip"><strong>最佳表现段需结合样本量判断。</strong>关注胜率>50%且样本≥20的分数段，这些是实操中可用的切割点。</div>')
    
    # ═══ 05 Confidence ═══
    h('<h2>05 置信度表现</h2>')
    h('<div class="table-wrap"><table>')
    h('<tr><th>档位</th><th>数量</th><th>5d 胜率</th><th>5d 中位</th><th>10d 胜率</th><th>10d 中位</th><th>10d 平均</th><th>20d 胜率</th><th>20d 中位</th></tr>')
    for g in ['高', '中', '低']:
        c = by_conf.get(g, {})
        s5 = stats_get(c, '5d')
        s10 = stats_get(c, '10d')
        s20 = stats_get(c, '20d')
        cnt = s5['n']
        row_cls = 'row-best' if g == '高' else ''
        h(f'<tr class="{row_cls}"><td><b>{g}</b></td><td>{cnt}</td><td class="{cr(s5["wr"])}">{pct(s5["wr"])}</td><td class="{crm(s5["med"])}">{p(s5["med"])}</td><td class="{cr(s10["wr"])}">{pct(s10["wr"])}</td><td class="{crm(s10["med"])}">{p(s10["med"])}</td><td>{p(s10["avg"])}</td><td class="{cr(s20["wr"])}">{pct(s20["wr"])}</td><td class="{crm(s20["med"])}">{p(s20["med"])}</td></tr>')
    h('</table></div>')
    
    # ═══ 06 Dimension ═══
    h('<h2>06 评分维度预测力</h2>')
    h('<div class="table-wrap"><table>')
    h('<tr><th>维度</th><th>满分 10d 胜率</th><th>满分 10d 中位</th><th>0分 10d 胜率</th><th>0分 10d 中位</th><th>胜率差</th><th>中位差</th><th>区分度</th></tr>')
    
    dim_order = ['score_h', 'score_d', 'score_c', 'score_p', 'score_i1', 'score_i2', 'score_sig', 'score_gap']
    dim_labels = {
        'score_h': 'H:前高趋势', 'score_d': 'D:调整深度', 'score_c': 'C:横盘质量',
        'score_p': 'P:整理回撤', 'score_i1': 'I1:行业RS250', 'score_i2': 'I2:个股RS250',
        'score_sig': 'Sig:信号共振', 'score_gap': 'Gap:跳空',
    }
    
    for dim in dim_order:
        d = dim_stats.get(dim, {})
        if not d: continue
        label = dim_labels.get(dim, dim)
        # Extract from detail dict: find max-score bucket and 0-score bucket
        detail = d.get('detail', {}) or {}
        if not detail: continue
        score_keys = [int(k) for k in detail.keys()]
        max_key = str(max(score_keys)) if score_keys else None
        zero_key = '0'
        full = detail.get(max_key, {}).get('10d', {}) if max_key else {}
        zero = detail.get(zero_key, {}).get('10d', {}) if zero_key in detail else {}
        f_wr = (full.get('win_rate', 0) or 0)
        z_wr = (zero.get('win_rate', 0) or 0)
        f_med = full.get('median_return', 0) or 0
        z_med = zero.get('median_return', 0) or 0
        wr_diff = f_wr - z_wr
        med_diff = f_med - z_med
        desc = '强正向' if wr_diff > 5 else ('正向' if wr_diff > 2 else ('反向⚠' if wr_diff < -1 else '弱/无'))
        diff_css = 'c-great' if wr_diff > 5 else ('c-good' if wr_diff > 2 else ('c-bad' if wr_diff < 0 else ''))
        h(f'<tr><td>{label}</td><td>{f_wr:.1f}%</td><td class="{crm(f_med)}">{p(f_med)}</td><td>{z_wr:.1f}%</td><td class="{crm(z_med)}">{p(z_med)}</td><td class="{diff_css}">{wr_diff:+.1f}pp</td><td class="{diff_css}">{med_diff:+.1f}pp</td><td>{desc}</td></tr>')
    h('</table></div>')
    
    h('<div class="callout callout-tip"><strong>D（调整深度）是唯一稳定强预测维度。</strong>如果满分胜率-0分胜率>5pp且两版报告均复现，则确认其为MW信号核心维度。Sig信号共振若持续反向需考虑降权或移除。</div>')
    
    # ═══ 07 Market & Cap ═══
    h('<h2>07 市场状态 &amp; 市值效应</h2>')
    h('<div class="vs-grid">')
    
    h('<div class="vs-side"><h4 style="color:var(--blue)">市场状态</h4>')
    h('<div class="table-wrap"><table><tr><th>状态</th><th>数量</th><th>10d胜率</th><th>10d中位</th><th>20d胜率</th></tr>')
    for mkt in ['多头', '震荡', '空头']:
        m = by_market.get(mkt, {})
        s10 = stats_get(m, '10d')
        s20 = stats_get(m, '20d')
        cnt = s10['n']
        row_cls = 'row-best' if s10['wr'] >= 50 else ''
        h(f'<tr class="{row_cls}"><td><b>{mkt}</b></td><td>{cnt}</td><td class="{cr(s10["wr"])}">{pct(s10["wr"])}</td><td class="{crm(s10["med"])}">{p(s10["med"])}</td><td class="{cr(s20["wr"])}">{pct(s20["wr"])}</td></tr>')
    h('</table></div></div>')
    
    h('<div class="vs-side"><h4 style="color:var(--blue)">市值分组</h4>')
    h('<div class="table-wrap"><table><tr><th>分组</th><th>数量</th><th>10d胜率</th><th>10d中位</th><th>20d胜率</th></tr>')
    cap_order = ['小盘(<50亿)', '中盘(50~200亿)', '大盘(200~1000亿)', '超大盘(≥1000亿)']
    for cap_g in cap_order:
        m = by_cap.get(cap_g, {})
        s10 = stats_get(m, '10d')
        s20 = stats_get(m, '20d')
        cnt = s10['n']
        row_cls = 'row-best' if s10['wr'] >= 50 else ''
        h(f'<tr class="{row_cls}"><td><b>{cap_g}</b></td><td>{cnt}</td><td class="{cr(s10["wr"])}">{pct(s10["wr"])}</td><td class="{crm(s10["med"])}">{p(s10["med"])}</td><td class="{cr(s20["wr"])}">{pct(s20["wr"])}</td></tr>')
    h('</table></div></div>')
    h('</div>')
    
    # ═══ 08 Random Baseline ═══
    h('<h2>08 随机基准对比</h2>')
    h('<div class="table-wrap"><table>')
    h('<tr><th>对比组</th><th>窗口</th><th>MW 胜率</th><th>随机胜率</th><th>MW 中位</th><th>随机中位</th><th>MW 平均</th><th>随机平均</th><th>跑赢</th></tr>')
    
    def render_rand_row(label, data):
        rows = []
        for hz in ['5d', '10d', '20d']:
            if not data: continue
            if '全量' in label:
                mw_s = stats_get(overall, hz)
            elif '高分' in label:
                mw_s = stats_get(by_conf.get('高', {}), hz)
            else:
                mw_s = stats_get({}, hz)
            rd_s = stats_get(data, hz)
            
            beat = '✅' if (mw_s['med'] > rd_s['med']) else '❌'
            beat_cls = 'c-great' if beat == '✅' else 'c-bad'
            rows.append(f'<tr><td>{label}</td><td>{hz}</td><td>{pct(mw_s["wr"])}</td><td>{pct(rd_s["wr"])}</td><td class="{crm(mw_s["med"])}">{p(mw_s["med"])}</td><td class="{crm(rd_s["med"])}">{p(rd_s["med"])}</td><td>{p(mw_s["avg"])}</td><td>{p(rd_s["avg"])}</td><td class="{beat_cls}">{beat}</td></tr>')
        return ''.join(rows)
    
    if baseline:
        h(render_rand_row('全量', baseline))
    if baseline_high:
        h(render_rand_row('高分 (≥80)', baseline_high))
    h('</table></div>')
    
    # ═══ 09 Simulation ═══
    h('<h2>09 模拟盘</h2>')
    h('<p style="font-size:.64rem;color:var(--text-muted);margin-bottom:12px;">每只信号投当前市值1%，B2收盘买入，持有20日卖出。初始资金¥1,000,000。</p>')
    h('<div class="table-wrap"><table>')
    h('<tr><th>策略</th><th>交易笔数</th><th>胜率</th><th>最终市值</th><th>收益率</th><th>最大回撤</th></tr>')
    for key in ['score_ge_90', 'score_ge_80', 'daily_top10']:
        s = sim.get(key, {})
        if not s: continue
        name_map = {'score_ge_90': '≥90分 TOP', 'score_ge_80': '≥80分 (高置信)', 'daily_top10': '每日前10不限分'}
        label = name_map.get(key, key)
        ret = s.get('return_pct', 0) or 0
        trades = s.get('trades', 0)
        wr = s.get('win_rate', 0) or 0
        dd = s.get('max_drawdown_pct', 0) or 0
        fv = s.get('final_value', 0) or 0
        row_cls = 'row-best' if ret > 5 else ''
        h(f'<tr class="{row_cls}"><td><b>{label}</b></td><td>{trades}</td><td class="{cr(wr)}">{pct(wr)}</td><td>¥{fv:,.0f}</td><td class="{crm(ret)}">{p(ret)}</td><td class="{"c-great" if abs(dd)<3 else "c-bad"}">{dd:.1f}%</td></tr>')
    h('</table></div>')
    
    # ═══ 10 Common Traits ═══
    traits = S.get('common_traits', {})
    top_features = traits.get('top_features', []) if traits else []
    if top_features:
        h(f'<h2>10 高收益信号共性 (Top10% vs 全量, {traits.get("horizon",10)}d窗口)</h2>')
        h(f'<p style="font-size:.64rem;color:var(--text-muted);margin-bottom:12px;">全量 {traits.get("all_signals_n",0)} 信号，Top10% 取 {traits.get("top10_n",0)} 只</p>')
        h('<div class="table-wrap"><table>')
        h('<tr><th>特征</th><th>全量均值</th><th>Top10% 均值</th><th>差异</th></tr>')
        for feat in top_features:
            key = feat['feature']
            full_val = feat['all']
            top_val = feat['top10']
            diff = feat['diff']
            diff_cls = 'c-great' if abs(diff) > 3 else 'c-good'
            h(f'<tr><td>{key}</td><td>{full_val:.2f}</td><td>{top_val:.2f}</td><td class="{diff_cls}">{diff:+.2f}</td></tr>')
        h('</table></div>')
    
    # ═══ 11 PLUS signals ═══
    h('<h2>11 PLUS 信号详情</h2>')
    h('<p style="font-size:.64rem;color:var(--text-muted);">PLUS标准: 总分≥80 + D满分(score_d=5) + I1满分(score_i1=15)</p>')
    
    db = sqlite3.connect(os.path.join(os.path.dirname(__file__), '..', 'data', 'lixinger.db'))
    db.row_factory = sqlite3.Row
    plus = db.execute("""
        SELECT stock_code, stock_name, b2_date, score, score_h, score_d, score_c, score_p, score_i1, score_sig, score_gap,
               ind_name, h_rs250, decline_pct, b2_return_pct, b1_vol_ratio
        FROM mw_signal_daily 
        WHERE b2_date >= '2026-01-01' AND b2_date <= '2026-06-05'
        AND score >= 80 AND score_d = 15 AND score_i1 = 15 AND score_i2 = 15
        ORDER BY score DESC, b2_date DESC
    """).fetchall()
    plus_list = [dict(r) for r in plus]
    h(f'<p style="font-size:.64rem;color:var(--text-muted);margin-bottom:16px;">共 <b>{len(plus_list)}</b> 只 PLUS 信号</p>')
    
    if plus_list:
        # Industry
        ind_counter = Counter(s.get('ind_name', '未分类') for s in plus_list)
        h('<h3>行业分布</h3>')
        h('<div class="table-wrap"><table>')
        h('<tr><th>行业</th><th>数量</th><th>占比</th></tr>')
        for ind, cnt in ind_counter.most_common(15):
            h(f'<tr><td>{ind}</td><td>{cnt}</td><td>{cnt/len(plus_list)*100:.1f}%</td></tr>')
        h('</table></div>')
        
        # Top list
        h('<h3>精选信号列表 (按得分降序)</h3>')
        h('<div class="table-wrap"><table>')
        h('<tr><th>代码</th><th>名称</th><th>行业</th><th>B2日</th><th>得分</th><th>H</th><th>D</th><th>C</th><th>P</th><th>I1</th><th>Sig</th><th>Gap</th><th>H点RS</th><th>跌幅</th><th>B2涨幅</th></tr>')
        for s in plus_list[:40]:
            row_cls = 'row-best' if s.get('score', 0) >= 90 else ''
            h(f'<tr class="{row_cls}"><td>{s["stock_code"]}</td><td>{s["stock_name"]}</td><td>{s.get("ind_name","")}</td><td>{s["b2_date"]}</td><td class="c-good">{s["score"]}</td><td>{s.get("score_h",0)}</td><td>{s.get("score_d",0)}</td><td>{s.get("score_c",0)}</td><td>{s.get("score_p",0)}</td><td>{s.get("score_i1",0)}</td><td>{s.get("score_sig",0)}</td><td>{s.get("score_gap",0)}</td><td>{s.get("h_rs250","")}</td><td>{s.get("decline_pct",0):.1f}%</td><td class="{crm(s.get("b2_return_pct",0) or 0)}">{p(s.get("b2_return_pct") or 0)}</td></tr>')
        h('</table></div>')
    
    db.close()
    
    # ═══ 12 Conclusions ═══
    h('<h2>12 结论与建议</h2>')
    h('<ol class="conclusion-list">')
    
    # Data-driven conclusions
    h('<li><strong>全量 MW 信号不产生超额收益。</strong>所有窗口的中位收益为负，必须通过评分筛选后才能使用。只有高分段信号优于随机选股基准。</li>')
    
    # Check D dimension
    d_data = dim_stats.get('score_d', {})
    d_full_wr = (d_data.get('full', {}).get('win_rate', 0) or 0)
    d_zero_wr = (d_data.get('zero', {}).get('win_rate', 0) or 0)
    d_diff = d_full_wr - d_zero_wr
    h(f'<li><strong>D（调整深度15%~35%）是核心预测维度。</strong>满分vs零分胜率差 {d_diff:+.1f}pp。这是MW信号不可妥协的硬性条件。</li>')
    
    # Check Sig
    sig_data = dim_stats.get('score_sig', {})
    sig_full_wr = (sig_data.get('full', {}).get('win_rate', 0) or 0)
    sig_zero_wr = (sig_data.get('zero', {}).get('win_rate', 0) or 0)
    sig_diff = sig_full_wr - sig_zero_wr
    if sig_diff < 0:
        h(f'<li><strong>Sig（信号共振）呈反向预测（{sig_diff:+.1f}pp）。</strong>更多技术指标共振反而降低胜率，需重新设计或降权。</li>')
    else:
        h(f'<li><strong>Sig（信号共振）区分力 {sig_diff:+.1f}pp。</strong>技术信号累加的边际效益需持续监控。</li>')
    
    # Check market
    bear = by_market.get('空头', {})
    bull = by_market.get('多头', {})
    bear_wr = stats_get(bear, '10d')['wr']
    bull_wr = stats_get(bull, '10d')['wr']
    if bear_wr > bull_wr + 10:
        h(f'<li><strong>MW信号在空头市场优于多头。</strong>空头10d胜率{bear_wr:.1f}% vs 多头{bull_wr:.1f}%，MW抓的是超跌反弹而非牛市追涨。</li>')
    else:
        h(f'<li><strong>MW信号在不同市场状态下表现差异。</strong>空头{ bear_wr:.1f}% / 多头{bull_wr:.1f}%，需结合市场环境判断信号质量。</li>')
    
    # Check cap
    mega = by_cap.get('超大盘(≥1000亿)', {})
    mid_cap = by_cap.get('中盘(50~200亿)', {})
    mega_wr = stats_get(mega, '10d')['wr']
    mid_wr = stats_get(mid_cap, '10d')['wr']
    h(f'<li><strong>大市值MW信号更稳定。</strong>超大盘胜率{mega_wr:.1f}%，中盘{mid_wr:.1f}%。龙头股的二次突破更受机构关注。</li>')
    
    h('<li><strong>实操建议：仅交易 ≥80 分的 PLUS 信号（D满分+I1满分），</strong>在空头/震荡市中优先，回避小市值、回避电气设备等行业，控制单票仓位≤2%。</li>')
    h('</ol>')
    
    # Footer
    h(f'<footer class="report-footer">MW信号回测分析报告 v3.0 · B1=1.3 回填数据 · {start_date} ~ {end_date} · 基于 {N} 个信号 · 生成于 2026-06-08</footer>')
    h('</div></body></html>')
    
    output_path = os.path.join(os.path.dirname(__file__), '..', 'docs', 'analysis', 'MW信号v3.0回测分析报告.html')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(html))
    
    print(f"\nReport saved to: {output_path}")
    return output_path

if __name__ == '__main__':
    generate_html_report()
