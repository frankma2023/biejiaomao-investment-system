# -*- coding: utf-8 -*-
"""小红书笔记 API 抓取"""
import requests, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

NOTE_ID = '6a953067000000002102c9fb'
XSEC = 'CBxLX3moLDYMY2Nsjqq6xY_UwB-HLG7eT9KCiXU9C9fh0='

s = requests.Session()
s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
                  'Referer': 'https://www.xiaohongshu.com/'})

# 先访问页面拿 cookie
try:
    r = s.get(f'https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token={XSEC}', timeout=15)
    print('页面 status:', r.status_code, 'len:', len(r.text))
    print('cookies:', dict(s.cookies))
except Exception as e:
    print('页面失败:', str(e)[:80])

# 笔记详情 API
try:
    r = s.post('https://www.xiaohongshu.com/api/sns/web/v1/feed',
               json={'source_note_id': NOTE_ID, 'xsec_token': XSEC},
               headers={'Content-Type': 'application/json'}, timeout=15)
    print('\nAPI status:', r.status_code)
    d = r.json()
    print('code:', d.get('code'), '| msg:', d.get('msg') or d.get('message'))
    note = (d.get('data') or {}).get('items') or []
    if note:
        n = note[0].get('note_card') or {}
        print('标题:', n.get('display_title'))
        print('正文:', (n.get('desc') or '')[:1500])
        imgs = [i.get('url_default') or i.get('url') for i in (n.get('image_list') or [])]
        print(f'\n图片 {len(imgs)} 张:')
        for u in imgs:
            print(' ', u)
    else:
        print('响应:', str(d)[:500])
except Exception as e:
    print('API失败:', str(e)[:100])
