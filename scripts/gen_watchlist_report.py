# -*- coding: utf-8 -*-
"""
自选池日报 · 每日生成器（daily_update 步骤 32）

流程：generate_report() → ① 落盘 DB（watchlist_report_daily）→ ② 生成静态 HTML 快照
      → ③ 更新 index.json（日历索引）
"""
import os
import sys
import json
import sqlite3
import requests
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))

from src.scanners.watchlist_report import generate_report

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
REPORTS_DIR = os.path.join(PROJECT_DIR, 'web', 'discipline', 'reports')
INDEX_PATH = os.path.join(REPORTS_DIR, 'index.json')

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN" class="dark">
<head>
<meta charset="UTF-8">
<link rel="icon" href="../../images/favicon.svg" type="image/svg+xml">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>自选池日报 · {date}</title>
<link rel="stylesheet" href="../../shared/css/hanako-glass.css">
<style>
body{{font-family:var(--font-body);background:var(--bg);color:var(--text-primary)}}
.page-header{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:12px}}
.page-header h1{{font-family:var(--font-display);font-size:1.2rem;font-weight:400;color:var(--text-primary);margin:0}}
#cal-select{{background:var(--card);border:1px solid var(--border);border-radius:10px;color:var(--text-primary);padding:6px 10px;font-size:12px;font-family:var(--font-mono);outline:none}}
#cal-select option{{background:var(--card)}}
.r-top{{margin-bottom:12px}}
.r-title{{font-family:var(--font-display);font-size:1.05rem;color:var(--text-primary);display:flex;align-items:baseline;gap:10px;margin-bottom:8px}}
.dim{{color:var(--muted);font-size:0.72rem;font-weight:300}}
.sum-bar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-size:0.72rem}}
.sum-chip{{border:1px solid;border-radius:8px;padding:3px 10px;font-weight:300}}
.sum-chip b{{font-weight:600}}
.r-card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 16px;margin-bottom:10px;backdrop-filter:blur(16px);box-shadow:var(--shadow-card)}}
.r-head{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.r-name{{font-family:var(--font-display);font-size:0.95rem;color:var(--text-primary)}}
.kind-tag{{font-size:9px;border:1px solid var(--border);border-radius:5px;padding:1px 5px;color:var(--muted)}}
.r-code{{font-family:var(--font-mono);font-size:0.68rem;color:var(--muted)}}
.r-lv{{font-size:10px;font-weight:600;border:1px solid;border-radius:8px;padding:2px 8px}}
.r-price{{margin-left:auto;font-family:var(--font-mono);font-size:0.8rem;color:var(--text-primary)}}
.r-net{{font-size:0.68rem;color:var(--muted);margin:4px 0}}
.r-net b{{color:var(--accent);font-family:var(--font-mono)}}
.r-ctx{{display:flex;gap:10px;flex-wrap:wrap;font-size:0.64rem;margin-bottom:4px}}
.hold-line,.chan-line{{font-size:0.66rem;margin:2px 0}}
.miss-line{{font-size:0.66rem;color:#fbbf24;margin:3px 0;line-height:1.7}}
.miss-line .ok{{color:#34d399;border:1px solid #34d39944;border-radius:5px;padding:0 5px;font-size:0.62rem}}
.r-reasons{{font-size:0.66rem;color:var(--text-secondary);line-height:1.9;margin-top:4px;border-top:1px dashed var(--border);padding-top:6px}}
.sig-line{{display:flex;flex-wrap:wrap;gap:4px;margin-top:5px}}
.sig-tag{{font-size:0.6rem;border-radius:6px;padding:2px 7px;font-family:var(--font-mono)}}
.sig-long{{background:rgba(239,68,68,.08);color:#ef4444;border:1px solid rgba(239,68,68,.2)}}
.sig-short{{background:rgba(16,185,129,.08);color:#10b981;border:1px solid rgba(16,185,129,.2)}}
.cb-line{{font-size:0.66rem;color:var(--text-secondary);margin-top:4px}}
.r-tips{{font-size:0.64rem;color:#f59e0b;margin-top:4px}}
.err-line{{font-size:0.66rem;color:#f87171;margin-top:4px}}
html[data-theme="light"] .r-card{{background:rgba(255,255,255,.75)}}
</style>
</head>
<body>
<div class="app-container">
<nav id="top-nav"></nav>
<script src="../../shared/js/nav.js"></script>
<script>Nav.init({{brandIcon:'🦊',brandText:'知行',currentPage:'watchlist-report'}})</script>
<div class="page-header">
  <h1>📋 自选池日报</h1>
  <select id="cal-select" onchange="location.href='report-'+this.value+'.html'"><option>加载中...</option></select>
</div>
<div id="report-root"><div style="text-align:center;padding:60px;color:var(--muted)">加载中...</div></div>
</div>
<script>window.REPORT_DATA = {data_json};</script>
<script>
  function toggleTheme(){{var h=document.documentElement,n=h.dataset.theme==='dark'?'light':'dark';h.dataset.theme=n;localStorage.setItem('theme',n)}}
  (function(){{var s=localStorage.getItem('theme')||'dark';document.documentElement.dataset.theme=s}})();
  window.API_BASE = 'http://localhost:8788';
</script>
<script src="watchlist-report.js"></script>
<script>window.renderReport && renderReport();</script>
</body>
</html>
"""


def _feishu_webhook():
    """读取 FEISHU_WEBHOOK：环境变量 → D:/hanako/.env → ~/.hermes/.env"""
    v = os.environ.get('FEISHU_WEBHOOK', '')
    if v:
        return v
    for p in [r'D:\hanako\.env', os.path.expanduser('~/.hermes/.env')]:
        try:
            with open(p, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('FEISHU_WEBHOOK='):
                        return line.split('=', 1)[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return ''


def push_feishu(report):
    """日报生成后推送飞书摘要（webhook 配置 FEISHU_WEBHOOK，未配置则跳过）"""
    webhook = _feishu_webhook()
    if not webhook:
        print('[watchlist-report] 未配置 FEISHU_WEBHOOK，跳过推送')
        return False
    s = report.get('summary') or {}
    lines = [f"📋 自选池日报 {report.get('date')}",
             f"🔴 回避 {s.get('avoid', 0)} · 🟢 买入 {(s.get('buy', 0) or 0) + (s.get('buy_strong', 0) or 0)} · ⏳ 等回调 {s.get('wait', 0)} · ⚪ 持有 {s.get('hold', 0)}"]
    # 关键卡片摘要
    for c in report.get('cards', []):
        ev = c.get('eval') or {}
        lv = ev.get('level')
        if lv in ('avoid', 'buy_strong', 'buy'):
            h = c.get('holding')
            pnl = f"({h['pnl_pct']:+.1f}%持仓)" if h and h.get('pnl_pct') is not None else ''
            lines.append(f"  {lv == 'avoid' and '🔴' or '🟢'} {c['name']} {ev.get('level_cn')} {pnl}")
            if ev.get('reasons'):
                lines.append(f"    {ev['reasons'][0][:80]}")
    lines.append("🔗 查看: http://localhost:8772/discipline/watchlist-report.html")
    try:
        r = requests.post(webhook, json={'msg_type': 'text', 'content': {'text': chr(10).join(lines)}},
                          timeout=10)
        ok = r.ok
        print(f'[watchlist-report] 飞书推送 {"成功" if ok else "失败"} ({r.status_code})')
        return ok
    except Exception as e:
        print(f'[watchlist-report] 飞书推送异常: {e}')
        return False


def generate(scan_date=None):
    """主入口：生成并落盘。返回 (date, html_path)"""
    rep = generate_report(scan_date)
    date = rep['date']
    os.makedirs(REPORTS_DIR, exist_ok=True)

    # ① 落盘 DB
    db = sqlite3.connect(DB_PATH)
    db.execute("CREATE TABLE IF NOT EXISTS watchlist_report_daily (date TEXT PRIMARY KEY, report_json TEXT, created_at TEXT)")
    db.execute("INSERT OR REPLACE INTO watchlist_report_daily(date, report_json, created_at) VALUES(?, ?, ?)",
               (date, json.dumps(rep, ensure_ascii=False), datetime.now().strftime('%Y-%m-%d %H:%M')))
    db.commit()
    db.close()

    # ② 静态 HTML 快照
    data_json = json.dumps(rep, ensure_ascii=False)
    html = HTML_TEMPLATE.format(date=date, data_json=data_json)
    html_path = os.path.join(REPORTS_DIR, f'report-{date}.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    # ③ index.json
    dates = []
    db = sqlite3.connect(DB_PATH)
    rows = db.execute("SELECT date FROM watchlist_report_daily ORDER BY date").fetchall()
    db.close()
    dates = [r[0] for r in rows]
    if date not in dates:
        dates.append(date)
    dates.sort()
    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        json.dump({'dates': dates, 'latest': date}, f, ensure_ascii=False, indent=1)

    print(f"[watchlist-report] 已生成 {date}：{len(rep['cards'])} 标的，摘要 {rep['summary']}")
    print(f"[watchlist-report] HTML: {html_path}")
    push_feishu(rep)
    return date, html_path


if __name__ == '__main__':
    generate()
