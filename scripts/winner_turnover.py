"""用已有股本数据(2102只)计算换手率，跑赢家分析"""
import sqlite3, numpy as np
from datetime import date

DB = r'D:\hanako\investment-system\data\lixinger.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# 加载股本数据到内存字典: {stock_code: [(change_date, outstanding_a), ...]} 按日期排序
print("加载股本...")
eq = {}
for r in conn.execute("SELECT stock_code, change_date, outstanding_shares_a FROM stock_equity_change ORDER BY stock_code, change_date"):
    if r['outstanding_shares_a']:
        if r['stock_code'] not in eq:
            eq[r['stock_code']] = []
        eq[r['stock_code']].append((r['change_date'], r['outstanding_shares_a']))
print(f"  {len(eq)}只")

# 加载回测
bt = {}
for r in conn.execute("SELECT stock_code,signal_date,net_ret_pct,is_win FROM backtest_results WHERE signal_mask & 1 = 1 AND entry_method='T+1_O' AND hold_days=20"):
    bt[(r['stock_code'], r['signal_date'])] = (r['net_ret_pct'], r['is_win'])

# 加载B1信号 + K线收盘价
print("加载B1+K线...")
kl = {}
for r in conn.execute("SELECT stock_code, date, close FROM daily_kline WHERE date>='2015-01-01'"):
    kl[(r['stock_code'], r['date'])] = r['close']

rows = conn.execute("SELECT * FROM mw_signal_daily WHERE b1_date>='2016-01-01' AND stock_code!='_sentinel_'")

data = []
no_eq = 0
for r in rows:
    key = (r['stock_code'], r['b1_date'])
    if key not in bt: continue
    ret, win = bt[key]
    
    # 换手率 = c_amount_avg / (流通A股 × B1日收盘价) × 100
    to_rate = 0
    eq_list = eq.get(r['stock_code'], [])
    if eq_list and r['c_amount_avg'] and r['b1_date']:
        # 二分查找 B1日期之前的最近股本
        target = r['b1_date']
        lo, hi = 0, len(eq_list)-1
        best = None
        while lo <= hi:
            mid = (lo+hi)//2
            if eq_list[mid][0] <= target:
                best = eq_list[mid]
                lo = mid+1
            else:
                hi = mid-1
        if best:
            shares = best[1]
            close_p = kl.get((r['stock_code'], r['b1_date']), 0)
            if shares > 0 and close_p > 0 and r['c_amount_avg'] > 0:
                # c_amount_avg 是日均成交额(元) = 元/日
                # 换手率 = (日均成交额/日) / (流通A股 × 收盘价) × 100%
                to_rate = (r['c_amount_avg']) / (shares * close_p) * 100
    if to_rate == 0:
        no_eq += 1
        continue
    
    dh = 0
    if r['h_date'] and r['b1_date'] and r['h_date']>'2000':
        dh = (date.fromisoformat(r['b1_date'])-date.fromisoformat(r['h_date'])).days
    
    data.append([
        ret, win,
        r['h_rs250'] or 0,
        r['decline_pct'] or 0,
        r['b1_return_pct'] or 0,
        to_rate,  # 5: 换手率
        dh,
        1 if (r['b2_date'] and r['b2_date']>r['b1_date']) else 0,
    ])

arr = np.array(data, dtype=np.float64)
print(f"有效: {len(arr)}笔 (无股本数据: {no_eq}笔)")

win_mask = arr[:,1]==1
w_arr = arr[win_mask]; l_arr = arr[~win_mask]
w_ret = w_arr[:,0]
hi_arr = w_arr[w_ret >= np.percentile(w_ret, 75)]

print(f"全量:{len(arr)} 赢家:{len(w_arr)}({len(w_arr)/len(arr)*100:.1f}%) 高收益:{len(hi_arr)}(均{np.mean(hi_arr[:,0]):.1f}%)")

def pdist(a,col):
    v=a[:,col]; return f"P10={np.percentile(v,10):.2f} P25={np.percentile(v,25):.2f} P50={np.percentile(v,50):.2f} P75={np.percentile(v,75):.2f} P90={np.percentile(v,90):.2f} 均值={np.mean(v):.2f}"

# ── 换手率分析 ──
print("\n" + "="*60)
print("换手率 = 横盘日均成交额 / (流通A股 × B1收盘价) × 100%")
print("="*60)
for label, a in [("全量",arr),("赢家",w_arr),("输家",l_arr),("高收益",hi_arr)]:
    print(f"  {label}: {pdist(a,5)}")

# 分档
p25,p50,p75 = np.percentile(arr[:,5],[25,50,75])
print(f"\n按换手率分四档:")
for lo,hi,label in [(0,p25,'低换手'),(p25,p50,'中下'),(p50,p75,'中上'),(p75,99,'高换手')]:
    m = (arr[:,5]>=lo)&(arr[:,5]<hi)
    if m.sum()<50: continue
    wr=np.mean(arr[m,1])*100; ar=np.mean(arr[m,0])
    print(f"  {label}({lo:.2f}-{hi:.2f}%): {m.sum():>5d}条 胜率{wr:.1f}% 收益{ar:.1f}%")

# 阈值扫描
print(f"\n换手率阈值扫描:")
base_wr=np.mean(arr[:,1])*100
for t in [0.5,1.0,1.5,2.0,3.0,5.0]:
    m=arr[:,5]>=t; m2=arr[:,5]<t
    if m.sum()<100 or m2.sum()<100: continue
    print(f"  ≥{t}%: {m.sum():>5d}条 胜率{np.mean(arr[m,1])*100:.1f}% | <{t}%: {m2.sum():>5d}条 胜率{np.mean(arr[m2,1])*100:.1f}%")

# 换手率 × RS 组合
print(f"\n换手率 × h_rs250 组合:")
for rs_t in [60,70,80]:
    for to_t in [0.5,1.0,2.0]:
        m = (arr[:,2]>=rs_t) & (arr[:,5]>=to_t)
        if m.sum()<50: continue
        wr=np.mean(arr[m,1])*100; ar=np.mean(arr[m,0])
        print(f"  RS≥{rs_t} + 换手≥{to_t}%: {m.sum():>5d}条 胜率{wr:.1f}% 收益{ar:.1f}%")

conn.close()
