# -*- coding: utf-8 -*-
"""雪球抓取 v3：完整登录 cookie 串"""
import requests, re

COOKIE = (
    'xq_r_token=07bb87c11f1d5184f2693df94e2a7f74d49c3011; '
    'xq_a_token=b9043447774d18b539232a6ce651b8f5e10a90d1; '
    'xq_id_token=eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJ1aWQiOjkzNTI1MDY4MTYsImlzcyI6InVjIiwiZXhwIjoxNzg5MjI5ODgyLCJjdG0iOjE3ODY2Mzc4ODI4NjgsImNpZCI6ImQ5ZDBuNEFadXAifQ.Pd6zlRgJCfFaA2WqJQy5AG7ygMgl6IQLERLzw38pIAgmS2zEFAEp63C6MshaDrqUozgwEmhe4xVgvID6dn9vrgNozunYn348c3Wp7nDnH42u-jObodycLxYWiIG-lXeMNyRS41c39oke9yYZUtM4NaQrGbSTEQz1WHCESXrzFTBgLpJwORygjKcj1A5pCa1J1XuaXzDKyvrXO63JfQHNnFOjMdQPmxOKTPAqSwtNojXgrbLuhBUg3gO8MNEf0aFQ79F3J9F5EzkQHNv-znEoX_zLwYdHK07LmhXfuHQH73wCHpZNspxhNrJM5TsR7-pEE_Dqu6m8SusTxiUej9wnOg; '
    'xq_is_login=xq_is_login; '
    'xqat=b9043447774d18b539232a6ce651b8f5e10a90d1'
)
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'

s = requests.Session()
s.headers.update({'User-Agent': UA, 'Cookie': COOKIE, 'Referer': 'https://xueqiu.com/'})

# 先主页（种 acw_tc）
try:
    s.get('https://xueqiu.com/', timeout=15)
    print('主页 cookies:', list(s.cookies.keys()))
except Exception as e:
    print('主页 ERR', e)

for url in [
    'https://xueqiu.com/statuses/show.json?id=403136581',
    'https://xueqiu.com/statuses/show.json?code=xueqiu&id=403136581',
]:
    r = s.get(url, timeout=15)
    ct = r.headers.get('content-type', '')
    print(f'{url[:55]} → {r.status_code} {ct[:25]} len={len(r.text)}')
    if r.status_code == 200 and 'json' in ct:
        try:
            d = r.json()
            t = d.get('target') or d
            if isinstance(t, dict):
                txt = t.get('text') or t.get('description') or ''
                title = t.get('title', '')
                user = t.get('user', {})
                if isinstance(user, dict):
                    print('作者:', user.get('screen_name'))
                if txt:
                    clean = re.sub(r'<[^>]+>', '', txt)
                    clean = clean.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>').replace('&#39;', "'")
                    clean = re.sub(r'\n{3,}', '\n\n', clean).strip()
                    with open(r'D:\hanako\investment-system\scripts\_xueqiu_article.txt', 'w', encoding='utf-8') as f:
                        f.write(title + '\n\n' + clean)
                    print(f'✅ 正文 {len(clean)} 字符已存')
                    print('--- 开头 600 字 ---')
                    print(clean[:600])
                    break
        except Exception as e:
            print('json ERR', e)
    elif r.status_code == 200 and 'text/html' in ct and 'renderData' in r.text:
        print('⚠️ 仍被 WAF 拦截')
