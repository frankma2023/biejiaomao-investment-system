"""不同仓位比例的收益/回撤对比"""
import sqlite3, numpy as np
from collections import defaultdict
from datetime import datetime

DB = r'D:\hanako\investment-system\data\lixinger.db'
START, END = '2016-01-01', '2026-07-03'
INIT = 1_000_000

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# 加载信号
signals = conn.execute("""
    SELECT stock_code, stock_name, b1_date, b2_date, score
    FROM mw_signal_daily
    WHERE b2_date >= ? AND b2_date <= ? AND score >= 70 AND stock_code != '_sentinel_'
    ORDER BY b2_date
""", (START, END)).fetchall()

# 加载K线
codes = list(set(s['stock_code'] for s in signals))
klines = defaultdict(list)
for batch in range(0, len(codes), 500):
    b = codes[batch:batch+500]
    ph = ','.join('?'*len(b))
    for r in conn.execute(f"SELECT stock_code,date,open,close FROM daily_kline WHERE date>='2015-11-01' AND date<='2026-08-01' AND stock_code IN ({ph}) ORDER BY stock_code,date", b):
        klines[r['stock_code']].append((r['date'], r['open'], r['close']))

kl_idx = {}
for code, kls in klines.items():
    for i, (d, o, c) in enumerate(kls):
        kl_idx[(code, d)] = i

trading_days = [r['date'] for r in conn.execute("SELECT DISTINCT date FROM index_daily_kline WHERE stock_code='000985' AND date>=? AND date<=? ORDER BY date", (START,'2026-08-01'))]

def run(pct, label):
    """运行回测，仓位=pct%"""
    cash = INIT
    positions = []  # [(code, entry_price, shares, entry_date)]
    closed = []
    signal_idx = 0
    daily_vals = []
    
    for day_idx, today in enumerate(trading_days):
        # 持仓市值
        sv = sum(
            klines[p[0]][kl_idx[(p[0],today)]][2] * p[2]
            if (p[0], today) in kl_idx else p[1] * p[2]
            for p in positions
        )
        tv = cash + sv
        daily_vals.append(tv)
        
        # 处理持仓
        for i in range(len(positions)-1, -1, -1):
            code, entry, shares, edate = positions[i]
            if (code, today) not in kl_idx: continue
            kidx = kl_idx[(code, today)]
            price = klines[code][kidx][2]
            ret_pct = (price - entry) / entry * 100
            
            sold = False
            reason = ''
            
            # 止损-7%
            if ret_pct <= -7:
                cash += price * shares; sold = True; reason = '止损'
            # 跌破MA20清仓
            elif kidx >= 19:
                ma20 = sum(klines[code][j][2] for j in range(kidx-19, kidx+1)) / 20
                if price < ma20:
                    cash += price * shares; sold = True; reason = 'MA20'
            # 跌破MA10卖50%
            elif kidx >= 9:
                ma10 = sum(klines[code][j][2] for j in range(kidx-9, kidx+1)) / 10
                if price < ma10 and shares > 100:
                    sell_sh = max(1, shares // 2)
                    cash += price * sell_sh
                    profit = (price - entry) * sell_sh
                    closed.append((today, reason, profit))
                    positions[i] = (code, entry, shares - sell_sh, edate)
            
            if sold:
                profit = (price - entry) * shares
                closed.append((today, reason, profit))
                positions.pop(i)
        
        # 买入
        while signal_idx < len(signals):
            sig = signals[signal_idx]
            b2 = sig['b2_date']
            if b2 >= today: break
            
            try: b2_idx = trading_days.index(b2)
            except ValueError: signal_idx += 1; continue
            
            t1 = b2_idx + 1
            if t1 >= len(trading_days): signal_idx += 1; continue
            if day_idx < t1: break
            
            if day_idx == t1:
                code = sig['stock_code']
                if (code, today) not in kl_idx: signal_idx += 1; continue
                entry = klines[code][kl_idx[(code, today)]][1]
                if entry <= 0: signal_idx += 1; continue
                
                # 重新计算总资产
                sv = sum(klines[p[0]][kl_idx[(p[0],today)]][2] * p[2] if (p[0],today) in kl_idx else p[1]*p[2] for p in positions)
                tv = cash + sv
                
                buy_amt = tv * pct
                if buy_amt < 10000: signal_idx += 1; continue
                
                shares = int(buy_amt / entry / 100) * 100
                if shares < 100: signal_idx += 1; continue
                
                cost = shares * entry
                if cost > cash: signal_idx += 1; continue
                
                cash -= cost
                positions.append((code, entry, shares, today))
            
            signal_idx += 1
    
    # 清仓
    last_day = trading_days[-1]
    for code, entry, shares, edate in positions:
        if (code, last_day) in kl_idx:
            price = klines[code][kl_idx[(code, last_day)]][2]
            cash += price * shares
    
    final = cash
    total_ret = (final / INIT - 1) * 100
    # 正确计算最大回撤(peak to trough)
    peak = daily_vals[0]
    max_dd = 0
    for v in daily_vals:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd: max_dd = dd
    
    wins = sum(1 for c in closed if c[2] > 0)
    total = len(closed)
    wr = wins / total * 100 if total > 0 else 0
    
    return {'label': label, 'final': final, 'ret': total_ret, 'dd': max_dd, 
            'trades': total, 'wr': wr, 'daily': daily_vals}

results = []
for pct in [0.05, 0.08, 0.10, 0.12, 0.15, 0.20]:
    r = run(pct, f'{pct*100:.0f}%')
    results.append(r)
    print(f"{r['label']}: ¥{r['final']:,.0f} (+{r['ret']:.1f}%) 回撤{r['dd']:.1f}% {r['trades']}笔 胜率{r['wr']:.1f}%")

# 简化年化
years = 10.5
for r in results:
    cagr = ((r['final'] / INIT) ** (1/years) - 1) * 100
    print(f"\n{r['label']}: CAGR {cagr:.1f}%  Sharpe≈{r['ret']/max(r['dd'],1):.1f}")

conn.close()
