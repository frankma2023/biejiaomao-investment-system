# -*- coding: utf-8 -*-
import requests, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
url = 'https://fund.eastmoney.com/Company/f10/fhsp_80055334.html'
r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
r.encoding = 'utf-8'
t = r.text
print('页面长度:', len(t))
# 找 512890 的所有行
# 表格结构：基金代码/名称/权益登记日/除息日/每份分红/红利发放日
blocks = re.split(r'512890', t)
print(f'512890 出现 {len(blocks)-1} 次')
# 提取每段上下文
for i, seg in enumerate(blocks[1:], 1):
    ctx = re.sub(r'<[^>]+>', '|', seg[:400])
    ctx = re.sub(r'\|+', '|', ctx).strip()
    print(f'--- 第{i}段 ---')
    print(ctx[:280])
