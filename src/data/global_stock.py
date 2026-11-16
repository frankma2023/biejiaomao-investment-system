# -*- coding: utf-8 -*-
"""global-stock-data 的对外封装层。

定位：**外部事件与宏观读数**，不是行情主源。
A 股仍以本地库 data/lixinger.db 为唯一来源；本模块只补三类本地拿不到的东西：

  1. 美股事件原文（SEC EDGAR：8-K / 10-Q / Form 4 / 13F / 每日申报流）
  2. 宏观与利率（美国财政部国债曲线、VIX 期限结构）
  3. 海外行情兜底（腾讯美股行情；仅在本地无该标的时使用）

设计约定：
  * 所有函数不抛异常，失败返回 {'error': ...}，便于日更脚本吞掉错误继续跑。
  * 结果统一为 dict/list，字段名用英文，单位写进键名（如 pe、change_pct）。
  * 需要 SEC Contact 的函数会先读 config/global_stock.yaml 的 sec_contact。
  * 已知在本机不可用的源：FINRA 做空（CDN 重定向）、Yahoo 期权链（403）、
    BLS 宏观（403）。health() 会把它们标成 fail，不要依赖。

上游：https://github.com/simonlin1212/global-stock-data
实现：src/data/gsd_vendor.py（由 scripts/extract_gsd.py 自动抽取）
"""
from __future__ import annotations

import io
import os
import re
import socket
import time

import requests
import yaml

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG = os.path.join(PROJECT, 'config', 'global_stock.yaml')

socket.setdefaulttimeout(30)

_VENDOR = None


def _cfg() -> dict:
    try:
        return yaml.safe_load(io.open(CONFIG, encoding='utf-8')) or {}
    except Exception:
        return {}


def _vendor():
    """惰性加载 vendor 模块，并把 SEC Contact 注入进去。"""
    global _VENDOR
    if _VENDOR is not None:
        return _VENDOR
    from src.data import gsd_vendor as m
    contact = (_cfg().get('sec_contact') or '').strip()
    if contact:
        m.SEC_CONTACT = contact
    _VENDOR = m
    return m


def _wrap(fn, *a, **kw):
    t0 = time.time()
    try:
        data = fn(*a, **kw)
        return {'ok': True, 'ms': int((time.time() - t0) * 1000), 'data': data}
    except Exception as e:
        return {'ok': False, 'ms': int((time.time() - t0) * 1000),
                'error': '%s: %s' % (type(e).__name__, str(e)[:200])}


# ── 1. SEC 事件 ──────────────────────────────────────────────

def cik(ticker: str) -> str:
    """ticker → 10 位 CIK。失败返回空字符串。"""
    r = _wrap(_vendor().ticker_to_cik, ticker)
    return (r.get('data') or {}).get('cik', '') if r.get('ok') else ''


def filings(ticker: str, forms=None, days: int = 60, limit: int = 20) -> dict:
    """公司近期申报列表。

    forms: 如 ['8-K', '10-Q', '4']；None 取全部。
    days:  只保留申报日在该窗口内的记录（按申报日，不是事件日）。
    返回 {'ticker','cik','company','filings':[{form,date,url,primary_document}]}
    """
    m = _vendor()
    c = cik(ticker)
    if not c:
        return {'error': 'ticker %s 找不到 CIK' % ticker}
    out = {}
    for f in (forms or [None]):
        r = _wrap(m.sec_filings, c, f)
        if not r['ok']:
            return {'error': r['error'], 'ticker': ticker}
        d = r['data']
        out.setdefault('company', d.get('company_name'))
        out.setdefault('cik', c)
        out.setdefault('filings', []).extend(d.get('filings') or [])
    seen, rows = set(), []
    for x in out.get('filings', []):
        key = (x.get('form'), x.get('date'), x.get('accession_number'))
        if key in seen or not x.get('date'):
            continue
        seen.add(key)
        rows.append({'form': x.get('form'), 'date': x.get('date'),
                     'url': x.get('url'), 'doc': x.get('primary_document')})
    if days:
        cutoff = _days_ago(days)
        rows = [x for x in rows if x['date'] >= cutoff]
    rows.sort(key=lambda x: x['date'], reverse=True)
    return {'ticker': ticker.upper(), 'cik': c, 'company': out.get('company'),
            'n': len(rows), 'filings': rows[:limit]}


def daily_filings(date_yyyymmdd: str = None, forms=None) -> dict:
    """EDGAR 每日申报流（全市场）。forms 如 ['8-K','13F-HR']。"""
    r = _wrap(_vendor().daily_filings, date_yyyymmdd, forms)
    if not r['ok']:
        return {'error': r['error']}
    d = r['data']
    return {'date': d.get('date'), 'total': d.get('total'),
            'by_form': d.get('by_form'),
            'filings': (d.get('filings') or [])[:200]}


def filing_text(url: str, max_chars: int = 6000) -> dict:
    """抓一份申报的纯文本（去掉标签）。用于读 8-K 原文。"""
    h = {'User-Agent': (_cfg().get('sec_contact') or 'research contact'),
         'Accept-Encoding': 'gzip, deflate'}
    try:
        r = requests.get(url, headers=h, timeout=30)
        r.raise_for_status()
        html = r.content.decode('utf-8', 'replace')
    except Exception as e:
        return {'error': '%s: %s' % (type(e).__name__, str(e)[:200]), 'url': url}
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;?', ' ', text)
    text = re.sub(r'&[a-zA-Z#0-9]+;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return {'url': url, 'chars': len(text), 'text': text[:max_chars]}


def insider(ticker: str, days: int = 90) -> dict:
    """内部人交易（Form 4）。

    上游把「赠与（code G）」也算进卖出统计，会误报成减持。这里按交易代码重算：
    买 = P，卖 = S，赠与 = G（单列，不并入买卖）。
    """
    r = _wrap(_vendor().insider_trades, ticker, None, days)
    if not r['ok']:
        return {'error': r['error'], 'ticker': ticker}
    d = dict(r['data'])
    d['ticker'] = ticker.upper()
    trades = d.get('trades') or []
    buy = [t for t in trades if (t.get('code') or '').upper() == 'P']
    sell = [t for t in trades if (t.get('code') or '').upper() == 'S']
    gift = [t for t in trades if (t.get('code') or '').upper() == 'G']
    d['summary'] = {
        'buy_trades': len(buy), 'buy_shares': sum(t.get('shares') or 0 for t in buy),
        'sell_trades': len(sell), 'sell_shares': sum(t.get('shares') or 0 for t in sell),
        'gift_trades': len(gift), 'gift_shares': sum(t.get('shares') or 0 for t in gift),
    }
    return d


# ── 2. 宏观与利率 ────────────────────────────────────────────

def treasury_curve(days: int = 5) -> dict:
    """美国国债收益率曲线（1M~30Y，日频）。返回最近 days 行。"""
    r = _wrap(_vendor().treasury_yield_curve)
    if not r['ok']:
        return {'error': r['error']}
    rows = r['data'] or []
    return {'n': len(rows), 'rows': rows[:days]}


def vix_structure() -> dict:
    """VIX 期限结构。shape=contango 常态 / backwardation 恐慌。"""
    r = _wrap(_vendor().vix_term_structure)
    return r['data'] if r['ok'] else {'error': r['error']}


def quote_us(ticker: str) -> dict:
    """腾讯美股行情（兜底用）。"""
    r = _wrap(_vendor().us_stock_quote_tencent, ticker)
    return r['data'] if r['ok'] else {'error': r['error']}


# ── 3. 体检 ─────────────────────────────────────────────────

PROBES = [
    ('treasury', lambda m: m.treasury_yield_curve()),
    ('vix', lambda m: m.vix_term_structure()),
    ('sec_cik', lambda m: m.ticker_to_cik('NVDA')),
    ('sec_filings', lambda m: m.sec_filings(m.ticker_to_cik('NVDA')['cik'], '8-K')),
    ('sec_daily', lambda m: m.daily_filings(None, ['8-K'])),
    ('insider', lambda m: m.insider_trades('NVDA', days=30, max_filings=2)),
    ('quote_tencent', lambda m: m.us_stock_quote_tencent('NVDA')),
    ('finra_short', lambda m: m.short_volume_symbol('NVDA', 3)),
    ('yahoo_options', lambda m: m.options_chain('NVDA')),
    ('bls_macro', lambda m: m.us_macro_latest()),
]


def health(verbose: bool = False) -> dict:
    """逐个探测数据源可用性。返回 {name: 'ok'|'fail', ...} 与耗时。"""
    m = _vendor()
    res = {}
    for name, fn in PROBES:
        r = _wrap(fn, m)
        res[name] = {'status': 'ok' if r['ok'] else 'fail', 'ms': r['ms']}
        if not r['ok']:
            res[name]['error'] = r['error']
        if verbose:
            print('%-14s %-4s %5dms %s' % (name, res[name]['status'], r['ms'],
                                           res[name].get('error', '')))
    ok = sum(1 for v in res.values() if v['status'] == 'ok')
    return {'ok': ok, 'total': len(res), 'sources': res}


# ── 4. 事件简报（喂给分析用） ────────────────────────────────

def events_brief(ticker: str, days: int = 7) -> str:
    """把「近期申报 + 内部人 + 利率 + 情绪」压成一段纯文本，供事件分析引用。"""
    lines = ['### %s 外部事件快照（近 %d 日）' % (ticker.upper(), days)]
    f = filings(ticker, forms=['8-K', '10-Q', '4', '13F-HR'], days=days, limit=10)
    if f.get('error'):
        lines.append('- SEC 申报：取数失败（%s）' % f['error'])
    else:
        lines.append('- 公司：%s（CIK %s）' % (f.get('company'), f.get('cik')))
        for x in f.get('filings', []):
            lines.append('  · %s %s %s' % (x['date'], x['form'], x['url']))
        if not f.get('filings'):
            lines.append('  · 窗口内无申报')
    ins = insider(ticker, days=max(days, 30))
    if ins.get('error'):
        lines.append('- 内部人：取数失败（%s）' % ins['error'])
    else:
        s = ins.get('summary') or {}
        lines.append('- 内部人（近 %d 日）：买 %d 笔 / 卖 %d 笔 / 赠与 %d 笔' % (
            max(days, 30), s.get('buy_trades', 0), s.get('sell_trades', 0),
            s.get('gift_trades', 0)))
        for t in (ins.get('trades') or [])[:5]:
            lines.append('  · %s %s %s %s 股（%s）' % (
                t.get('filed'), t.get('owner'), t.get('role'), t.get('shares'),
                t.get('code_label')))
    tc = treasury_curve(2)
    if tc.get('error'):
        lines.append('- 美债：取数失败（%s）' % tc['error'])
    else:
        row = (tc.get('rows') or [{}])[0]
        lines.append('- 美债 %s：2Y %s / 10Y %s / 30Y %s' % (
            row.get('Date'), row.get('2 Yr'), row.get('10 Yr'), row.get('30 Yr')))
    v = vix_structure()
    if v.get('error'):
        lines.append('- 情绪：取数失败（%s）' % v['error'])
    else:
        lines.append('- 情绪 %s：VIX %.2f，期限结构 %s，近远端价差 %.2f' % (
            v.get('date'), (v.get('points') or [{}])[1].get('close', 0),
            v.get('shape'), v.get('front_back_spread', 0)))
    return '\n'.join(lines)


def _days_ago(n: int) -> str:
    import datetime
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()
