"""
每日双强股批量形态扫描

1. 从 stock_rs_daily 查最新日期的双强股票（稳健龙头 / 加速爆发）
2. 排除ST + 市值<5000万
3. 逐只运行6引擎形态扫描
4. 输出 JSON + 静态HTML看板

用法：
    python scripts/daily_pattern_scan.py                    # 最新日期
    python scripts/daily_pattern_scan.py --date 2026-05-13  # 指定日期
    python scripts/daily_pattern_scan.py --top 50           # 只扫TOP50（快速测试）
"""

import sys, os, sqlite3, json, time, argparse
from datetime import datetime, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")
OUT_JSON = os.path.join(PROJECT_DIR, "web", "daily-pattern-scan", "data.json")
OUT_HTML = os.path.join(PROJECT_DIR, "web", "daily-pattern-scan", "index.html")

# RS 双强阈值（与 stock_rs.py 保持一致）
ROBUST_RPS_250 = 90
ROBUST_RPS_20 = 85
BURST_RPS_250 = 80
BURST_RPS_20 = 95
MIN_AMOUNT = 50_000_000    # 5000万成交额
MIN_MCAP = 5_000_000_000   # 50亿市值（close × 总股本）


def get_candidates(date_str, top_n=None, all_stocks=False):
    """查询候选股票列表。
    all_stocks=False: 仅双强/稳健龙头/加速爆发
    all_stocks=True:  全A股（排除ST/市值<5000万），但仍标注 RS 类型
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    ds_case = """
        CASE
            WHEN r.rps_250>=? AND r.rps_20>=? AND r.rps_250>=? AND r.rps_20>=?
                THEN '双强'
            WHEN r.rps_250>=? AND r.rps_20>=? THEN '稳健龙头'
            WHEN r.rps_250>=? AND r.rps_20>=? THEN '加速爆发'
        END as ds_type
    """

    if all_stocks:
        query = f"""
            SELECT r.stock_code, b.name, r.close, r.rps_20, r.rps_60, r.rps_120, r.rps_250,
                   rs_line, r.amount,
                   {ds_case}
            FROM stock_rs_daily r
            JOIN stock_basic b ON r.stock_code = b.stock_code
            LEFT JOIN (
                SELECT e.stock_code, e.capitalization
                FROM stock_equity_change e
                JOIN (
                    SELECT stock_code, MAX(date) as max_date
                    FROM stock_equity_change
                    WHERE date <= ?
                    GROUP BY stock_code
                ) latest ON e.stock_code = latest.stock_code AND e.date = latest.max_date
            ) eq ON r.stock_code = eq.stock_code
            WHERE r.date = ?
              AND b.listing_status = 'normally_listed'
              AND b.name NOT LIKE '%ST%'
              AND b.name NOT LIKE '%*ST%'
              AND r.amount >= ?
              AND (eq.capitalization IS NULL OR r.close * eq.capitalization >= ?)
            ORDER BY r.rps_250 DESC, r.rps_20 DESC
        """
        params = (
            ROBUST_RPS_250, ROBUST_RPS_20, BURST_RPS_250, BURST_RPS_20,
            ROBUST_RPS_250, ROBUST_RPS_20,
            BURST_RPS_250, BURST_RPS_20,
            date_str,  # for stock_equity_change subquery date filter
            date_str,  # for stock_rs_daily date filter
            MIN_AMOUNT,
            MIN_MCAP
        )
    else:
        query = f"""
            SELECT r.stock_code, b.name, r.close, r.rps_20, r.rps_60, r.rps_120, r.rps_250,
                   rs_line, r.amount,
                   {ds_case}
            FROM stock_rs_daily r
            JOIN stock_basic b ON r.stock_code = b.stock_code
            WHERE r.date = ?
              AND ((r.rps_250 >= ? AND r.rps_20 >= ?) OR (r.rps_250 >= ? AND r.rps_20 >= ?))
              AND b.listing_status = 'normally_listed'
              AND b.name NOT LIKE '%ST%'
              AND b.name NOT LIKE '%*ST%'
              AND r.amount >= ?
            ORDER BY r.rps_250 DESC, r.rps_20 DESC
        """
        params = (
            ROBUST_RPS_250, ROBUST_RPS_20, BURST_RPS_250, BURST_RPS_20,
            ROBUST_RPS_250, ROBUST_RPS_20,
            BURST_RPS_250, BURST_RPS_20,
            date_str,
            ROBUST_RPS_250, ROBUST_RPS_20, BURST_RPS_250, BURST_RPS_20,
            MIN_AMOUNT
        )

    rows = conn.execute(query, params).fetchall()
    conn.close()

    # 计算当日涨幅
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    enriched = []
    for r in rows:
        code = r['stock_code']
        # 查前一日收盘价
        prev = conn.execute("""
            SELECT close FROM daily_kline
            WHERE stock_code=? AND date < ? ORDER BY date DESC LIMIT 1
        """, (code, date_str)).fetchone()
        prev_close = prev['close'] if prev and prev['close'] else r['close']
        change_pct = (r['close'] - prev_close) / prev_close * 100 if prev_close > 0 else 0

        enriched.append({
            'stock_code': code,
            'name': r['name'],
            'close': round(r['close'], 2),
            'change_pct': round(change_pct, 2),
            'rps_20': r['rps_20'],
            'rps_60': r['rps_60'],
            'rps_120': r['rps_120'],
            'rps_250': r['rps_250'],
            'rs_line': round(r['rs_line'], 2) if r['rs_line'] else None,
            'amount': r['amount'],
            'ds_type': r['ds_type'],
        })

    conn.close()

    if top_n:
        enriched = enriched[:top_n]

    return enriched


def scan_stock(code, date_str):
    """对单只股票运行6引擎扫描"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 取最近400天K线（覆盖 climax_top 78周需求）
    start_date = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=400)).strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT date, open, high, low, close, volume
        FROM daily_kline
        WHERE stock_code=? AND date>=? AND date<=?
        ORDER BY date
    """, (code, start_date, date_str)).fetchall()
    conn.close()

    if len(rows) < 50:
        return []

    klines = [dict(r) for r in rows]

    try:
        from engine_registry import run_all_engines
        signals = run_all_engines(klines=klines, indicators=None)
    except Exception as e:
        print(f"  [{code}] engine error: {e}")
        return []

    # 只保留当天信号
    today_signals = [s for s in signals if s['date'] == date_str]
    return today_signals


def format_signal_summary(signals):
    """将信号列表展开为完整描述，多信号用 <br> 分隔"""
    if not signals:
        return '—'

    source_names = {
        'cdl': 'K线形态', 'talib': 'TA-Lib', 'pocket_pivot': '口袋支点',
        'double_bottom': '双重底', 'breakout': '标准突破', 'flat_base': '扁平基部',
        'base_breakout_v2': '基部突破V2', 'climax_top': '高潮见顶',
        'railroad_tracks': '铁轨线', 'top_pattern': '头部形态',
        'mw_signal': 'MW信号',
    }

    parts = []
    for s in signals:
        src = source_names.get(s['source'], s['source'])
        tp = '看跌' if s.get('type') == 'bearish' else '看涨'
        desc = ''
        det = s.get('details', {})
        if det:
            desc = det.get('cdl_name') or det.get('description') or det.get('signal_type') or ''
        if desc:
            parts.append('%s·%s（%s）' % (src, tp, desc))
        else:
            parts.append('%s·%s' % (src, tp))

    return '<br>'.join(parts)


def generate_html(candidates, date_str, elapsed, all_stocks=False):
    """生成静态HTML看板"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    title = '全市场股票形态扫描'
    subtitle = '—— 双强精选' if not all_stocks else '—— 全量扫描'

    rows_html = ''
    for i, c in enumerate(candidates):
        code = c['stock_code']
        name = c['name']
        ds = c['ds_type'] or ''
        ds_badge = ''
        if ds == '双强':
            ds_badge = '<span class="badge ds-double">双强</span>'
        elif ds == '稳健龙头':
            ds_badge = '<span class="badge ds-robust">稳健龙头</span>'
        elif ds == '加速爆发':
            ds_badge = '<span class="badge ds-burst">加速爆发</span>'
        else:
            ds_badge = '<span class="badge">—</span>'

        # RS 值着色
        def rs_td(val):
            if val is None: return '<td>—</td>'
            if val >= 90: cls = 'rs-hot'
            elif val >= 80: cls = 'rs-warm'
            else: cls = ''
            return '<td class="%s">%d</td>' % (cls, val) if cls else '<td>%d</td>' % val

        change = c['change_pct']
        chg_cls = 'up' if change > 0 else 'down' if change < 0 else ''
        chg_html = '<span class="%s">%+.2f%%</span>' % (chg_cls, change)

        signals = c.get('signals', [])
        signal_txt = c.get('signal_summary', '—')

        if signals:
            signal_html = '<span class="sig-text">%s</span>' % signal_txt
        else:
            signal_html = '<span class="sig-none">—</span>'

        rows_html += '''
            <tr data-ds="%s">
                <td><a class="code-link" href="../pattern-scan/?code=%s" target="_blank">%s</a></td>
                <td>%s</td>
                <td class="num">%.2f</td>
                <td class="num">%s</td>
                %s%s%s%s
                <td>%s</td>
                <td>%s</td>
            </tr>''' % (
            ds, code, code, name, c['close'], chg_html,
            rs_td(c['rps_250']), rs_td(c['rps_120']),
            rs_td(c['rps_60']), rs_td(c['rps_20']),
            ds_badge, signal_html
        )

    html = '''<!DOCTYPE html>
<html lang="zh-CN" class="dark">
<head>
<meta charset="UTF-8">
<link rel="icon" href="../images/favicon.svg" type="image/svg+xml">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>每日双强股形态扫描</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="../shared/css/hanako-glass.css">
<style>
body { font-family: var(--font-body); background: var(--bg); color: var(--text-primary); }
.app-container { max-width: 1400px; margin:0 auto; padding:12px 16px; }
.top-bar { display:flex; justify-content:space-between; align-items:center; padding:14px 20px; background:var(--card); border-radius:14px; margin-bottom:12px; border:1px solid var(--border); }
.top-bar h1 { font-family:var(--font-display); font-size:1.1rem; margin:0; color:var(--text-primary); }
.top-bar .meta { font-size:0.65rem; color:var(--muted); }
.table-wrap { background:var(--card); border-radius:14px; padding:10px; border:1px solid var(--border); overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:0.7rem; }
th { padding:8px 8px; text-align:right; font-weight:700; color:var(--text-secondary); font-size:0.6rem; border-bottom:2px solid var(--border); white-space:nowrap; position:sticky; top:0; background:var(--card); cursor:pointer; }
th:hover { color:var(--accent); }
td { padding:6px 8px; border-bottom:1px solid var(--border); white-space:nowrap; text-align:right; }
tr:hover { background:var(--accent-subtle); }
.code-link { color:var(--accent); font-weight:700; text-decoration:none; }
.code-link:hover { text-decoration:underline; }
.up { color:#ef4444; font-weight:700; }
.down { color:#10b981; font-weight:700; }
.rs-hot { color:#ef4444; font-weight:700; }
.rs-warm { color:#f59e0b; font-weight:600; }
.badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:0.58rem; font-weight:700; }
.ds-double { background:rgba(16,185,129,.1); color:#10b981; }
.ds-robust { background:rgba(59,130,246,.1); color:#3b82f6; }
.ds-burst { background:rgba(245,158,11,.1); color:#f59e0b; }
.sig-text { font-size:0.62rem; color:var(--text-secondary); }
.sig-none { font-size:0.62rem; color:var(--muted); }
.footer { text-align:center; padding:16px; font-size:0.6rem; color:var(--muted); }
.filter-bar { display:flex; gap:6px; align-items:center; padding:8px 0; margin-bottom:6px; flex-wrap:wrap; }
.filter-bar .filter-label { font-size:0.62rem; font-weight:600; color:var(--muted); }
.filter-bar .filter-btn { padding:3px 10px; border:1px solid var(--border); border-radius:8px; background:var(--card); color:var(--text-secondary); cursor:pointer; font-size:0.6rem; font-family:var(--font-body); }
.filter-bar .filter-btn:hover { border-color:var(--accent); color:var(--accent); }
.filter-bar .filter-btn.active { background:var(--accent); color:#FFF; border-color:var(--accent); }
.pager-bar { display:flex; gap:6px; align-items:center; padding:6px 0; font-size:0.62rem; color:var(--text-secondary); flex-wrap:wrap; }
.pager-bar .pager-info { margin-right:8px; color:var(--muted); }
.pager-bar .pager-btn { padding:2px 8px; border:1px solid var(--border); border-radius:6px; background:var(--card); color:var(--text-secondary); cursor:pointer; font-size:0.6rem; }
.pager-bar .pager-btn:hover { border-color:var(--accent); color:var(--accent); }
.pager-bar .pager-btn:disabled { opacity:0.3; cursor:default; }
.pager-bar .pager-cur { font-weight:700; min-width:50px; text-align:center; }
.pager-bar .pager-label { font-size:0.58rem; color:var(--muted); }
.pager-bar .pager-size { padding:2px 4px; border:1px solid var(--border); border-radius:6px; background:var(--card); color:var(--text-secondary); font-size:0.6rem; }
html[data-theme="light"] .top-bar { background:rgba(255,255,255,.75); }
html[data-theme="light"] .table-wrap { background:rgba(255,255,255,.75); }
html[data-theme="light"] th { background:rgba(255,255,255,.75); }
</style>
</head>
<body>
<div class="app-container">
<nav id="top-nav"></nav>

<div class="top-bar">
  <div>
    <h1>📋 ''' + title + ''' <span style="font-size:0.65rem;font-weight:400;color:var(--muted)">''' + subtitle + '''</span></h1>
  </div>
  <div class="meta">
    扫描日期: ''' + date_str + ''' | 股票数: ''' + str(len(candidates)) + ''' | 耗时: ''' + str(round(elapsed, 1)) + '''s | 生成: ''' + now + '''
  </div>
</div>

<div class="filter-bar">
  <span class="filter-label">RS类型：</span>
  <button class="filter-btn active" onclick="filterDS('all')">全部</button>
  <button class="filter-btn" onclick="filterDS('双强')">双强</button>
  <button class="filter-btn" onclick="filterDS('稳健龙头')">稳健龙头</button>
  <button class="filter-btn" onclick="filterDS('加速爆发')">加速爆发</button>
  <button class="filter-btn" onclick="filterDS('有信号')">有信号</button>
  <button class="filter-btn" onclick="filterDS('看涨')">看涨</button>
  <button class="filter-btn" onclick="filterDS('看跌')">看跌</button>
  <button class="filter-btn" onclick="filterDS('标准突破')">标准突破</button>
  <button class="filter-btn" onclick="filterDS('口袋支点')">口袋支点</button>
  <button class="filter-btn" onclick="filterDS('双重底')">双重底</button>
  <button class="filter-btn" onclick="filterDS('扁平基部')">扁平基部</button>
</div>

<div class="pager-bar" id="pager-top">
  <span class="pager-info" id="pager-info-top">共 0 条</span>
  <button class="pager-btn" onclick="goPage(1)">««</button>
  <button class="pager-btn" onclick="goPage(currentPage-1)">«</button>
  <span class="pager-cur" id="pager-cur-top">第 1 页</span>
  <button class="pager-btn" onclick="goPage(currentPage+1)">»</button>
  <button class="pager-btn" onclick="goPage(99999)">»»</button>
  <span class="pager-label">每页</span>
  <select class="pager-size" onchange="setPageSize(parseInt(this.value))">
    <option value="50">50</option>
    <option value="100">100</option>
    <option value="200" selected>200</option>
    <option value="500">500</option>
    <option value="99999">全部</option>
  </select>
  <span class="pager-label">条</span>
</div>

<div class="table-wrap">
<table id="stock-table">
<thead>
<tr>
  <th onclick="sortTable(0)">代码</th>
  <th onclick="sortTable(1)">名称</th>
  <th onclick="sortTable(2)">价格</th>
  <th onclick="sortTable(3)">涨幅</th>
  <th onclick="sortTable(4)">RS_250</th>
  <th onclick="sortTable(5)">RS_120</th>
  <th onclick="sortTable(6)">RS_60</th>
  <th onclick="sortTable(7)">RS_20</th>
  <th onclick="sortTable(8)">类型</th>
  <th onclick="sortTable(9)">信号摘要</th>
</tr>
</thead>
<tbody>
''' + rows_html + '''
</tbody>
</table>
</div>

<div class="pager-bar" id="pager-bottom">
  <span class="pager-info" id="pager-info-bottom">共 0 条</span>
  <button class="pager-btn" onclick="goPage(1)">««</button>
  <button class="pager-btn" onclick="goPage(currentPage-1)">«</button>
  <span class="pager-cur" id="pager-cur-bottom">第 1 页</span>
  <button class="pager-btn" onclick="goPage(currentPage+1)">»</button>
  <button class="pager-btn" onclick="goPage(99999)">»»</button>
</div>

<div class="footer">
  每日自动扫描 · 数据来源 stock_rs_daily · 引擎: Pocket Pivot / Double Bottom / Breakout / Flat Base / TA-Lib CDL
</div>
</div>

<script>
// ── RS 类型筛选 ──
function filterDS(type) {
  // 信号类筛选关键词
  var sigFilters = ['看涨', '看跌', '标准突破', '口袋支点', '双重底', '扁平基部'];
  var rows = document.querySelectorAll('#stock-table tbody tr');
  rows.forEach(function(r) {
    if (type === 'all') {
      r.style.display = '';
    } else if (type === '有信号') {
      var txt = r.cells[9].textContent.trim();
      r.style.display = (txt !== '—') ? '' : 'none';
    } else if (sigFilters.indexOf(type) >= 0) {
      var txt = r.cells[9].textContent.trim();
      r.style.display = (txt.indexOf(type) >= 0) ? '' : 'none';
    } else {
      var ds = r.getAttribute('data-ds') || '';
      r.style.display = (ds === type) ? '' : 'none';
    }
  });
  // 按钮激活态（信号类按钮的文字本身即是 type，直接比较即可）
  document.querySelectorAll('.filter-btn').forEach(function(b) {
    b.className = 'filter-btn' + (b.textContent === type || (type === 'all' && b.textContent === '全部') ? ' active' : '');
  });
  // 筛选后回到第1页
  currentPage = 1;
  applyPage();
}

// ── 分页 ──
var pageSize = 200;
var currentPage = 1;
var allRows = [];

function initPage() {
  allRows = Array.from(document.querySelectorAll('#stock-table tbody tr'));
  applyPage();
}

function setPageSize(size) {
  pageSize = size;
  currentPage = 1;
  applyPage();
}

function goPage(n) {
  var visible = getVisibleRows();
  var totalPages = Math.ceil(visible.length / pageSize) || 1;
  if (n < 1) n = 1;
  if (n > totalPages) n = totalPages;
  currentPage = n;
  applyPage();
}

function getVisibleRows() {
  return allRows.filter(function(r) { return r.style.display !== 'none'; });
}

function applyPage() {
  var visible = getVisibleRows();
  var total = visible.length;
  var totalPages = Math.ceil(total / pageSize) || 1;
  if (currentPage > totalPages) currentPage = totalPages;

  var start = (currentPage - 1) * pageSize;
  var end = Math.min(start + pageSize, total);

  allRows.forEach(function(r) {
    if (r.style.display === 'none') return; // already hidden by filter
    r.classList.add('page-hidden');
  });

  for (var i = start; i < end; i++) {
    visible[i].classList.remove('page-hidden');
  }

  updatePagerUI(total, totalPages);
}

function updatePagerUI(total, totalPages) {
  var ids = ['top', 'bottom'];
  ids.forEach(function(id) {
    var info = document.getElementById('pager-info-' + id);
    var cur = document.getElementById('pager-cur-' + id);
    if (info) info.textContent = '共 ' + total + ' 条';
    if (cur) cur.textContent = '第 ' + currentPage + '/' + totalPages + ' 页';
  });
}

// 隐藏非当前页的行
var styleEl = document.createElement('style');
styleEl.textContent = '.page-hidden { display: none !important; }';
document.head.appendChild(styleEl);

// 全列排序（点击切换升/降序，表头 ▲/▼ 指示）
var sortCol = -1, sortDir = 1;
function sortTable(col) {
  var table = document.getElementById('stock-table');
  var tbody = table.querySelector('tbody');
  var rows = Array.from(tbody.querySelectorAll('tr'));

  // 切换方向：再次点击同一列则反转
  if (sortCol === col) { sortDir = -sortDir; } else { sortCol = col; sortDir = 1; }

  // 数字列：[代码, 名称, 价格, 涨幅, RS_250, RS_120, RS_60, RS_20, 类型, 信号]
  var isNum = [true, false, true, true, true, true, true, true, false, false];

  rows.sort(function(a, b) {
    var va = a.cells[col].textContent.trim();
    var vb = b.cells[col].textContent.trim();
    if (isNum[col]) {
      va = parseFloat(va.replace(/[^0-9.\\-]/g, '')) || 0;
      vb = parseFloat(vb.replace(/[^0-9.\\-]/g, '')) || 0;
    }
    if (va < vb) return -1 * sortDir;
    if (va > vb) return 1 * sortDir;
    return 0;
  });

  rows.forEach(function(r) { tbody.appendChild(r); });

  // 刷新分页（DOM 重排后 allRows 引用失效，重新抓取）
  allRows = Array.from(tbody.querySelectorAll('tr'));
  applyPage();

  // 更新表头排序指示
  var ths = document.querySelectorAll('#stock-table th');
  for (var i = 0; i < ths.length; i++) {
    ths[i].textContent = ths[i].textContent.replace(/ [▲▼]/g, '');
    if (i === col) ths[i].textContent += sortDir > 0 ? ' ▲' : ' ▼';
  }
}

// 主题切换
document.querySelector('.theme-toggle').addEventListener('click', function() {
  var html = document.documentElement;
  html.dataset.theme = html.dataset.theme === 'light' ? 'dark' : 'light';
  document.querySelector('.theme-toggle').textContent = html.dataset.theme === 'light' ? '🌙' : '☀️';
});

// 初始化：分页 + 默认按 RS_250 降序
initPage();
sortTable(4); sortDir = -1; sortTable(4);
</script>
  <script src="../shared/js/nav.js"></script>
  <script>Nav.init({brandIcon:'📋',brandText:'形态扫描',currentPage:'daily-pattern-scan'});
function toggleTheme(){var h=document.documentElement,n=h.dataset.theme==='dark'?'light':'dark';h.dataset.theme=n;localStorage.setItem('theme',n)}
(function(){var s=localStorage.getItem('theme')||'dark';document.documentElement.dataset.theme=s})();</script>
</body>
</html>'''

    return html


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, default=None)
    parser.add_argument('--top', type=int, default=None, help='只扫描TOP N（测试用）')
    parser.add_argument('--all', dest='all_stocks', action='store_true', help='扫描全部A股（非仅双强股）')
    args = parser.parse_args()

    if args.date:
        date_str = args.date
    else:
        # 从 stock_rs_daily 取最新日期
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("SELECT MAX(date) FROM stock_rs_daily")
        date_str = cur.fetchone()[0]
        conn.close()

    if not date_str:
        print("No data in stock_rs_daily")
        return

    print(f"Scan date: {date_str}", flush=True)
    t0 = time.time()

    # Step 1: 获取候选
    candidates = get_candidates(date_str, args.top, all_stocks=args.all_stocks)
    print(f"Candidates: {len(candidates)}", flush=True)

    if not candidates:
        print("No candidates found")
        return

    # Step 2: 逐只扫描
    for i, c in enumerate(candidates):
        code = c['stock_code']
        try:
            signals = scan_stock(code, date_str)
            c['signals'] = signals
            c['signal_summary'] = format_signal_summary(signals)
        except Exception as e:
            print(f"  [{code}] scan error: {e}")
            c['signals'] = []
            c['signal_summary'] = 'ERR'

        if (i + 1) % 20 == 0:
            elapsed = time.time() - t0
            print(f"  Progress: {i+1}/{len(candidates)} ({elapsed:.1f}s)", flush=True)

    elapsed = time.time() - t0
    print(f"Scan complete: {len(candidates)} stocks in {elapsed:.1f}s", flush=True)

    # Step 3: 保存 JSON
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump({
            'date': date_str,
            'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'count': len(candidates),
            'elapsed': round(elapsed, 1),
            'candidates': candidates
        }, f, ensure_ascii=False, indent=2)
    print(f"JSON saved: {OUT_JSON}", flush=True)

    # Step 3.5: 写入数据库（供知行系统消费）
    conn = sqlite3.connect(DB_PATH)
    for c in candidates:
        signals = c.get('signals', [])
        if signals:
            conn.execute(
                "INSERT OR REPLACE INTO pattern_scan_signals (stock_code, date, signals_json) VALUES (?,?,?)",
                (c['stock_code'], date_str, json.dumps(signals, ensure_ascii=False))
            )
    conn.commit()
    conn.close()
    signal_count = sum(1 for c in candidates if c.get('signals'))
    print(f"DB saved: {len(candidates)} stocks ({signal_count} with signals) to pattern_scan_signals", flush=True)

    # Step 4: 生成 HTML
    html = generate_html(candidates, date_str, elapsed, all_stocks=args.all_stocks)
    with open(OUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"HTML saved: {OUT_HTML}", flush=True)
    print("Done.", flush=True)


if __name__ == '__main__':
    main()
