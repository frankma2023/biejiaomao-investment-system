"""
PLUS信号 B2+2买入回测 → 追加到报告
"""
import sqlite3, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from analytics.mw_backtest import calc_stats
from collections import defaultdict
from datetime import datetime

DB = "D:/hanako/investment-system/data/lixinger.db"
db = sqlite3.connect(DB); db.row_factory = sqlite3.Row

# PLUS signals (score>=80, D=15, I1=15, I2=15)
plus = db.execute("""
    SELECT * FROM mw_signal_daily
    WHERE b2_date >= '2023-06-01' AND b2_date <= '2026-06-05'
    AND score >= 80 AND score_d = 15 AND score_i1 = 15 AND score_i2 = 15
    ORDER BY b2_date
""").fetchall()
print(f"PLUS signals: {len(plus)}")

# Load K-line
codes = list(set(p['stock_code'] for p in plus))
pc = {}
for code in codes:
    rows = db.execute("SELECT date, open, close FROM daily_kline WHERE stock_code=? AND date >= '2023-01-01' AND date <= '2026-07-31' ORDER BY date", (code,)).fetchall()
    pc[code] = {'dates': [r['date'] for r in rows], 'prices': {r['date']: {'o': r['open'], 'c': r['close']} for r in rows}}

def find_nth_day(dates, base_date, n):
    try: idx = dates.index(base_date)
    except: return None
    t = idx + n
    if t >= len(dates): return None
    return dates[t]

# B2+2 entry
results = {5:[],10:[],20:[]}
trades = []
for p in plus:
    code = p['stock_code']; b2 = p['b2_date']
    dates = pc[code]['dates']; prices = pc[code]['prices']
    
    entry_date = find_nth_day(dates, b2, 2)
    if not entry_date or entry_date not in prices: continue
    entry_price = prices[entry_date]['o']
    if entry_price <= 0: continue
    
    trade = {'code': code, 'name': p['stock_name'], 'b2': b2, 'entry': entry_date,
             'score': p['score'], 'decline': p['decline_pct']}
    
    try: idx = dates.index(entry_date)
    except: continue
    for h in [5,10,20]:
        fut = idx + h
        if fut < len(dates):
            r = (prices[dates[fut]]['c'] - entry_price) / entry_price * 100
            results[h].append(r)
            trade[f'r{h}'] = r
    
    if all(trade.get(f'r{h}') is not None for h in [5,10,20]):
        trades.append(trade)

print(f"Valid trades: {len(trades)}")

s5 = calc_stats(results[5]); s10 = calc_stats(results[10]); s20 = calc_stats(results[20])

# Kelly-relevant metrics
wins_10 = [v for v in results[10] if v > 0]
losses_10 = [v for v in results[10] if v <= 0]
avg_win = sum(wins_10)/len(wins_10) if wins_10 else 0
avg_loss = sum(losses_10)/len(losses_10) if losses_10 else 0
win_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
p_win = s10['win_rate'] / 100
q_lose = 1 - p_win
kelly_full = (win_loss_ratio * p_win - q_lose) / win_loss_ratio if win_loss_ratio > 0 else 0

print(f"10d: WR={s10['win_rate']:.1f}% median={s10['median_return']:+.2f}% avg={s10['avg_return']:+.2f}%")
print(f"Kelly: avg_win={avg_win:+.2f}% avg_loss={avg_loss:+.2f}% ratio={win_loss_ratio:.1f}x f_full={kelly_full:.1%} f_half={kelly_full/2:.1%}")

# Monthly
monthly = defaultdict(lambda: {'n':0,'wins':0})
for t in trades:
    m = t['b2'][:7]
    monthly[m]['n'] += 1
    if t.get('r10') and t['r10'] > 0: monthly[m]['wins'] += 1

# Generate HTML section
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

section = f"""
<h2>06 MW PLUS信号 · B2+2日买入回测</h2>
<p style="font-size:.64rem;color:var(--text-muted);margin-bottom:12px;">PLUS标准: score≥80 ∧ D满分 ∧ I1满分 ∧ I2满分。B2日后第2个交易日开盘买入。用于凯利仓位计算。</p>

<div class="insight-grid">
<div class="insight-card"><div class="label">PLUS信号数</div><div class="value" style="color:var(--accent)">{len(plus)}</div><div class="detail">3年全库 · {len(trades)}笔有效交易</div></div>
<div class="insight-card"><div class="label">10d 胜率</div><div class="value" style="color:var(--green)">{pct(s10['win_rate'])}</div><div class="detail">中位 {p(s10['median_return'])} · 平均{p(s10['avg_return'])}</div></div>
<div class="insight-card"><div class="label">平均盈利</div><div class="value" style="color:var(--green)">{p(avg_win)}</div><div class="detail">平均亏损 {p(avg_loss)} · 盈亏比{win_loss_ratio:.1f}x</div></div>
<div class="insight-card"><div class="label">20d 胜率</div><div class="value" style="color:var(--green)">{pct(s20['win_rate'])}</div><div class="detail">中位 {p(s20['median_return'])} · 平均{p(s20['avg_return'])}</div></div>
<div class="insight-card"><div class="label">满凯利仓位</div><div class="value" style="color:var(--accent)">{kelly_full:.0%}</div><div class="detail">半凯利 {kelly_full/2:.0%} · 四分之一凯利 {kelly_full/4:.0%}</div></div>
<div class="insight-card"><div class="label">5d 胜率</div><div class="value" style="color:var(--green)">{pct(s5['win_rate'])}</div><div class="detail">中位 {p(s5['median_return'])}</div></div>
</div>

<h3>三窗口对比</h3>
<div class="table-wrap"><table>
<tr><th>窗口</th><th>笔数</th><th>胜率</th><th>中位</th><th>平均</th><th>最大盈利</th><th>最大亏损</th></tr>
"""
for h, label in [(5,'5日'),(10,'10日'),(20,'20日')]:
    r = results[h]; s = calc_stats(r)
    mmax = max(r) if r else 0; mmin = min(r) if r else 0
    section += f'<tr><td><b>{label}</b></td><td>{len(r)}</td><td class="{cr(s["win_rate"])}">{pct(s["win_rate"])}</td><td class="{crm(s["median_return"])}">{p(s["median_return"])}</td><td>{p(s["avg_return"])}</td><td class="c-great">{p(mmax)}</td><td class="c-bad">{p(mmin)}</td></tr>\n'

section += '</table></div>\n'

# Kelly table
section += f"""
<h3>凯利仓位计算参数 (10日持有)</h3>
<div class="table-wrap"><table>
<tr><th>参数</th><th>值</th><th>说明</th></tr>
<tr><td>胜率 p</td><td class="c-good">{p_win:.1%}</td><td>10d 盈利概率</td></tr>
<tr><td>败率 q</td><td>{q_lose:.1%}</td><td>1 − p</td></tr>
<tr><td>平均盈利</td><td class="c-great">{p(avg_win)}</td><td>盈利交易的平均收益</td></tr>
<tr><td>平均亏损</td><td class="c-bad">{p(avg_loss)}</td><td>亏损交易的平均损失</td></tr>
<tr><td>盈亏比 b</td><td class="c-good">{win_loss_ratio:.1f}x</td><td>|平均盈利 / 平均亏损|</td></tr>
<tr class="row-best"><td><b>满凯利 f</b></td><td class="c-great"><b>{kelly_full:.1%}</b></td><td>f = (b×p − q) / b</td></tr>
<tr><td>半凯利 f/2</td><td class="c-good">{kelly_full/2:.1%}</td><td>实操推荐（参数有误差）</td></tr>
<tr><td>四分之一凯利 f/4</td><td>{kelly_full/4:.1%}</td><td>保守（新策略/不确定环境）</td></tr>
</table></div>
"""

# Monthly
section += '<h3>月度分布</h3><div class="table-wrap"><table><tr><th>月份</th><th>笔数</th><th>10d胜率</th></tr>\n'
for m in sorted(monthly):
    d = monthly[m]
    wr = d['wins']/d['n']*100 if d['n'] else 0
    cls = 'row-best' if wr >= 70 else ('c-bad' if wr < 30 else '')
    section += f'<tr class="{cls}"><td><b>{m}</b></td><td>{d["n"]}</td><td class="{cr(wr)}">{pct(wr)}</td></tr>\n'
section += '</table></div>\n'

section += f"""
<div class="callout callout-info"><strong>凯利公式实操建议：</strong>PLUS B2+2 策略的满凯利仓位为 {kelly_full:.0%}，但这是基于 {len(trades)} 笔交易的估算。实操中应使用<b>半凯利（{kelly_full/2:.0%}）</b>。如果是新策略或市场环境不确定，用<b>四分之一凯利（{kelly_full/4:.0%}）</b>。注意：凯利公式假设每次交易独立——PLUS 信号高度集中在 4 月，且存在行业重叠，实际仓位应进一步打折。</div>
"""

# Append to report
report_path = os.path.join(os.path.dirname(__file__), '..', 'docs', 'analysis', '高置信度口袋支点回测报告.html')
with open(report_path, 'r', encoding='utf-8') as f:
    html = f.read()

insert_pos = html.find('</div></body></html>')
new_html = html[:insert_pos] + section

with open(report_path, 'w', encoding='utf-8') as f:
    f.write(new_html)

print(f"已追加到报告: {len(new_html)} bytes")
db.close()
