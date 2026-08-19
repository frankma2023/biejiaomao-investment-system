# -*- coding: utf-8 -*-
"""515100 费率 + 分红历史 + 100032 费率对比"""
import sys
sys.path.insert(0, r'D:\hanako\investment-system')
import akshare as ak

# 1. 515100 ETF 基本信息（费率）
try:
    info = ak.fund_etf_fund_info_em(symbol="515100")
    print('515100 基本信息:')
    print(info.to_string()[:1500])
except Exception as e:
    print('ETF info FAIL:', e)

# 2. 100032 场外费率（雪球）
try:
    info2 = ak.fund_individual_basic_info_xq(symbol="100032")
    print('\n100032 基本信息(雪球):')
    for _, r in info2.iterrows():
        print(' ', r['item'], ':', r['value'])
except Exception as e:
    print('100032 xq FAIL:', e)

# 3. 515100 分红记录
try:
    div = ak.fund_etf_dividend_sina(symbol="515100")
    print('\n515100 分红记录:')
    print(div.to_string()[:1000])
except Exception as e:
    print('分红查询失败:', e)
