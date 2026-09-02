# -*- coding: utf-8 -*-
"""从页面 HTML 提取所有图片 URL"""
import requests, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

NOTE_ID = '6a953067000000002102c9fb'
XSEC = 'CBxLX3moLDYMY2Nsjqq6xY_UwB-HLG7eT9KCiXU9C9fh0='
s = requests.Session()
s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36'})
r = s.get(f'https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token={XSEC}', timeout=15)
t = r.text

# 找所有 xhscdn 图片 URL
urls = re.findall(r'https?://[^\s"\'\\]+xhscdn\.com[^\s"\'\\]+', t)
urls = [u.replace('\\u002F', '/') for u in urls]
# 去重 + 过滤 jpg
seen = set()
out = []
for u in urls:
    if u not in seen and ('.jpg' in u or '.png' in u or 'webp' in u):
        seen.add(u)
        out.append(u)
print(f'图片 URL {len(out)} 个:')
for i, u in enumerate(out):
    print(f'  [{i}] {u[:120]}')
