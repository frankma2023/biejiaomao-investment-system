# -*- coding: utf-8 -*-
"""688531 CANSLIM 评分验证（新浪研报源）"""
import requests
d = requests.get('http://localhost:8788/api/canslim-score?code=688531&date=2026-08-27', timeout=60).json()
print('CANSLIM 总分:', d.get('total') if isinstance(d, dict) else d)
if isinstance(d, dict):
    for k, v in d.items():
        if 'score' in str(k).lower() or k in ('i_institution', 'institution'):
            print(f'  {k}: {v}')
    i = d.get('i') or d.get('i_institution') or {}
    print('I 维度:', i)
