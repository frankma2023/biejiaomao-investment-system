# -*- coding: utf-8 -*-
import requests, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0', 'Accept': 'application/json'}

# skillhub.cn API 猜测
for url in [
    'https://skillhub.cn/api/skills/financial-report-analysis-pro',
    'https://skillhub.cn/api/skill/financial-report-analysis-pro',
    'https://skillhub.cn/skills/financial-report-analysis-pro.json',
    'https://skillhub.cn/api/skills/financial-report-analysis-pro/download',
]:
    try:
        r = requests.get(url, headers=H, timeout=15)
        print(f'{url} -> {r.status_code} len={len(r.text)}')
        if r.status_code == 200 and len(r.text) > 200:
            print('   ', r.text[:400])
    except Exception as e:
        print(f'{url} 异常 {str(e)[:50]}')

# 腾讯 SkillHub 主站
for url in [
    'https://skillhub.cloud.tencent.com/skills/financial-report-analysis-pro',
    'https://skillhub.cloud.tencent.com/api/skills/financial-report-analysis-pro',
]:
    try:
        r = requests.get(url, headers=H, timeout=15)
        print(f'{url} -> {r.status_code} len={len(r.text)}')
        if r.status_code == 200:
            print('   ', r.text[:300].replace(chr(10), ' '))
    except Exception as e:
        print(f'{url} 异常 {str(e)[:50]}')
