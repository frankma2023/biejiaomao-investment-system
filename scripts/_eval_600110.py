import sqlite3, json
c = sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
c.row_factory = sqlite3.Row

code = '600110'

# 1. Recent K-line (30 days)
kl = c.execute("SELECT date, open, high, low, close, volume, amount FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 30", (code,)).fetchall()
kl.reverse()
print("=== 最近30日K线 ===")
for r in kl:
    chg = (r['close']-r['open'])/r['open']*100
    print(f"  {r['date']} O={r['open']:.2f} C={r['close']:.2f} V={r['volume']/10000:.0f}万 额={r['amount']/10000:.0f}万 {chg:+.1f}%")

# 2. RS
rs = c.execute("SELECT date, rps_20, rps_250 FROM stock_rs_daily WHERE stock_code=? ORDER BY date DESC LIMIT 5", (code,)).fetchall()
print("\n=== RS强度 ===")
for r in rs:
    print(f"  {r['date']} RPS20={r['rps_20']} RPS250={r['rps_250']}")

# 3. Fundamentals
fund = c.execute("SELECT date, revenue_yoy, net_profit_yoy, roe, eps_ttm, pe_ttm, pb FROM fundamental_indicator WHERE stock_code=? ORDER BY date DESC LIMIT 3", (code,)).fetchall()
print("\n=== 基本面 ===")
for r in fund:
    print(f"  {r['date']} 营收YoY={r['revenue_yoy']} 净利YoY={r['net_profit_yoy']} ROE={r['roe']} EPS_TTM={r['eps_ttm']} PE_TTM={r['pe_ttm']} PB={r['pb']}")

# 4. Industry
indices = c.execute("SELECT index_code FROM index_constituents WHERE stock_code=?", (code,)).fetchall()
ics = [r['index_code'] for r in indices]
if ics:
    names = c.execute(f"SELECT stock_code, name FROM index_basic WHERE stock_code IN ({','.join('?'*len(ics))})", ics).fetchall()
    print(f"\n=== 行业归属 ===")
    for n in names:
        ir = c.execute("SELECT rs_20, rs_250 FROM index_rs_daily WHERE stock_code=? ORDER BY date DESC LIMIT 1", (n['stock_code'],)).fetchone()
        if ir:
            print(f"  {n['stock_code']} {n['name']} RS20={ir['rs_20']} RS250={ir['rs_250']}")

# 5. MW signals
mw = c.execute("SELECT * FROM mw_signal_daily WHERE stock_code=? ORDER BY b2_date DESC LIMIT 3", (code,)).fetchall()
print("\n=== MW信号 ===")
if mw:
    for r in mw:
        print(f"  B2={r['b2_date']} H={r['h_date']} 得分{r['score']} {r['confidence']} D={r['score_d']} I1={r['score_i1']} I2={r['score_i2']} PLUS={'是' if r['is_plus'] else '否'}")
else:
    print("  无MW信号")

# 6. Pattern signals
ps = c.execute("SELECT date, signals_json FROM pattern_scan_signals WHERE stock_code=? ORDER BY date DESC LIMIT 5", (code,)).fetchall()
print("\n=== 形态信号 ===")
for r in ps:
    try:
        sigs = json.loads(r['signals_json']) if isinstance(r['signals_json'], str) else r['signals_json']
        sources = {}
        for s in (sigs if isinstance(sigs, list) else []):
            src = s.get('source','')
            sources[src] = sources.get(src, 0) + 1
        print(f"  {r['date']}: {sources}")
    except:
        print(f"  {r['date']}: parse error")

# 7. Market cap
cap = c.execute("SELECT MAX(capitalization) FROM stock_equity_change WHERE stock_code=?", (code,)).fetchone()
mc = cap[0] if cap and cap[0] else 0
close_latest = kl[-1]['close'] if kl else 0
mcap = mc * close_latest / 100000000 if mc and close_latest else 0
print(f"\n总股本: {mc/10000:.0f}万股, 市值: ~{mcap:.0f}亿")

# 8. MA calculations
closes = [r['close'] for r in kl]
if len(closes) >= 50:
    ma10 = sum(closes[-10:])/10
    ma20 = sum(closes[-20:])/20
    ma50 = sum(closes[-50:])/50 if len(closes) >= 50 else 0
    cur = closes[-1]
    print(f"\n均线: MA10={ma10:.2f} MA20={ma20:.2f} MA50={ma50:.2f} 当前={cur:.2f}")
    print(f"  站上MA10:{'是' if cur>ma10 else '否'} MA20:{'是' if cur>ma20 else '否'} MA50:{'是' if cur>ma50 else '否'}")

c.close()
