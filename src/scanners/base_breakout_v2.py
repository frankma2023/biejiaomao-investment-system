"""
基部突破识别引擎 V2 — 缠论H/L/C驱动

与 V1 的关键区别:
  1. H/L 定位: 缠论笔顶/笔底替代局部窗口法
  2. C 区间: 缠论定义（L后振幅<10%的横盘段）
  3. BO 单信号: 取代 V1 的自研基部检测
  4. 参数对齐欧奈尔经典规则 (深度8~40%, MA10>MA20, 量比≥1.5x)

用法:
  python base_breakout_v2.py --stock 600519 --date 2026-06-05
"""

import sys, os, json, argparse, sqlite3, yaml
from datetime import datetime, timedelta
from typing import Optional, Dict, List

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.dirname(os.path.abspath(__file__))  # src/scanners/
PARENT_SRC = os.path.dirname(SRC_DIR)  # src/
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, PARENT_SRC)  # 确保 from scanners.chanlun import 可解析
sys.path.insert(0, SRC_DIR)   # 确保 from chanlun import 可解析
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "base_breakout_v2",
    "display_name": "基部突破V2",
    "category": "breakout",
    "version": "2.0",
    "description": "缠论H/L/C驱动的通用基部突破检测，覆盖杯柄/碟形/双重底/平底/高紧旗等形态"
}

_chanlun_cache = {}


def load_params():
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "base_breakout_v2.yaml")
    defaults = {
        'drawdown_min': 0.08,        # 最小调整深度 8%
        'drawdown_max': 0.40,        # 最大调整深度 40%
        'min_c_days': 5,             # C 区间最少交易日
        'c_amp_max': 0.15,           # C 区间最大振幅
        'bo_gain_min': 0.03,         # 突破日最低涨幅 3%
        'bo_vol_ratio': 1.5,         # 突破日量 vs 20日均量
        'bo_close_pos_min': 0.50,    # 收盘在日内最低位置
        'require_ma_cross': True,    # 要求 MA10 > MA20
        'rs_threshold': 80,          # RS 最低阈值
        'quiet_vol_check': True,     # 盘整期量能萎缩检查
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        defaults.update(cfg.get('base_breakout', {}))
    return defaults


def sma(values, n):
    clean = [v for v in values if v is not None]
    if len(clean) < n: return None
    return sum(clean[-n:]) / n


def get_hlc_structure(klines, code):
    """
    获取 H/L/C 结构，优先从 mw_signal_daily 表，其次缠论笔
    """
    # 1. 尝试从 MW 信号表获取
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT h_date, h_price, l_date, l_price, c_start, c_end, decline_pct
        FROM mw_signal_daily WHERE stock_code=? ORDER BY b2_date DESC LIMIT 1
    """, (code,)).fetchone()
    conn.close()
    
    if row and row['h_date'] and row['l_date']:
        dates = [k['date'] for k in klines]
        try:
            h_idx = dates.index(row['h_date'])
            l_idx = dates.index(row['l_date'])
            c_start = dates.index(row['c_start']) if row['c_start'] in dates else l_idx
            c_end = dates.index(row['c_end']) if row['c_end'] in dates else l_idx
            # 计算真实回撤深度：从 H 到 K 线区间最低点
            real_low = min(klines[i]['close'] for i in range(h_idx, len(klines)))
            real_decline = round((row['h_price'] - real_low) / row['h_price'] * 100, 2)
            return {
                'h_date': row['h_date'], 'h_price': row['h_price'], 'h_idx': h_idx,
                'l_date': row['l_date'], 'l_price': row['l_price'], 'l_idx': l_idx,
                'c_start_idx': c_start, 'c_end_idx': c_end,
                'c_start_date': row['c_start'], 'c_end_date': row['c_end'],
                'decline_pct': real_decline
            }
        except (ValueError, KeyError):
            pass
    
    # 2. 兜底：缠论笔检测
    return get_hlc_from_chanlun(klines, code)
    """复用口袋支点V3/MW引擎的缠论H/L/C检测"""
    global _chanlun_cache
    dates = [k['date'] for k in klines]
    n = len(klines)
    
    bi_list = None
    if code not in _chanlun_cache:
        bi_list = None
        try:
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute(
                "SELECT bi_json FROM chanlun_scan_daily WHERE stock_code=? ORDER BY scan_date DESC LIMIT 1",
                (code,)).fetchone()
            conn.close()
            if row and row[0]:
                try: bi_list = json.loads(row[0])
                except: bi_list = None
        except Exception as e:
            pass
        if bi_list is None:
            try:
                from scanners.chanlun import analyze
                result = analyze(code, 'D', 500, data_mode='stock')
                bi_list = result.get('bi_list', [])
            except Exception as e:
                bi_list = []
        _chanlun_cache[code] = bi_list
    else:
        bi_list = _chanlun_cache[code]
    
    if not bi_list: return None
    
    # 找最近的前高 H（笔顶，方向=向下）
    tops = [(b['sdt'][:10], b['high']) for b in bi_list if b['direction'] == '向下']
    tops.sort(key=lambda x: x[0], reverse=True)
    h_date = h_price = h_idx = None
    for top_date, top_price in tops:
        if top_date > klines[-1]['date']: continue
        try: top_idx = dates.index(top_date)
        except: continue
        if top_idx + 1 < n:
            future_low = min(klines[j]['close'] for j in range(top_idx+1, n))
            decline = (top_price - future_low) / top_price if top_price > 0 else 0
            if decline < 0.10: continue
            pre60_start = max(0, top_idx - 60)
            pre60_low = min(klines[j]['close'] for j in range(pre60_start, top_idx)) if pre60_start < top_idx else top_price
            pre_rise = (top_price - pre60_low) / pre60_low if pre60_low > 0 else 0
            if pre_rise >= 0.20:
                h_date, h_price, h_idx = top_date, top_price, top_idx
                break
    if h_idx is None: return None
    
    # 找 L（笔底，方向=向上）
    bots = [(b['sdt'][:10], b['low']) for b in bi_list if b['direction'] == '向上']
    l_idx = l_price = None
    for bot_date, bot_price in bots:
        if bot_date > h_date:
            try: l_idx = dates.index(bot_date); l_price = bot_price
            except: pass
            break
    if l_idx is None: return None
    
    # 找 C 区间（L 之后振幅 < 10% 的横盘段）
    c_start = l_idx; c_end = l_idx
    for i in range(l_idx, min(l_idx + 30, n)):
        seg = [klines[j]['close'] for j in range(l_idx, i+1)]
        seg_min, seg_max = min(seg), max(seg)
        amp = (seg_max - seg_min) / seg_min if seg_min > 0 else 999
        if amp <= 0.10: c_end = i
        elif i - l_idx >= 3: break
    
    return {
        'h_date': h_date, 'h_price': h_price, 'h_idx': h_idx,
        'l_date': dates[l_idx], 'l_price': l_price, 'l_idx': l_idx,
        'c_start_idx': c_start, 'c_end_idx': c_end,
        'c_start_date': dates[c_start], 'c_end_date': dates[c_end],
        'decline_pct': round((h_price - l_price) / h_price * 100, 2)
    }


def detect(daily, params=None):
    """
    检测基部突破信号

    Args:
        daily: list[dict], 至少 120 条 K 线 (date, open, high, low, close, volume, amount)
        params: dict or None, 参数覆盖

    Returns:
        list[dict]: 信号列表，每个信号含 signal_date, prior_high_date, trough_date 等
    """
    if params is None: params = load_params()
    
    code = params.get('stock_code', '') or (daily[-1].get('stock_code', '') if daily else '')
    
    n = len(daily)
    if n < 120: return []
    
    # 获取缠论 H/L/C 结构
    hlc = get_hlc_structure(daily, code)
    if not hlc: return []
    
    # 参数提取
    dd_min = params.get('drawdown_min', 0.08)
    dd_max = params.get('drawdown_max', 0.40)
    min_c_days = params.get('min_c_days', 5)
    c_amp_max = params.get('c_amp_max', 0.15)
    bo_gain = params.get('bo_gain_min', 0.03)
    bo_vol = params.get('bo_vol_ratio', 1.5)
    bo_pos = params.get('bo_close_pos_min', 0.50)
    require_ma = params.get('require_ma_cross', True)
    rs_min = params.get('rs_threshold', 80)
    quiet_vol = params.get('quiet_vol_check', True)
    
    decline = hlc['decline_pct']
    if decline < dd_min * 100 or decline > dd_max * 100: return []
    
    c_days = hlc['c_end_idx'] - hlc['c_start_idx'] + 1
    if c_days < min_c_days: return []
    
    # C 区间振幅检查
    c_closes = [daily[i]['close'] for i in range(hlc['c_start_idx'], hlc['c_end_idx'] + 1)]
    c_amp = (max(c_closes) - min(c_closes)) / min(c_closes) if min(c_closes) > 0 else 999
    if c_amp > c_amp_max: return []
    
    # 盘整期量能萎缩
    if quiet_vol and c_days >= 6:
        mid = c_days // 2
        v1 = [daily[hlc['c_start_idx'] + i]['volume'] for i in range(mid)]
        v2 = [daily[hlc['c_start_idx'] + mid + i]['volume'] for i in range(mid)]
        if v1 and v2 and sum(v2)/len(v2) > sum(v1)/len(v1) * 1.15: return []
    
    # 扫描 C 区间之后的日子，找 BO 突破日
    signals = []
    today = daily[-1]  # 默认检测最后一天
    last_idx = n - 1
    
    # 只在 C 区间之后检查
    if last_idx <= hlc['c_end_idx']: return []
    
    for t_idx in range(hlc['c_end_idx'] + 1, n):
        k = daily[t_idx]
        c, v, o, h, l = k['close'], k['volume'], k['open'], k['high'], k['low']
        if c <= 0 or v <= 0: continue
        
        # BO 涨幅 ≥ 3%
        if t_idx == 0: continue
        gain = (c - daily[t_idx-1]['close']) / daily[t_idx-1]['close']
        if gain < bo_gain: continue
        
        # BO 量 ≥ 20日均量 × 1.5
        vol_20 = [daily[j]['volume'] for j in range(max(0, t_idx-20), t_idx)]
        avg20 = sum(vol_20) / len(vol_20) if vol_20 else 0
        if avg20 <= 0 or v < avg20 * bo_vol: continue
        
        # BO 量 > 前10天最大下跌量
        max_down = 0
        for j in range(max(0, t_idx-10), t_idx):
            if daily[j]['close'] < daily[j-1]['close']:
                if daily[j]['volume'] > max_down: max_down = daily[j]['volume']
        if max_down > 0 and v <= max_down: continue
        
        # 收盘位置
        if h > l:
            pos = (c - l) / (h - l)
            if pos < bo_pos: continue
        
        # MA10 > MA20
        closes_all = [d['close'] for d in daily[:t_idx+1]]
        ma10 = sma(closes_all, 10)
        ma20 = sma(closes_all, 20)
        if require_ma and (ma10 is None or ma20 is None or ma10 <= ma20): continue
        
        # 收盘站上 MA10 和 MA20
        if ma10 and c <= ma10: continue
        if ma20 and c <= ma20: continue
        
        # 突破 C 区间最高价
        c_max = max(daily[i]['close'] for i in range(hlc['c_start_idx'], hlc['c_end_idx'] + 1))
        if c <= c_max: continue
        
        # 当日最高 ≥ 前10天最高（突破前期阻力）
        prev_highs = [daily[j]['high'] for j in range(max(0, t_idx-10), t_idx)]
        if prev_highs and h < max(prev_highs): continue
        
        # RS 强度（如果有的话）
        rps20 = k.get('rps_20', 0) or k.get('rps20', 0) or 0
        rps250 = k.get('rps_250', 0) or k.get('rps250', 0) or 0
        if rps20 > 0 and rps250 > 0:
            if rps20 < rs_min and rps250 < rs_min: continue
        
        # === 通过所有检查，产生信号 ===
        signals.append({
            'signal_date': k['date'],
            'prior_high_date': hlc['h_date'],
            'prior_high_price': round(hlc['h_price'], 2),
            'trough_date': hlc['l_date'],
            'trough_price': round(hlc['l_price'], 2),
            'drawdown_pct': hlc['decline_pct'],
            'base_days': c_days,
            'c_amplitude': round(c_amp * 100, 1),
            'breakout_close': round(c, 2),
            'breakout_gain_pct': round(gain * 100, 2),
            'breakout_vol_ratio': round(v / avg20, 2) if avg20 > 0 else 0,
            'close_position': round(pos, 2),
            'ma10': round(ma10, 2) if ma10 else None,
            'ma20': round(ma20, 2) if ma20 else None,
            'rps_20': rps20 if rps20 > 0 else None,
            'rps_250': rps250 if rps250 > 0 else None,
            'buy_point': round(c_max + 0.01, 2),
        })
    
    return signals


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='基部突破检测 V2')
    parser.add_argument('--stock', type=str, default='600519')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--mode', type=str, default='stock')
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    table = 'index_daily_kline' if args.mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if args.mode == 'index' else ''
    rows = conn.execute(f"""
        SELECT date, open, high, low, close, volume, amount FROM {table}
        WHERE stock_code=? {kf} AND date<=? AND date>=date(?, '-500 days')
        ORDER BY date
    """, (args.stock, args.date, args.date)).fetchall()
    conn.close()

    if len(rows) < 120:
        print(f"K线不足: {len(rows)} 条 (需要 >= 120)")
        sys.exit(1)

    daily = [dict(r) for r in rows]
    params = load_params()
    params['stock_code'] = args.stock
    signals = detect(daily, params)

    print(f"🔍 {args.stock} @ {args.date}")
    print(f"   信号数: {len(signals)}")
    for s in signals:
        print(f"   📅 {s['signal_date']} | 前高¥{s['prior_high_price']} → 低¥{s['trough_price']} "
              f"({s['drawdown_pct']}%) | 基部{s['base_days']}天")
        print(f"      突破¥{s['breakout_close']} (+{s['breakout_gain_pct']}%) "
              f"量比{s['breakout_vol_ratio']}x | MA10={s['ma10']} MA20={s['ma20']}")
