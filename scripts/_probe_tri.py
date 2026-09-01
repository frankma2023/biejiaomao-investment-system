# -*- coding: utf-8 -*-
"""探测各指数全收益代码 + akshare 可拉性"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import akshare as ak

# 已知：000922 中证红利 -> H00922（库里已有）
# 待验证代码：
candidates = {
    'H30269': '中证红利低波动（价格，已知）',
    'H30269_F': '红利低波全收益(猜测)',
    'H50269': '红利低波全收益(猜测)',
    'H30955': '930955红利低波100全收益(猜测)',
    'H31468': '931468红利质量全收益(猜测)',
    'H30914': '930914港股通高股息全收益(猜测)',
    'H00922': '中证红利全收益(已知)',
}

for code, note in candidates.items():
    try:
        df = ak.stock_zh_index_daily_em(symbol=code)
        if df is not None and len(df) > 0:
            print(f'✅ {code} ({note}): {len(df)} 条 {str(df.iloc[0]["date"])[:10]} ~ {str(df.iloc[-1]["date"])[:10]} 末值 {df.iloc[-1]["close"]:.2f}')
        else:
            print(f'❌ {code} ({note}): 空')
    except Exception as e:
        print(f'❌ {code} ({note}): {str(e)[:50]}')
