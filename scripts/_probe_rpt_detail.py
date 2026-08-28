# -*- coding: utf-8 -*-
"""新浪研报详情页盈利预测表验证"""
import requests, re
# 688531 最新研报（中原证券 8-25 中报点评）
url = 'http://stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/search/rptid/841200078001/index.phtml'
r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
r.encoding = 'gbk'
txt = r.text
print('页面长度:', len(txt))
# 找盈利预测线索
for kw in ['盈利预测', '预测', 'EPS', '目标价', '评级', 'table']:
    c = txt.count(kw)
    print(f'{kw}: {c} 次')
# 标题
m = re.search(r'<title>([^<]+)</title>', txt)
print('标题:', m.group(1) if m else '?')
# 找正文表格
i = txt.find('盈利预测')
if i > 0:
    print('盈利预测上下文:', txt[max(0,i-100):i+400].replace('\n', ' ')[:450])
