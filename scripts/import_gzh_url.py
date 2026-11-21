# -*- coding: utf-8 -*-
"""import_gzh_url.py — 用单篇微信文章链接补入库（搜狐镜像不覆盖的图文）。

用法：
    python scripts/import_gzh_url.py https://mp.weixin.qq.com/s/xxxx
    python scripts/import_gzh_url.py <url1> <url2> --account 指汇盈

只做三件事：抓标题 / 公众号名 / 发布时间 / 正文纯文本，写进 data/gzh.db 的
gzh_articles（source='wechat'，body_html 留空）。正文 HTML 与配图由
scripts/export_gzh_html.py 统一导出（它认 #js_content）。

为什么单独写：搜狐号镜像只覆盖图文，作者的部分文章只在微信发布；
用户手工给链接时，需要一条不依赖镜像的入库通道。
"""
import argparse
import datetime as dt
import hashlib
import os
import re
import sqlite3
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'data', 'gzh.db')

JS_META = """() => {
    const t = (s) => (s ? s.innerText.trim() : '');
    const pick = (sel) => { const e = document.querySelector(sel); return e ? e.innerText.trim() : ''; };
    const src = document.documentElement.innerHTML;
    let pub = pick('#publish_time');
    if (!pub) {
        const m = src.match(/var\\s+createTime\\s*=\\s*'([^']+)'/);
        if (m) pub = m[1];
    }
    let ct = '';
    const mc = src.match(/var\\s+ct\\s*=\\s*"(\\d+)"/);
    if (mc) ct = mc[1];
    let cn = '';
    const mn = (document.body.innerText || '').match(/20\\d{2}年\\d{1,2}月\\d{1,2}日\\s*\\d{1,2}:\\d{2}/);
    if (mn) cn = mn[0];
    const cont = document.querySelector('#js_content');
    cont && cont.querySelectorAll('script,style').forEach(e => e.remove());
    return {
        title: pick('#activity-name') || document.title,
        account: pick('#js_name'),
        publish: pub,
        ct: ct,
        cn: cn,
        body: cont ? cont.innerText.trim() : '',
        len: cont ? cont.innerText.trim().length : 0
    };
}"""


def norm_pub(pub, ct='', cn=''):
    """把各种时间写法归一成 'YYYY-MM-DD HH:MM:SS'（东八区）。

    微信页面上的时间有三种载体：`#publish_time` 文本、`var createTime`、
    `var ct`（unix 秒，UTC）；另有「2026年11月20日 21:00 广东」这类可见文本。
    """
    if ct and ct.isdigit():
        try:
            ts = int(ct) + 8 * 3600
            return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        except (ValueError, OSError):
            pass
    for s in (pub, cn):
        if not s:
            continue
        s = s.strip().replace('T', ' ')
        m = re.search(r'(20\d{2})\D{1,2}(\d{1,2})\D{1,2}(\d{1,2})\D{1,2}\s*(\d{1,2}):(\d{2})(?::(\d{2}))?', s)
        if m:
            y, mo, d, hh, mi, ss = m.groups()
            return f'{y}-{int(mo):02d}-{int(d):02d} {int(hh):02d}:{mi}:{ss or "00"}'
    return None


def article_id(url):
    tail = url.rstrip('/').split('/')[-1]
    if len(tail) >= 16:
        return tail[:32]
    return hashlib.md5(url.encode()).hexdigest()[:16]


def ensure_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS gzh_articles(
        id TEXT PRIMARY KEY, account TEXT, source TEXT, title TEXT, url TEXT,
        brief TEXT, body TEXT, publish_at TEXT, fetched_at TEXT)""")
    cols = [r[1] for r in c.execute('PRAGMA table_info(gzh_articles)')]
    for col in ('body_html', 'images'):
        if col not in cols:
            c.execute(f'ALTER TABLE gzh_articles ADD COLUMN {col} TEXT')
    c.commit()
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('urls', nargs='+')
    ap.add_argument('--account', default='指汇盈')
    a = ap.parse_args()

    db = ensure_db()
    ok = 0
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 1280, 'height': 900},
                        user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                                    'Chrome/131.0 Safari/537.36'))
        for url in a.urls:
            try:
                pg.goto(url, wait_until='domcontentloaded', timeout=60000)
                pg.wait_for_timeout(1200)
                for _ in range(6):
                    pg.mouse.wheel(0, 900)
                    pg.wait_for_timeout(200)
                m = pg.evaluate(JS_META)
            except Exception as e:
                print(f'[FAIL] {url} -> {type(e).__name__}: {e}')
                continue

            if not m or m['len'] < 30:
                print(f'[SKIP] 无正文（可能是视频/图文卡片）：{url}')
                continue

            pub = norm_pub(m['publish'], m.get('ct', ''), m.get('cn', '')) or dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            aid = article_id(url)
            body = m['body']
            brief = re.sub(r'\s+', ' ', body)[:120]
            db.execute(
                'INSERT OR REPLACE INTO gzh_articles'
                '(id,account,source,title,url,brief,body,publish_at,fetched_at,body_html,images)'
                ' VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL)',
                (aid, m['account'] or a.account, 'wechat', m['title'], url, brief,
                 body, pub, dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            db.commit()
            ok += 1
            print(f'[OK] {pub} | {m["title"][:40]} | {m["len"]} 字 | id={aid}')
            print(f'     公众号：{m["account"] or a.account}')
        b.close()
    print(f'完成：入库 {ok} 篇。下一步导出正文与配图：python scripts/export_gzh_html.py --limit {ok or 1}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
