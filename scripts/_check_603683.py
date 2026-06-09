import sqlite3, json
from collections import defaultdict

DB = "D:/hanako/investment-system/data/lixinger.db"
db = sqlite3.connect(DB); db.row_factory = sqlite3.Row

code = '603683'

# K-line around 06-01 to 06-05
kl = db.execute("""
    SELECT date, open, high, low, close, volume, amount 
    FROM daily_kline WHERE stock_code=? AND date >= '2026-05-15' AND date <= '2026-06-05'
    ORDER BY date
""", (code,)).fetchall()

print("=== 晶华新材 603683 K线 (05-15 ~ 06-05) ===")
for r in kl:
    chg = (r['close'] - r['open']) / r['open'] * 100
    prev_c = None
    if len([x for x in kl if x['date'] < r['date']]) > 0:
        prev = [x for x in kl if x['date'] < r['date']][-1]
        prev_c = prev['close']
    day_chg = (r['close'] - prev_c) / prev_c * 100 if prev_c else 0
    print(f"  {r['date']} O={r['open']:.2f} H={r['high']:.2f} L={r['low']:.2f} C={r['close']:.2f} V={r['volume']/10000:.0f}万 额={r['amount']/10000:.0f}万 日内{chg:+.1f}% 涨跌{day_chg:+.1f}%")

# MW structure
mw = db.execute("""
    SELECT h_date, h_price, l_date, l_price, c_start, c_end, b1_date, b2_date, decline_pct
    FROM mw_signal_daily WHERE stock_code=?
    ORDER BY b2_date DESC LIMIT 1
""", (code,)).fetchone()

print(f"\n=== MW结构 ===")
if mw:
    for k in mw.keys(): print(f"  {k}: {mw[k]}")
else:
    print("  无MW信号")

# RS
rs = db.execute("""
    SELECT date, rps_20, rps_250 FROM stock_rs_daily 
    WHERE stock_code=? AND date >= '2026-05-28' ORDER BY date
""", (code,)).fetchall()
print(f"\n=== RS ===")
for r in rs: print(f"  {r['date']} RPS20={r['rps_20']} RPS250={r['rps_250']}")

# Calculate SMAs and down day volumes for both 06-01 and 06-05
all_kl = db.execute("""
    SELECT date, open, high, low, close, volume FROM daily_kline 
    WHERE stock_code=? AND date <= '2026-06-05' ORDER BY date
""", (code,)).fetchall()
all_kl = [dict(r) for r in all_kl]

def analyze_day(target_date):
    dates = [k['date'] for k in all_kl]
    if target_date not in dates: return
    idx = dates.index(target_date)
    
    closes = [k['close'] for k in all_kl[:idx+1]]
    sma10 = sum(closes[-10:])/10
    sma50 = sum(closes[-50:])/50 if len(closes) >= 50 else 0
    
    today = all_kl[idx]
    c, v, h, l = today['close'], today['volume'], today['high'], today['low']
    gain = (c - all_kl[idx-1]['close']) / all_kl[idx-1]['close'] * 100
    
    # Down day volumes
    down_vols = []
    for i in range(max(0, idx-10), idx):
        if all_kl[i]['close'] < all_kl[i-1]['close']:
            down_vols.append(all_kl[i]['volume'])
    
    max_down = max(down_vols) if down_vols else 0
    vol_ok = v > max_down if down_vols else False
    
    # Close position
    close_pos = (c - l) / (h - l) * 100 if h > l else 0
    
    # Breakout: high > prev 10-day max high
    prev_highs = [all_kl[i]['high'] for i in range(max(0, idx-10), idx)]
    breakout = h >= max(prev_highs) if prev_highs else False
    
    # SMA50 slope
    sma50_10d_ago = sum(all_kl[j]['close'] for j in range(max(0,idx-60), idx-10))/50 if idx >= 60 else sma50
    
    print(f"\n  {target_date}: C={c:.2f} 涨{gain:+.1f}% V={v/10000:.0f}万")
    print(f"    SMA10={sma10:.2f} SMA50={sma50:.2f} C>SMA10:{c>sma10} C>SMA50:{c>sma50}")
    print(f"    收盘位置={close_pos:.0f}% 突破前高={breakout}")
    print(f"    前10天下跌日最大量={max_down/10000:.0f}万 今日量>最大下跌量={vol_ok}")
    print(f"    SMA50斜率>0: {sma50 > sma50_10d_ago}")
    print(f"    距MA10: {(c-sma10)/sma10*100:+.1f}%")

analyze_day('2026-06-01')
analyze_day('2026-06-05')

db.close()
