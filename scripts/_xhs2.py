# -*- coding: utf-8 -*-
"""小红书页面 SSR 数据解析"""
import requests, re, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

NOTE_ID = '6a953067000000002102c9fb'
XSEC = 'CBxLX3moLDYMY2Nsjqq6xY_UwB-HLG7eT9KCiXU9C9fh0='
s = requests.Session()
s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36'})
r = s.get(f'https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token={XSEC}', timeout=15)
t = r.text

# 提取 __INITIAL_STATE__
m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>', t, re.S)
if not m:
    m = re.search(r'__INITIAL_STATE__\s*=\s*(\{.*)', t, re.S)
if m:
    raw = m.group(1)
    # 处理 undefined
    raw = re.sub(r'undefined', 'null', raw)
    try:
        state = json.loads(raw)
        # 找 note
        def find_note(obj, path=''):
            if isinstance(obj, dict):
                if 'noteCard' in obj or 'noteDetailMap' in obj or 'imageList' in obj:
                    return obj
                for k, v in obj.items():
                    r = find_note(v, path + '/' + k)
                    if r: return r
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    r = find_note(v, path + f'[{i}]')
                    if r: return r
            return None
        note = find_note(state)
        if note:
            print('找到 note 数据，keys:', list(note.keys())[:15])
            # 正文
            desc = note.get('desc') or note.get('noteDesc') or note.get('title')
            if desc: print('\n正文:\n', desc[:2000])
            # 图片
            imgs = note.get('imageList') or note.get('images') or []
            print(f'\n图片 {len(imgs)} 张:')
            for i in imgs:
                if isinstance(i, dict):
                    u = i.get('urlDefault') or i.get('url') or i.get('masterUrl') or i.get('originalUrl') or ''
                    if u: print(' ', u)
                else:
                    print(' ', i)
        else:
            print('未找到 note，顶层 keys:', list(state.keys())[:20])
    except Exception as e:
        print('JSON 解析失败:', str(e)[:100])
        print('前 300 字符:', raw[:300])
else:
    print('未找到 __INITIAL_STATE__')
    print('页面片段:', t[t.find('INITIAL'):t.find('INITIAL')+200] if 'INITIAL' in t else t[:300])
