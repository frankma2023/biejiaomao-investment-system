# -*- coding: utf-8 -*-
import requests, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
url = 'https://skillhub.cn/skills/financial-report-analysis-pro'
r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0'}, timeout=20)
print('status:', r.status_code, 'len:', len(r.text))
# 找 github / 安装链接
import re
for kw in ['github', 'install', 'clawhub', '下载', '安装', '.zip']:
    for m in list(re.finditer(kw, r.text, re.I))[:3]:
        seg = r.text[max(0, m.start()-120):m.start()+150]
        seg = re.sub(r'<[^>]+>', ' ', seg)
        seg = re.sub(r'\s+', ' ', seg)
        print(f'[{kw}]', seg[:200])
        print('---')
