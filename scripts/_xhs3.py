# -*- coding: utf-8 -*-
"""解析 noteDetailMap 拿正文+图片"""
import requests, re, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

NOTE_ID = '6a953067000000002102c9fb'
XSEC = 'CBxLX3moLDYMY2Nsjqq6xY_UwB-HLG7eT9KCiXU9C9fh0='
s = requests.Session()
s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36'})
r = s.get(f'https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token={XSEC}', timeout=15)
t = r.text
m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>', t, re.S)
raw = re.sub(r'undefined', 'null', m.group(1))
state = json.loads(raw)
ndm = state.get('noteDetailMap', {})
print('noteDetailMap keys:', list(ndm.keys()))
for nid, n in ndm.items():
    note = n.get('note', {})
    print('\n标题:', note.get('title') or note.get('display_title'))
    print('正文:\n', (note.get('desc') or '')[:3000])
    imgs = note.get('imageList') or []
    print(f'\n图片 {len(imgs)} 张:')
    for i in imgs:
        if isinstance(i, dict):
            u = i.get('urlDefault') or i.get('url') or i.get('masterUrl') or ''
            if u: print(' ', u)
