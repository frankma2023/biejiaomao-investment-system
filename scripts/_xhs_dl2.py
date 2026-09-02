# -*- coding: utf-8 -*-
"""下载小红书图片（带完整 !nd_dft_wlteh_jpg_3 后缀 + Referer）"""
import requests, re, sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

NOTE_ID = '6a953067000000002102c9fb'
XSEC = 'CBxLX3moLDYMY2Nsjqq6xY_UwB-HLG7eT9KCiXU9C9fh0='
s = requests.Session()
s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
                  'Referer': 'https://www.xiaohongshu.com/explore/' + NOTE_ID,
                  'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8'})
r = s.get(f'https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token={XSEC}', timeout=15)
t = r.text
urls = re.findall(r'https?://[^\s"\'\\]+xhscdn\.com[^\s"\'\\]+', t)
urls = [u.replace('\\u002F', '/') for u in urls]
seen, out = set(), []
for u in urls:
    if u not in seen:
        seen.add(u)
        out.append(u)

os.makedirs(r'D:\hanako\investment-system\.scratch\xhs_imgs', exist_ok=True)
ok = 0
for i, u in enumerate(out):
    # 完整 URL（保留 !nd_dft_wlteh_jpg_3 签名后缀）
    full = u if u.endswith(('jpg', 'png', 'webp')) or 'nd_dft' in u else u + 'nd_dft_wlteh_jpg_3'
    try:
        r2 = s.get(full, timeout=25)
        if r2.status_code == 200 and len(r2.content) > 3000:
            fn = os.path.join(r'D:\hanako\investment-system\.scratch\xhs_imgs', f'xhs_{i}.jpg')
            open(fn, 'wb').write(r2.content)
            ok += 1
            print(f'[{i}] OK {len(r2.content)//1024}KB')
        else:
            print(f'[{i}] {r2.status_code} len={len(r2.content)}')
    except Exception as e:
        print(f'[{i}] {str(e)[:50]}')
print(f'\n成功 {ok}/{len(out)}')
