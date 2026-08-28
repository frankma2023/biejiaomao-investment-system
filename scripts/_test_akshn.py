# -*- coding: utf-8 -*-
"""akshare 股东人数接口测试"""
import akshare as ak
# 个股历史
try:
    df = ak.stock_zh_a_gdhs_detail_em(symbol="002648")
    print('stock_zh_a_gdhs_detail_em 002648:')
    print(df.head(5))
    print('... 共', len(df), '条')
except Exception as e:
    print('detail_em 失败:', str(e)[:100])
    # 备用接口
    for name in ['stock_zh_a_gdhs', 'stock_zh_a_gdhs_detail_sina']:
        try:
            fn = getattr(ak, name)
            df = fn(symbol='002648') if 'detail' in name else fn()
            print(f'{name}:')
            print(df.head(3))
            break
        except Exception as e2:
            print(f'{name} 失败: {str(e2)[:80]}')
