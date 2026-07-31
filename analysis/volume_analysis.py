"""
A股牛熊转换成交量特征分析 (2016-2026)
比较主要指数每次牛转熊的成交额变化模式
"""
import sqlite3, json
from datetime import datetime

DB = 'D:/hanako/investment-system/data/lixinger.db'
OUT = 'D:/hanako/investment-system/analysis/nx_volume_analysis.json'

# 分析目标
INDICES = {
    '000001': '上证指数',
    '000300': '沪深300',
    '399006': '创业板指',
    '000688': '科创50',
    '000685': '科创芯片',
    '990001': '中华半导体芯片',
    'H30184': '半导体',
}

def get_index_data(code):
    """获取指数日线数据（收盘价+成交额）"""
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT date, close, amount FROM index_daily_kline "
        "WHERE stock_code=? AND kline_type='normal' AND date>='2015-01-01' "
        "ORDER BY date", (code,)
    ).fetchall()
    db.close()
    return [{'date': r['date'], 'close': r['close'], 'amount': r['amount']} for r in rows]

def find_peaks_troughs(data, lookback=250):
    """
    识别主要顶部和底部
    顶部：向前看250天最高点，且之后下跌>15%
    底部：向前看250天最低点，且之后上涨>15%
    """
    peaks = []
    troughs = []
    n = len(data)
    for i in range(lookback, n):
        # 检查是否为 forward peak
        window = data[max(0,i-lookback):i+1]
        window_high = max(d['close'] for d in window)
        if data[i]['close'] == window_high:
            # 之后下跌>12%才算有效顶部
            fwd = data[i:min(n, i+120)]
            if len(fwd) > 20:
                fwd_low = min(d['close'] for d in fwd)
                decline = (data[i]['close'] - fwd_low) / data[i]['close']
                if decline > 0.12:
                    peaks.append({'date': data[i]['date'], 'close': data[i]['close'], 'decline': decline})

        # 底部
        window_low = min(d['close'] for d in window)
        if data[i]['close'] == window_low and i > lookback:
            fwd = data[i:min(n, i+120)]
            if len(fwd) > 20:
                fwd_high = max(d['close'] for d in fwd)
                rise = (fwd_high - data[i]['close']) / data[i]['close']
                if rise > 0.15:
                    troughs.append({'date': data[i]['date'], 'close': data[i]['close'], 'rise': rise})

    # 过滤：保持主要转折点（合并相近的顶/底）
    filtered = []
    for p in peaks:
        if not filtered or (datetime.strptime(p['date'],'%Y-%m-%d') - datetime.strptime(filtered[-1]['date'],'%Y-%m-%d')).days > 180:
            filtered.append(p)
    
    filtered_b = []
    for t in troughs:
        if not filtered_b or (datetime.strptime(t['date'],'%Y-%m-%d') - datetime.strptime(filtered_b[-1]['date'],'%Y-%m-%d')).days > 180:
            filtered_b.append(t)
    
    return filtered, filtered_b

def analyze_volume_around_peak(data, peak_idx, lookback=60, lookfwd=60):
    """分析顶部前后的成交额变化模式"""
    start = max(0, peak_idx - lookback)
    end = min(len(data), peak_idx + lookfwd)
    segment = data[start:end]
    
    if len(segment) < 30:
        return None
    
    # 计算成交额均值
    pre = [d['amount'] for d in segment[:lookback] if d['amount']]
    post = [d['amount'] for d in segment[lookback:] if d['amount']]
    
    if not pre or not post:
        return None
    
    pre_avg = sum(pre) / len(pre)
    post_avg = sum(post) / len(post)
    peak_amount = data[peak_idx]['amount'] or 0
    
    # 成交额变化的各个阶段
    return {
        'pre_avg': round(pre_avg/1e8, 1),    # 顶前60日均额(亿)
        'peak_amount': round(peak_amount/1e8, 1),  # 顶部当日成交额
        'post_avg_30d': round(sum([d['amount'] for d in data[peak_idx:min(len(data),peak_idx+30)] if d['amount']]) / max(1, len([d for d in data[peak_idx:min(len(data),peak_idx+30)] if d['amount']])) / 1e8, 1),
        'post_avg_60d': round(post_avg/1e8, 1),
        'volume_ratio_peak_vs_pre': round((data[peak_idx]['amount'] or 0) / (pre_avg or 1), 2),
        'volume_ratio_post60_vs_pre60': round(post_avg / (pre_avg or 1), 2),
        'pattern': classify_pattern(pre, post)
    }

def classify_pattern(pre_volumes, post_volumes):
    """分类成交额变化模式"""
    pre_avg = sum(pre_volumes) / len(pre_volumes)
    post_avg = sum(post_volumes) / len(post_volumes)
    
    # 将后60日均分3段
    seg_len = len(post_volumes) // 3
    if seg_len < 5:
        return '数据不足'
    s1 = sum(post_volumes[:seg_len]) / seg_len
    s2 = sum(post_volumes[seg_len:2*seg_len]) / seg_len
    s3 = sum(post_volumes[2*seg_len:]) / seg_len
    
    ratio_s1 = s1 / pre_avg if pre_avg else 1
    ratio_s3 = s3 / pre_avg if pre_avg else 1
    
    if pre_avg > 0 and (post_avg / pre_avg) < 0.5:
        return '持续萎缩'
    elif s1 > pre_avg * 0.8 and s3 < pre_avg * 0.5:
        return '先放大后萎缩'
    elif s1 > pre_avg and s3 > pre_avg * 0.7:
        return '活跃维持'
    elif s1 < pre_avg * 0.7 and s3 < pre_avg * 0.4:
        return '快速萎缩'
    elif s1 > pre_avg * 1.2 and s3 < pre_avg * 0.5:
        return '放量见顶后萎缩'
    elif s1 >= pre_avg * 0.6 and s3 <= pre_avg * 0.3:
        return '阶梯式萎缩'
    elif abs(post_avg / pre_avg - 1) < 0.15:
        return '平稳过渡'
    else:
        return '缓慢萎缩'

def run():
    results = {}
    for code, name in INDICES.items():
        print(f"\n分析 {name}({code})...")
        data = get_index_data(code)
        if len(data) < 200:
            print(f"  数据不足: {len(data)} 天")
            continue
        
        closings = [d['close'] for d in data]
        
        peaks, troughs = find_peaks_troughs(data)
        print(f"  识别到 {len(peaks)} 个主要顶部")
        
        index_results = []
        for p in peaks:
            # 找顶部在data中的索引
            idx = next((i for i, d in enumerate(data) if d['date'] == p['date']), None)
            if idx is None or idx < 60 or idx > len(data) - 30:
                continue
            
            vol = analyze_volume_around_peak(data, idx)
            if vol:
                # 找当前顶之前最近的一个底部
                prev_trough = None
                for t in reversed(troughs):
                    if t['date'] < p['date']:
                        prev_trough = t
                        break
                
                # 计算顶部前涨了多少
                rise_from_trough = 0
                if prev_trough:
                    trough_idx = next((i for i, d in enumerate(data) if d['date'] == prev_trough['date']), None)
                    if trough_idx:
                        rise = (p['close'] - prev_trough['close']) / prev_trough['close'] * 100
                        rise_from_trough = round(rise, 1)
                
                entry = {
                    'top_date': p['date'],
                    'top_close': round(p['close'], 2),
                    'decline_from_top': round(p['decline'] * 100, 1),
                    'rise_from_trough': rise_from_trough,
                    'pre_60d_avg_amount': vol['pre_avg'],
                    'peak_amount': vol['peak_amount'],
                    'post_30d_avg_amount': vol['post_avg_30d'],
                    'post_60d_avg_amount': vol['post_avg_60d'],
                    'peak_vs_pre_ratio': vol['volume_ratio_peak_vs_pre'],
                    'post60_vs_pre60_ratio': vol['volume_ratio_post60_vs_pre60'],
                    'pattern': vol['pattern']
                }
                index_results.append(entry)
                print(f"  {p['date']}: 顶={p['close']:.0f}, 跌幅={p['decline']*100:.0f}%, "
                      f"前60日均额={vol['pre_avg']}亿, 后60日={vol['post_avg_60d']}亿, "
                      f"模式={vol['pattern']}")
        
        results[name] = index_results
    
    # 输出总结
    print("\n" + "="*80)
    print("牛熊转换成交量模式总结")
    print("="*80)
    
    all_patterns = {}
    for name, tops in results.items():
        for t in tops:
            p = t['pattern']
            all_patterns[p] = all_patterns.get(p, 0) + 1
    
    for pattern, count in sorted(all_patterns.items(), key=lambda x: -x[1]):
        print(f"  {pattern}: {count} 次")
    
    # 保存
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到 {OUT}")

if __name__ == '__main__':
    run()
