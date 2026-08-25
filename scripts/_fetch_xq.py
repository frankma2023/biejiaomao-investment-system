# -*- coding: utf-8 -*-
"""带 cookie 抓取雪球文章"""
import requests, re, os

TOKEN = 'b9043447774d18b539232a6ce651b8f5e10a90d1'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'

s = requests.Session()
s.headers.update({
    'User-Agent': UA,
    'Cookie': f'xq_a_token={TOKEN}',
    'Referer': 'https://xueqiu.com/',
})

# 方式1：show.json API
r = s.get('https://xueqiu.com/statuses/show.json?id=403136581', timeout=15)
print('show.json:', r.status_code, 'len', len(r.text), '| type:', r.headers.get('content-type'))
text = ''
if r.status_code == 200 and 'text/html' not in (r.headers.get('content-type') or ''):
    try:
        d = r.json()
        t = d.get('target') or d
        if isinstance(t, dict):
            text = t.get('text') or t.get('description') or ''
            print('title:', (t.get('title') or '')[:80])
            print('author:', (t.get('user', {}).get('screen_name') if isinstance(t.get('user'), dict) else ''))
    except Exception as e:
        print('json ERR', e)
if not text:
    # 方式2：页面
    r2 = s.get('https://xueqiu.com/9548638136/403136581', timeout=15)
    print('页面:', r2.status_code, 'len', len(r2.text))
    if 'renderData' in r2.text:
        print('⚠️ 仍被 WAF 拦截')
    else:
        m = re.search(r'<div class="article__bd__detail">([\s\S]*?)</div>', r2.text)
        if m:
            text = m.group(1)

if text:
    clean = re.sub(r'<[^>]+>', '', text)
    clean = clean.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>').replace('&#39;', "'")
    clean = re.sub(r'\n{3,}', '\n\n', clean).strip()
    with open(r'D:\hanako\investment-system\scripts\_xueqiu_article.txt', 'w', encoding='utf-8') as f:
        f.write(clean)
    print(f'\n正文 {len(clean)} 字符，已存文件')
    print('--- 开头 800 字 ---')
    print(clean[:800])
else:
    print('未能提取正文')
