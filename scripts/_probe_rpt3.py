# -*- coding: utf-8 -*-
import requests, re
url = 'http://stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/search/rptid/840957409505/index.phtml'
r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
r.encoding = 'gbk'
t = r.text
i = t.find('盈利预测')
print('盈利预测上下文:')
print(re.sub(r'<[^>]+>', '|', t[max(0,i-200):i+1500]).replace('||', '|')[:900])
# 找表格（预测表）
print('\n--- 表格 ---')
for m in re.finditer(r'<table[^>]*>(.*?)</table>', t, re.S):
    rows = re.findall(r'<td[^>]*>(.*?)</td>', m.group(1), re.S)
    cells = [re.sub(r'<[^>]+>', '', c).strip() for c in rows]
    cells = [c for c in cells if c]
    print(cells[:24])
