# -*- coding: utf-8 -*-
"""抓取公众号文章（经搜狐号镜像，无需登录）。

用法：
    python scripts/fetch_gzh_articles.py            # 增量：抓到已入库的最新一篇即停
    python scripts/fetch_gzh_articles.py --pages 3  # 最多翻 3 页
    python scripts/fetch_gzh_articles.py --backfill # 从头翻，最多 --pages 页（默认 10）
    python scripts/fetch_gzh_articles.py --body     # 同时抓正文（较慢，每篇 ~1s）
    python scripts/fetch_gzh_articles.py --list     # 只列出未入库的新文章，不写库

数据落 data/gzh.db（与 lixinger.db 分开，避免日更时锁住 179 GB 主库）：
    gzh_articles(id TEXT PRIMARY KEY, account, source, title, url,
                 brief, body, publish_at, fetched_at)

口径说明：
- 来源是作者在搜狐号的同步发布，不是公众号原页。正文可能有排版差异/删改，
  付费或仅图片的内容拿不到。搜狐号 id 见 config/gzh_accounts.yaml。
- publicTime 是 UTC，入库转成东八区本地时间。
- 少量文章是视频稿（页面上只有标题和作者，没有段落），这类 body 留空。
  2026-11-12 实测 60 篇里 3 篇属此类，约占 5%。
"""
import argparse
import datetime as dt
import io
import os
import re
import sqlite3
import sys
import time

import requests
from bs4 import BeautifulSoup

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(PROJECT, 'config', 'gzh_accounts.yaml')
DB_PATH = os.path.join(PROJECT, 'data', 'gzh.db')

API = 'https://v2.sohu.com/author-page-api/author-articles/pc/{uid}?pNo={page}'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
TZ = dt.timezone(dt.timedelta(hours=8))

DDL = """
CREATE TABLE IF NOT EXISTS gzh_articles (
    id          TEXT PRIMARY KEY,
    account     TEXT NOT NULL,
    source      TEXT NOT NULL,
    title       TEXT,
    url         TEXT,
    brief       TEXT,
    body        TEXT,
    publish_at  TEXT,
    fetched_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_gzh_pub ON gzh_articles(account, publish_at DESC);
"""


def load_accounts():
    """读 config/gzh_accounts.yaml。没有 pyyaml 时用极简解析兜底。"""
    if not os.path.exists(CONFIG):
        sys.exit('缺少配置文件: %s' % CONFIG)
    text = io.open(CONFIG, encoding='utf-8').read()
    try:
        import yaml
        cfg = yaml.safe_load(text) or {}
        rows = cfg.get('accounts') or []
    except ImportError:
        rows = []
        cur = None
        for line in text.splitlines():
            s = line.strip()
            if s.startswith('- name:'):
                cur = {'name': s.split(':', 1)[1].strip().strip('"\'')}
                rows.append(cur)
            elif cur is not None and ':' in s:
                k, v = s.split(':', 1)
                cur[k.strip()] = v.strip().strip('"\'')
    return [r for r in rows if r.get('enabled', True) not in (False, 'false', 'False')]


def conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.executescript(DDL)
    return c


def to_local(s):
    """'2026-11-12T14:11:37.000+00:00' -> '2026-11-12 22:11:37'（东八区）"""
    if not s:
        return None
    t = s.replace('Z', '+00:00')
    try:
        d = dt.datetime.fromisoformat(t)
    except ValueError:
        return s[:19]
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(TZ).strftime('%Y-%m-%d %H:%M:%S')


def fetch_page(sess, uid, page):
    r = sess.get(API.format(uid=uid, page=page), timeout=20)
    r.raise_for_status()
    j = r.json()
    return (j.get('data') or {}).get('pcArticleVOS') or []


def fetch_body(sess, url):
    """取正文纯文本。搜狐正文容器有几种，按顺序试。"""
    try:
        r = sess.get(url, timeout=25)
        r.raise_for_status()
    except Exception as e:
        return None, str(e)
    soup = BeautifulSoup(r.text, 'html.parser')
    for tag in soup(['script', 'style']):
        tag.decompose()
    node = (soup.select_one('article.article')
            or soup.select_one('#mp-editor')
            or soup.select_one('.article-text')
            or soup.select_one('article'))
    if node is None:
        return None, 'no container'
    paras = [p.get_text(' ', strip=True) for p in node.find_all(['p', 'h1', 'h2', 'h3'])]
    body = '\n'.join(x for x in paras if x)
    return (body or None), None


def export_markdown(db, out_dir):
    """把库里的文章导出成 Markdown，一篇一个文件，另生成 index.md。"""
    rows = list(db.execute(
        'SELECT account,publish_at,title,url,brief,body FROM gzh_articles '
        'ORDER BY publish_at DESC'))
    if not rows:
        print('库里没有文章，无法导出')
        return 0
    os.makedirs(out_dir, exist_ok=True)
    index = ['# 公众号文章导出', '',
             '共 %d 篇，按发布时间倒序。' % len(rows), '']
    for acc, pub, title, url, brief, body in rows:
        safe = re.sub(r'[\\/:*?"<>|\r\n\t]', ' ', title or 'untitled').strip()[:80]
        stamp = (pub or '')[:16].replace(':', '').replace(' ', '_')
        name = '%s_%s.md' % (stamp or 'nodate', safe)
        path = os.path.join(out_dir, name)
        lines = ['# %s' % (title or ''), '',
                 '- 公众号：%s' % acc,
                 '- 发布：%s' % (pub or ''),
                 '- 原文：%s' % (url or ''),
                 '']
        if brief:
            lines += ['> %s' % brief, '']
        lines += [body or '_（本文无正文，原页为视频稿）_', '']
        io.open(path, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines))
        index.append('- %s  [%s](%s)  →  `%s`' % (pub or '', title or '', url or '', name))
    io.open(os.path.join(out_dir, 'index.md'), 'w',
            encoding='utf-8', newline='\n').write('\n'.join(index) + '\n')
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pages', type=int, default=None, help='最多翻几页（每页 20 篇）')
    ap.add_argument('--backfill', action='store_true', help='不因命中已入库而提前停止')
    ap.add_argument('--body', action='store_true', help='同时抓正文')
    ap.add_argument('--list', action='store_true', help='只打印新文章，不写库')
    ap.add_argument('--export', nargs='?', const='docs/gzh', default=None,
                    help='导出 Markdown 到目录（默认 docs/gzh），不抓取')
    args = ap.parse_args()

    if args.export:
        db = conn()
        out = args.export if os.path.isabs(args.export) else os.path.join(PROJECT, args.export)
        n = export_markdown(db, out)
        print('已导出 %d 篇到 %s' % (n, out))
        db.close()
        return

    accounts = load_accounts()
    if not accounts:
        sys.exit('config/gzh_accounts.yaml 里没有启用的账号')

    sess = requests.Session()
    sess.headers.update({'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9'})
    db = conn()
    known = set(r[0] for r in db.execute('SELECT id FROM gzh_articles'))

    max_pages = args.pages or (10 if args.backfill else 3)
    total_new = 0
    for acc in accounts:
        uid = str(acc.get('sohu_id') or '').strip()
        if not uid:
            print('[SKIP] %s 未配置 sohu_id' % acc.get('name'))
            continue
        print('=== %s (sohu %s)' % (acc['name'], uid))
        for page in range(1, max_pages + 1):
            arts = fetch_page(sess, uid, page)
            if not arts:
                print('  p%d 空，停止' % page)
                break
            fresh = [a for a in arts if str(a['id']) not in known]
            print('  p%d %d 篇，其中新 %d 篇' % (page, len(arts), len(fresh)))
            if args.list:
                for a in fresh:
                    print('    %s  %s' % (to_local(a.get('publicTime')), a['title']))
            for a in fresh:
                url = a.get('link') or ''
                if url and not url.startswith('http'):
                    url = 'https://' + url
                body, err = (None, None)
                if args.body and not args.list:
                    body, err = fetch_body(sess, url)
                    time.sleep(0.6)
                if not args.list:
                    db.execute(
                        'INSERT OR REPLACE INTO gzh_articles'
                        '(id,account,source,title,url,brief,body,publish_at,fetched_at)'
                        ' VALUES(?,?,?,?,?,?,?,?,?)',
                        (str(a['id']), acc['name'], 'sohu', a.get('title'), url,
                         a.get('brief'), body, to_local(a.get('publicTime')),
                         dt.datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')))
                total_new += 1
            if not args.list:
                db.commit()
            if not args.backfill and not fresh:
                print('  本页无新文章，停止')
                break
            if fresh and len(fresh) < len(arts) and not args.backfill:
                print('  命中已入库，增量停止')
                break
            if len(arts) < 20:
                break
            time.sleep(0.8)

    n, body_n = db.execute(
        'SELECT COUNT(*), SUM(body IS NOT NULL) FROM gzh_articles').fetchone()
    print('\n本次新增 %d 篇；库内共 %d 篇（含正文 %d 篇）' % (total_new, n, body_n or 0))
    if n:
        for row in db.execute('SELECT publish_at,title FROM gzh_articles '
                              'ORDER BY publish_at DESC LIMIT 5'):
            print('  ', row[0], row[1][:44])
    db.close()


if __name__ == '__main__':
    main()
