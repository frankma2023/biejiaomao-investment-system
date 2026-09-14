# -*- coding: utf-8 -*-
"""
回测 3 人工核验页：K 线（未复权，与 czsc 画笔输入一致）+ 笔标记 + 除权日标注

用途：肉眼核验
  1. 笔顶是否落在真实摆动高点
  2. 未复权序列上的除权缺口是否制造了假笔 / 假收敛
  3. 笔的滞后（末笔 edt 距最新交易日）

输出：web/review/cpa-bi-review.html
"""
import sqlite3, os, json, datetime as dt

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')
OUT = os.path.join(PROJECT, 'web', 'review', 'cpa-bi-review.html')
WIN = 300          # 展示最近 N 个交易日

FOCUS = ['002648', '600309', '000338', '601012', '300750', '600875']
EXTRA = ['301178', '000883', '301489', '301026', '688399', '603596']


def load(conn, code):
    kl = conn.execute("""SELECT date, open, high, low, close, volume, change_pct, complex_factor
                         FROM daily_kline WHERE stock_code=? ORDER BY date""", (code,)).fetchall()
    row = conn.execute("""SELECT scan_date, bi_json FROM chanlun_bi_json
                          WHERE stock_code=? ORDER BY scan_date DESC LIMIT 1""", (code,)).fetchone()
    bis = json.loads(row['bi_json']) if row and row['bi_json'] else []
    ex = conn.execute("""SELECT date, complex_factor FROM daily_kline
                         WHERE stock_code=? AND complex_factor IS NOT NULL ORDER BY date""", (code,)).fetchall()
    return kl, bis, row['scan_date'] if row else '', ex


def build(conn, code, name):
    kl, bis, scan, ex = load(conn, code)
    if not kl:
        return None
    kl = kl[-WIN:]
    dates = [r['date'] for r in kl]
    idx = {d: i for i, d in enumerate(dates)}
    # 除权日（窗口内）：complex_factor 非空，或用 change_pct 与环比不一致补齐
    ex_dates = []
    for i, r in enumerate(kl):
        if r['complex_factor'] is not None:
            ex_dates.append((r['date'], 'cf'))
    for i in range(1, len(kl)):
        chg = kl[i]['change_pct']
        if chg is None or not kl[i - 1]['close'] or not kl[i]['close']:
            continue
        cc = chg / 100 if abs(chg) > 1 else chg
        raw = kl[i]['close'] / kl[i - 1]['close'] - 1
        gap = raw - cc
        if abs(gap) > 0.02:
            d = kl[i]['date']
            if not any(x[0] == d for x in ex_dates):
                ex_dates.append((d, 'chg'))
    ex_in = [d for d, _ in ex_dates if d in idx]

    ohlc, vol = [], []
    for r in kl:
        ohlc.append([r['open'], r['close'], r['low'], r['high']])
        vol.append(r['volume'])
    # 笔（窗口内）
    pts, tops, bots = [], [], []
    win_bi = [b for b in bis if (b.get('edt') or '')[:10] in idx or (b.get('sdt') or '')[:10] in idx]
    for b in win_bi:
        s, e = (b['sdt'] or '')[:10], (b['edt'] or '')[:10]
        up = b.get('direction') == '向上'
        if s in idx:
            pts.append((s, b['low'] if up else b['high']))
        if e in idx:
            pts.append((e, b['high'] if up else b['low']))
        if e in idx:
            if up:
                tops.append((e, b['high']))
            else:
                bots.append((e, b['low']))
    pts = [p for p in pts if p[0] in idx]
    # 去重相邻同点
    ded = []
    for p in pts:
        if ded and ded[-1] == p:
            continue
        ded.append(p)
    line = [[idx[d], v] for d, v in ded]

    last = dates[-1]
    last_edt = (bis[-1]['edt'] or '')[:10] if bis else ''
    lag = '—'
    if last_edt:
        try:
            lag = (dt.date.fromisoformat(last) - dt.date.fromisoformat(last_edt)).days
        except Exception:
            pass
    return dict(code=code, name=name, dates=dates, ohlc=ohlc, vol=vol,
                line=line, tops=tops, bots=bots, ex=ex_in,
                scan=scan, n_bi=len(bis), n_win=len(win_bi), last=last,
                last_edt=last_edt, lag=lag)


def names(conn, codes):
    m = {}
    try:
        for r in conn.execute("SELECT stock_code, name FROM stock_basic"):
            m[r['stock_code']] = r['name']
    except Exception:
        pass
    return {c: m.get(c, c) for c in codes}


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    codes = FOCUS + EXTRA
    nm = names(conn, codes)
    panels = []
    for c in codes:
        d = build(conn, c, nm.get(c, c))
        if d:
            panels.append(d)
        else:
            print('  跳过', c, flush=True)
    print('面板数', len(panels), flush=True)

    html = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
            '<title>CPA 回测 3 · 缠论笔序人工核验</title>',
            '<script src="../shared/js/echarts.min.js"></script>',
            '<style>',
            'body{background:#141416;color:#e8e8ea;font-family:-apple-system,"Segoe UI","Noto Sans SC",sans-serif;margin:0;padding:20px}',
            'h1{font-size:18px;margin:0 0 6px}h2{font-size:14px;margin:22px 0 6px;color:#cfcfd4}',
            '.meta{font-size:12px;color:#8a8a92;margin-bottom:14px;line-height:1.7}',
            '.card{background:#1c1c20;border:1px solid #2a2a30;border-radius:10px;padding:12px;margin-bottom:16px}',
            '.chart{height:400px}',
            '.kv{font-size:12px;color:#9a9aa2;margin:8px 0 0;line-height:1.8}',
            'table{border-collapse:collapse;font-size:12px;margin-top:8px;width:100%}',
            'th,td{border-bottom:1px solid #2a2a30;padding:3px 8px;text-align:left}',
            'th{color:#8a8a92;font-weight:500}',
            '.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;margin-right:4px}',
            '.warn{background:#3a2418;color:#e8a06a}.ok{background:#16302a;color:#4ecb9a}',
            '</style></head><body>']
    html.append('<h1>CPA 回测 3 · 缠论笔序人工核验</h1>')
    html.append('<div class="meta">'
                'K 线为<b>未复权</b>原始价（与 <code>chanlun.py</code> 画笔输入完全一致）｜'
                '红涨绿跌｜<span style="color:#ef4444">▼ 笔顶</span> <span style="color:#10b981">▲ 笔底</span>｜'
                '橙色竖线 = 除权日（未复权序列的缺口来源）<br>'
                '核验重点：笔顶是否落在真实摆动高点；除权缺口是否在缺口两侧制造了假笔顶/假收敛；末笔滞后。</div>')

    for p in panels:
        html.append('<div class="card">')
        html.append('<h2>%s %s</h2>' % (p['code'], p['name']))
        warn = 'warn' if p['ex'] else 'ok'
        html.append('<div class="kv">快照 %s ｜ 全序列 %d 笔（窗口内 %d）｜ 末笔 edt %s ｜ 滞后 <span class="tag %s">%s 天</span> ｜ 窗口内除权 %d 次</div>'
                    % (p['scan'], p['n_bi'], p['n_win'], p['last_edt'], warn, p['lag'], len(p['ex'])))
        html.append('<div class="chart" id="c_%s"></div>' % p['code'])
        if p['ex']:
            html.append('<div class="kv">除权日：%s</div>' % '、'.join(p['ex']))
        # 笔顶表
        if p['tops']:
            html.append('<table><tr><th>窗口内笔顶</th><th>价格</th><th>窗口内笔底</th><th>价格</th></tr>')
            m = max(len(p['tops']), len(p['bots']))
            for i in range(m):
                t = p['tops'][i] if i < len(p['tops']) else ('', '')
                b = p['bots'][i] if i < len(p['bots']) else ('', '')
                html.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                            % (t[0], t[1], b[0], b[1]))
            html.append('</table>')
        html.append('</div>')

    html.append('<script>')
    html.append('var DATA=%s;' % json.dumps(panels, ensure_ascii=False))
    html.append('''
DATA.forEach(function(p){
  var el=document.getElementById('c_'+p.code); if(!el) return;
  var ch=echarts.init(el);
  var exLines=p.ex.map(function(d){
    return {xAxis:d, lineStyle:{color:'#e8a06a',type:'dashed',width:1},
            label:{show:false}};
  });
  var marks=[];
  p.tops.forEach(function(t){marks.push({name:'顶',coord:[t[0],t[1]],symbol:'triangle',symbolSize:9,
     itemStyle:{color:'#ef4444'},symbolRotate:180,label:{show:false}});});
  p.bots.forEach(function(t){marks.push({name:'底',coord:[t[0],t[1]],symbol:'triangle',symbolSize:9,
     itemStyle:{color:'#10b981'},label:{show:false}});});
  ch.setOption({
    backgroundColor:'transparent',
    animation:false,
    grid:{left:52,right:16,top:20,bottom:56},
    tooltip:{trigger:'axis',axisPointer:{type:'cross'},
      backgroundColor:'rgba(28,28,32,.95)',borderColor:'#3a3a42',textStyle:{color:'#e8e8ea',fontSize:11}},
    xAxis:{type:'category',data:p.dates,axisLine:{lineStyle:{color:'#3a3a42'}},
      axisLabel:{color:'#8a8a92',fontSize:10},splitLine:{show:false}},
    yAxis:{scale:true,axisLine:{lineStyle:{color:'#3a3a42'}},axisLabel:{color:'#8a8a92',fontSize:10},
      splitLine:{lineStyle:{color:'#232328'}}},
    dataZoom:[{type:'inside'},{type:'slider',height:16,bottom:12,
      backgroundColor:'#1c1c20',borderColor:'#2a2a30',textStyle:{color:'#8a8a92',fontSize:10},
      handleStyle:{color:'#4a4a52'},fillerColor:'rgba(90,90,110,.18)'}],
    series:[
      {type:'candlestick',name:'K线',data:p.ohlc,
       itemStyle:{color:'#ef4444',color0:'#10b981',borderColor:'#ef4444',borderColor0:'#10b981'},
       markLine:{symbol:'none',silent:true,data:exLines}},
      {type:'line',name:'笔',data:p.line,symbol:'circle',symbolSize:5,
       lineStyle:{color:'#f0c674',width:1.6},itemStyle:{color:'#f0c674'},
       xAxisIndex:0,yAxisIndex:0,encode:{x:0,y:1}},
      {type:'scatter',name:'笔端点',data:marks,silent:true}
    ]
  });
  window.addEventListener('resize',function(){ch.resize();});
});
''')
    html.append('</script></body></html>')
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(html))
    print('生成 ->', OUT, flush=True)
    conn.close()


if __name__ == '__main__':
    main()
