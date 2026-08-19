# -*- coding: utf-8 -*-
"""515100 费率/分红 补充查询"""
import sys
sys.path.insert(0, r'D:\hanako\investment-system')
import akshare as ak

# 1. 雪球基金详情（含费率）
for code in ['515100', '100032']:
    try:
        d = ak.fund_individual_detail_xq(symbol=code)
        print(f'=== {code} 雪球详情 ===')
        print(d.to_string()[:1200])
        print()
    except Exception as e:
        print(f'{code} detail FAIL:', e)

# 2. 515100 分红（天天基金分红送配）
try:
    fh = ak.fund_fhsp_em(symbol="515100")
    print('515100 分红送配:')
    print(fh.to_string()[:800])
except Exception as e:
    print('fhsp FAIL:', e)
