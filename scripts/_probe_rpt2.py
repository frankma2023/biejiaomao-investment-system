# -*- coding: utf-8 -*-
"""取 688531 真实研报 rptid → 详情页盈利预测验证"""
import requests, re

def fetch_page(code, p=1):
    url = f'http://stock.finance.sina.com.cn/stock/go.php/vReport_List/kind/search/index.phtml?t1=2&symbol=sh{code}&p={p}&num=50'
    r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
    r.encoding = 'gbk'
    return r.text

txt = fetch_page('688531')
# 提取第一条研报的 rptid + 标题
m = re.search(r'vReport_Show/kind/search/rptid/(\d+)', txt)
print('第一条 rptid:', m.group(1) if m else '无')
m2 = re.search(r'title="([^"]{5,80})"', txt)
print('第一条标题:', m2.group(1) if m2 else '?')

if m:
    url = f'http://stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/search/rptid/{m.group(1)}/index.phtml'
    r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
    r.encoding = 'gbk'
    t = r.text
    print(f'\n详情页长度: {len(t)}')
    for kw in ['盈利预测', '预测', 'EPS', '目标价', '评级', '营业收入', '净利润']:
        print(f'  {kw}: {t.count(kw)} 次')
    # 标题
    mt = re.search(r'<title>([^<]+)</title>', t)
    print('详情页标题:', mt.group(1) if mt else '?')
    # 看正文核心
    mt2 = re.search(r'class="docText"|class="content"|id="artibody"', t)
    if mt2:
        seg = t[mt2.start():mt2.start()+800]
        print('正文开头:', re.sub(r'<[^>]+>', ' ', seg)[:300])
