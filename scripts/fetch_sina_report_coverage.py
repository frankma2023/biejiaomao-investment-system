# -*- coding: utf-8 -*-
"""
scripts/fetch_sina_report_coverage.py — 新浪研报覆盖拉取（CANSLIM I 因子主数据源）
============================================================================
接口：新浪研报中心 t1=2&symbol=sh{code}（个股研报列表，GBK 编码）
替代：东财 RPTA_WEB_GETJSON（stock_analyst_reports 批处理滞后 1-2 周且漏数据，
      688531 实测东财 org_count=3 vs 新浪 10，2026-08-26）

统计口径（对齐 CANSLIM PRD §6 I-研报覆盖）：
- org_count：近 lookback_days 天发布研报的券商数（研报覆盖阈值核心）
- first_coverage：近 90 天首次覆盖的机构数（全历史机构集合差集，精确）
- report_count / orgs_json / top_orgs_json

用法：
  python fetch_sina_report_coverage.py 688531          # 单只
  python fetch_sina_report_coverage.py --codes 600519,000858
  python fetch_sina_report_coverage.py --watchlist     # 自选池+观察池
  python fetch_sina_report_coverage.py --days 180      # 统计窗口
"""
import os
import sys
import re
import time
import json
import sqlite3
import requests
from datetime import datetime, date, timedelta
from collections import Counter, defaultdict

BASE = 'http://stock.finance.sina.com.cn/stock/go.php/vReport_List/kind/search/index.phtml'
HEADERS = {'User-Agent': 'Mozilla/5.0'}
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'lixinger.db')

ROW_RE = re.compile(
    r'<td>(\d+)</td>\s*<td class="tal f14">\s*<a[^>]*title="([^"]+)"[^>]*>.*?</td>\s*'
    r'<td>([^<]*)</td>\s*<td>(\d{4}-\d{2}-\d{2})</td>\s*'
    r'<td>.*?<span>([^<]*)</span>.*?</td>\s*<td><div class="fname"><span>([^<]*)</span>',
    re.S)


def sina_symbol(code):
    return ('sh' if code.startswith(('5', '6', '9')) else 'sz') + code


def fetch_page(code, p=1, num=50, retries=3):
    url = f'{BASE}?t1=2&symbol={sina_symbol(code)}&p={p}&num={num}'
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.encoding = 'gbk'
            return r.text
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))


def parse_reports(txt):
    """解析研报行 → [(date, title, type, org, author)]"""
    return [(d, t, ty, o, a) for n, t, ty, d, o, a in ROW_RE.findall(txt)]


def fetch_all(code, max_pages=6, delay=0.6):
    """拉全量研报（分页），返回 [(date, title, type, org, author)]"""
    reports, p = [], 1
    while p <= max_pages:
        txt = fetch_page(code, p)
        rows = parse_reports(txt)
        if not rows:
            break
        reports.extend(rows)
        # 判断还有下一页
        if f"set_page_num('{p + 1}')" not in txt:
            break
        p += 1
        time.sleep(delay)
    return reports


def compute(reports, code, lookback_days=90):
    today = date.today()
    cut = (today - timedelta(days=lookback_days)).isoformat()
    recent = [r for r in reports if r[0] >= cut]
    all_orgs = set(r[3] for r in reports)
    recent_orgs = set(r[3] for r in recent)
    # 首次覆盖 = 近90天机构中从未覆盖过的
    first_new = recent_orgs - all_orgs  # 历史全集含 recent，需排除——见下修正
    # 修正：历史覆盖集合 = 全部研报机构；首次覆盖 = 90天内出现且此前从未出现
    older = [r for r in reports if r[0] < cut]
    older_orgs = set(r[3] for r in older)
    first_new = recent_orgs - older_orgs
    org_counts = Counter(r[3] for r in recent)
    return {
        'report_count': len(recent),
        'org_count': len(recent_orgs),
        'orgs_json': json.dumps(sorted(recent_orgs), ensure_ascii=False),
        'top_orgs_json': json.dumps(
            [{'name': o, 'count': c} for o, c in org_counts.most_common(10)], ensure_ascii=False),
        'first_coverage': len(first_new),
        'first_orgs': sorted(first_new),
        'coverage_date': cut,
    }


def save(code, result, lookback_days):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("ALTER TABLE stock_analyst_reports ADD COLUMN source TEXT DEFAULT 'lx'"
                 if not [c for c in conn.execute('PRAGMA table_info(stock_analyst_reports)') if c[1] == 'source']
                 else "SELECT 1")
    conn.execute("""
        INSERT OR REPLACE INTO stock_analyst_reports
        (stock_code, date, lookback_days, report_count, org_count, first_coverage,
         orgs_json, top_orgs_json, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sina', ?)
    """, (code, date.today().isoformat(), lookback_days, result['report_count'],
          result['org_count'], result['first_coverage'], result['orgs_json'],
          result['top_orgs_json'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()


def run(code, lookback_days=90):
    try:
        reports = fetch_all(code)
    except Exception as e:
        print(f'{code} 拉取失败: {str(e)[:60]}')
        return None
    if not reports:
        print(f'{code} 无研报')
        return None
    res = compute(reports, code, lookback_days)
    save(code, res, lookback_days)
    print(f'{code}: 近{lookback_days}天 {res["report_count"]}篇 / {res["org_count"]}家机构'
          f'（首次覆盖 {res["first_coverage"]}家）{" | ".join(res["orgs_json"][1:80] if False else [])}')
    if res['first_orgs']:
        print(f'   首次覆盖: {", ".join(res["first_orgs"])}')
    return res


def main():
    args = sys.argv[1:]
    days = 90
    codes = None
    if '--days' in args:
        days = int(args[args.index('--days') + 1])
    if '--codes' in args:
        codes = args[args.index('--codes') + 1].split(',')
    elif '--watchlist' in args:
        conn = sqlite3.connect(DB_PATH)
        codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM watchlist_report_daily UNION SELECT stock_code FROM observation_pool").fetchall()]
        conn.close()
    elif args and not args[0].startswith('-'):
        codes = [args[0]]

    if not codes:
        print('用法: fetch_sina_report_coverage.py <code> | --codes a,b,c | --watchlist [--days N]')
        return
    for code in codes:
        run(code, days)
        time.sleep(0.8)


if __name__ == '__main__':
    main()
