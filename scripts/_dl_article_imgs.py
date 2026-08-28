# -*- coding: utf-8 -*-
"""提取文章所有图片 URL 并下载"""
import re, os, requests
md = open(r'D:\frankma资料\obsidianDB\Frank的日记\投资\投资心得\一文看清：红利、低波、现金流ETF，附清单（08.17）.md', encoding='utf-8').read()
urls = re.findall(r'!\[[^\]]*\]\((https?://[^)]+)\)', md)
print(f'共 {len(urls)} 张图片:')
outdir = r'D:\hanako\investment-system\.scratch\article_imgs'
os.makedirs(outdir, exist_ok=True)
for i, u in enumerate(urls):
    u = u.split('!800')[0]  # 去掉缩放后缀拿原图
    fn = os.path.join(outdir, f'img{i}.png' if '.png' in u else f'img{i}.jpg')
    try:
        r = requests.get(u, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://xueqiu.com/'}, timeout=20)
        if r.status_code == 200 and len(r.content) > 5000:
            open(fn, 'wb').write(r.content)
            print(f'  [{i}] {len(r.content)//1024}KB -> {fn}  | {u[:80]}')
        else:
            print(f'  [{i}] 失败 status={r.status_code} len={len(r.content)} | {u[:80]}')
    except Exception as e:
        print(f'  [{i}] 异常: {str(e)[:60]} | {u[:80]}')
