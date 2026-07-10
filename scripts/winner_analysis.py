"""
B1赢家因子精确分析 v4（向量化版）
"""
import sqlite3, numpy as np
from datetime import date

DB = r'D:\hanako\investment-system\data\lixinger.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

bt = {}
for r in conn.execute("SELECT stock_code,signal_date,net_ret_pct,is_win FROM backtest_results WHERE signal_mask & 1 = 1 AND entry_method='T+1_O' AND hold_days=20"):
    bt[(r['stock_code'], r['signal_date'])] = (r['net_ret_pct'], r['is_win'])

rows = conn.execute("SELECT * FROM mw_signal_daily WHERE b1_date>='2016-01-01' AND stock_code!='_sentinel_'")

data = []
for r in rows:
    k = (r['stock_code'], r['b1_date'])
    if k not in bt: continue
    ret, win = bt[k]
    dh = 0
    if r['h_date'] and r['b1_date'] and r['h_date'] > '2000':
        dh = (date.fromisoformat(r['b1_date']) - date.fromisoformat(r['h_date'])).days
    data.append([
        ret, win,
        r['h_rs250'] or 0,           # 2: h_rs250
        r['score_i2'] or 0,          # 3: score_i2  
        (r['c_amount_avg'] or 0)/10000, # 4: c_amt(万)
        r['decline_pct'] or 0,       # 5: decline%
        dh,                           # 6: days_since_h
        1 if (r['b2_date'] and r['b2_date'] > r['b1_date']) else 0, # 7: has_b2
    ])

arr = np.array(data, dtype=np.float64)
N = len(arr)
print(f"总信号: {N}")

win_mask = arr[:,1] == 1
w_arr = arr[win_mask]; l_arr = arr[~win_mask]
w_ret = w_arr[:,0]
hi_mask = w_ret >= np.percentile(w_ret, 75)
hi_arr = w_arr[hi_mask]

print(f"赢家: {len(w_arr)}({len(w_arr)/N*100:.1f}%)  高收益: {len(hi_arr)}(均{np.mean(hi_arr[:,0]):.1f}%)")

def pdist(arr, col):
    """分位数分布"""
    v = arr[:,col]
    return f"n={len(v)} P10={np.percentile(v,10):.1f} P25={np.percentile(v,25):.1f} P50={np.percentile(v,50):.1f} P75={np.percentile(v,75):.1f} P90={np.percentile(v,90):.1f} 均值={np.mean(v):.1f}"

# ═══════════════════════════════════════════════
print("\n1. h_rs250 — 前高H点时的个股RS250（0-99分位）")
print("   来源: stock_rs_daily，按H日期查该股rps_250")
print(f"   全量: {pdist(arr,2)}")
print(f"   赢家: {pdist(w_arr,2)}")
print(f"   输家: {pdist(l_arr,2)}")
print(f"   高收益: {pdist(hi_arr,2)}")

print("\n   阈值扫描:")
base_wr = np.mean(arr[:,1])*100; base_ar = np.mean(arr[:,0])
for t in [50,60,70,80,90]:
    m = arr[:,2] >= t
    if m.sum() < 50: continue
    wr = np.mean(arr[m,1])*100; ar = np.mean(arr[m,0])
    print(f"   RS≥{t}: {m.sum():>6d}条({m.sum()/N*100:.0f}%) 胜率{wr:.1f}% 收益{ar:.1f}% vs全量{wr-base_wr:+.1f}pp")

# ═══════════════════════════════════════════════
print("\n2. score_I2 — 个股RS的HDC子项得分（0/10/20/30离散值）")
print("   规则: RS≥90→30分, ≥85→20分, ≥75→10分, <75→0")
for label, a in [("全量",arr),("赢家",w_arr),("输家",l_arr),("高收益",hi_arr)]:
    parts = []
    for sc in [0,10,20,30]:
        parts.append(f"{sc}分={np.mean(a[:,3]==sc)*100:.1f}%")
    print(f"   {label}: {' | '.join(parts)}  均值={np.mean(a[:,3]):.2f}")

# ═══════════════════════════════════════════════
print("\n3. 成交额 — 横盘期C区间的日均成交额（万元）")
print("   来源: daily_kline.amount 在 c_start~c_end 期间平均值")
print("   局限: 绝对值，未归一化。换手率=成交额/流通市值，需流通股本数据（当前未采集）")
print(f"   全量: {pdist(arr,4)}")
print(f"   赢家: {pdist(w_arr,4)}")
print(f"   输家: {pdist(l_arr,4)}")
print(f"   高收益: {pdist(hi_arr,4)}")

print("\n   按成交额分四档:")
p25,p50,p75 = np.percentile(arr[:,4],[25,50,75])
for lo,hi,label in [(0,p25,'小盘'),(p25,p50,'中下'),(p50,p75,'中上'),(p75,999999,'大盘')]:
    m = (arr[:,4]>=lo) & (arr[:,4]<hi)
    if m.sum() < 10: continue
    wr = np.mean(arr[m,1])*100; ar = np.mean(arr[m,0])
    print(f"   {label}({lo:.0f}-{hi:.0f}万): {m.sum():>5d}条 胜率{wr:.1f}% 收益{ar:.1f}%")

# ═══════════════════════════════════════════════
print("\n4. decline_pct — 从H高点到L低点的调整深度（%）")
print("   来源: (h_price - l_price)/h_price*100")
print("   含义: 前高之后的最大回调幅度，不是H到B1的跌幅")
print(f"   全量: {pdist(arr,5)}")
print(f"   赢家: {pdist(w_arr,5)}")
print(f"   输家: {pdist(l_arr,5)}")
print(f"   高收益: {pdist(hi_arr,5)}")

print("\n   按调整深度分档:")
for lo,hi,label in [(0,10,'<10%'),(10,15,'10-15%'),(15,20,'15-20%'),(20,25,'20-25%'),(25,35,'25-35%'),(35,99,'>35%')]:
    m = (arr[:,5]>=lo) & (arr[:,5]<hi)
    if m.sum() < 50: continue
    wr = np.mean(arr[m,1])*100; ar = np.mean(arr[m,0])
    print(f"   {label}: {m.sum():>5d}条 胜率{wr:.1f}% 收益{ar:.1f}%")

# ═══════════════════════════════════════════════
print("\n5. B2确认率 — B1之后是否出现了B2")
print("   来源: mw_signal_daily.b2_date > b1_date")
for label, a in [("全量",arr),("赢家",w_arr),("输家",l_arr),("高收益",hi_arr)]:
    print(f"   {label}: {np.mean(a[:,7])*100:.1f}%")

# ═══════════════════════════════════════════════
print("\n6. 距H天数 — 从H高点到B1日的自然日数")
print("   来源: (b1_date - h_date).days")
print(f"   全量: {pdist(arr,6)}")
print(f"   赢家: {pdist(w_arr,6)}")
print(f"   输家: {pdist(l_arr,6)}")
print(f"   高收益: {pdist(hi_arr,6)}")

print("\n   按距H天分档:")
for lo,hi,label in [(0,20,'<20天'),(20,40,'20-40天'),(40,60,'40-60天'),(60,999,'>60天')]:
    m = (arr[:,6]>=lo) & (arr[:,6]<hi)
    if m.sum() < 50: continue
    wr = np.mean(arr[m,1])*100; ar = np.mean(arr[m,0])
    print(f"   {label}: {m.sum():>5d}条 胜率{wr:.1f}% 收益{ar:.1f}%")

conn.close()
