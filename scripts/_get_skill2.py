# -*- coding: utf-8 -*-
import requests, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
r = requests.get('https://skillhub.cn/skills/financial-report-analysis-pro',
                 headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
t = r.text
print('=== 所有链接 ===')
for m in re.finditer(r'href="([^"]+)"', t):
    u = m.group(1)
    if u and not u.startswith('#'):
        print(u[:150])
print('\n=== 正文文本 ===')
body = re.sub(r'<script.*?</script>|<style.*?</style>', '', t, flags=re.S)
body = re.sub(r'<[^>]+>', ' ', body)
body = re.sub(r'\s+', ' ', body).strip()
print(body[:1500])
