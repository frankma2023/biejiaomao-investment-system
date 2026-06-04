"""重新计算 stats 并保存 data.json + signals.json（不重跑回测）"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from scanners.chanlun_backtest_compare import compute_stats

OUT = r'D:\hanako\investment-system\web\chanlun-backtest-compare'

# 读取信号
with open(os.path.join(OUT, 'signals.json'), encoding='utf-8') as f:
    sig_raw = json.load(f)

# 转换为内部格式
signals = []
for s in sig_raw:
    rets = {}
    if s.get('ret_5d') is not None: rets[5] = s['ret_5d']
    if s.get('ret_10d') is not None: rets[10] = s['ret_10d']
    if s.get('ret_20d') is not None: rets[20] = s['ret_20d']
    signals.append({
        'code': s['code'], 'type': s['type'], 'side': 'buy',
        'dt': s['dt'], 'price': s['price'], 'confidence': s.get('confidence','低'),
        'returns': rets
    })

# 暂时不处理随机点
stats = compute_stats(signals, [])

with open(os.path.join(OUT, 'data.json'), 'w', encoding='utf-8') as f:
    json.dump(stats, f, ensure_ascii=False, indent=2)

print('Stats recomputed:')
for k, v in sorted(stats.items()):
    if 'avg' in v: print(f'  {k}: {v}')
