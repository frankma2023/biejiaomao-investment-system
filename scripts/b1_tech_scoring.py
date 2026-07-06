"""
B1-only 技术面置信度评分 · 实验版
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
基于刚完成的技术因子分析，构建尝试性评分表
满分100分，每个因子根据实证数据赋权
"""
import sqlite3, numpy as np
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 80)
print('B1 技术面置信度评分 · 实验版')
print('=' * 80)

# ═══ 1. 加载数据 + 计算因子（复用前次逻辑）═══
print('[1] 加载数据...')
rows = c.execute("""
    SELECT br.stock_code, br.signal_date as b1_date, br.net_ret_pct, br.is_win,
           m.score, m.confidence, m.b2_date, m.is_plus,
           m.decline_pct, m.h_rs250, m.b1_vol_ratio, m.b1_return_pct
    FROM backtest_results br
    JOIN mw_signal_daily m ON br.stock_code=m.stock_code AND br.signal_date=m.b1_date
    WHERE br.combo_label='MW_B1' AND br.hold_days=20 AND br.entry_method='T+1_O'
      AND br.pool_mode='full' AND br.signal_date >= '2024-01-01' AND br.signal_date <= '2026-06-22'
""").fetchall()
print(f'  {len(rows)} 条信号')

# 预加载K线
c.execute("SELECT stock_code, date, open, high, low, close, volume, adj_close FROM daily_kline WHERE date >= '2023-06-01' AND date <= '2026-06-22' ORDER BY stock_code, date")
kline_by_code = defaultdict(list)
kline_idx = {}
for r in c.fetchall():
    kline_by_code[r['stock_code']].append({'date': r['date'], 'open': r['open'], 'high': r['high'], 'low': r['low'], 'close': r['close'], 'volume': r['volume'], 'adj_close': r['adj_close']})
for code, kls in kline_by_code.items():
    for i, kl in enumerate(kls):
        kline_idx[(code, kl['date'])] = (i, kl)

# 加载RS
c.execute("SELECT stock_code, date, rps_20, rps_60, rps_250 FROM stock_rs_daily WHERE date >= '2024-01-01' AND date <= '2026-06-22'")
rs_dict = {}
for r in c.fetchall():
    rs_dict[(r['stock_code'], r['date'])] = (r['rps_20'] or 0, r['rps_60'] or 0, r['rps_250'] or 0)
db.close()

# ═══ 2. 定义评分规则 ═══
def ma(arr, period):
    if len(arr) < period: return None
    return np.mean(arr[-period:])

def score_b1_technical(code, b1_date):
    """返回满分100的技术面评分"""
    kls = kline_by_code.get(code)
    if not kls: return None
    idx_info = kline_idx.get((code, b1_date))
    if not idx_info: return None
    idx, kl = idx_info
    if idx < 250: return None
    
    closes = np.array([k['adj_close'] for k in kls[max(0,idx-260):idx+1]], dtype=np.float64)
    volumes = np.array([k['volume'] for k in kls[max(0,idx-260):idx+1]], dtype=np.float64)
    close_now = closes[-1]
    
    total = 0
    details = {}
    
    # ── 1. 价格距MA20 (满分15) ──
    ma20 = ma(closes, 20)
    if ma20 and ma20 > 0:
        pct20 = (close_now - ma20) / ma20 * 100
        if pct20 <= 5: s = 15          # 刚站上或贴近
        elif pct20 <= 10: s = 12        # 小幅偏离
        elif pct20 <= 15: s = 8
        elif pct20 <= 25: s = 4
        else: s = 0                      # 太远
        total += s; details['距MA20'] = (round(pct20,1), s)
    
    # ── 2. 价格距MA50 (满分15) ──
    ma50 = ma(closes, 50)
    if ma50 and ma50 > 0:
        pct50 = (close_now - ma50) / ma50 * 100
        if pct50 <= 8: s = 15
        elif pct50 <= 15: s = 10
        elif pct50 <= 25: s = 5
        else: s = 0
        total += s; details['距MA50'] = (round(pct50,1), s)
    
    # ── 3. 价格距MA250 (满分15) ──
    ma250 = ma(closes, 250)
    if ma250 and ma250 > 0:
        pct250 = (close_now - ma250) / ma250 * 100
        if pct250 <= 15: s = 15
        elif pct250 <= 25: s = 10
        elif pct250 <= 35: s = 5
        else: s = 0
        total += s; details['距MA250'] = (round(pct250,1), s)
    
    # ── 4. BIAS vs MA60 (满分10) ──
    ma60 = ma(closes, 60)
    if ma60 and ma60 > 0:
        bias = (close_now - ma60) / ma60 * 100
        if bias <= 8: s = 10
        elif bias <= 15: s = 7
        elif bias <= 25: s = 3
        else: s = 0
        total += s; details['BIAS'] = (round(bias,1), s)
    
    # ── 5. RPS20 (满分10) ──
    rs = rs_dict.get((code, b1_date))
    rps20 = rs[0] if rs else 0
    if 40 <= rps20 <= 75: s = 10
    elif 30 <= rps20 < 40 or 75 < rps20 <= 85: s = 6
    elif rps20 > 85: s = 2
    else: s = 4
    total += s; details['RPS20'] = (rps20, s)
    
    # ── 6. RPS60 (满分10) ──
    rps60 = rs[1] if rs else 0
    if 40 <= rps60 <= 70: s = 10
    elif 30 <= rps60 < 40 or 70 < rps60 <= 80: s = 6
    elif rps60 > 80: s = 2
    else: s = 4
    total += s; details['RPS60'] = (rps60, s)
    
    # ── 7. RPS250 (满分5) ──
    rps250 = rs[2] if rs else 0
    if 50 <= rps250 <= 70: s = 5
    elif rps250 > 70: s = 3
    else: s = 2
    total += s; details['RPS250'] = (rps250, s)
    
    # ── 8. MACD DIF (满分15) ──
    if len(closes) >= 26:
        ema12 = closes[-1]; ema26 = closes[-1]
        k12 = 2/13; k26 = 2/27
        for i in range(len(closes)-2, max(0, len(closes)-27), -1):
            ema12 = closes[i] * k12 + ema12 * (1-k12)
            ema26 = closes[i] * k26 + ema26 * (1-k26)
        dif = ema12 - ema26
        
        # 也计算前一天的DIF用于判断金叉
        ema12_p = closes[-2]; ema26_p = closes[-2]
        for i in range(len(closes)-3, max(0, len(closes)-28), -1):
            ema12_p = closes[i] * k12 + ema12_p * (1-k12)
            ema26_p = closes[i] * k26 + ema26_p * (1-k26)
        dif_p = ema12_p - ema26_p
        
        # 简化DEA
        dea = dif * 0.2 + dif * 0.8
        dea_p = dif_p * 0.2 + dif_p * 0.8
        
        golden = (dif > dea) and (dif_p <= dea_p)
        
        if dif > 0 and dif < close_now * 0.02: s = 15  # DIF>0但不大
        elif dif > 0: s = 12
        elif dif > close_now * -0.01: s = 8  # 接近零轴
        else: s = 3
        
        if golden: s -= 3  # 金叉当日略扣分（数据说金叉不好）
        s = max(0, min(15, s))
        total += s; details['MACD_DIF'] = (round(dif, 3), s, '金叉' if golden else '')
    
    # ── 9. KDJ (满分5) ──
    if len(closes) >= 9:
        highs = np.array([k['high'] for k in kls[max(0,idx-8):idx+1]], dtype=np.float64)
        lows = np.array([k['low'] for k in kls[max(0,idx-8):idx+1]], dtype=np.float64)
        h9 = np.max(highs); l9 = np.min(lows)
        if h9 > l9:
            rsv = (close_now - l9) / (h9 - l9) * 100
            k_val = rsv * 2/3 + 50 * 1/3
            if k_val <= 75: s = 5
            elif k_val <= 85: s = 3
            else: s = 0
            total += s; details['KDJ_K'] = (round(k_val,1), s)
    
    return {'score': total, 'details': details}

# ═══ 3. 计算所有信号评分 ═══
print('[2] 计算技术面评分...')
scored = []
for i, r in enumerate(rows):
    if i % 3000 == 0: print(f'  {i}/{len(rows)}...')
    s = score_b1_technical(r['stock_code'], r['b1_date'])
    if s:
        scored.append({
            'code': r['stock_code'], 'b1_date': r['b1_date'],
            'tech_score': s['score'], 'details': s['details'],
            'net_ret': r['net_ret_pct'], 'is_win': r['is_win'],
            'orig_score': r['score'], 'orig_conf': r['confidence'],
            'has_b2': r['b2_date'] is not None,
        })

print(f'  有效: {len(scored)} 条')

# ═══ 4. 验证：按评分分组看胜率 ═══
print(f'\n{"=" * 80}')
print('验证：技术面评分 vs 实际表现')
print(f'{"=" * 80}')
print(f'{"评分区间":<12s} {"数量":>7s} {"胜率":>8s} {"平均收益":>9s} {"中位收益":>9s} {"有B2":>8s} {"盈亏比":>7s}')
print('-' * 65)

for lo, hi in [(0,20),(20,30),(30,40),(40,50),(50,60),(60,70),(70,100)]:
    bucket = [s for s in scored if lo <= s['tech_score'] < hi]
    if len(bucket) < 5: continue
    n = len(bucket)
    wr = np.mean([s['is_win'] for s in bucket]) * 100
    avg = np.mean([s['net_ret'] for s in bucket])
    med = np.median([s['net_ret'] for s in bucket])
    b2_rate = np.mean([s['has_b2'] for s in bucket]) * 100
    pos = [s['net_ret'] for s in bucket if s['net_ret'] > 0]
    neg = [s['net_ret'] for s in bucket if s['net_ret'] < 0]
    plr = np.mean(pos)/abs(np.mean(neg)) if neg else 0
    marker = ' ⚠<30' if n < 30 else ''
    print(f'{lo}-{hi:<7d}  {n:>7d}  {wr:>7.1f}%  {avg:>8.2f}%  {med:>8.2f}%  {b2_rate:>7.1f}%  {plr:>6.2f}{marker}')

# ═══ 5. 推荐置信度分层 ═══
print(f'\n{"=" * 80}')
print('推荐技术面置信度分层')
print(f'{"=" * 80}')

for lo, hi, conf in [(55,100,'高'), (40,54,'中'), (0,39,'低')]:
    bucket = [s for s in scored if lo <= s['tech_score'] < hi]
    if not bucket: continue
    n = len(bucket)
    wr = np.mean([s['is_win'] for s in bucket]) * 100
    avg = np.mean([s['net_ret'] for s in bucket])
    pct = n / len(scored) * 100
    print(f'  {conf}: {lo}-{hi-1}分  {n}条({pct:.1f}%)  胜率={wr:.1f}%  收益={avg:.2f}%')

# ═══ 6. 与原始评分对比 ═══
print(f'\n{"=" * 80}')
print('技术面评分 vs 原始MW评分 对比')
print(f'{"=" * 80}')

# 原始评分分层
for conf, filter_fn in [('高', lambda s: s['orig_conf']=='高'), ('中', lambda s: s['orig_conf']=='中'), ('低', lambda s: s['orig_conf']=='低')]:
    bucket = [s for s in scored if filter_fn(s)]
    if not bucket: continue
    tech_avg = np.mean([s['tech_score'] for s in bucket])
    n = len(bucket)
    wr = np.mean([s['is_win'] for s in bucket]) * 100
    avg = np.mean([s['net_ret'] for s in bucket])
    print(f'  原始{conf}置信: {n}条 原始胜率={wr:.1f}%  平均技术分={tech_avg:.0f}')

# 技术面评分分层交叉
print(f'\n  交叉: 原始×技术面')
for orig_conf in ['高','中','低']:
    for tech_lo, tech_hi, tech_conf in [(55,100,'高'),(40,54,'中'),(0,39,'低')]:
        bucket = [s for s in scored if s['orig_conf']==orig_conf and tech_lo<=s['tech_score']<tech_hi]
        if len(bucket) < 5: continue
        n = len(bucket); wr = np.mean([s['is_win'] for s in bucket]) * 100
        avg = np.mean([s['net_ret'] for s in bucket])
        print(f'    原始{orig_conf}+技术{tech_conf}: {n}条 胜率={wr:.1f}% 收益={avg:.2f}%')

print('\n评分规则总结:')
print('  距MA20≤5%(15) 5-10(12) 10-15(8) 15-25(4) >25(0)')
print('  距MA50≤8%(15) 8-15(10) 15-25(5) >25(0)')
print('  距MA250≤15%(15) 15-25(10) 25-35(5) >35(0)')
print('  BIAS≤8%(10) 8-15(7) 15-25(3) >25(0)')
print('  RPS20: 40-75(10) 30-40或75-85(6) >85(2) <30(4)')
print('  RPS60: 40-70(10) 30-40或70-80(6) >80(2) <30(4)')
print('  RPS250: 50-70(5) >70(3) else(2)')
print('  MACD: DIF>0且<2%(15) DIF>0(12) 近零轴(8) else(3) 金叉扣3')
print('  KDJ: K≤75(5) 75-85(3) >85(0)')
print('  满分: 100')
