# -*- coding: utf-8 -*-
"""980081/932305 估值（正确 metricsList）"""
import sys, os, io
sys.path.insert(0, r'D:\hanako\investment-system\scripts')
from common import api_post
from fetch_index_fundamental import METRICS
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

for code in ['980081', '932305']:
    try:
        data = api_post('/index/fundamental', {
            'stockCodes': [code], 'metricsList': METRICS,
            'startDate': '2024-01-01', 'endDate': '2026-08-31',
        })
        if data and len(data) > 0:
            last = data[-1]
            print(f'✅ {code}: {len(data)} 条 | 末 {last["date"][:10]}')
            print('   PE:', last.get('pe_ttm.mcw'), '| PE分位:', last.get('pe_ttm.y10.mcw.cvpos'),
                  '| PB:', last.get('pb.mcw'), '| 股息率:', last.get('dyr.mcw'))
        else:
            print(f'❌ {code}: 空')
    except Exception as e:
        print(f'❌ {code}: {str(e)[:80]}')
