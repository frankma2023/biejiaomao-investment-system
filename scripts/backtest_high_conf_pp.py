"""
高置信度口袋支点回测：口袋支点日满足 B1 条件 → 次日开盘买入
回测区间：2023-06-01 ~ 2026-06-05
"""
import sqlite3, sys, os, json
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

DB = "D:/hanako/investment-system/data/lixinger.db"

def sma(values, n):
    if len(values) < n: return None
    return sum(values[-n:]) / n

def get_trading_dates(start, end):
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT DISTINCT date FROM daily_kline WHERE stock_code='000001' AND date>=? AND date<=? ORDER BY date",
                      (start, end)).fetchall()
    db.close()
    return [r[0] for r in rows]

def check_b1(klines, idx, c_start, c_end):
    """检查当天是否满足 MW B1 条件"""
    k = klines[idx]
    if idx < 10: return False
    
    # B1 涨幅 ≥ 2%
    ret = (k['close'] / klines[idx-1]['close'] - 1) if idx > 0 else 0
    if ret < 0.02: return False
    
    # B1 量：> 前10天最大下跌量 AND > 20日均量 × 1.3
    max_down_vol = 0
    for j in range(max(0, idx-10), idx):
        if klines[j]['close'] < klines[j-1]['close']:
            if klines[j]['volume'] > max_down_vol:
                max_down_vol = klines[j]['volume']
    vol_20 = [k['volume'] for k in klines[max(0,idx-20):idx]]
    avg20 = sum(vol_20)/len(vol_20) if vol_20 else 0
    if not (k['volume'] > max_down_vol and (avg20 == 0 or k['volume'] / avg20 >= 1.3)):
        return False
    
    # B1 MA: close > MA5, close > MA10, MA5 > MA10
    closes_all = [kl['close'] for kl in klines[:idx+1]]
    ma5 = sma(closes_all, 5)
    ma10 = sma(closes_all, 10)
    if not (ma5 and ma10 and k['close'] > ma5 and k['close'] > ma10 and ma5 > ma10):
        return False
    
    # B1 空间：收盘 > C区最高收盘价
    if c_start >= 0 and c_end < idx:
        c_max_close = max(klines[j]['close'] for j in range(c_start, c_end+1))
        if k['close'] <= c_max_close:
            return False
    
    return True

def find_hlc_from_chanlun(klines, code, db_conn):
    """复用口袋支点引擎的缠论H/L/C检测"""
    global _chanlun_cache
    dates = [k['date'] for k in klines]
    n = len(klines)
    
    if '_chanlun_cache' not in globals():
        globals()['_chanlun_cache'] = {}
    
    bi_list = None
    if code not in _chanlun_cache:
        if db_conn:
            row = db_conn.execute(
                "SELECT bi_json FROM chanlun_scan_daily WHERE stock_code=? ORDER BY scan_date DESC LIMIT 1",
                (code,)).fetchone()
            if row and row[0]:
                try: bi_list = json.loads(row[0])
                except: pass
        if bi_list is None:
            try:
                from scanners.chanlun import analyze
                result = analyze(code, 'D', 500, data_mode='stock')
                bi_list = result.get('bi_list', [])
            except:
                bi_list = []
        _chanlun_cache[code] = bi_list
    else:
        bi_list = _chanlun_cache[code]
    
    if not bi_list: return None
    
    # 找 H
    tops = [(b['sdt'][:10], b['high']) for b in bi_list if b['direction'] == '向下']
    tops.sort(key=lambda x: x[0], reverse=True)
    h_date = h_price = h_idx = None
    for top_date, top_price in tops:
        if top_date > klines[-1]['date']: continue
        try: top_idx = dates.index(top_date)
        except: continue
        if top_idx + 1 < n:
            future_low = min(klines[j]['close'] for j in range(top_idx+1, n))
            decline = (top_price - future_low)/top_price if top_price > 0 else 0
            if decline < 0.10: continue
            pre60_start = max(0, top_idx-60)
            pre60_low = min(klines[j]['close'] for j in range(pre60_start, top_idx)) if pre60_start < top_idx else top_price
            pre_rise = (top_price - pre60_low)/pre60_low if pre60_low > 0 else 0
            if pre_rise >= 0.20:
                h_date, h_price, h_idx = top_date, top_price, top_idx
                break
    if h_idx is None: return None
    
    # 找 L
    bots = [(b['sdt'][:10], b['low']) for b in bi_list if b['direction'] == '向上']
    l_idx = l_price = None
    for bot_date, bot_price in bots:
        if bot_date > h_date:
            try: l_idx = dates.index(bot_date); l_price = bot_price
            except: pass
            break
    if l_idx is None: return None
    
    # 找 C
    c_start = l_idx; c_end = l_idx
    for i in range(l_idx, min(l_idx+30, n)):
        seg = [klines[j]['close'] for j in range(l_idx, i+1)]
        seg_min, seg_max = min(seg), max(seg)
        amp = (seg_max - seg_min)/seg_min if seg_min > 0 else 999
        if amp <= 0.10: c_end = i
        elif i - l_idx >= 3: break
    
    return {'h_date': h_date, 'h_price': h_price, 'l_date': dates[l_idx],
            'c_start': c_start, 'c_end': c_end}

def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    
    # 获取持仓期间的 K 线
    all_codes = set()
    code_rows = db.execute("SELECT DISTINCT stock_code FROM daily_kline WHERE date >= '2023-06-01'").fetchall()
    all_codes = set(r[0] for r in code_rows)
    
    # 先获取候选股票池（成交额 >= 5000万的活跃股）
    print("Loading candidate stocks...")
    candidates = set()
    for r in db.execute("""
        SELECT k.stock_code FROM daily_kline k JOIN stock_basic b ON k.stock_code=b.stock_code
        WHERE b.listing_status='normally_listed' AND b.name NOT LIKE '%ST%'
        AND k.date >= '2023-06-01' AND k.amount >= 50000000
        GROUP BY k.stock_code
    """).fetchall():
        candidates.add(r[0])
    print(f"  {len(candidates)} candidates")
    
    # 预加载 MW 结构的 H/L/C
    print("Loading MW structures...")
    mw_structures = {}
    for r in db.execute("""
        SELECT stock_code, h_date, h_price, l_date, l_price, c_start, c_end, b1_date, decline_pct
        FROM mw_signal_daily WHERE b2_date >= '2023-06-01' ORDER BY b2_date DESC
    """).fetchall():
        code = r['stock_code']
        if code not in mw_structures:
            mw_structures[code] = {
                'h_date': r['h_date'], 'h_price': r['h_price'],
                'l_date': r['l_date'], 'l_price': r['l_price'],
                'c_start': r['c_start'], 'c_end': r['c_end'],
                'b1_date': r['b1_date']
            }
    print(f"  {len(mw_structures)} stocks with MW structures")
    
    # 按月批量处理
    start_date = datetime(2023, 6, 1)
    end_date = datetime(2026, 6, 5)
    current = start_date
    
    all_signals = []  # {date, code, name, b1_gain, ...}
    
    month_count = 0
    while current <= end_date:
        month_end = min(datetime(current.year, current.month, 28) + timedelta(days=4), end_date)
        month_end = month_end.replace(day=1) + timedelta(days=32)
        month_end = month_end.replace(day=1) - timedelta(days=1)
        if month_end > end_date: month_end = end_date
        
        month_start_str = current.strftime('%Y-%m-%d')
        month_end_str = month_end.strftime('%Y-%m-%d')
        
        month_count += 1
        print(f"\n[{month_count}] {month_start_str} ~ {month_end_str}", end=' ', flush=True)
        
        # 加载这个月的 K 线数据
        kline_data = defaultdict(list)
        rows = db.execute("""
            SELECT stock_code, date, open, high, low, close, volume, amount
            FROM daily_kline WHERE date >= ? AND date <= ?
            ORDER BY stock_code, date
        """, (month_start_str, month_end_str)).fetchall()
        
        for r in rows:
            if r['stock_code'] in candidates:
                kline_data[r['stock_code']].append(dict(r))
        
        # 遍历每天每只股票
        days_in_month = get_trading_dates(month_start_str, month_end_str)
        month_signals = 0
        
        for day in days_in_month:
            # 加载这天的 RS 数据
            rs_data = {}
            for r in db.execute("""
                SELECT stock_code, rps_20, rps_250 FROM stock_rs_daily
                WHERE date <= ? ORDER BY date DESC
            """, (day,)).fetchall():
                if r['stock_code'] not in rs_data:
                    rs_data[r['stock_code']] = (r['rps_20'], r['rps_250'])
            
            for code in list(kline_data.keys())[:500]:  # 限制每月处理数量
                klines = kline_data[code]
                dates_k = [k['date'] for k in klines]
                if day not in dates_k: continue
                idx = dates_k.index(day)
                if idx < 65: continue
                
                today = klines[idx]
                c, v = today['close'], today['volume']
                if c <= 0 or v <= 0: continue
                
                # 趋势过滤
                closes_all = [k['close'] for k in klines[:idx+1]]
                sma10 = sma(closes_all, 10); sma60 = sma(closes_all, 60)
                if not (sma10 and sma60 and c > sma60 and c > sma10): continue
                
                # RS 过滤
                rps = rs_data.get(code, (None, None))
                if not (rps[0] and rps[1]): continue
                if not (rps[0] >= 80 or rps[1] >= 80): continue
                
                # 口袋支点量价规则
                gain_pct = (c - klines[idx-1]['close'])/klines[idx-1]['close']*100
                if gain_pct < 3: continue
                
                # 成交量
                down_vols = []
                for i in range(max(0, idx-10), idx):
                    if klines[i]['close'] < klines[i-1]['close']:
                        down_vols.append(klines[i]['volume'])
                if down_vols and v <= max(down_vols): continue
                
                # 收盘位置
                hl_range = today['high'] - today['low']
                if hl_range <= 0: continue
                close_pos = (c - today['low'])/hl_range
                if close_pos < 0.50: continue
                
                # 突破前高
                prev_highs = [klines[i]['high'] for i in range(max(0, idx-10), idx)]
                if prev_highs and today['high'] < max(prev_highs): continue
                
                # 以上是口袋支点条件。现在检查 B1 条件
                # 先获取 H/L/C
                structure = mw_structures.get(code)
                c_start_idx = c_end_idx = -1
                if structure:
                    l_date = structure['l_date']
                    if l_date in dates_k:
                        c_start_idx = dates_k.index(l_date)
                        c_end_str = structure['c_end']
                        if c_end_str in dates_k:
                            c_end_idx = dates_k.index(c_end_str)
                
                if c_start_idx < 0:
                    hlc = find_hlc_from_chanlun(klines, code, db)
                    if hlc:
                        c_start_idx = hlc['c_start']
                        c_end_idx = hlc['c_end']
                
                is_b1 = check_b1(klines, idx, c_start_idx, c_end_idx) if c_start_idx >= 0 else False
                
                if is_b1:
                    # 找次日开盘价
                    next_idx = idx + 1
                    if next_idx >= len(klines): continue
                    next_open = klines[next_idx]['open']
                    if not next_open or next_open <= 0: continue
                    
                    all_signals.append({
                        'date': day,
                        'code': code,
                        'entry_date': klines[next_idx]['date'],
                        'entry_price': next_open,
                        'b1_gain': gain_pct,
                        'close': c,
                        'volume': v,
                        'rps20': rps[0], 'rps250': rps[1],
                    })
                    month_signals += 1
        
        print(f"→ {month_signals} signals ({len(all_signals)} total)", end='', flush=True)
        current = month_end + timedelta(days=1)
    
    print(f"\n\n总计: {len(all_signals)} 个高置信度口袋支点信号")
    
    if not all_signals:
        print("无信号，请检查数据")
        db.close()
        return
    
    # 计算 Forward Returns（从次日开盘）
    print("\n计算 forward returns...")
    # 批量加载 K 线
    codes = list(set(s['code'] for s in all_signals))
    pc = defaultdict(dict)
    for code in codes:
        rows = db.execute("""
            SELECT date, close FROM daily_kline
            WHERE stock_code=? AND date >= '2023-06-01' AND date <= '2026-07-31'
            ORDER BY date
        """, (code,)).fetchall()
        pc[code] = {r['date']: r['close'] for r in rows}
    
    rets = {5: [], 10: [], 20: []}
    for s in all_signals:
        code = s['code']
        prices = pc.get(code, {})
        dates_list = sorted(prices.keys())
        entry_date = s['entry_date']
        if entry_date not in prices: continue
        entry = s['entry_price']
        try: idx = dates_list.index(entry_date)
        except: continue
        for h in [5, 10, 20]:
            fut = idx + h
            if fut < len(dates_list):
                rets[h].append((prices[dates_list[fut]] - entry) / entry * 100)
    
    # 统计
    from collections import Counter
    print(f"\n{'='*60}")
    print(f"  高置信度口袋支点（B1重合）回测")
    print(f"  区间: 2023-06-01 ~ 2026-06-05")
    print(f"  入场: 信号次日开盘价")
    print(f"{'='*60}")
    
    for h in [5, 10, 20]:
        r = rets[h]
        if not r: continue
        wins = sum(1 for v in r if v > 0)
        wr = wins / len(r) * 100
        median = sorted(r)[len(r)//2]
        avg = sum(r) / len(r)
        print(f"\n  {h}日持有:")
        print(f"    信号数: {len(r)}")
        print(f"    胜率:   {wr:.1f}%")
        print(f"    中位:   {median:+.2f}%")
        print(f"    平均:   {avg:+.2f}%")
        
        # 收益分布
        buckets = [('>20%', 20), ('10~20%', 10), ('5~10%', 5), ('0~5%', 0),
                   ('-5~0%', -5), ('-10~-5%', -10), ('<-10%', float('-inf'))]
        print(f"    分布:")
        for label, threshold in buckets:
            if threshold == float('-inf'):
                cnt = sum(1 for v in r if v <= -10)
            else:
                cnt = sum(1 for v in r if v > threshold and (label.startswith('>') or v <= threshold + (10 if '10' in label else 5)))
            # simpler approach
            cnt2 = 0
            if label == '>20%': cnt2 = sum(1 for v in r if v > 20)
            elif label == '10~20%': cnt2 = sum(1 for v in r if 10 < v <= 20)
            elif label == '5~10%': cnt2 = sum(1 for v in r if 5 < v <= 10)
            elif label == '0~5%': cnt2 = sum(1 for v in r if 0 < v <= 5)
            elif label == '-5~0%': cnt2 = sum(1 for v in r if -5 < v <= 0)
            elif label == '-10~-5%': cnt2 = sum(1 for v in r if -10 < v <= -5)
            elif label == '<-10%': cnt2 = sum(1 for v in r if v <= -10)
            bar = '█' * int(cnt2 / max(1, len(r)) * 40)
            print(f"      {label}: {cnt2:>4} ({cnt2/len(r)*100:>5.1f}%) {bar}")
    
    # 月度分布
    monthly = defaultdict(lambda: {'count': 0, 'wins_10': 0, 'ret_sum_10': 0})
    for i, s in enumerate(all_signals):
        m = s['date'][:7]
        monthly[m]['count'] += 1
        if i < len(rets[10]):
            if rets[10][i] > 0:
                monthly[m]['wins_10'] += 1
            monthly[m]['ret_sum_10'] += rets[10][i]
    
    print(f"\n  月度信号分布 (Top 10):")
    for m in sorted(monthly, key=lambda x: -monthly[x]['count'])[:10]:
        d = monthly[m]
        wr10 = d['wins_10']/d['count']*100 if d['count'] else 0
        print(f"    {m}: {d['count']:>4}个 10d胜率{wr10:.0f}%")
    
    db.close()

if __name__ == '__main__':
    main()
