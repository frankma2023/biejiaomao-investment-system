"""缠论批量扫描 — 对观察池+精选池股票进行缠论分析并写入快照表

每天盘后运行，为缠论扫描看板提供数据。

使用方式:
  python src/scanners/chanlun_scan.py                  # 扫描当日
  python src/scanners/chanlun_scan.py --date 2026-05-29 # 指定日期
"""

import sqlite3, sys, os, json
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'lixinger.db')


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chanlun_scan_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            bi_count INTEGER DEFAULT 0,
            zs_count INTEGER DEFAULT 0,
            segment_count INTEGER DEFAULT 0,
            latest_bi_dir TEXT,
            latest_bi_power REAL,
            divergence_count INTEGER DEFAULT 0,
            latest_div_type TEXT,
            trade_signal_count INTEGER DEFAULT 0,
            latest_trade_type TEXT,
            latest_trade_side TEXT,
            latest_trade_price REAL,
            resonance_strength TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(scan_date, stock_code)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chanlun_bi_json (
            stock_code TEXT NOT NULL, scan_date TEXT NOT NULL,
            bi_json TEXT, PRIMARY KEY(stock_code, scan_date)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_chanlun_scan_date 
        ON chanlun_scan_daily(scan_date)
    """)
    # 加 bi_json 列（如果旧表没有）
    try: conn.execute("ALTER TABLE chanlun_scan_daily ADD COLUMN bi_json TEXT")
    except: pass
    conn.commit()


def get_target_stocks(conn, all_market=False):
    """获取待扫描的股票代码
    
    两种模式：
    - 默认模式（all_market=False）：观察池 + 精选池股票（~300-500只），用于缠论扫描看板
    - 全市场模式（all_market=True）：全A股过滤后的股票（~4500只），用于MW信号引擎等下游模块
      ─ 过滤规则：排除ST/*ST、近20日日均成交额 < 5000万元
    """
    stocks = {}
    
    if all_market:
        # 全市场模式：所有正常上市 + 流动性过滤
        rows = conn.execute("""
            SELECT DISTINCT k.stock_code, b.name
            FROM daily_kline k
            INNER JOIN stock_basic b ON k.stock_code=b.stock_code
            WHERE b.listing_status='normally_listed'
            AND b.name NOT LIKE '%ST%'
            AND k.date >= date('now','-20 days')
            GROUP BY k.stock_code
            HAVING AVG(k.amount) >= 50000000
        """).fetchall()
        for r in rows:
            if r[0]: stocks[r[0]] = r[1] or r[0]
        return [(code, name) for code, name in sorted(stocks.items())]
    
    # 默认模式：观察池 + 精选池
    try:
        rows = conn.execute(
            "SELECT DISTINCT stock_code, stock_name FROM discipline_observation_pool "
            "WHERE date=(SELECT MAX(date) FROM discipline_observation_pool)"
        ).fetchall()
        for r in rows:
            if r[0]:
                stocks[r[0]] = r[1] or r[0]
    except Exception as e:
        print(f"  观察池读取异常: {e}")
    
    try:
        rows = conn.execute(
            "SELECT DISTINCT stock_code FROM discipline_screening_daily "
            "WHERE date=(SELECT MAX(date) FROM discipline_screening_daily)"
        ).fetchall()
        for r in rows:
            if r[0] and r[0] not in stocks:
                stocks[r[0]] = r[0]
    except Exception as e:
        print(f"  精选池读取异常: {e}")
    
    return [(code, name) for code, name in sorted(stocks.items())]


def get_stock_name(conn, code):
    """获取股票名称"""
    try:
        row = conn.execute(
            "SELECT stock_name FROM daily_kline WHERE stock_code=? LIMIT 1", (code,)
        ).fetchone()
        return row[0] if row else code
    except Exception:
        return code


def scan_stock(code, scan_date):
    """对单只股票执行缠论分析并提取摘要
    
    Returns:
        dict: 扫描结果，失败返回 None
    """
    from scanners.chanlun import analyze
    
    try:
        r = analyze(code, "D", 400, data_mode="stock", end_date=scan_date)
        if r.get("error"):
            return None
        
        # 最新笔
        bi_list = r.get("bi_list", [])
        latest_bi_dir = None
        latest_bi_power = None
        if bi_list:
            last_bi = bi_list[-1]
            latest_bi_dir = str(last_bi.get("direction", ""))
            latest_bi_power = round(float(last_bi.get("power", 0)), 1)
        
        # 最新背驰信号
        div_list = r.get("divergence_signals", [])
        latest_div_type = None
        if div_list:
            latest_div_type = div_list[0].get("type", "")
        
        # 最新买卖信号
        trade_list = r.get("trade_signals", [])
        latest_trade_type = None
        latest_trade_side = None
        latest_trade_price = None
        if trade_list:
            ts = trade_list[0]
            latest_trade_type = ts.get("type", "")
            latest_trade_side = ts.get("side", "")
            latest_trade_price = ts.get("price", None)
        
        return {
            "scan_date": scan_date,
            "stock_code": code,
            "bi_count": r.get("bi_count", 0),
            "zs_count": r.get("zs_count", 0),
            "segment_count": r.get("segment_count", 0),
            "latest_bi_dir": latest_bi_dir,
            "latest_bi_power": latest_bi_power,
            "divergence_count": r.get("divergence_count", 0),
            "latest_div_type": latest_div_type,
            "trade_signal_count": r.get("trade_signal_count", 0),
            "latest_trade_type": latest_trade_type,
            "latest_trade_side": latest_trade_side,
            "latest_trade_price": latest_trade_price,
            "resonance_strength": None,
            "bi_json": json.dumps(r.get("bi_list", []), ensure_ascii=False)
        }
    except Exception as e:
        print(f"  {code} 扫描异常: {e}")
        return None


def run_scan(scan_date=None, all_market=False):
    """执行批量扫描
    
    Args:
        scan_date: 扫描日期，默认今天
        all_market: True=全市场过滤后股票，False=观察池+精选池（默认）
    """
    if scan_date is None:
        scan_date = datetime.now().strftime("%Y-%m-%d")
    
    conn = _connect()
    _ensure_table(conn)
    
    stocks = get_target_stocks(conn, all_market=all_market)
    if not stocks:
        print("无待扫描股票")
        conn.close()
        return
    
    mode = "全市场(已过滤)" if all_market else "观察池+精选池"
    print(f"缠论批量扫描: {scan_date}, {mode}, 共 {len(stocks)} 只股票")
    
    scanned = 0
    has_signal = 0
    for i, (code, name) in enumerate(stocks):
        result = scan_stock(code, scan_date)
        
        if result:
            result["stock_name"] = name
            conn.execute("""
                INSERT OR REPLACE INTO chanlun_scan_daily 
                (scan_date, stock_code, stock_name, bi_count, zs_count, segment_count,
                 latest_bi_dir, latest_bi_power, divergence_count, latest_div_type,
                 trade_signal_count, latest_trade_type, latest_trade_side, latest_trade_price,
                 resonance_strength)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result["scan_date"], result["stock_code"], result["stock_name"],
                result["bi_count"], result["zs_count"], result["segment_count"],
                result["latest_bi_dir"], result["latest_bi_power"],
                result["divergence_count"], result["latest_div_type"],
                result["trade_signal_count"], result["latest_trade_type"],
                result["latest_trade_side"], result["latest_trade_price"],
                result["resonance_strength"]
            ))
            if result.get("bi_json"):
                conn.execute(
                    "INSERT OR REPLACE INTO chanlun_bi_json (stock_code, scan_date, bi_json) VALUES (?,?,?)",
                    (result["stock_code"], result["scan_date"], result["bi_json"])
                )
            scanned += 1
            if result["trade_signal_count"] > 0 or result["divergence_count"] > 0:
                has_signal += 1
        
        if (i + 1) % 20 == 0:
            print(f"  进度: {i+1}/{len(stocks)} ({scanned} 成功, {has_signal} 有信号)")
    
    conn.commit()
    conn.close()
    
    print(f"完成: {scanned}/{len(stocks)} 成功, {has_signal} 只有信号")
    
    # 生成 HTML 快照
    html_path = generate_html(scan_date)
    if html_path:
        print(f"HTML 快照: {html_path}")


def generate_html(scan_date=None):
    """生成缠论扫描看板 HTML 快照
    
    Args:
        scan_date: 扫描日期
    
    Returns:
        str: HTML 文件路径
    """
    if scan_date is None:
        scan_date = datetime.now().strftime("%Y-%m-%d")
    
    conn = _connect()
    rows = conn.execute("""
        SELECT stock_code, stock_name, bi_count, zs_count, segment_count,
               latest_bi_dir, latest_bi_power,
               divergence_count, latest_div_type,
               trade_signal_count, latest_trade_type, latest_trade_side, latest_trade_price
        FROM chanlun_scan_daily
        WHERE scan_date = ?
        ORDER BY trade_signal_count DESC, divergence_count DESC
    """, (scan_date,)).fetchall()
    conn.close()
    
    if not rows:
        print(f"  {scan_date} 无扫描数据，跳过 HTML 生成")
        return None
    
    # 构建 JSON 数据
    import json
    items = []
    for r in rows:
        items.append({
            "code": r[0], "name": r[1] or r[0],
            "bi": r[2], "zs": r[3], "seg": r[4],
            "bi_dir": r[5] or "",
            "bi_power": round(r[6], 1) if r[6] else 0,
            "div_cnt": r[7], "div_type": r[8] or "",
            "sig_cnt": r[9], "sig_type": r[10] or "", "sig_side": r[11] or "", "sig_price": r[12]
        })
    
    data_json = json.dumps(items, ensure_ascii=False)
    
    # 确定输出路径
    project_root = os.path.join(os.path.dirname(__file__), '..', '..')
    web_dir = os.path.join(project_root, 'web', 'chanlun-scan')
    os.makedirs(web_dir, exist_ok=True)
    html_path = os.path.join(web_dir, 'index.html')
    
    html = _build_html(data_json, scan_date, len(items),
                       sum(1 for it in items if it["sig_cnt"] > 0 or it["div_cnt"] > 0))
    
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    return html_path


def _build_html(data_json, scan_date, total, has_signal):
    """构建完整的 HTML 页面"""
    return f'''<!DOCTYPE html>
<html lang="zh-CN" class="dark">
<head>
<meta charset="UTF-8">
<link rel="icon" href="../images/favicon.svg" type="image/svg+xml">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>缠论扫描</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="../shared/css/hanako-glass.css">
<style>
  body{{font-family:var(--font-body);background:var(--bg);color:var(--text-primary)}}
  .page-header{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px}}
  .page-header h1{{font-family:var(--font-display);font-size:1.2rem;font-weight:400;color:var(--text-primary)}}
  .page-header .meta{{font-size:0.65rem;color:var(--muted)}}
  html[data-theme="light"] .page-header h1{{color:#374151}}
  .filter-bar{{display:flex;gap:6px;align-items:center;padding:8px 0;margin-bottom:6px;flex-wrap:wrap}}
  .filter-bar .fl{{font-size:0.62rem;font-weight:600;color:var(--muted);margin-right:2px}}
  .filter-bar .fb{{padding:3px 10px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text-secondary);font-size:0.62rem;cursor:pointer;transition:all .15s;font-family:var(--font-body)}}
  .filter-bar .fb:hover{{border-color:var(--accent);color:var(--accent)}}
  .filter-bar .fb.active{{background:var(--accent-subtle);color:var(--accent);border-color:var(--accent-border);font-weight:700}}
  .table-shell{{background:var(--card);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid var(--border);border-radius:14px;overflow:hidden;box-shadow:var(--shadow-card)}}
  .cs-table{{width:100%;border-collapse:collapse;font-size:12px}}
  .cs-table thead{{position:sticky;top:0;z-index:5}}
  .cs-table th{{text-align:center;padding:9px 4px;border-bottom:1px solid var(--border);font-size:10px;font-weight:500;color:var(--muted);cursor:pointer;user-select:none;white-space:nowrap;background:var(--card);backdrop-filter:blur(10px);text-transform:uppercase;letter-spacing:.04em}}
  .cs-table th:hover{{color:var(--text-primary)}}
  .cs-table td{{padding:8px 4px;border-bottom:1px solid rgba(255,255,255,.03);text-align:center;font-weight:300;font-variant-numeric:tabular-nums}}
  .cs-table tbody tr:hover{{background:rgba(255,255,255,.02)}}
  .cs-table a{{color:var(--accent);text-decoration:none;font-weight:500}}
  .cs-table a:hover{{text-decoration:underline}}
  .tag{{display:inline-block;padding:2px 6px;border-radius:5px;font-size:10px;font-weight:500}}
  .tag-buy{{background:rgba(239,68,68,.08);color:#ef4444;border:1px solid rgba(239,68,68,.15)}}
  .tag-sell{{background:rgba(16,185,129,.08);color:#10b981;border:1px solid rgba(16,185,129,.15)}}
  .tag-div-b{{background:rgba(239,68,68,.06);color:#ef4444;border:1px solid rgba(239,68,68,.1)}}
  .tag-div-t{{background:rgba(16,185,129,.06);color:#10b981;border:1px solid rgba(16,185,129,.1)}}
  .tag-none{{color:var(--muted);font-size:10px}}
  html[data-theme="light"] .table-shell{{background:rgba(255,255,255,.7);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(0,0,0,.06)}}
  html[data-theme="light"] .cs-table th{{background:rgba(255,255,255,.9);color:#6b7280;border-bottom:1px solid rgba(0,0,0,.06)}}
  html[data-theme="light"] .cs-table td{{border-bottom:1px solid rgba(0,0,0,.03)}}
  html[data-theme="light"] .cs-table tbody tr:hover{{background:rgba(0,0,0,.02)}}
  html[data-theme="light"] .cs-table a{{color:#d97706}}
  html[data-theme="light"] .filter-bar .fb{{background:rgba(255,255,255,.6)}}
</style>
</head>
<body>
<div class="app-container">
<nav id="top-nav"></nav>
<script src="../shared/js/nav.js"></script>
<script>Nav.init({{brandIcon:'🎋',brandText:'缠论扫描',currentPage:'chanlun-scan'}})</script>

<div class="page-header">
  <h1>🎋 缠论批量扫描</h1>
  <div class="meta">扫描日期: {scan_date} | 股票数: {total} | 有信号: {has_signal}</div>
</div>

<div class="filter-bar">
  <span class="fl">信号:</span>
  <button class="fb" data-g="sig" data-v="" onclick="filter('sig','')">全部</button>
  <button class="fb" data-g="sig" data-v="一买" onclick="filter('sig','一买')">一买</button>
  <button class="fb" data-g="sig" data-v="一卖" onclick="filter('sig','一卖')">一卖</button>
  <button class="fb" data-g="sig" data-v="三买" onclick="filter('sig','三买')">三买</button>
  <button class="fb" data-g="sig" data-v="三卖" onclick="filter('sig','三卖')">三卖</button>
  <span class="fl" style="margin-left:8px">背驰:</span>
  <button class="fb" data-g="div" data-v="" onclick="filter('div','')">全部</button>
  <button class="fb" data-g="div" data-v="底背驰" onclick="filter('div','底背驰')">底背驰</button>
  <button class="fb" data-g="div" data-v="顶背驰" onclick="filter('div','顶背驰')">顶背驰</button>
  <span class="fl" style="margin-left:8px">方向:</span>
  <button class="fb" data-g="side" data-v="" onclick="filter('side','')">全部</button>
  <button class="fb" data-g="side" data-v="buy" onclick="filter('side','buy')">看涨</button>
  <button class="fb" data-g="side" data-v="sell" onclick="filter('side','sell')">看跌</button>
  <span class="fl" style="margin-left:8px">笔:</span>
  <button class="fb" data-g="bi" data-v="" onclick="filter('bi','')">全部</button>
  <button class="fb" data-g="bi" data-v="↑" onclick="filter('bi','↑')">↑向上</button>
  <button class="fb" data-g="bi" data-v="↓" onclick="filter('bi','↓')">↓向下</button>
</div>

<div class="table-shell">
<table class="cs-table">
<thead><tr>
  <th onclick="sortTable(0)">代码</th><th onclick="sortTable(1)">名称</th>
  <th onclick="sortTable(2)">笔</th><th onclick="sortTable(3)">中枢</th><th onclick="sortTable(4)">线段</th>
  <th onclick="sortTable(5)">方向</th><th onclick="sortTable(6)">力度</th>
  <th onclick="sortTable(7)">背驰</th><th onclick="sortTable(8)">信号</th><th onclick="sortTable(9)">价格</th>
</tr></thead>
<tbody id="tb"></tbody>
</table>
</div>
</div>

<script>
var DATA = {data_json};
var sortCol = -1, sortDir = 1;
var activeFilters = {{sig:'', div:'', side:'', bi:''}};

function filter(g, v) {{
  if(activeFilters[g]===v){{activeFilters[g]=''}}else{{activeFilters[g]=v}}
  document.querySelectorAll('.fb').forEach(function(b){{b.className='fb'}});
  for(var k in activeFilters){{if(activeFilters[k]){{
    var btn=document.querySelector('.fb[data-g="'+k+'"][data-v="'+activeFilters[k]+'"]');
    if(btn)btn.className='fb active';
  }}}}
  renderTable();
}}

function sortTable(col) {{
  if(sortCol===col){{sortDir=-sortDir}}else{{sortCol=col;sortDir=1}}
  renderTable();
}}

function renderTable() {{
  var rows = DATA.slice();
  if(activeFilters.sig){{rows=rows.filter(function(r){{return r.sig_type===activeFilters.sig}})}}
  if(activeFilters.div){{rows=rows.filter(function(r){{return r.div_type===activeFilters.div}})}}
  if(activeFilters.side==='buy'){{rows=rows.filter(function(r){{return r.sig_side==='buy'}})}}
  else if(activeFilters.side==='sell'){{rows=rows.filter(function(r){{return r.sig_side==='sell'}})}}
  if(activeFilters.bi==='↑'){{rows=rows.filter(function(r){{return r.bi_dir==='向上'||r.bi_dir==='up'}})}}
  else if(activeFilters.bi==='↓'){{rows=rows.filter(function(r){{return r.bi_dir==='向下'||r.bi_dir==='down'}})}}
  var nums=[true,false,true,true,true,false,true,true,false,true];
  rows.sort(function(a,b) {{
    var keys=['code','name','bi','zs','seg','bi_dir','bi_power','div_cnt','sig_type','sig_price'];
    var va=a[keys[sortCol]], vb=b[keys[sortCol]];
    if(nums[sortCol]) {{va=parseFloat(va)||0; vb=parseFloat(vb)||0}}
    if(va<vb) return -1*sortDir; if(va>vb) return 1*sortDir; return 0;
  }});
  var h='';
  rows.forEach(function(r) {{
    var isUp=r.bi_dir==='向上'||r.bi_dir==='up';
    var dirSym=isUp?'↑':'↓', dirClr=isUp?'#ef4444':'#10b981';
    var sigTag='', divTag='';
    if(r.sig_type){{var isBuy=r.sig_side==='buy'; sigTag='<span class=\"tag '+(isBuy?'tag-buy':'tag-sell')+'\">'+r.sig_type+'</span>'}}
    else{{sigTag='<span class=\"tag-none\">—</span>'}}
    if(r.div_type){{var isDB=r.div_type==='底背驰'; divTag='<span class=\"tag '+(isDB?'tag-div-b':'tag-div-t')+'\">'+r.div_type+'</span>'}}
    else{{divTag='<span class=\"tag-none\">—</span>'}}
    h+='<tr><td><a href=\"../chanlun-backtest/?code='+r.code+'\" target=\"_blank\">'+r.code+'</a></td>';
    h+='<td>'+r.name+'</td><td>'+r.bi+'</td><td>'+r.zs+'</td><td>'+r.seg+'</td>';
    h+='<td style=\"color:'+dirClr+';font-weight:500\">'+dirSym+'</td><td>'+(r.bi_power||0).toFixed(1)+'</td>';
    h+='<td>'+r.div_cnt+' '+divTag+'</td><td>'+sigTag+'</td>';
    h+='<td>'+(r.sig_price?'¥'+r.sig_price.toFixed(2):'—')+'</td></tr>';
  }});
  document.getElementById('tb').innerHTML=h||'<tr><td colspan=\"10\" style=\"padding:20px;color:var(--muted)\">无匹配结果</td></tr>';
}}

renderTable();

function toggleTheme(){{var h=document.documentElement,n=h.dataset.theme==='dark'?'light':'dark';h.dataset.theme=n;localStorage.setItem('theme',n)}}
(function(){{var s=localStorage.getItem('theme')||'dark';document.documentElement.dataset.theme=s}})();
</script>
</body>
</html>'''


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="扫描日期")
    parser.add_argument("--all", action="store_true", help="全市场模式（过滤ST+低量），默认仅观察池+精选池")
    args = parser.parse_args()
    run_scan(args.date, all_market=args.all)
