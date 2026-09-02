# -*- coding: utf-8 -*-
"""验证用户 4 只候选基金"""
import requests, re, sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

cands = [
    ('025497', '易方达国证价值100联接A'),
    ('023389', '易方达中证港股通高股息ETF联接A'),
    ('023917', '华夏国证自由现金流ETF联接A'),
    ('007466', '华泰柏瑞中证红利低波ETF联接A'),
]
print(f"{'代码':<8}{'跟踪':<46}{'费率':<8}")
for code, name in cands:
    try:
        r = requests.get(f'https://fundf10.eastmoney.com/jbgk_{code}.html',
                         headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        r.encoding = 'utf-8'
        t = r.text
        m = re.search(r'业绩比较基准.{0,260}', t, re.S)
        seg = re.sub(r'<[^>]+>', '|', m.group(0)) if m else ''
        seg = re.sub(r'\|+', '|', seg)[:180]
        # 成立时间
        m2 = re.search(r'成立日期</td>\s*<td[^>]*>\s*([^<]+)', t)
        est = m2.group(1).strip() if m2 else '?'
        r2 = requests.get(f'https://fundf10.eastmoney.com/jjfl_{code}.html',
                          headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        r2.encoding = 'utf-8'
        t2 = r2.text
        mg = re.search(r'管理费率</td>\s*<td[^>]*>\s*([\d.]+)%', t2)
        cu = re.search(r'托管费率</td>\s*<td[^>]*>\s*([\d.]+)%', t2)
        mgv = float(mg.group(1)) if mg else 0
        cuv = float(cu.group(1)) if cu else 0
        print(f'{code} 成立{est} | {seg} | {mgv+cuv:.2f}%')
    except Exception as e:
        print(f'{code} 失败: {str(e)[:50]}')
    time.sleep(0.5)
