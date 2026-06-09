import sqlite3,sys,os
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..','src'))

# inline calc_stats to avoid import issue
def calc_s(rets):
    valid=[r for r in rets if r is not None]
    if not valid:return{'wr':0,'med':0,'avg':0}
    return{'wr':round(sum(1 for v in valid if v>2)/len(valid)*100,1),
           'med':round(sorted(valid)[len(valid)//2],2),
           'avg':round(sum(valid)/len(valid),2)}

db=sqlite3.connect("D:/hanako/investment-system/data/lixinger.db");db.row_factory=sqlite3.Row
plus_pp=db.execute("""
    SELECT * FROM mw_signal_daily WHERE b2_date>='2023-06-01' AND b2_date<='2026-06-05'
    AND score>=80 AND score_d=15 AND score_i1=15 AND score_i2=15
    AND stock_code IN ('002428','300666','688295','688387','000657') ORDER BY b2_date
""").fetchall()

pc={}
for code in set(p['stock_code'] for p in plus_pp):
    rows=db.execute("SELECT date,open,close FROM daily_kline WHERE stock_code=? AND date>='2023-01-01' AND date<='2026-07-31' ORDER BY date",(code,)).fetchall()
    pc[code]={'dates':[r['date'] for r in rows],'prices':{r['date']:{'o':r['open'],'c':r['close']} for r in rows}}

def nth(dates,base,n):
    try:i=dates.index(base);t=i+n
    except:return None
    return dates[t] if t<len(dates) else None

ret={5:[],10:[],20:[],30:[],60:[]}
for p in plus_pp:
    code=p['stock_code'];b2=p['b2_date']
    d=pc[code]['dates'];pr=pc[code]['prices']
    ed=nth(d,b2,2)
    if not ed or ed not in pr:continue
    ep=pr[ed]['o']
    if ep<=0:continue
    try:i=d.index(ed)
    except:continue
    for h in [5,10,20,30,60]:
        f=i+h
        if f<len(d):ret[h].append((pr[d[f]]['c']-ep)/ep*100)

def pf(v):return f"{v:+.1f}%" if v is not None else "—"
def pctf(v):return f"{v:.1f}%" if v is not None else "—"
def crf(v,g=50,b=40):
    if v is None:return'c-muted'
    if v>=g:return'c-great'
    if v<b:return'c-bad'
    return'c-good'

section='''
<h2>07 PLUS × 口袋支点 双重确认 · B2+2日买入</h2>
<p style="font-size:.64rem;color:var(--text-muted);margin-bottom:12px;">22只PLUS信号中，仅5只在B1~B2区间内有口袋支点覆盖。这5只视为"双重确认"信号。</p>

<div class="table-wrap"><table>
<tr><th>代码</th><th>名称</th><th>B1</th><th>B2</th><th>得分</th><th>跌幅</th></tr>
'''
for p in plus_pp:
    section+=f'<tr><td>{p["stock_code"]}</td><td>{p["stock_name"]}</td><td>{p["b1_date"]}</td><td>{p["b2_date"]}</td><td class="c-good">{p["score"]}</td><td>{p["decline_pct"]:.1f}%</td></tr>\n'
section+='</table></div>\n'

section+='<h3>B2+2日买入 · 持有收益</h3>'
section+='<div class="table-wrap"><table><tr><th>窗口</th><th>有效笔数</th><th>胜率</th><th>中位</th><th>平均</th></tr>\n'
for h in [5,10,20,30,60]:
    r=[v for v in ret[h] if v is not None]
    if r:
        s=calc_s(r)
        section+=f'<tr><td><b>{h}日</b></td><td>{len(r)}</td><td class="{crf(s["wr"])}">{pctf(s["wr"])}</td><td>{pf(s["med"])}</td><td>{pf(s["avg"])}</td></tr>\n'
section+='</table></div>\n'

section+='''
<div class="callout callout-note"><strong>⚠ 仅5个样本，统计意义有限。</strong>但方向明确：B1~B2之间有口袋支点覆盖的PLUS信号，20日以上持有回报可能非常可观。10日短期可能受个别信号拖累而表现不佳。需要更多样本验证。</div>
'''

rpt="D:/hanako/investment-system/docs/analysis/高置信度口袋支点回测报告.html"
with open(rpt,'r',encoding='utf-8')as f:html=f.read()
pos=html.find('</div></body></html>')
with open(rpt,'w',encoding='utf-8')as f:f.write(html[:pos]+section)
print("Appended section 07.")
db.close()
