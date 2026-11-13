# -*- coding: utf-8 -*-
"""把 data/gzh.db 里的文章导出成自包含 HTML（含配图本地化）。

为什么需要浏览器：搜狐正文里的 <img> 初始是加密串，浏览器 JS 解码后才写入真实
URL（q*.itc.cn/images01/...）。纯 requests 只能拿到密文，所以用 Playwright 渲染
并滚动触发解码，再用同一上下文下载图片，避免 403。

用法：
    python scripts/export_gzh_html.py                 # 全部未导出的文章
    python scripts/export_gzh_html.py --limit 3       # 只导最近 3 篇（试跑）
    python scripts/export_gzh_html.py --force         # 重新导出全部
输出：
    docs/gzh/html/<日期>_<时间>_<标题>.html
    docs/gzh/html/images/<文章id>/<hash>.<ext>
    docs/gzh/html/index.html
"""
import argparse
import datetime as dt
import hashlib
import html as H
import io
import json
import os
import re
import sqlite3
import sys

from playwright.sync_api import sync_playwright

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT, 'data', 'gzh.db')
OUT_DIR = os.path.join(PROJECT, 'docs', 'gzh', 'html')
IMG_DIR = os.path.join(OUT_DIR, 'images')

CSS = """
:root{--bg:#f7f7f9;--card:#fff;--txt:#1f2328;--muted:#6b7280;--line:#e5e7eb;--accent:#b45309}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
 font:16px/1.85 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:28px 20px 60px}
.meta{color:var(--muted);font-size:13px;margin-bottom:6px}
h1{font-size:26px;line-height:1.4;margin:6px 0 14px}
.src{font-size:13px;color:var(--muted);margin-bottom:22px}
.src a{color:var(--accent)}
article{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:26px 28px}
article p{margin:0 0 1em}
article img{max-width:100%;height:auto;display:block;margin:18px auto;border-radius:8px}
article h2,article h3{margin:1.4em 0 .6em}
.foot{margin-top:26px;font-size:12px;color:var(--muted);text-align:center}
"""


def slug(s, n=70):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', ' ', s or 'untitled').strip()
    s = re.sub(r'\s+', ' ', s)
    return s[:n]


def db_conn():
    c = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in c.execute('PRAGMA table_info(gzh_articles)')]
    if 'body_html' not in cols:
        c.execute('ALTER TABLE gzh_articles ADD COLUMN body_html TEXT')
    if 'images' not in cols:
        c.execute('ALTER TABLE gzh_articles ADD COLUMN images TEXT')
    c.commit()
    return c


def extract(pg):
    """返回 (innerHTML, [真实图片URL], 纯文本长度)。"""
    return pg.evaluate("""() => {
        const cont = document.querySelector('article.article')
                  || document.querySelector('#mp-editor')
                  || document.querySelector('.article-text')
                  || document.querySelector('article');
        if (!cont) return null;
        cont.querySelectorAll('script,style').forEach(e => e.remove());
        const imgs = [...cont.querySelectorAll('img')]
            .map(i => i.currentSrc || i.src).filter(u => u && u.startsWith('http'));
        return {html: cont.innerHTML, imgs: imgs, len: cont.innerText.length};
    }""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='只处理最近 N 篇')
    ap.add_argument('--force', action='store_true', help='已导出的也重做')
    args = ap.parse_args()

    os.makedirs(IMG_DIR, exist_ok=True)
    db = db_conn()
    sql = ('SELECT id,account,publish_at,title,url,body FROM gzh_articles '
           + ('' if args.force else 'WHERE body_html IS NULL ')
           + 'ORDER BY publish_at DESC')
    rows = list(db.execute(sql))
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print('没有待导出的文章')
        return
    print('待导出 %d 篇' % len(rows))

    done = 0
    failures = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 1280, 'height': 900})
        for i, (aid, acc, pub, title, url, body) in enumerate(rows, 1):
            try:
                pg.goto(url, wait_until='domcontentloaded', timeout=45000)
                pg.wait_for_timeout(600)
                for _ in range(8):
                    pg.mouse.wheel(0, 900)
                    pg.wait_for_timeout(250)
                pg.wait_for_timeout(600)
                data = extract(pg)
                if not data or data['len'] < 20:
                    failures.append((title, 'no content'))
                    print('  [%d/%d] %s -> 无正文' % (i, len(rows), title[:26]))
                    continue

                body_html = data['html']
                img_urls = list(dict.fromkeys(data['imgs']))
                sub = os.path.join(IMG_DIR, str(aid))
                local = {}
                if img_urls:
                    os.makedirs(sub, exist_ok=True)
                for u in img_urls:
                    try:
                        resp = pg.request.get(u, timeout=25000)
                        if not resp.ok:
                            continue
                        raw = resp.body()
                        ext = (re.search(r'\.(png|jpe?g|gif|webp|bmp)(?:\?|$)', u, re.I) or [None, 'jpg'])[1]
                        name = hashlib.md5(u.encode()).hexdigest()[:12] + '.' + ext.lower()
                        io.open(os.path.join(sub, name), 'wb').write(raw)
                        local[u] = 'images/%s/%s' % (aid, name)
                    except Exception:
                        continue
                for u, rel in local.items():
                    body_html = body_html.replace('"%s"' % u, '"%s"' % rel)
                    body_html = body_html.replace("'%s'" % u, "'%s'" % rel)
                    # 容器里常留协议相对写法 //q8.itc.cn/...，currentSrc 却是绝对地址
                    body_html = body_html.replace('"%s"' % u.replace('https:', ''),
                                                  '"%s"' % rel)
                    body_html = body_html.replace("'%s'" % u.replace('https:', ''),
                                                  "'%s'" % rel)
                body_html = re.sub(r'\s(width|height|max-width)="\d+"', '', body_html)

                stamp = (pub or '')[:16].replace(':', '').replace(' ', '_')
                fname = '%s_%s.html' % (stamp or 'nodate', slug(title))
                page = ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width,initial-scale=1">'
                        '<title>%s</title><style>%s</style></head><body><div class="wrap">'
                        '<div class="meta">%s · %s</div><h1>%s</h1>'
                        '<div class="src">原文：<a href="%s" target="_blank" rel="noopener">%s</a></div>'
                        '<article>%s</article>'
                        '<div class="foot">本地快照 %s · 图片已本地化</div>'
                        '</div></body></html>') % (
                    H.escape(title or ''), CSS, H.escape(acc or ''), H.escape(pub or ''),
                    H.escape(title or ''), H.escape(url or ''), H.escape(url or ''),
                    body_html, dt.datetime.now().strftime('%Y-%m-%d %H:%M'))
                io.open(os.path.join(OUT_DIR, fname), 'w',
                        encoding='utf-8', newline='\n').write(page)
                db.execute('UPDATE gzh_articles SET body_html=?, images=? WHERE id=?',
                           (body_html, json.dumps(list(local.values()), ensure_ascii=False), aid))
                db.commit()
                done += 1
                print('  [%d/%d] %s -> %s（图 %d）'
                      % (i, len(rows), title[:26], fname[:34], len(local)))
            except Exception as e:
                failures.append((title, str(e)[:80]))
                print('  [%d/%d] %s -> 失败 %s' % (i, len(rows), title[:26], str(e)[:60]))
        b.close()

    # 索引页
    items = list(db.execute(
        'SELECT publish_at,title,url,id FROM gzh_articles ORDER BY publish_at DESC'))
    rows_html = []
    for pub, title, url, aid in items:
        stamp = (pub or '')[:16].replace(':', '').replace(' ', '_')
        import glob
        cand = glob.glob(os.path.join(OUT_DIR, '%s_*.html' % stamp)) if stamp else []
        name = os.path.basename(cand[0]) if cand else None
        if name and os.path.exists(os.path.join(OUT_DIR, name)):
            rows_html.append('<li><span class="d">%s</span><a href="%s">%s</a></li>'
                             % (H.escape(pub or ''), H.escape(name), H.escape(title or '')))
        else:
            rows_html.append('<li><span class="d">%s</span><a href="%s" target="_blank">%s</a>'
                             '<span class="ext">（未导出）</span></li>'
                             % (H.escape(pub or ''), H.escape(url or ''), H.escape(title or '')))
    idx = ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
           '<title>公众号文章 · 指汇盈</title><style>%s'
           'ul{list-style:none;padding:0}li{background:var(--card);border:1px solid var(--line);'
           'border-radius:10px;padding:12px 16px;margin-bottom:8px}'
           'li a{color:var(--txt);text-decoration:none;font-weight:600}'
           'li a:hover{color:var(--accent)}.d{color:var(--muted);font-size:12px;'
           'display:block;margin-bottom:2px}.ext{color:var(--muted);font-size:12px}'
           '</style></head><body><div class="wrap"><h1>公众号文章 · 指汇盈</h1>'
           '<div class="meta">共 %d 篇 · 按发布时间倒序 · 本地 HTML 快照</div><ul>%s</ul>'
           '</div></body></html>') % (CSS, len(items), '\n'.join(rows_html))
    io.open(os.path.join(OUT_DIR, 'index.html'), 'w',
            encoding='utf-8', newline='\n').write(idx)
    print('\n导出成功 %d 篇，失败 %d 篇；索引页 %s'
          % (done, len(failures), os.path.join(OUT_DIR, 'index.html')))
    for t, e in failures[:5]:
        print('  失败：%s | %s' % (t[:30], e))


if __name__ == '__main__':
    main()
