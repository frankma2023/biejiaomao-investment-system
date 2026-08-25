# -*- coding: utf-8 -*-
"""雪球抓取 v2：先主页种 WAF cookie，再请求 API + 多端点变体"""
import requests, re

TOKEN = 'b9043447774d18b539232a6ce651b8f5e10a90d1'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'

s = requests.Session()
s.headers['User-Agent'] = UA

# 1. 先访问主页（种 aliyun WAF cookie）
try:
    r0 = s.get('https://xueqiu.com/', timeout=15)
    print('主页:', r0.status_code, 'cookies:', list(s.cookies.keys()))
except Exception as e:
    print('主页 ERR', e)

# 2. 带 xq_a_token + WAF cookie 请求 API（多种端点变体）
s.cookies.set('xq_a_token', TOKEN, domain='.xueqiu.com')
for url in [
    'https://xueqiu.com/statuses/show.json?id=403136581',
    'https://xueqiu.com/statuses/show.json?id=403136581&_=0',
    'https://stock.xueqiu.com/v5/stock/comment/status.json?status_id=403136581',
]:
    try:
        r = s.get(url, timeout=15, headers={'Referer': 'https://xueqiu.com/'})
        ct = r.headers.get('content-type', '')
        print(f'{url[:60]} → {r.status_code} {ct[:30]} len={len(r.text)}')
        if r.status_code == 200 and 'json' in ct:
            d = r.json()
            t = d.get('target') or d
            if isinstance(t, dict) and (t.get('text') or t.get('description')):
                txt = t.get('text') or t.get('description')
                clean = re.sub(r'<[^>]+>', '', txt)
                clean = clean.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>')
                with open(r'D:\hanako\investment-system\scripts\_xueqiu_article.txt', 'w', encoding='utf-8') as f:
                    f.write(clean)
                print(f'✅ 正文 {len(clean)} 字符已存')
                print(clean[:500])
                break
    except Exception as e:
        print(f'{url[:50]} ERR {str(e)[:80]}')
