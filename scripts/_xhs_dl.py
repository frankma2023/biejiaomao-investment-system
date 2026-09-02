# -*- coding: utf-8 -*-
"""下载小红书 19 张图（补全 !nd_dft_wlteh_jpg_3 后缀）"""
import requests, re, sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

NOTE_ID = '6a953067000000002102c9fb'
XSEC = 'CBxLX3moLDYMY2Nsjqq6xY_UwB-HLG7eT9KCiXU9C9fh0='
s = requests.Session()
s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36',
                  'Referer': 'https://www.xiaohongshu.com/'})
r = s.get(f'https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token={XSEC}', timeout=15)
t = r.text
urls = re.findall(r'https?://[^\s"\'\\]+xhscdn\.com[^\s"\'\\]+', t)
urls = [u.replace('\\u002F', '/') for u in urls]
seen, out = set(), []
for u in urls:
    if u not in seen and ('.jpg' in u or '.png' in u or 'webp' in u):
        seen.add(u)
        out.append(u)

os.makedirs(r'D:\hanako\investment-system\.scratch\xhs_imgs', exist_ok=True)
for i, u in enumerate(out):
    # 补全后缀：URL 截断在 !n，完整为 !nd_dft_wlteh_jpg_3
    if not u.endswith('jpg') and '!' in u and not u.rstrip().endswith(('jpg', 'png', 'webp')):
        u = u.rstrip() + 'd_dft_wlteh_jpg_3' if not u.endswith('!') else u + 'nd_dft_wlteh_jpg_3'
    # 简化：直接去掉 ! 后的参数（原图）
    base = u.split('!')[0] if '!' in u else u
    try:
        r2 = s.get(base, timeout=20)
        if r2.status_code == 200 and len(r2.content) > 3000:
            ext = '.jpg'
            fn = os.path.join(r'D:\hanako\investment-system\.scratch\xhs_imgs', f'xhs_{i}{ext}')
            open(fn, 'wb').write(r2.content)
            print(f'[{i}] {len(r2.content)//1024}KB -> xhs_{i}{ext}')
        else:
            print(f'[{i}] 失败 status={r2.status_code} len={len(r2.content)}')
    except Exception as e:
        print(f'[{i}] 异常 {str(e)[:50]}')
