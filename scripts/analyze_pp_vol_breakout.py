"""
口袋支点 × 放量倍数分层 + 连续信号分析
"""
import sqlite3, numpy as np
from collections import defaultdict

DB = r"D:\hanako\investment-system\data\lixinger.db"
START = '2016-01-01'

def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    
    print("加载口袋支点信号...")
    sigs = conn.execute("""
        SELECT stock_code, date, close FROM pocket_pivot_daily
        WHERE date >= ? ORDER BY stock_code, date
    """, (START,)).fetchall()
    print(f"  {len(sigs)} 条")
    
    # 按股票分组
    by_code = defaultdict(list)
    for s in sigs:
        by_code[s['stock_code']].append((s['date'], s['close']))
    
    # 分层: 量比<1.5, 1.5~3, 3~5, 5~10, >=10
    tiers = [
        ('量比<1.5', lambda r: r < 1.5, {5:[],10:[],20:[],60:[]}),
        ('量比1.5~3x', lambda r: 1.5 <= r < 3, {5:[],10:[],20:[],60:[]}),
        ('量比3~5x', lambda r: 3 <= r < 5, {5:[],10:[],20:[],60:[]}),
        ('量比5~10x', lambda r: 5 <= r < 10, {5:[],10:[],20:[],60:[]}),
        ('量比≥10x', lambda r: r >= 10, {5:[],10:[],20:[],60:[]}),
    ]
    # 连续信号
    consecutive = {'连续1天': {5:[],10:[],20:[],60:[]},
                   '连续2天': {5:[],10:[],20:[],60:[]},
                   '连续≥3天': {5:[],10:[],20:[],60:[]}}
    
    counts = defaultdict(int)
    
    for ci, (code, dates) in enumerate(by_code.items()):
        if ci % 500 == 0:
            print(f"  进度: {ci}/{len(by_code)}")
        
        krows = conn.execute("""
            SELECT date, amount, close FROM daily_kline
            WHERE stock_code=? AND date >= '2015-10-01'
            ORDER BY date
        """, (code,)).fetchall()
        if len(krows) < 60: continue
        
        kdates = [r['date'] for r in krows]
        amounts = [r['amount'] for r in krows]
        closes = [r['close'] for r in krows]
        
        # 找连续信号段
        sig_dates = set(d[0] for d in dates)
        date_idx_map = {d: i for i, d in enumerate(kdates)}
        
        # Build consecutive runs
        runs = []
        current_run = []
        for sdate, sclose in dates:
            idx = date_idx_map.get(sdate)
            if idx is None or idx < 50: continue
            if current_run and current_run[-1][0] != sdate:  # Check if consecutive trading days
                prev_date = current_run[-1][0]
                prev_idx = date_idx_map.get(prev_date)
                if prev_idx is not None and date_idx_map.get(sdate) == prev_idx + 1:
                    current_run.append((sdate, sclose))
                else:
                    runs.append(current_run)
                    current_run = [(sdate, sclose)]
            else:
                current_run.append((sdate, sclose))
        if current_run:
            runs.append(current_run)
        
        for run in runs:
            run_len = min(len(run), 3)  # cap at 3
            con_key = '连续1天' if run_len == 1 else ('连续2天' if run_len == 2 else '连续≥3天')
            
            for sdate, sclose in run:
                idx = date_idx_map[sdate]
                ma50 = sum(amounts[idx-50:idx]) / 50
                ratio = amounts[idx] / ma50 if ma50 > 0 else 0
                
                # Assign tier
                matched = False
                for tname, check, storage in tiers:
                    if check(ratio):
                        for days in [5, 10, 20, 60]:
                            fwd = idx + days
                            if fwd < len(krows):
                                fwd_close = closes[fwd]
                                if fwd_close and sclose and sclose > 0:
                                    ret = (fwd_close - sclose) / sclose
                                    storage[days].append(ret)
                        matched = True
                        counts[tname] += 1
                        break
                
                # Also add to consecutive group
                for days in [5, 10, 20, 60]:
                    fwd = idx + days
                    if fwd < len(krows):
                        fwd_close = closes[fwd]
                        if fwd_close and sclose and sclose > 0:
                            ret = (fwd_close - sclose) / sclose
                            consecutive[con_key][days].append(ret)
    
    conn.close()
    
    # Report: Volume tiers
    print(f"\n{'='*70}")
    print(f"📊 量比分层分析 (2024~2026)")
    print(f"{'='*70}")
    for days in [5, 10, 20, 60]:
        print(f"\n{'─'*50}")
        print(f"持有 {days} 日:")
        print(f"{'量比层':<16} {'样本':>7} {'胜率':>7} {'中位':>8} {'均值':>8}")
        for tname, _, storage in tiers:
            data = storage[days]
            if not data: continue
            arr = np.array(data)
            win = (arr > 0).sum() / len(arr) * 100
            med = np.median(arr) * 100
            avg = arr.mean() * 100
            print(f"{tname:<16} {len(arr):>7} {win:>6.1f}% {med:>7.2f}% {avg:>7.2f}%")
    
    # Report: Consecutive
    print(f"\n{'='*70}")
    print(f"📊 连续信号分析 (2024~2026)")
    print(f"{'='*70}")
    for days in [5, 10, 20, 60]:
        print(f"\n持有 {days} 日:")
        print(f"{'连续天数':<16} {'样本':>7} {'胜率':>7} {'中位':>8} {'均值':>8}")
        for ck in ['连续1天', '连续2天', '连续≥3天']:
            data = consecutive[ck][days]
            if not data: continue
            arr = np.array(data)
            win = (arr > 0).sum() / len(arr) * 100
            med = np.median(arr) * 100
            avg = arr.mean() * 100
            print(f"{ck:<16} {len(arr):>7} {win:>6.1f}% {med:>7.2f}% {avg:>7.2f}%")
    
    print(f"\n各层样本量: {dict(counts)}")

if __name__ == '__main__':
    main()
